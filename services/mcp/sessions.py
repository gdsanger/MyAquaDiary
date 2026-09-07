"""Offene SSE-Sitzungen — **nur noch für den alten Transport**.

Der SSE-Transport des MCP besteht aus zwei Requests: der Client hält einen
GET-Strom offen und schickt seine Anfragen per POST an eine zweite Adresse. Die
Antwort läuft über den Strom zurück. Zwischen beiden Requests muss also etwas
vermitteln — diese Warteschlange.

Sie liegt im Prozess und nicht im Cache oder in der Datenbank, denn sie hängt
an einer offenen Verbindung: ein anderer Prozess könnte mit der Antwort nichts
anfangen. Solange dieses Modul benutzt wird, ist der MCP-Dienst deshalb an
**einen** Prozess gebunden.

Der neue Transport (Streamable HTTP, ``POST /mcp/``) braucht das alles nicht:
er antwortet in derselben Antwort, es gibt nichts zu vermitteln. Dieses Modul
lebt nur noch, damit bestehende Konfigurationen bis zum Abschalttermin
weiterlaufen (``MCP_LEGACY_SSE``), und verschwindet mit ihnen — samt
Aufräumlogik, Keep-alive-Takt und Obergrenze offener Sitzungen.

Am Eintrag steht nur die Token-Kennung, nicht der Token selbst: ob ein Zugang
noch gilt, wird bei jedem POST neu geprüft. Ein Widerruf wirkt damit sofort,
auch mitten in einer laufenden Sitzung.
"""

import logging
import queue
import secrets
import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

#: So lange darf eine Sitzung unbenutzt herumliegen, bevor sie beim nächsten
#: Verbindungsaufbau aufgeräumt wird.
DEFAULT_IDLE_TIMEOUT = 3600
#: Obergrenze offener Sitzungen. Erreicht der Server sie, ist etwas kaputt —
#: dann lieber die älteste schließen als unbegrenzt Speicher belegen.
MAX_SESSIONS = 100

_sessions: dict[str, "Session"] = {}
_lock = threading.Lock()


class Session:
    """Eine offene Verbindung eines Clients."""

    def __init__(self, token):
        self.id = secrets.token_urlsafe(16)
        self.token_id = token.pk
        self.user_id = token.user_id
        self.opened_at = timezone.now()
        self.last_active = self.opened_at
        self.outbox = queue.Queue()

    def push(self, message) -> None:
        """Legt eine Antwort für den Strom bereit."""
        self.heartbeat()
        self.outbox.put(message)

    def heartbeat(self) -> None:
        """Merkt sich, dass die Sitzung eben noch gelebt hat.

        Gesetzt wird das nach jedem erfolgreichen Schreibvorgang im Strom.
        Damit unterscheidet :func:`_prune` eine stille, aber offene Verbindung
        von einer, die abgerissen ist, ohne sich abzumelden.
        """
        self.last_active = timezone.now()

    def take(self, timeout):
        """Wartet auf die nächste Antwort; ``None`` heißt: Zeit abgelaufen.

        Das Warten mit Zeitlimit ist die Grundlage der Keep-alive-Zeilen im
        Strom — ohne sie hält kein Proxy die Verbindung offen.
        """
        try:
            return self.outbox.get(timeout=timeout)
        except queue.Empty:
            return None


def idle_timeout() -> int:
    return int(getattr(settings, "MCP_SESSION_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT))


def open_session(token) -> Session:
    session = Session(token)
    with _lock:
        _prune()
        _sessions[session.id] = session
    logger.info("MCP-Sitzung %s für Token %s geöffnet", session.id, token.pk)
    return session


def get(session_id: str):
    if not session_id:
        return None
    return _sessions.get(session_id)


def close(session: Session) -> None:
    with _lock:
        _sessions.pop(session.id, None)
    logger.info("MCP-Sitzung %s geschlossen", session.id)


def count() -> int:
    return len(_sessions)


def _prune() -> None:
    """Räumt vergessene Sitzungen weg. Aufrufer hält ``_lock``.

    Eine Verbindung, die abreißt, ohne dass der Server es merkt (Netzwerk weg,
    Client abgestürzt), hinterlässt sonst einen Eintrag, den niemand mehr holt.
    """
    deadline = timezone.now() - timedelta(seconds=idle_timeout())
    for session_id, session in list(_sessions.items()):
        if session.last_active < deadline:
            del _sessions[session_id]

    while len(_sessions) >= MAX_SESSIONS:
        oldest = min(_sessions.values(), key=lambda item: item.last_active)
        logger.warning("Zu viele offene MCP-Sitzungen — %s wird verworfen", oldest.id)
        del _sessions[oldest.id]
