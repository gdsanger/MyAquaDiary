"""Der Endpunkt: SSE-Strom und Nachrichtenkanal.

Der SSE-Transport des MCP besteht aus zwei Adressen:

``GET /mcp/sse/``
    hält die Verbindung offen. Erste Nachricht ist ein ``endpoint``-Ereignis
    mit der Adresse, an die der Client seine Anfragen schicken soll; danach
    kommen die Antworten und in den Pausen Keep-alive-Zeilen.

``POST /mcp/messages/?session=…``
    nimmt eine JSON-RPC-Nachricht entgegen, quittiert mit ``202`` und legt die
    Antwort in den Strom.

Angemeldet wird sich bei **beiden** mit ``Authorization: Bearer <Token>``. Die
Sitzungskennung allein ist kein Zugang: sie steht in der Antwort des ersten
Requests und wäre damit ein zu leicht abzuschauendes Geheimnis. Geprüft wird
deshalb bei jedem POST erneut — und zwar der Token, nicht die Sitzung.
"""

import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from services.models import MCPToken

from . import protocol, sessions
from .runner import Context

logger = logging.getLogger(__name__)

#: Abstand der Keep-alive-Zeilen. Kurz genug, dass kein Proxy die Verbindung
#: für tot hält, lang genug, dass es kein Dauerfeuer wird.
DEFAULT_KEEPALIVE = 15


def keepalive_seconds() -> int:
    return int(getattr(settings, "MCP_KEEPALIVE_SECONDS", DEFAULT_KEEPALIVE))


# --------------------------------------------------------------------------
# Anmeldung
# --------------------------------------------------------------------------


def bearer_token(request):
    """Der Token aus dem ``Authorization``-Header — ``None``, wenn keiner gilt.

    Bewusst nur der Header und kein Query-Parameter: eine Adresse mit Token
    darin landet in Proxy-Logs, im Verlauf und in Fehlerberichten.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return MCPToken.resolve(value.strip())


def _unauthorized(message="Kein gültiger Zugang."):
    response = JsonResponse({"error": message}, status=401)
    response["WWW-Authenticate"] = 'Bearer realm="MyAquaDiary MCP"'
    return response


# --------------------------------------------------------------------------
# SSE-Strom
# --------------------------------------------------------------------------


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def stream(session, endpoint_url, keepalive):
    """Der Inhalt des SSE-Stroms.

    Läuft, bis der Client geht: dann scheitert der nächste Schreibvorgang, die
    Generatorfunktion endet und ``finally`` räumt die Sitzung ab.
    """
    try:
        yield _sse("endpoint", endpoint_url)
        while True:
            message = session.take(keepalive)
            if message is None:
                # Kommentarzeile — für SSE-Clients bedeutungslos, für Proxies
                # das Lebenszeichen, das die Verbindung offen hält.
                yield ": keep-alive\n\n"
            else:
                yield _sse("message", json.dumps(message, ensure_ascii=False))
            session.heartbeat()
    finally:
        sessions.close(session)


@csrf_exempt
@require_GET
def sse(request):
    """Öffnet den Strom. Ohne gültigen Token gibt es hier gar nichts."""
    token = bearer_token(request)
    if token is None:
        return _unauthorized()

    token.touch()
    session = sessions.open_session(token)
    endpoint_url = f"{reverse('mcp:messages')}?session={session.id}"

    response = StreamingHttpResponse(
        stream(session, endpoint_url, keepalive_seconds()),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    # Ohne diesen Kopf puffert nginx den Strom und der Client wartet auf
    # Antworten, die längst geschrieben sind.
    response["X-Accel-Buffering"] = "no"
    return response


# --------------------------------------------------------------------------
# Nachrichten
# --------------------------------------------------------------------------


@csrf_exempt
@require_POST
def messages(request):
    """Nimmt eine JSON-RPC-Nachricht an und beantwortet sie über den Strom.

    Kein CSRF-Schutz und keine Session-Cookies: die Anmeldung läuft über den
    Token im Header, und ein Browser-Formular kann diesen Header nicht setzen.
    """
    token = bearer_token(request)
    if token is None:
        return _unauthorized()

    session = sessions.get(request.GET.get("session", ""))
    if session is None or session.token_id != token.pk:
        # Auch die fremde Sitzung endet hier: eine Sitzungskennung ist kein
        # Ausweis, und wer sie hat, kommt trotzdem nicht an fremde Ströme.
        return JsonResponse(
            {"error": "Unbekannte Sitzung. Zuerst den SSE-Strom öffnen."}, status=404
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return JsonResponse(
            protocol.error_response(None, protocol.PARSE_ERROR, "Kein gültiges JSON."),
            status=400,
        )

    answer = protocol.handle_message(Context(token=token, user=token.user), payload)
    if answer is not None:
        session.push(answer)
    return HttpResponse(status=202)
