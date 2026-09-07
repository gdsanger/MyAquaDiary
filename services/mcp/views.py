"""Der Endpunkt.

Ein Pfad, ein Verfahren: ``POST /mcp/`` nimmt eine JSON-RPC-Nachricht entgegen
und antwortet **in derselben Antwort**. Das ist der Transport *Streamable HTTP*
der MCP-Spezifikation, der den älteren HTTP+SSE-Transport ab Revision
2025-03-26 ablöst.

Was dadurch wegfällt, ist mehr als ein Pfad: Es gibt nichts mehr, das zwischen
zwei Requests vermitteln müsste. Kein Zustand im Prozess, keine Sitzung, kein
``Mcp-Session-Id``, keine Aufräumlogik — jede Anfrage bringt ihren Zugang
selbst mit und wird für sich autorisiert. Der Dienst darf damit auf mehreren
Workern laufen, und ein Widerruf wirkt sofort, weil es nichts gibt, das ihn
überdauert.

Angemeldet wird sich über ``Authorization: Bearer <Token>`` **oder** über
``?token=<Token>`` in der Adresse (siehe :mod:`services.mcp.auth`). Der zweite
Weg ist der Grund, warum eine reine URL genügt und keine Brücke mit Node.js auf
dem Client-Rechner nötig ist.

Die alten Adressen ``/mcp/sse/`` und ``/mcp/messages/`` bleiben vorerst
bedienbar, damit bestehende Konfigurationen weiterlaufen. Sie melden ihren
Verfall über die Header ``Deprecation`` und ``Sunset`` und ins Log; abschalten
lassen sie sich mit ``MCP_LEGACY_SSE = False`` — dann ist der Dienst
tatsächlich zustandslos.
"""

import json
import logging
from datetime import datetime, time, timezone as dt_timezone

from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import http_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import auth, protocol, sessions
from .runner import Context

logger = logging.getLogger(__name__)

#: Abstand der Keep-alive-Zeilen im alten SSE-Strom. Kurz genug, dass kein
#: Proxy die Verbindung für tot hält, lang genug, dass es kein Dauerfeuer wird.
DEFAULT_KEEPALIVE = 15

#: Ab wann die alten Adressen abgeschaltet werden. Steht in den ``Sunset``-
#: Headern und ist damit für den Client maschinell lesbar.
DEFAULT_SUNSET = "2026-12-31"


def keepalive_seconds() -> int:
    return int(getattr(settings, "MCP_KEEPALIVE_SECONDS", DEFAULT_KEEPALIVE))


def legacy_enabled() -> bool:
    return bool(getattr(settings, "MCP_LEGACY_SSE", True))


# --------------------------------------------------------------------------
# Streamable HTTP
# --------------------------------------------------------------------------


@csrf_exempt
def endpoint(request):
    """``POST /mcp/`` — eine Nachricht rein, die Antwort direkt zurück.

    Kein CSRF-Schutz und keine Session-Cookies: die Anmeldung hängt am Token,
    nicht am Browser. Gegen die Lücke, die das im Browser aufmachen würde,
    steht die Herkunftsprüfung davor.
    """
    if not auth.origin_allowed(request):
        return auth.forbidden_origin(request)

    if request.method != "POST":
        # Ein serverseitiger Strom (``GET /mcp/``) ist ausdrücklich nicht
        # umgesetzt: wir verschicken keine unaufgeforderten Nachrichten, und
        # eine dauerhaft offene Verbindung brächte genau die Betriebsprobleme
        # zurück, die dieser Transport loswird. Die Spezifikation sieht die
        # Absage mit 405 vor.
        response = JsonResponse(
            {"error": "Dieser Endpunkt beantwortet ausschließlich POST."}, status=405
        )
        response["Allow"] = "POST"
        return response

    token = auth.token_of(request)
    if token is None:
        return auth.unauthorized()

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return JsonResponse(
            protocol.error_response(None, protocol.PARSE_ERROR, "Kein gültiges JSON."),
            status=400,
        )

    if _is_initialize(payload):
        # „Zuletzt benutzt“ soll auch dann etwas zeigen, wenn ein Client sich
        # nur verbindet und die Werkzeugliste liest. Der Werkzeugaufruf selbst
        # zählt in :mod:`services.mcp.runner` mit.
        token.touch()

    answer = protocol.handle_message(Context(token=token, user=token.user), payload)
    if answer is None:
        # Nur Benachrichtigungen — es gibt nichts zu antworten. Die
        # Spezifikation verlangt hier 202 mit leerem Rumpf.
        return HttpResponse(status=202)
    return JsonResponse(answer, safe=False, json_dumps_params={"ensure_ascii": False})


def _is_initialize(payload) -> bool:
    messages = payload if isinstance(payload, list) else [payload]
    return any(
        isinstance(item, dict) and item.get("method") == "initialize" for item in messages
    )


# --------------------------------------------------------------------------
# Alter Transport: SSE-Strom und Nachrichtenkanal
# --------------------------------------------------------------------------


def sunset_date() -> str:
    return str(getattr(settings, "MCP_LEGACY_SUNSET", DEFAULT_SUNSET))


def _deprecate(response):
    """Kennzeichnet eine Antwort des alten Transports als auslaufend.

    ``Deprecation`` und ``Sunset`` (RFC 9745, RFC 8594) sagen einem Client
    maschinenlesbar, dass diese Adresse verschwindet, ``Link`` nennt die
    Nachfolgerin. An **jeder** Antwort des alten Transports, nicht nur an der
    ersten: welche davon ein Client liest, entscheidet er.
    """
    moment = datetime.combine(
        datetime.strptime(sunset_date(), "%Y-%m-%d").date(), time.min, dt_timezone.utc
    )
    response["Deprecation"] = "@%d" % int(timezone.now().timestamp())
    response["Sunset"] = http_date(moment.timestamp())
    response["Link"] = '<%s>; rel="successor-version"' % reverse("mcp:endpoint")
    return response


def _gone():
    return JsonResponse(
        {
            "error": "Der SSE-Transport ist abgeschaltet. Der Endpunkt ist "
            f"jetzt {reverse('mcp:endpoint')} (Streamable HTTP)."
        },
        status=410,
    )


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
    if not auth.origin_allowed(request):
        return auth.forbidden_origin(request)
    if not legacy_enabled():
        return _gone()

    token = auth.token_of(request)
    if token is None:
        return auth.unauthorized()

    logger.warning(
        "Veralteter MCP-Transport benutzt: %s. Streamable HTTP steht unter %s "
        "bereit, Abschaltung zum %s.",
        request.path,
        reverse("mcp:endpoint"),
        sunset_date(),
    )

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
    return _deprecate(response)


@csrf_exempt
@require_POST
def messages(request):
    """Nimmt eine JSON-RPC-Nachricht an und beantwortet sie über den Strom."""
    if not auth.origin_allowed(request):
        return auth.forbidden_origin(request)
    if not legacy_enabled():
        return _gone()

    token = auth.token_of(request)
    if token is None:
        return auth.unauthorized()

    session = sessions.get(request.GET.get("session", ""))
    if session is None or session.token_id != token.pk:
        # Auch die fremde Sitzung endet hier: eine Sitzungskennung ist kein
        # Ausweis, und wer sie hat, kommt trotzdem nicht an fremde Ströme.
        return _deprecate(
            JsonResponse(
                {"error": "Unbekannte Sitzung. Zuerst den SSE-Strom öffnen."}, status=404
            )
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _deprecate(
            JsonResponse(
                protocol.error_response(None, protocol.PARSE_ERROR, "Kein gültiges JSON."),
                status=400,
            )
        )

    answer = protocol.handle_message(Context(token=token, user=token.user), payload)
    if answer is not None:
        session.push(answer)
    return _deprecate(HttpResponse(status=202))
