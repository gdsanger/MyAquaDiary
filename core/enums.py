"""Auswahllisten, die von mehreren Apps geteilt werden.

Die Werte sind bewusst so gewaehlt, dass sie direkt als CSS-Klassensuffix
taugen (``mad-watertype--fresh``, ``mad-status--critical``). So bleibt die
Farbzuordnung an einer Stelle — im Stylesheet — und Templates kommen ohne
Hex-Werte und ohne Farb-Fallunterscheidungen aus.
"""

from django.db import models


class WaterType(models.TextChoices):
    FRESHWATER = "fresh", "Süßwasser"
    BRACKISH = "brackish", "Brackwasser"
    MARINE = "marine", "Meerwasser"


class Difficulty(models.TextChoices):
    EASY = "easy", "Einfach"
    MEDIUM = "medium", "Mittel"
    HARD = "hard", "Anspruchsvoll"


class Status(models.TextChoices):
    """Einheitliche Statusskala: gruen — orange — rot.

    Gilt fuer Messwerte, Termine, Geraete und Besatzgruppen gleichermassen.
    ``UNKNOWN`` faellt im Stylesheet auf die neutrale Darstellung zurueck.
    """

    OK = "ok", "In Ordnung"
    WARN = "warn", "Abweichung"
    CRITICAL = "critical", "Kritisch"
    UNKNOWN = "unknown", "Unbekannt"


#: Rangfolge fuer das Sortieren von Warnungen (kritisch zuerst).
STATUS_SEVERITY = {
    Status.CRITICAL: 0,
    Status.WARN: 1,
    Status.OK: 2,
    Status.UNKNOWN: 3,
}
