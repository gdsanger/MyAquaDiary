"""Kontext, den jedes Template braucht."""

from .ai import ai_enabled


def ai_status(request) -> dict:
    """Stellt ``ai_enabled`` bereit.

    Die Navigation blendet die KI-Funktionen damit vollständig aus, solange
    kein API-Key hinterlegt ist — ein Menüpunkt, der nur zu einer Fehlermeldung
    führt, ist schlechter als gar keiner.
    """
    return {"ai_enabled": ai_enabled()}
