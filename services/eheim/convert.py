"""Umrechnung zwischen den Konventionen der Eheim-API und Python-Typen.

Die API hat drei Eigenheiten, die genau hier gekapselt werden und in Views,
Templates oder Modellen nicht mehr auftauchen dürfen:

1. **Zeitangaben sind Minuten seit Mitternacht.** 15:00 Uhr = ``900``, die
   Tagphase 11:00–23:00 also ``660``–``1380``.
2. **Temperaturen sind Zehntelwerte ohne Komma**, die Einheit steckt separat
   im Feld ``mUnit`` (0 = Celsius, 1 = Fahrenheit). 23,5 °C = ``235``.
   Wechselt die Einheit, müssen alle betroffenen Parameter neu gesendet
   werden — dafür gibt es :func:`convert_temperature`.
3. **Booleans sind 1 und 0**, nicht ``true``/``false``.

Alle ``to_*``-Funktionen sind streng (ungültige Eingaben werfen
``ValueError``), alle ``parse_*``-Funktionen sind tolerant und liefern bei
unbrauchbaren Werten ``None`` — Gerätantworten sind nichts, worauf man eine
Seite abstürzen lassen möchte.
"""

import datetime
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from enum import IntEnum

MINUTES_PER_DAY = 24 * 60

_TRUTHY = {"1", "true", "on", "yes", "ja"}
_FALSY = {"0", "false", "off", "no", "nein"}


class TemperatureUnit(IntEnum):
    """Werte des API-Feldes ``mUnit``."""

    CELSIUS = 0
    FAHRENHEIT = 1


# --------------------------------------------------------------------------
# 1. Minuten seit Mitternacht
# --------------------------------------------------------------------------


def to_minutes(value) -> int:
    """Wandelt eine Uhrzeit in Minuten seit Mitternacht.

    Akzeptiert ``datetime.time``, ``datetime.datetime``, ``"15:00"`` sowie
    bereits fertige Minutenwerte (``900``).

    >>> to_minutes("15:00")
    900
    """
    if isinstance(value, bool):
        raise ValueError("Uhrzeit erwartet, kein Boolean")
    if isinstance(value, datetime.datetime):
        value = value.time()
    if isinstance(value, datetime.time):
        return value.hour * 60 + value.minute
    if isinstance(value, str):
        text = value.strip()
        if ":" in text:
            parts = text.split(":")
            try:
                hours, minutes = int(parts[0]), int(parts[1])
            except (ValueError, IndexError) as exc:
                raise ValueError(f"Uhrzeit {value!r} nicht lesbar") from exc
            return _validate_minutes(hours * 60 + minutes)
        value = text
    try:
        return _validate_minutes(int(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Uhrzeit {value!r} nicht lesbar") from exc


def from_minutes(value) -> datetime.time:
    """Minuten seit Mitternacht als ``datetime.time``.

    >>> from_minutes(900)
    datetime.time(15, 0)
    """
    minutes = _validate_minutes(int(value))
    return datetime.time(hour=minutes // 60, minute=minutes % 60)


def format_minutes(value) -> str:
    """Minuten seit Mitternacht als ``"15:00"`` — für die Anzeige."""
    return from_minutes(value).strftime("%H:%M")


def parse_minutes(value):
    """Tolerante Variante von :func:`to_minutes` für Gerätantworten."""
    try:
        return to_minutes(value)
    except (ValueError, TypeError):
        return None


def _validate_minutes(minutes: int) -> int:
    if not 0 <= minutes < MINUTES_PER_DAY:
        raise ValueError(f"Minuten seit Mitternacht müssen 0–{MINUTES_PER_DAY - 1} sein, nicht {minutes}")
    return minutes


# --------------------------------------------------------------------------
# 2. Zehntelwerte (Temperatur)
# --------------------------------------------------------------------------


def to_tenths(value) -> int:
    """Wandelt einen Messwert in den Zehntelwert der API.

    >>> to_tenths(Decimal("23.5"))
    235
    """
    decimal = _as_decimal(value)
    return int((decimal * 10).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_tenths(value) -> Decimal:
    """Zehntelwert der API als Dezimalzahl.

    >>> from_tenths(235)
    Decimal('23.5')
    """
    return (_as_decimal(value) / 10).quantize(Decimal("0.1"))


def parse_tenths(value):
    """Tolerante Variante von :func:`from_tenths` für Gerätantworten."""
    try:
        return from_tenths(value)
    except (ValueError, TypeError, DecimalException):
        return None


def convert_temperature(value, from_unit, to_unit) -> Decimal:
    """Rechnet eine Temperatur zwischen Celsius und Fahrenheit um.

    Nötig beim Wechsel von ``mUnit``: alle betroffenen Parameter müssen danach
    in der neuen Einheit neu gesendet werden.
    """
    from_unit, to_unit = TemperatureUnit(int(from_unit)), TemperatureUnit(int(to_unit))
    decimal = _as_decimal(value)
    if from_unit is to_unit:
        return decimal.quantize(Decimal("0.1"))
    if to_unit is TemperatureUnit.FAHRENHEIT:
        converted = decimal * Decimal("1.8") + 32
    else:
        converted = (decimal - 32) / Decimal("1.8")
    return converted.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Zahl erwartet, nicht {value!r}")
    if isinstance(value, str):
        # Deutsche Eingaben aus Formularen kommen mit Komma an.
        value = value.strip().replace(",", ".")
    try:
        return Decimal(str(value))
    except (ArithmeticError, DecimalException, ValueError) as exc:
        raise ValueError(f"Zahl erwartet, nicht {value!r}") from exc


# --------------------------------------------------------------------------
# 3. Booleans als 1 / 0
# --------------------------------------------------------------------------


def to_api_bool(value) -> int:
    """Wandelt einen Wahrheitswert in die ``1``/``0`` der API."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUTHY:
            return 1
        if text in _FALSY:
            return 0
        raise ValueError(f"Wahrheitswert erwartet, nicht {value!r}")
    return 1 if value else 0


def parse_api_bool(value):
    """``1``/``0`` der API als ``bool`` — unbekannte Werte ergeben ``None``."""
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


# --------------------------------------------------------------------------
# Tolerante Ganzzahl für Gerätantworten
# --------------------------------------------------------------------------


def parse_int(value):
    """Ganzzahl aus einer Gerätantwort — ``None``, wenn nicht lesbar."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(_as_decimal(value).to_integral_value(rounding=ROUND_HALF_UP))
    except (ValueError, TypeError, DecimalException):
        return None
