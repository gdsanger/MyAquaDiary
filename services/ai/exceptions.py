"""Fehlerklassen der KI-Assistenz."""


class AIError(Exception):
    """Ein Aufruf der Claude-API ist fehlgeschlagen."""


class AINotConfigured(AIError):
    """Es liegt kein API-Key vor oder die Assistenz ist abgeschaltet."""


class AIBudgetExceeded(AIError):
    """Das Token-Budget ist erschöpft.

    Die Meldung ist für den Benutzer bestimmt und deshalb ausformuliert.
    """


class AIResponseError(AIError):
    """Die Antwort war nicht verwertbar (leer, abgeschnitten, kein JSON)."""
