"""Anmeldung und Absicherung des Endpunkts.

Drei Dinge, die vor jeder Nachricht geklärt sein müssen: **wer** fragt,
**woher** die Frage kommt und dass der Zugang nirgends nachlesbar liegenbleibt.

**Wer.** Zwei Wege führen zum Token, in dieser Reihenfolge geprüft:

1. ``Authorization: Bearer <Token>`` — der bessere Weg. Header stehen in keinem
   Zugriffsprotokoll und in keinem ``Referer``.
2. ``?token=<Token>`` im Query-String — die Rückfallebene. Damit genügt eine
   Adresse, und ein Client kommt ohne Brücke aus, die Node.js auf jedem Rechner
   voraussetzt.

Der zweite Weg ist der schwächere, und das soll hier stehen: ein Query-String
landet in Zugriffsprotokollen, in Verlaufslisten und möglicherweise im
``Referer``. Wir nehmen es in Kauf, weil eine Node-Abhängigkeit auf jedem
Client-Rechner der höhere Preis wäre — und gleichen es an drei Stellen aus: der
Query-String bleibt aus den Zugriffsprotokollen (Gunicorn-Format, NPM), was
trotzdem irgendwo auftaucht, wird durch :func:`mask` gekürzt, und ein Zugang
läuft von selbst ab.

**Woher.** Die MCP-Spezifikation verlangt für Streamable HTTP die Prüfung des
``Origin``-Headers gegen DNS-Rebinding: eine fremde Webseite soll den lokal
erreichbaren Server nicht im Namen des Browsers ansprechen können. Fehlt der
Header, ist das kein Ablehnungsgrund — ``curl`` und native Clients senden
keinen, und genau die sollen sich verbinden dürfen.
"""

import logging

from django.conf import settings
from django.http import JsonResponse

from services.models import MCPToken

logger = logging.getLogger(__name__)

#: Name des Query-Parameters. Gleich benannt wie bei Zenico, Agira und
#: Moneyplan — eine Adresse aus einem dieser Dienste liest sich damit wie die
#: aus jedem anderen.
TOKEN_PARAM = "token"


# --------------------------------------------------------------------------
# Wer fragt
# --------------------------------------------------------------------------


def _from_header(request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _from_query(request) -> str:
    return request.GET.get(TOKEN_PARAM, "").strip()


def token_of(request):
    """Der benutzbare Token der Anfrage — ``None``, wenn keiner gilt.

    Der Header hat Vorrang: schickt ein Client beides, ist das gewollte
    Geheimnis das aus dem Kopf. Widerruf und Ablauf sind bei
    :meth:`MCPToken.resolve` bereits ausgewertet und wirken damit auf beiden
    Wegen sofort.
    """
    key = _from_header(request) or _from_query(request)
    if not key:
        return None
    return MCPToken.resolve(key)


def unauthorized(message="Kein gültiger Zugang."):
    response = JsonResponse({"error": message}, status=401)
    response["WWW-Authenticate"] = 'Bearer realm="MyAquaDiary MCP"'
    return response


# --------------------------------------------------------------------------
# Woher die Anfrage kommt
# --------------------------------------------------------------------------


def allowed_origins() -> list:
    return list(getattr(settings, "MCP_ALLOWED_ORIGINS", []))


def origin_allowed(request) -> bool:
    """Ob die Anfrage von einer zugelassenen Herkunft kommt.

    Ohne ``Origin``-Header: ja. Der Header ist eine Angabe des Browsers über
    sich selbst; wer keinen schickt, ist keiner, und gegen den richtet sich die
    Prüfung nicht.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return origin in allowed_origins()


def forbidden_origin(request):
    logger.warning("MCP-Anfrage von unzulässiger Herkunft %s", request.headers.get("Origin"))
    return JsonResponse(
        {"error": "Diese Herkunft ist für den MCP-Endpunkt nicht zugelassen."},
        status=403,
    )
