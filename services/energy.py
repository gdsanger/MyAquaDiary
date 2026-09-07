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

Gemessen wird allerdings nur, was an einer Steckdose hängt — bei uns zwei
Geräte. Beleuchtung, Heizung und CO₂-Magnetventil hängen an keiner messenden
Dose, und eine Auswertung, die nur zwei von zehn Geräten zeigt, beantwortet die
Frage nach den Stromkosten eines Beckens nicht. Für alles ohne Zähler wird
deshalb aus Nennleistung und täglicher Laufzeit hochgerechnet
(:func:`device_estimate`). Das ist eine Schätzung und wird überall, wo sie
auftaucht, als solche gekennzeichnet — ein geschätzter Wert, der wie ein
gemessener aussieht, ist schlimmer als gar keiner.

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


def bucket_start_of(day: datetime.date, period: str) -> datetime.date:
    """Beginn des Zeitraums, in den ein Tag fällt."""
    if period == PERIOD_YEAR:
        return day.replace(month=1, day=1)
    if period == PERIOD_MONTH:
        return day.replace(day=1)
    return day


def bucket_start(moment, period: str) -> datetime.date:
    """Beginn des Zeitraums, in den ein Zeitpunkt fällt."""
    day = timezone.localtime(moment).date() if timezone.is_aware(moment) else moment.date()
    return bucket_start_of(day, period)


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
    #: True, wenn der Wert hochgerechnet und nicht gemessen ist.
    estimated: bool = False

    @property
    def cost(self) -> Decimal:
        return _money(self.kwh * price_per_kwh())


@dataclass(frozen=True)
class Usage:
    """Verbrauch einer Gruppe (Becken oder Gerät) im gewählten Zeitraum.

    ``kwh`` ist die Summe, ``estimated_kwh`` der hochgerechnete Anteil daran.
    Beides getrennt, weil ein Becken beides enthalten kann: die Steckdose der
    Pumpe misst, der Heizer daneben wird geschätzt. Die Anzeige soll sagen
    können, welcher Teil worauf beruht.
    """

    label: str
    kwh: Decimal
    devices: list = field(default_factory=list)
    #: Gesetzt, wenn die Gruppe ein Becken ist — für den Link in der Auswertung.
    tank: object = None
    estimated_kwh: Decimal = Decimal("0.000")

    @property
    def cost(self) -> Decimal:
        return _money(self.kwh * price_per_kwh())

    @property
    def has_estimate(self) -> bool:
        return self.estimated_kwh > 0

    @property
    def is_fully_estimated(self) -> bool:
        return self.has_estimate and self.estimated_kwh == self.kwh

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
    """Verbrauchshistorie eines Geräts — die letzten ``limit`` Zeiträume.

    Ohne Zähler kommt die Hochrechnung; ohne Nennleistung kommt nichts. Ein
    Gerät, über dessen Verbrauch sich nichts sagen lässt, bekommt kein Diagramm
    mit einer Null darin.
    """
    if not device.is_metered:
        return estimate_buckets(device, period, limit)
    readings = DeviceReading.objects.filter(device=device, energy_total_wh__isnull=False).only(
        "read_at", "energy_total_wh"
    )
    buckets = consumption_buckets(readings, period)
    return buckets[-limit:] if limit else buckets


def device_consumption(device: Device, period: str, start: datetime.date | None = None) -> Decimal:
    """Verbrauch eines Geräts in genau einem Zeitraum (Vorgabe: der laufende).

    Gemessen, wo gemessen wird, sonst hochgerechnet. Ob der Wert das eine oder
    das andere ist, sagt :attr:`Device.is_metered` — hier fließt beides
    zusammen, damit die Aufrufer nicht zwei Wege kennen müssen.
    """
    start = start or bucket_start(timezone.now(), period)
    if not device.is_metered:
        return device_estimate(device, period, start)
    for bucket in consumption_buckets(_readings_since(device, start), period):
        if bucket.start == start:
            return bucket.kwh
    return Decimal("0.000")


# --------------------------------------------------------------------------
# Hochrechnung für Geräte ohne Zähler
# --------------------------------------------------------------------------


def period_end(start: datetime.date, period: str) -> datetime.date:
    """Letzter Tag des Zeitraums, der bei ``start`` beginnt."""
    if period == PERIOD_YEAR:
        return start.replace(month=12, day=31)
    if period == PERIOD_MONTH:
        return _next_month(start) - datetime.timedelta(days=1)
    return start


def running_days(device: Device, start: datetime.date, period: str, today=None) -> int:
    """Tage, an denen das Gerät im Zeitraum in Betrieb war.

    Begrenzt an drei Stellen, und jede davon würde die Schätzung sonst zu groß
    machen: vor der Inbetriebnahme lief das Gerät nicht, nach heute noch nicht,
    und über den Zeitraum hinaus geht es ohnehin nicht.
    """
    today = today or timezone.localdate()
    first = start
    if device.installed_on and device.installed_on > first:
        first = device.installed_on
    last = min(period_end(start, period), today)
    return max((last - first).days + 1, 0)


def device_estimate(device: Device, period: str, start: datetime.date | None = None) -> Decimal:
    """Hochgerechneter Verbrauch eines Geräts ohne Zähler.

    Nennleistung mal Laufzeit mal Betriebstage. Ohne Nennleistung gibt es
    nichts zu rechnen — dann ist das Ergebnis null und nicht etwa geraten.
    """
    if device.power_watts is None:
        return Decimal("0.000")
    start = start or bucket_start(timezone.now(), period)
    days = running_days(device, start, period)
    if not days:
        return Decimal("0.000")
    watt_hours = Decimal(device.power_watts) * device.runtime_hours_per_day * days
    return _kwh(watt_hours)


def estimate_buckets(device: Device, period: str, limit: int = DEFAULT_BUCKETS) -> list[Bucket]:
    """Hochgerechnete Historie — dieselben Zeiträume wie bei einer Messung."""
    if device.power_watts is None:
        return []
    buckets = [
        Bucket(
            start=start,
            label=bucket_label(start, period),
            kwh=device_estimate(device, period, start),
            estimated=True,
        )
        for start in _recent_starts(period, limit)
    ]
    # Zeiträume vor der Inbetriebnahme sind keine leeren Zeiträume, sondern
    # keine — sie stehen dem Diagramm nur im Weg.
    return [bucket for bucket in buckets if bucket.kwh > 0]


# --------------------------------------------------------------------------
# Vergleich
# --------------------------------------------------------------------------


def usage_by_device(user, period: str, start: datetime.date | None = None) -> list[Usage]:
    """Verbrauch je Gerät im gewählten Zeitraum, absteigend sortiert.

    Steckdosen mit ihrem Zählerstand, alles andere mit seiner Hochrechnung —
    und an jeder Zeile steht, was von beidem sie ist.
    """
    start = start or bucket_start(timezone.now(), period)
    usages = []
    for device in accounted_devices(user).select_related("tank"):
        kwh = device_consumption(device, period, start)
        usages.append(
            Usage(
                label=device.name,
                kwh=kwh,
                devices=[device],
                estimated_kwh=kwh if not device.is_metered else Decimal("0.000"),
            )
        )
    return _sorted(usages)


def usage_by_tank(user, period: str, start: datetime.date | None = None) -> list[Usage]:
    """Verbrauch je Becken im gewählten Zeitraum, absteigend sortiert.

    Der eigentliche Nutzen der Anbindung: was kostet welches Becken im Monat.
    Gruppiert wird über die Beckenkennung, nicht über den Namen — sonst
    entstünde aus einem Tippfehler oder einer Umbenennung stillschweigend eine
    zweite Gruppe.
    """
    start = start or bucket_start(timezone.now(), period)
    grouped: dict[int, list] = {}
    for device in accounted_devices(user).select_related("tank"):
        grouped.setdefault(device.tank_id, []).append(
            (device, device_consumption(device, period, start))
        )

    usages = [
        Usage(
            label=entries[0][0].tank.name,
            kwh=_quantize(sum((kwh for _device, kwh in entries), Decimal(0))),
            devices=[device for device, _kwh in entries],
            tank=entries[0][0].tank,
            estimated_kwh=_quantize(
                sum((kwh for device, kwh in entries if not device.is_metered), Decimal(0))
            ),
        )
        for entries in grouped.values()
    ]
    return _sorted(usages)


def total_kwh(usages) -> Decimal:
    return _quantize(sum((usage.kwh for usage in usages), Decimal(0)))


def total_estimated_kwh(usages) -> Decimal:
    return _quantize(sum((usage.estimated_kwh for usage in usages), Decimal(0)))


def total_cost(usages) -> Decimal:
    return _money(total_kwh(usages) * price_per_kwh())


def metered_devices(user):
    """Geräte des Benutzers, die überhaupt einen Zähler haben.

    Nur zugeordnete: ein eingelagertes Gerät (ohne Becken) hängt an keinem
    Strom und gehört in keine Beckenauswertung — es hätte auch kein Becken, dem
    die Gruppierung es zuschlagen könnte.
    """
    return Device.objects.filter(
        owner=user, kind__in=Device.METERED_KINDS, tank__isnull=False
    )


def estimated_devices(user):
    """Geräte ohne Zähler, deren Verbrauch sich hochrechnen lässt.

    Nur aktive und zugeordnete: ein abgemeldetes oder eingelagertes Gerät
    verbraucht nichts mehr, und eine Schätzung, die es weiterlaufen lässt, wäre
    schlicht falsch.
    """
    return (
        Device.objects.filter(
            owner=user, is_active=True, power_watts__isnull=False, tank__isnull=False
        )
        .exclude(kind__in=Device.METERED_KINDS)
    )


def accounted_devices(user):
    """Alles, was in der Auswertung vorkommt — gemessen oder geschätzt."""
    return metered_devices(user) | estimated_devices(user)


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


def _next_month(day: datetime.date) -> datetime.date:
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1, day=1)
    return day.replace(month=day.month + 1, day=1)


def _recent_starts(period: str, limit: int) -> list[datetime.date]:
    """Die letzten ``limit`` Zeitraumanfänge, chronologisch aufsteigend.

    Rückwärts gegangen wird über den Tag vor dem jeweiligen Anfang — der liegt
    im Zeitraum davor, egal ob der 28, 30 oder 366 Tage hat.
    """
    starts = [bucket_start(timezone.now(), period)]
    while len(starts) < max(limit, 1):
        starts.insert(0, bucket_start_of(starts[0] - datetime.timedelta(days=1), period))
    return starts


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
