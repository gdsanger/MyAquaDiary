"""Ratelimit je Token.

Ein fehlkonfigurierter Client, der in einer Schleife ``create_event`` aufruft,
schreibt sonst die Datenbank voll. Gezählt wird je Token und Minute im Cache:
das kostet keine Datenbankschreibvorgänge und ist genau dort schnell, wo es
schnell sein muss — vor jedem Aufruf.

Bewusst ein festes Minutenfenster und kein gleitendes: an der Fensterkante sind
kurzzeitig bis zu zwei Fenster voll möglich. Das ist als Schutz gegen eine
Endlosschleife völlig ausreichend und in drei Zeilen zu verstehen.

Der Standard-Cache ist prozesslokal. Der MCP-Server ist ein eigener Prozess —
das passt. Wer ihn auf mehrere Worker verteilt, hinterlegt einen gemeinsamen
Cache (Redis, Memcached); an diesem Modul ändert sich dadurch nichts.
"""

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .exceptions import RateLimited

#: So lange bleibt ein Zähler stehen — knapp über der Fensterlänge, damit ein
#: Aufruf am Fensterende seinen eigenen Zähler nicht überlebt.
COUNTER_TTL = 120


def limit_per_minute() -> int:
    """Aufrufe je Token und Minute; 0 oder kleiner schaltet die Prüfung ab."""
    return int(getattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 60))


def _key(token_id: int, window: int) -> str:
    return f"mcp:rate:{token_id}:{window}"


def check(token) -> int:
    """Zählt einen Aufruf und lässt ihn durch, solange das Limit reicht.

    :returns: Anzahl der Aufrufe im laufenden Fenster.
    :raises RateLimited: wenn das Limit überschritten ist.
    """
    limit = limit_per_minute()
    if limit <= 0:
        return 0

    window = int(timezone.now().timestamp() // 60)
    key = _key(token.pk, window)
    # add() schreibt nur, wenn der Schlüssel noch nicht existiert — danach ist
    # incr() atomar und der Zähler auch bei parallelen Aufrufen richtig.
    cache.add(key, 0, COUNTER_TTL)
    try:
        used = cache.incr(key)
    except ValueError:
        # Der Schlüssel ist zwischen add() und incr() abgelaufen.
        cache.set(key, 1, COUNTER_TTL)
        used = 1

    if used > limit:
        raise RateLimited(
            f"Zu viele Aufrufe: höchstens {limit} je Minute. Warte einen Moment "
            "und versuche es erneut."
        )
    return used
