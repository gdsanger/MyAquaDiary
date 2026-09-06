"""Umrechnung zwischen den Konventionen der Shelly-API und Python-Typen.

Die eine Eigenheit, auf die es hier ankommt: **Gen1 zählt den Gesamtverbrauch
in Wattminuten**, Gen2 in Wattstunden. Ein Gen1-Zählerstand von ``60000``
entspricht also 1000 Wh. Genau hier wird das umgerechnet; oberhalb dieser
Schicht gibt es nur noch Wattstunden.

Zweite Eigenheit: der Zählerstand ist ein Betriebsstundenzähler, kein
Verbrauch je Zeitraum. Er läuft monoton nach oben und fängt nach einem
Stromausfall (Gen1) wieder bei null an. Aus Zählerständen einen Verbrauch zu
machen ist Sache von :mod:`services.energy`.

Wie bei Eheim gilt: ``parse_*`` ist tolerant und liefert bei unbrauchbaren
Werten ``None`` — Gerätantworten sind nichts, worauf man eine Seite abstürzen
lassen möchte.
"""

from decimal import Decimal, DecimalException, ROUND_HALF_UP

#: Gen1 zählt in Wattminuten.
WATT_MINUTES_PER_WATT_HOUR = Decimal(60)
WATT_HOURS_PER_KILOWATT_HOUR = Decimal(1000)

_TRUTHY = {"1", "true", "on", "yes", "ja"}
_FALSY = {"0", "false", "off", "no", "nein"}


def parse_decimal(value, places="0.01"):
    """Dezimalzahl aus einer Gerätantwort — ``None``, wenn nicht lesbar."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            return None
    try:
        return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)
    except (ArithmeticError, DecimalException, ValueError, TypeError):
        return None


def parse_bool(value):
    """Schaltzustand als ``bool``. Gen1 antwortet mit ``true``/``false``,
    einzelne Endpunkte mit ``"on"``/``"off"`` — beides ist hier zulässig."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUTHY:
            return True
        if text in _FALSY:
            return False
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def parse_int(value):
    """Ganzzahl aus einer Gerätantwort — ``None``, wenn nicht lesbar."""
    decimal = parse_decimal(value, places="1")
    return None if decimal is None else int(decimal)


def watt_minutes_to_watt_hours(value):
    """Gen1-Zählerstand (Wattminuten) als Wattstunden.

    >>> watt_minutes_to_watt_hours(60000)
    Decimal('1000.00')
    """
    minutes = parse_decimal(value, places="0.0001")
    if minutes is None:
        return None
    return (minutes / WATT_MINUTES_PER_WATT_HOUR).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def watt_hours_to_kilowatt_hours(value, places="0.001"):
    """Wattstunden als Kilowattstunden — die Einheit der Auswertung."""
    watt_hours = parse_decimal(value, places="0.0001")
    if watt_hours is None:
        return None
    return (watt_hours / WATT_HOURS_PER_KILOWATT_HOUR).quantize(
        Decimal(places), rounding=ROUND_HALF_UP
    )
