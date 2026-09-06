"""Fehler des MCP-Servers.

Alles, was ein Client falsch machen kann, endet in einem :class:`ToolError`.
Dessen Meldung geht als Werkzeug-Fehler zurück an das Modell und ist deshalb in
ganzen deutschen Sätzen formuliert: sie ist keine Log-Zeile, sondern die
Antwort, aus der ein Modell ableiten soll, was es anders machen muss.

Nicht in der Meldung stehen darf, ob ein fremder Datensatz existiert. Ein
unbekanntes und ein fremdes Becken sind für den Client derselbe Fall.
"""


class MCPError(Exception):
    """Basisklasse aller MCP-Fehler."""


class ToolError(MCPError):
    """Ein Aufruf, den der Client so nicht stellen durfte oder konnte."""


class NotFound(ToolError):
    """Kein Datensatz dieser Kennung im Zugriff des Token-Inhabers."""


class WriteNotAllowed(ToolError):
    """Schreibendes Werkzeug an einem Token ohne Schreibrecht."""


class RateLimited(ToolError):
    """Zu viele Aufrufe in zu kurzer Zeit."""


class DataModelUnavailable(ToolError):
    """Die Tagebuch-Modelle sind in dieser Installation (noch) nicht da."""
