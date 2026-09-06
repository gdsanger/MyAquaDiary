"""KI-Assistenz auf Basis von Anthropic Claude.

Assistenz, kein Automat: jeder Aufruf geht von einer Handlung des Benutzers
aus, jedes Ergebnis ist ein Vorschlag, und nichts davon verändert von selbst
Katalog, Technik oder Termine.

Öffentliche Schnittstelle::

    from services.ai import ai_enabled, identify, save_suggestion, draft_profile

    if ai_enabled():
        found = identify(request.FILES["photo"], "animal", user=request.user)
        for candidate in found.candidates:
            ...

Der API-Key wird ausschließlich in :mod:`services.ai.client` gelesen und
erscheint in keinem Log und keiner Meldung.
"""

import logging

from django.db import DatabaseError

from ..models import AIConfig
from .budget import BudgetStatus, check as check_budget
from .budget import status as budget_status
from .catalog import CatalogMatch, find_matches
from .client import AIResult, AIService
from .exceptions import AIBudgetExceeded, AIError, AINotConfigured, AIResponseError
from .images import PreparedImage, prepare_image
from .prompts import ReportPeriod, StockItem, TankFacts
from .usecases import (
    Answer,
    Candidate,
    Identification,
    check_stocking,
    confirm_suggestion,
    draft_profile,
    identify,
    read_measurements,
    reject_suggestion,
    save_suggestion,
    tank_report,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AIBudgetExceeded",
    "AIError",
    "AINotConfigured",
    "AIResponseError",
    "AIResult",
    "AIService",
    "Answer",
    "BudgetStatus",
    "Candidate",
    "CatalogMatch",
    "Identification",
    "PreparedImage",
    "ReportPeriod",
    "StockItem",
    "TankFacts",
    "ai_enabled",
    "budget_status",
    "check_budget",
    "check_stocking",
    "confirm_suggestion",
    "draft_profile",
    "find_matches",
    "identify",
    "prepare_image",
    "read_measurements",
    "reject_suggestion",
    "save_suggestion",
    "tank_report",
]


def ai_enabled() -> bool:
    """True, wenn die KI-Funktionen benutzbar sind.

    Fängt auch Datenbankfehler ab (z. B. noch nicht migrierte Tabelle), damit
    Templates und Views die KI-Funktionen gefahrlos ausblenden können — ohne
    Key ist schlicht nichts davon zu sehen.
    """
    try:
        return AIConfig.load().is_configured
    except DatabaseError:
        logger.warning("KI-Konfiguration konnte nicht gelesen werden — Assistenz inaktiv")
        return False
