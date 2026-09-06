"""Der Rahmen um jeden Werkzeugaufruf.

Fünf Schritte, immer dieselben, an genau einer Stelle:

1. Werkzeug nachschlagen,
2. Schreibrecht prüfen — ein Lese-Token schreibt nicht, auch nicht mit
   erratenem Werkzeugnamen,
3. Ratelimit zählen,
4. Werkzeug ausführen, eingegrenzt auf den Token-Inhaber,
5. schreibende Aufrufe protokollieren — auch die abgewiesenen.

Dass hier alles zusammenläuft, ist Absicht: eine Regel, die in jedem Werkzeug
einzeln stünde, fehlt irgendwann in einem.
"""

import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import DatabaseError

from . import ratelimit, registry
from .audit import access_log
from .exceptions import ToolError, WriteNotAllowed
from .params import Arguments
from .registry import Written

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Context:
    """Wer da fragt.

    Der Token bestimmt den Benutzer, der Benutzer bestimmt die Daten. Einen
    Weg, das im Aufruf zu überschreiben, gibt es nicht — kein Werkzeug nimmt
    eine Benutzerkennung entgegen.
    """

    token: object
    user: object

    @property
    def can_write(self) -> bool:
        return bool(self.token.allow_write)


def call_tool(context: Context, name: str, arguments: dict):
    """Führt ein Werkzeug aus und gibt sein Ergebnis zurück.

    :raises ToolError: bei allem, was der Client falsch gemacht hat. Der
        Aufrufer macht daraus die Fehlerantwort des Protokolls.
    """
    found = registry.get(name)

    if found.writes and not context.can_write:
        message = (
            "Dieser Zugang darf nur lesen. Schreibende Werkzeuge sind für den "
            "verwendeten Token abgeschaltet."
        )
        _log(context, found, arguments, succeeded=False, error=message)
        raise WriteNotAllowed(message)

    ratelimit.check(context.token)
    # „Zuletzt benutzt“ zählt den Werkzeugaufruf, nicht die offene Verbindung:
    # ein Client, der nur die Leitung hält, hat den Zugang nicht gebraucht.
    context.token.touch()

    try:
        result = found.handler(context, Arguments(arguments))
    except ToolError as exc:
        if found.writes:
            _log(context, found, arguments, succeeded=False, error=str(exc))
        raise
    except ValidationError as exc:
        # Modellprüfungen der Web-App (z. B. „Bestand kann nicht unter null
        # fallen“) sind für den Client eine ganz normale Fehlermeldung.
        message = "; ".join(exc.messages)
        if found.writes:
            _log(context, found, arguments, succeeded=False, error=message)
        raise ToolError(message) from exc
    except DatabaseError:
        logger.exception("MCP-Werkzeug %s ist an der Datenbank gescheitert", name)
        if found.writes:
            _log(context, found, arguments, succeeded=False, error="Datenbankfehler")
        raise ToolError("Der Aufruf ist an der Datenbank gescheitert.") from None

    if isinstance(result, Written):
        _log(context, found, arguments, succeeded=True, ref=result.ref)
        return result.data
    return result


def _log(context, found, arguments, *, succeeded, ref="", error=""):
    """Schreibt den Protokolleintrag — und lässt den Aufruf nicht daran scheitern.

    Ein Protokoll, das den Vorgang mitreißt, den es protokollieren soll, ist
    schlechter als ein fehlender Eintrag mit Log-Zeile.
    """
    try:
        access_log(
            context,
            tool=found.name,
            arguments=arguments,
            succeeded=succeeded,
            object_ref=ref,
            error_message=error,
        )
    except DatabaseError:
        logger.exception("MCP-Protokolleintrag zu %s konnte nicht geschrieben werden", found.name)
