"""Verbrauchsauswertung aus den Zählerständen der Steckdosen.

Eine Shelly-Steckdose meldet keinen Verbrauch je Zeitraum, sondern einen
**Zählerstand**, der monoton nach oben läuft. Der Verbrauch eines Tages ist
damit die Differenz zweier Messwerte — und diese Differenzbildung ist der
gesamte Inhalt dieses Moduls.

Zwei Dinge muss sie aushalten:

* **Zählerreset.** Gen1 fängt nach einem Stromausfall wieder bei null an. Fällt
  der Zählerstand, wird der neue Stand als Verbrauch seit dem Reset gewertet —
  die einzige Annahme, die ohne zusätzliche Information vertretbar ist.
* **Lücken.** Ein Gerät, das eine Nacht lang nicht antwortet, hat keinen
  Verbrauch von null; die Differenz über die Lücke landet in dem Zeitraum, in
  dem wieder gemessen wurde. Das ist an der Grenze ungenau und in der Summe
  richtig.

Der Preis je Kilowattstunde steht in ``settings.ENERGY_PRICE_PER_KWH`` und ist
eine reine Anzeigehilfe: gespeichert werden Kilowattstunden, keine Beträge.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone

from .models import Device, DeviceReading

#: Auswertungszeiträume. Der Schlüssel steht in der URL.
PERIOD_DAY = "tag"
PERIOD_MONTH = "monat"
PERIOD_YEAR = "jahr"
PERIODS = (
    (PERIOD_DAY, "Tag"),
    (PERIOD_MONTH, "Monat"),
    (PERIOD_YEAR, "Jahr"),
)
DEFAULT_PERIOD = PERIOD_MONTH

#: So viele Zeiträume zeigt der Verlauf.
DEFAULT_BUCKETS = 12

WATT_HOURS_PER_KILOWATT_HOUR = Decimal(1000)
DEFAULT_PRICE_PER_KWH = Decimal("0.35")

KWH_PLACES = Decimal("0.001")


def price_per_kwh() -> Decimal:
    """Arbeitspreis je Kilowattstunde aus den Einstellungen."""
    configured = getattr(settings, "ENERGY_PRICE_PER_KWH", DEFAULT_PRICE_PER_KWH)
    try:
        return Decimal(str(configured))
    except (ArithmeticError, ValueError):
        return DEFAULT_PRICE_PER_KWH


def normalize_period(value) -> str:
    """Zeitraum aus einem Request — Unbekanntes fällt auf den Monat zurück."""
    value = str(value or "").strip().lower()
    return value if value in dict(PERIODS) else DEFAULT_PERIOD


def period_label(period: str) -> str:
    return dict(PERIODS).get(normalize_period(period))


def bucket_start(moment, period: str) -> datetime.date:
    """Beginn des Zeitraums, in den ein Zeitpunkt fällt."""
    day = timezone.localtime(moment).date() if timezone.is_aware(moment) else moment.date()
    if period == PERIOD_YEAR:
        return day.replace(month=1, day=1)
    if period == PERIOD_MONTH:
        return day.replace(day=1)
    return day


def bucket_label(start: datetime.date, period: str) -> str:
    if period == PERIOD_YEAR:
        return f"{start:%Y}"
    if period == PERIOD_MONTH:
        return f"{start:%m/%Y}"
    return f"{start:%d.%m.%Y}"


@dataclass(frozen=True)
class Bucket:
    """Verbrauch eines Zeitraums in Kilowattstunden."""

    start: datetime.date
    label: str
    kwh: Decimal

    @property
    def cost(self) -> Decimal:
        return _money(self.kwh * price_per_kwh())


@dataclass(frozen=True)
class Usage:
    """Verbrauch einer Gruppe (Becken oder Gerät) im gewählten Zeitraum."""

    label: str
    kwh: Decimal
    devices: list = field(default_factory=list)

    @property
    def cost(self) -> Decimal:
        return _money(self.kwh * price_per_kwh())

    @property
    def device_names(self) -> str:
        return ", ".join(device.name for device in self.devices)


# --------------------------------------------------------------------------
# Differenzbildung
# --------------------------------------------------------------------------


def consumption_buckets(readings, period: str) -> list[Bucket]:
    """Verbrauch je Zeitraum aus einer Folge von Zählerständen.

    Die Messwerte dürfen in beliebiger Reihenfolge kommen; Messwerte ohne
    Zählerstand fallen heraus. Das Ergebnis ist chronologisch sortiert.
    """
    usable = sorted(
        (reading for reading in readings if reading.energy_total_wh is not None),
        key=lambda reading: reading.read_at,
    )
    totals: dict[datetime.date, Decimal] = {}
    previous = None
    for reading in usable:
        current = Decimal(reading.energy_total_wh)
        if previous is not None:
            # Fällt der Zählerstand, hat das Gerät neu gezählt (Gen1 nach
            # Stromausfall) — dann ist der neue Stand der Verbrauch seither.
            delta = current - previous if current >= previous else current
            start = bucket_start(reading.read_at, period)
            totals[start] = totals.get(start, Decimal(0)) + delta
        previous = current

    return [
        Bucket(start=start, label=bucket_label(start, period), kwh=_kwh(totals[start]))
        for start in sorted(totals)
    ]


def device_buckets(device: Device, period: str, limit: int = DEFAULT_BUCKETS) -> list[Bucket]:
    """Verbrauchshistorie eines Geräts — die letzten ``limit`` Zeiträume."""
    readings = DeviceReading.objects.filter(device=device, energy_total_wh__isnull=False).only(
        "read_at", "energy_total_wh"
    )
    buckets = consumption_buckets(readings, period)
    return buckets[-limit:] if limit else buckets


def device_consumption(device: Device, period: str, start: datetime.date | None = None) -> Decimal:
    """Verbrauch eines Geräts in genau einem Zeitraum (Vorgabe: der laufende)."""
    start = start or bucket_start(timezone.now(), period)
    for bucket in consumption_buckets(_readings_since(device, start), period):
        if bucket.start == start:
            return bucket.kwh
    return Decimal("0.000")


# --------------------------------------------------------------------------
# Vergleich
# --------------------------------------------------------------------------


def usage_by_device(user, period: str, start: datetime.date | None = None) -> list[Usage]:
    """Verbrauch je Steckdose im gewählten Zeitraum, absteigend sortiert."""
    start = start or bucket_start(timezone.now(), period)
    usages = [
        Usage(label=device.name, kwh=device_consumption(device, period, start), devices=[device])
        for device in metered_devices(user)
    ]
    return _sorted(usages)


def usage_by_tank(user, period: str, start: datetime.date | None = None) -> list[Usage]:
    """Verbrauch je Becken im gewählten Zeitraum, absteigend sortiert.

    Der eigentliche Nutzen der Anbindung: was kostet welches Becken im Monat.
    Gruppiert wird über :attr:`services.models.Device.tank_name` — die einzige
    Stelle, die sich ändert, sobald das Becken ein Fremdschlüssel ist.
    """
    start = start or bucket_start(timezone.now(), period)
    grouped: dict[str, list] = {}
    for device in metered_devices(user):
        grouped.setdefault(device.tank_name, []).append(
            (device, device_consumption(device, period, start))
        )

    usages = [
        Usage(
            label=tank,
            kwh=_quantize(sum((kwh for _device, kwh in entries), Decimal(0))),
            devices=[device for device, _kwh in entries],
        )
        for tank, entries in grouped.items()
    ]
    return _sorted(usages)


def total_kwh(usages) -> Decimal:
    return _quantize(sum((usage.kwh for usage in usages), Decimal(0)))


def total_cost(usages) -> Decimal:
    return _money(total_kwh(usages) * price_per_kwh())


def metered_devices(user):
    """Geräte des Benutzers, die überhaupt einen Zähler haben."""
    return Device.objects.filter(owner=user, kind__in=Device.SHELLY_KINDS)


def _readings_since(device: Device, start: datetime.date):
    """Messwerte ab dem Zeitraumbeginn, plus den letzten davor.

    Der Messwert davor ist der Bezugspunkt: ohne ihn fehlte der Verbrauch
    zwischen dem letzten Messwert des Vorzeitraums und dem ersten des
    laufenden.
    """
    boundary = _start_of_day(start)
    readings = list(
        DeviceReading.objects.filter(
            device=device, energy_total_wh__isnull=False, read_at__gte=boundary
        ).only("read_at", "energy_total_wh")
    )
    previous = (
        DeviceReading.objects.filter(
            device=device, energy_total_wh__isnull=False, read_at__lt=boundary
        )
        .only("read_at", "energy_total_wh")
        .order_by("-read_at")
        .first()
    )
    if previous is not None:
        readings.append(previous)
    return readings


def _start_of_day(day: datetime.date):
    moment = datetime.datetime.combine(day, datetime.time.min)
    if settings.USE_TZ:
        return timezone.make_aware(moment, timezone.get_current_timezone())
    return moment


def _sorted(usages: list[Usage]) -> list[Usage]:
    return sorted(usages, key=lambda usage: (-usage.kwh, usage.label))


def _kwh(watt_hours) -> Decimal:
    return _quantize(Decimal(watt_hours) / WATT_HOURS_PER_KILOWATT_HOUR)


def _quantize(value) -> Decimal:
    return Decimal(value).quantize(KWH_PLACES, rounding=ROUND_HALF_UP)


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
