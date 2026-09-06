"""Prüfung der Aufrufparameter.

Ein Modell hält sich nicht zuverlässig an ein JSON-Schema: es schickt „12“
statt 12, ein Datum als „2026-09-01T00:00“ statt „2026-09-01“ oder ein Feld,
das es sich ausgedacht hat. Deshalb wird hier jeder Parameter einzeln geholt,
geprüft und umgewandelt — und bei Unsinn kommt ein Satz zurück, aus dem
hervorgeht, was erwartet wird.

Das Schema in der Werkzeugbeschreibung bleibt trotzdem wichtig: es ist die
Anleitung für den Client. Verlassen tut sich der Server darauf nicht.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .exceptions import ToolError

#: Obergrenze für ``limit`` in allen Listen-Werkzeugen. Ein Modell mit
#: begrenztem Kontext hat von 5000 Messreihen nichts — und der Server auch nicht.
MAX_LIMIT = 200


class Arguments:
    """Die ``arguments`` eines ``tools/call``, typsicher ausgelesen."""

    def __init__(self, raw):
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ToolError("Die Parameter müssen ein JSON-Objekt sein.")
        self.raw = raw

    # -- Grundtypen -----------------------------------------------------------

    def _get(self, name, required, default):
        value = self.raw.get(name)
        if value is None or value == "":
            if required:
                raise ToolError(f"Der Parameter „{name}“ fehlt.")
            return default
        return value

    def integer(self, name, *, required=False, default=None, minimum=None, maximum=None):
        value = self._get(name, required, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, str, float)):
            raise ToolError(f"„{name}“ muss eine ganze Zahl sein.")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ToolError(f"„{name}“ muss eine ganze Zahl sein.") from None
        if minimum is not None and number < minimum:
            raise ToolError(f"„{name}“ darf nicht kleiner als {minimum} sein.")
        if maximum is not None and number > maximum:
            raise ToolError(f"„{name}“ darf nicht größer als {maximum} sein.")
        return number

    def limit(self, default=50):
        """Anzahl der Treffer — immer gedeckelt, auch wenn der Client mehr will."""
        return self.integer("limit", default=default, minimum=1, maximum=MAX_LIMIT)

    def text(self, name, *, required=False, default="", max_length=None):
        value = self._get(name, required, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ToolError(f"„{name}“ muss Text sein.")
        value = value.strip()
        if required and not value:
            raise ToolError(f"Der Parameter „{name}“ fehlt.")
        if max_length and len(value) > max_length:
            raise ToolError(f"„{name}“ ist zu lang (höchstens {max_length} Zeichen).")
        return value

    def boolean(self, name, *, default=False):
        value = self.raw.get(name)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ToolError(f"„{name}“ muss true oder false sein.")

    def choice(self, name, choices, *, required=False, default=None):
        """Ein Wert aus einer festen Liste — bei Unsinn mit Aufzählung zurück."""
        value = self.text(name, required=required, default="")
        if not value:
            return default
        if value not in choices:
            raise ToolError(
                f"„{value}“ ist kein gültiger Wert für „{name}“. Möglich sind: "
                + ", ".join(sorted(choices))
                + "."
            )
        return value

    def decimal(self, name, *, required=False, default=None, minimum=None):
        value = self._get(name, required, default)
        if value is None:
            return None
        if isinstance(value, bool):
            raise ToolError(f"„{name}“ muss eine Zahl sein.")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ToolError(f"„{name}“ muss eine Zahl sein.") from None
        if minimum is not None and number < minimum:
            raise ToolError(f"„{name}“ darf nicht kleiner als {minimum} sein.")
        return number

    # -- Zeit -----------------------------------------------------------------

    def date(self, name, *, required=False, default=None):
        """Datum als ``JJJJ-MM-TT``; ein mitgeschickter Zeitanteil wird gekappt."""
        value = self.text(name, required=required)
        if not value:
            return default
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            raise ToolError(f"„{name}“ muss ein Datum im Format JJJJ-MM-TT sein.") from None

    def datetime(self, name, *, required=False, default=None):
        """Zeitpunkt nach ISO 8601.

        Ohne Zeitzone wird die des Servers angenommen: ein Client, der
        „2026-09-01 20:15“ meint, meint die Uhrzeit vor dem Becken.
        """
        value = self.text(name, required=required)
        if not value:
            return default
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(
                f"„{name}“ muss ein Zeitpunkt nach ISO 8601 sein, z. B. 2026-09-01T20:15."
            ) from None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    # -- Verschachteltes ------------------------------------------------------

    def objects(self, name, *, required=False):
        """Eine Liste von JSON-Objekten, z. B. die Messwerte einer Messreihe."""
        value = self.raw.get(name)
        if value is None:
            if required:
                raise ToolError(f"Der Parameter „{name}“ fehlt.")
            return []
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ToolError(f"„{name}“ muss eine Liste von Objekten sein.")
        if required and not value:
            raise ToolError(f"„{name}“ darf nicht leer sein.")
        return [Arguments(item) for item in value]
