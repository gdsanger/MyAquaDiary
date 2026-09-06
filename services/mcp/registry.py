"""Verzeichnis der Werkzeuge.

Ein Werkzeug ist eine Funktion plus die Beschreibung, mit der ein Modell
entscheidet, ob es sie aufruft. Beides steht beieinander — die Beschreibung ist
Teil der Schnittstelle, nicht Dokumentation nebenher.

``writes=True`` ist keine Beschriftung, sondern die Grundlage von zwei Regeln:
ein Token ohne Schreibrecht bekommt solche Werkzeuge gar nicht erst zu sehen,
und jeder Aufruf wird protokolliert (siehe :mod:`services.mcp.runner`).
"""

from dataclasses import dataclass, field
from typing import Callable

from .exceptions import ToolError

#: Leeres Schema für Werkzeuge ohne Parameter. MCP verlangt ein Objekt-Schema,
#: auch wenn nichts hineingeht.
EMPTY_SCHEMA = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class Written:
    """Rückgabe eines schreibenden Werkzeugs.

    ``ref`` verweist auf den angelegten Datensatz (``tanks.Measurement:12``) und
    landet im Protokoll — damit lässt sich später von einem fragwürdigen
    Datensatz auf den Aufruf zurückschließen, der ihn erzeugt hat.
    """

    data: object
    ref: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: Callable
    schema: dict = field(default_factory=lambda: dict(EMPTY_SCHEMA))
    writes: bool = False

    def definition(self) -> dict:
        """Der Eintrag, wie ihn ``tools/list`` ausliefert."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
            # Hinweis für Clients, die schreibende Werkzeuge bestätigen lassen.
            "annotations": {
                "readOnlyHint": not self.writes,
                "destructiveHint": False,
            },
        }


_TOOLS: dict[str, Tool] = {}


def tool(name, description, *, schema=None, writes=False):
    """Registriert eine Funktion als Werkzeug."""

    def decorator(func):
        if name in _TOOLS:
            raise RuntimeError(f"Das Werkzeug {name} ist bereits registriert.")
        _TOOLS[name] = Tool(
            name=name,
            description=description,
            handler=func,
            schema=schema or dict(EMPTY_SCHEMA),
            writes=writes,
        )
        return func

    return decorator


def get(name: str) -> Tool:
    found = _TOOLS.get(name)
    if found is None:
        raise ToolError(f"Ein Werkzeug namens „{name}“ gibt es hier nicht.")
    return found


def definitions(*, allow_write: bool) -> list[dict]:
    """Alle Werkzeuge, die dieser Token benutzen darf.

    Ein Lese-Token sieht die schreibenden Werkzeuge nicht. Das ist bequemer als
    eine Fehlermeldung nach dem Aufruf — und es hält ein Modell davon ab,
    einen Weg zu suchen, der ihm ohnehin verschlossen ist. Verlassen tut sich
    darauf niemand: geprüft wird beim Aufruf.
    """
    return [
        found.definition()
        for found in _TOOLS.values()
        if allow_write or not found.writes
    ]


def names() -> list[str]:
    return list(_TOOLS)
