"""JSON-RPC-Schicht des Model Context Protocol.

MCP ist JSON-RPC 2.0 mit einer festen Handvoll Methoden. Diese Schicht ist
bewusst dünn und ohne Transportbezug: sie bekommt eine Nachricht und gibt eine
Antwort zurück (oder nichts, wenn es eine Notification war). Das macht sie
prüfbar, ohne einen Socket zu öffnen — der SSE-Teil in
:mod:`services.mcp.views` besteht danach nur noch aus Weiterreichen.

Eine Unterscheidung, die das Protokoll trifft und die hier wichtig ist:

* **Protokollfehler** (unbekannte Methode, kaputtes JSON) sind
  ``error``-Antworten — der Client hat sich vertan.
* **Werkzeugfehler** (unbekanntes Becken, fehlender Parameter, kein
  Schreibrecht) sind *erfolgreiche* Antworten mit ``isError: true``. Sie gehen
  an das Modell, nicht an die Klempnerei des Clients: es soll den Fehler lesen
  und es anders versuchen können.
"""

import json
import logging

from . import registry
from .exceptions import ToolError
from .runner import call_tool

logger = logging.getLogger(__name__)

#: Protokollstand, den dieser Server spricht. Fragt ein Client eine andere
#: bekannte Fassung an, wird sie bestätigt — die hier benutzten Methoden sind
#: in allen gleich. Vorgegeben wird eine Revision, die Streamable HTTP kennt:
#: der alte HTTP+SSE-Transport stammt aus 2024-11-05 und ist seit 2025-03-26
#: abgelöst.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

SERVER_NAME = "myaquadiary"
SERVER_VERSION = "1.0"

#: Wird dem Modell einmal beim Verbinden mitgegeben.
INSTRUCTIONS = (
    "Aquarien-Tagebuch des angemeldeten Benutzers. Alle Werkzeuge arbeiten "
    "ausschließlich auf dessen eigenen Becken; eine fremde Kennung gibt es "
    "nicht. Beginne mit list_tanks und benutze die dortige tank_id. Geräte "
    "(Filter, Steckdosen) sind über diese Schnittstelle bewusst nicht "
    "erreichbar, ebenso wenig das Löschen von Daten."
)

# JSON-RPC-Fehlercodes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def handle_message(context, payload):
    """Beantwortet eine Nachricht oder einen Stapel davon.

    :returns: Antwortobjekt, Liste von Antworten oder ``None``, wenn nichts zu
        antworten ist (Notifications).
    """
    if isinstance(payload, list):
        if not payload:
            return error_response(None, INVALID_REQUEST, "Leerer Stapel.")
        answers = [answer for answer in (handle_message(context, item) for item in payload)
                   if answer is not None]
        return answers or None
    if not isinstance(payload, dict):
        return error_response(None, INVALID_REQUEST, "Erwartet wird ein JSON-Objekt.")

    message_id = payload.get("id")
    method = payload.get("method")
    if not isinstance(method, str):
        return error_response(message_id, INVALID_REQUEST, "Ohne „method“ ist das keine Anfrage.")

    handler = _METHODS.get(method)
    if handler is None:
        if method.startswith("notifications/"):
            # Unbekannte Benachrichtigungen sind kein Fehler: sie erwarten
            # keine Antwort, und ein Client darf mehr davon schicken als wir
            # kennen.
            return None
        return error_response(message_id, METHOD_NOT_FOUND, f"Unbekannte Methode „{method}“.")

    if message_id is None:
        # Notification: ausführen, aber nichts zurückgeben.
        handler(context, payload.get("params") or {})
        return None

    try:
        result = handler(context, payload.get("params") or {})
    except Exception:  # pragma: no cover - Notbremse, damit kein Client hängt
        logger.exception("MCP-Methode %s ist gescheitert", method)
        return error_response(message_id, INTERNAL_ERROR, "Interner Fehler.")
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id, code, message):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


# --------------------------------------------------------------------------
# Methoden
# --------------------------------------------------------------------------


def _initialize(context, params):
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": INSTRUCTIONS,
    }


def _tools_list(context, params):
    return {"tools": registry.definitions(allow_write=context.can_write)}


def _tools_call(context, params):
    """Führt ein Werkzeug aus und verpackt das Ergebnis als MCP-Inhalt.

    Ausgegeben wird formatiertes JSON als Text. Das ist der Inhaltstyp, den
    jeder Client versteht — und für ein Sprachmodell besser lesbar als eine
    einzeilige Serialisierung.
    """
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return _error_content("Der Aufruf nennt kein Werkzeug.")
    try:
        result = call_tool(context, name, params.get("arguments") or {})
    except ToolError as exc:
        return _error_content(str(exc))
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
        "isError": False,
    }


def _error_content(message):
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _ping(context, params):
    return {}


def _noop(context, params):
    return {}


_METHODS = {
    "initialize": _initialize,
    "notifications/initialized": _noop,
    "ping": _ping,
    "tools/list": _tools_list,
    "tools/call": _tools_call,
}
