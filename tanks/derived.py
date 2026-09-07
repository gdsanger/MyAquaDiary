"""Abgeleitete Messgrößen: gerechnet, nie gespeichert.

Bisher gibt es genau eine — CO₂ aus Karbonathärte und pH::

    CO₂ [mg/l] = 3 × KH [°dH] × 10^(7 − pH)

**Warum kein Feld.** Ein gespeicherter Wert driftet: wird ein KH- oder
pH-Wert nachträglich korrigiert, bliebe das danebenstehende CO₂ auf dem alten
Stand — und sähe dabei aus wie eine Messung. Gerechnet wird deshalb bei jeder
Anzeige.

**Warum kein Parameter-Katalogeintrag.** Ein Eintrag in ``tanks.Parameter``
hätte denselben Effekt von der anderen Seite: CO₂ stünde im Erfassungsformular
und ließe sich von Hand eintragen. Abgeleitete Größen gehören nicht in
denselben Topf wie gemessene, deshalb steht die Beschreibung hier im Code und
nicht in der Datenbank (#1240).

**Warum ein Zeitfenster.** Das Datenmodell speichert einen Wert je Zeile
(``tanks.Measurement``); einen Datensatz, an dem KH und pH gemeinsam hängen,
gibt es nicht. Nur über den gleichen Zeitstempel zu paaren bricht, sobald
jemand erst den Tröpfchentest für KH macht und zehn Minuten später den pH
abliest — und genau so wird gemessen. Gepaart wird deshalb über zeitliche
Nähe: zu jedem KH-Wert der nächstgelegene pH-Wert innerhalb des Fensters, und
umgekehrt.

**Ohne Partner keine Zahl.** Liegt im Fenster kein Gegenstück, entsteht kein
CO₂-Wert. Ein aus zwei Wochen auseinanderliegenden Messungen gerechnetes CO₂
wäre schlimmer als gar keines: es sieht echt aus.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings

from core.enums import Status
from core.values import ValueFormatMixin

#: Vorgabe der Fenstergröße in Stunden. Überschreibbar über die Einstellung
#: ``CO2_PAIR_WINDOW_HOURS`` — sechs Stunden decken einen Testdurchgang samt
#: Nachtragen am Abend ab, ohne den Wert von gestern mitzunehmen.
DEFAULT_PAIR_WINDOW_HOURS = 6

#: Genauigkeit der gerechneten Werte. Dieselbe wie bei ``Measurement.value``
#: (``Decimal(8,3)``) — was gerechnet wird, soll nicht feiner aussehen als
#: das, woraus es entsteht.
QUANTUM = Decimal("0.001")

#: Außerhalb dieser Spanne beschreibt ein pH-Wert kein Wasser, sondern einen
#: Tippfehler. Das Erfassungsfeld nimmt jede Zahl mit acht Stellen entgegen,
#: und ``10^(7 − pH)`` wächst exponentiell: aus einem verrutschten Minuszeichen
#: entstünde eine Zahl mit hunderttausend Stellen — und statt eines CO₂-Werts
#: ein Fehler in der Beckenübersicht. Gerechnet wird damit deshalb nicht.
PH_MIN, PH_MAX = Decimal(0), Decimal(14)


def pairing_window() -> timedelta:
    """Wie weit KH und pH auseinanderliegen dürfen, um noch ein Paar zu sein.

    Wird bei jedem Aufruf gelesen und nicht beim Import: sonst ließe sich die
    Fenstergröße im Test nicht umstellen und im Betrieb nicht ändern, ohne den
    Prozess neu zu starten.
    """
    hours = getattr(settings, "CO2_PAIR_WINDOW_HOURS", DEFAULT_PAIR_WINDOW_HOURS)
    return timedelta(hours=hours)


@dataclass(frozen=True)
class DerivedParameter(ValueFormatMixin):
    """Eine Größe, die aus Messwerten entsteht, statt gemessen zu werden.

    Trägt dieselben Namen wie :class:`tanks.models.Parameter` dort, wo
    Vorlagen und Serialisierung lesen (``key``, ``name``, ``unit``,
    ``format_value``, ``format_range``) — die Anzeigeschicht soll für eine
    gerechnete Zeile nicht zwei Fälle kennen müssen. Was sie unterscheiden
    **soll**, steht in ``is_derived``.
    """

    key: str
    name: str
    unit: str
    decimals: int
    default_min: Decimal | None
    default_max: Decimal | None
    #: Schlüssel der Messgrößen, aus denen gerechnet wird.
    sources: tuple[str, ...]
    #: Die Formel im Klartext — sie steht in der Oberfläche und in der
    #: MCP-Antwort, damit niemand raten muss, wie die Zahl zustande kommt.
    formula: str
    #: Hinter den echten Parametern, die nach ``sort_order`` sortiert sind.
    sort_order: int = 900

    #: Kennzeichen für Vorlagen und Serialisierung. Kein Feld des Datensatzes:
    #: es gilt für jede abgeleitete Größe gleichermaßen.
    is_derived = True

    def __str__(self):
        return self.name


CO2 = DerivedParameter(
    key="co2",
    name="CO₂",
    unit="mg/l",
    # Ohne Nachkommastelle: die Rechnung hängt an einem Tröpfchentest, und
    # 0,1 pH daneben sind schon ein Viertel des Ergebnisses. „18,9 mg/l"
    # verspricht eine Genauigkeit, die es nicht gibt.
    decimals=0,
    default_min=Decimal("15"),
    default_max=Decimal("25"),
    sources=("kh", "ph"),
    formula="3 × KH [°dH] × 10^(7 − pH)",
)

#: Schlüssel -> abgeleitete Größe. Die einzige Stelle, an der steht, was es
#: gibt; Zielbereiche und MCP-Schicht lesen von hier.
DERIVED_PARAMETERS = {CO2.key: CO2}

#: Auswahlliste für ``tanks.TankDerivedTarget.key``.
CHOICES = [(item.key, item.name) for item in DERIVED_PARAMETERS.values()]


def co2_from(kh, ph) -> Decimal | None:
    """CO₂ in mg/l aus Karbonathärte in °dH und pH.

    ``10^(7 − pH)``: der pH ist ein Logarithmus, und deshalb verzehnfacht
    sich CO₂ je Einheit, um die er fällt. Gerechnet wird in ``Decimal``, weil
    beide Eingangswerte als ``Decimal`` aus der Datenbank kommen.

    Gibt ``None`` zurück, wo die Eingangswerte kein Wasser beschreiben (siehe
    :data:`PH_MIN`) — dann gibt es kein CO₂ anzuzeigen.
    """
    kh, ph = Decimal(kh), Decimal(ph)
    if kh < 0 or not PH_MIN <= ph <= PH_MAX:
        return None
    return (Decimal(3) * kh * Decimal(10) ** (Decimal(7) - ph)).quantize(QUANTUM)


def target_range(parameter, target=None):
    """Zielbereich einer abgeleiteten Größe: Becken vor Vorgabe.

    ``target`` ist ein :class:`tanks.models.TankDerivedTarget` oder ``None``.
    Die Vorgabe steht am Parameter und damit im Code — anders als bei
    gemessenen Größen gibt es keine Katalogzeile, in der sie stehen könnte.
    """
    if target is not None:
        return target.minimum, target.maximum
    return parameter.default_min, parameter.default_max


@dataclass
class DerivedValue:
    """Ein gerechneter Wert samt den Messwerten, aus denen er stammt.

    Sieht für Vorlagen und Serialisierung aus wie ein ``Measurement``
    (``value``, ``measured_at``, ``display_value``, ``status_code``) — bis auf
    zweierlei: es gibt keine Kennung, denn der Wert steht nirgends, und
    ``is_derived`` ist wahr.
    """

    parameter: DerivedParameter
    value: Decimal
    measured_at: datetime
    #: Die Messwerte, aus denen gerechnet wurde, in der Reihenfolge von
    #: ``parameter.sources``.
    sources: tuple = ()

    #: Vom Statusabgleich nachgetragen (siehe ``tanks.selectors``). Vorbelegt,
    #: damit eine Zeile auch ohne Abgleich anzeigbar bleibt.
    status_code: str = Status.UNKNOWN
    status_label: str = Status.UNKNOWN.label
    target_minimum: Decimal | None = None
    target_maximum: Decimal | None = None
    target_label: str = "—"

    is_derived = True

    def __str__(self):
        return f"{self.parameter}: {self.display_value}"

    @property
    def tank(self):
        return self.sources[0].tank

    @property
    def tank_id(self):
        return self.sources[0].tank_id

    @property
    def display_value(self):
        return f"{self.parameter.format_value(self.value)} {self.parameter.unit}".strip()

    @property
    def span(self):
        """Wie weit die zugrundeliegenden Messwerte auseinanderliegen.

        Je enger, desto belastbarer die Zahl: KH und pH aus einem Durchgang
        beschreiben dasselbe Wasser, zwei Werte am Rand des Fensters nicht
        unbedingt.
        """
        times = [item.measured_at for item in self.sources]
        return max(times) - min(times) if times else timedelta()

    @property
    def source_label(self):
        """Woher die Zahl kommt: „aus Karbonathärte 5 °dH und pH-Wert 6,9"."""
        parts = " und ".join(
            f"{item.parameter.name} {item.display_value}" for item in self.sources
        )
        return f"aus {parts}" if parts else ""


def nearest_partners(rows, partners, window):
    """Zu jedem Eintrag aus ``rows`` den zeitlich nächstgelegenen aus ``partners``.

    Beide Listen sind nach ``measured_at`` aufsteigend sortiert; der Zeiger auf
    ``partners`` läuft mit und wandert nie zurück. Bei gleichem Abstand gewinnt
    der frühere Partner — irgendeine Regel braucht es, sonst hinge das Ergebnis
    an der Reihenfolge aus der Datenbank.

    Wo im Fenster nichts liegt, entsteht kein Paar; das ist der Fall „ohne
    Partner keine Zahl".
    """
    if not partners:
        return
    index = 0
    for row in rows:
        while index + 1 < len(partners) and abs(
            partners[index + 1].measured_at - row.measured_at
        ) < abs(partners[index].measured_at - row.measured_at):
            index += 1
        partner = partners[index]
        if abs(partner.measured_at - row.measured_at) <= window:
            yield row, partner


def co2_values(kh_rows, ph_rows, window=None) -> list[DerivedValue]:
    """CO₂-Werte aus den KH- und pH-Messwerten **eines** Beckens, neueste zuerst.

    Beide Listen sind nach ``measured_at`` aufsteigend sortiert. Gepaart wird
    in beide Richtungen: zu jedem KH der nächste pH und zu jedem pH die nächste
    KH. Das ist nicht dasselbe — liegen zwei pH-Werte um eine KH herum, gehören
    beide zu ihr, und beide ergeben einen eigenen CO₂-Wert. Doppelt gefundene
    Paare fallen über die Kennungen zusammen.
    """
    window = pairing_window() if window is None else window
    pairs = {}
    for kh, ph in nearest_partners(kh_rows, ph_rows, window):
        pairs[(kh.pk, ph.pk)] = (kh, ph)
    for ph, kh in nearest_partners(ph_rows, kh_rows, window):
        pairs[(kh.pk, ph.pk)] = (kh, ph)

    values = []
    for kh, ph in pairs.values():
        value = co2_from(kh.value, ph.value)
        if value is None:
            continue
        values.append(
            DerivedValue(
                parameter=CO2,
                value=value,
                # Der spätere der beiden Zeitpunkte: erst dann ist das Paar
                # vollständig. Mit dem früheren stünde die Zeile in der Liste
                # vor einem Messwert, den sie bereits verwendet.
                measured_at=max(kh.measured_at, ph.measured_at),
                sources=(kh, ph),
            )
        )
    # Neueste zuerst; bei gleichem Zeitpunkt das engere Paar. Zwei KH-Werte um
    # denselben pH herum ergeben zwei Zeilen mit identischem Zeitstempel — ohne
    # zweites Sortiermerkmal hinge ihre Reihenfolge an der Datenbank, und
    # „der aktuelle CO₂-Wert" wäre mal der eine und mal der andere.
    values.sort(key=lambda item: (item.measured_at, -item.span), reverse=True)
    return values
