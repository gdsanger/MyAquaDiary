"""Serverseitig gerechnete Diagramme für die Geräteanzeige.

Bewusst handgerechnete Inline-SVGs statt einer Chart-Bibliothek: es geht um
eine Linie über der Zeit und um ein paar Balken, und der Stack der Anwendung
ist Django + HTMX ohne Build-Schritt. Die Diagramme sind damit auch ohne
JavaScript vollständig da.

Die Farben kommen aus der Palette in ``static/css/main.css``. Bei der Drehzahl
steht die Achse fest auf 0–100 %, damit ein Verlauf nicht durch eine
mitwandernde Skala dramatischer aussieht, als er ist; bei der Leistung gibt es
keine natürliche Obergrenze, dort wächst die Achse mit — und ist beschriftet.
"""

from dataclasses import dataclass, field
from decimal import Decimal

#: Zeichenfläche inklusive Rand für die Achsenbeschriftung.
WIDTH = 680
HEIGHT = 200
PADDING_LEFT = 38
PADDING_RIGHT = 12
PADDING_TOP = 12
PADDING_BOTTOM = 26

PERCENT_TICKS = (0, 50, 100)
#: Y-Achse der Leistung: der höchste Messwert wird auf das nächste dieser
#: Raster aufgerundet, damit die Beschriftung runde Zahlen zeigt.
POWER_STEPS = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 3500)

#: Balkendiagramm: Zeile je Eintrag, Breite wie die Linienbilder.
BAR_HEIGHT = 22
BAR_GAP = 8
BAR_LABEL_WIDTH = 150
BAR_VALUE_WIDTH = 110


@dataclass(frozen=True)
class ChartPoint:
    x: float
    y: float
    value: object
    label: str


@dataclass(frozen=True)
class Chart:
    """Fertig gerechnetes Liniendiagramm — das Template zeichnet nur noch."""

    points: list[ChartPoint] = field(default_factory=list)
    y_ticks: list[tuple[float, str]] = field(default_factory=list)
    x_labels: list[tuple[float, str]] = field(default_factory=list)
    unit: str = "%"
    description: str = "Verlauf"
    width: int = WIDTH
    height: int = HEIGHT
    plot_left: int = PADDING_LEFT
    plot_right: int = WIDTH - PADDING_RIGHT
    plot_top: int = PADDING_TOP
    plot_bottom: int = HEIGHT - PADDING_BOTTOM

    @property
    def has_data(self) -> bool:
        return bool(self.points)

    @property
    def polyline(self) -> str:
        return " ".join(f"{point.x:.1f},{point.y:.1f}" for point in self.points)

    @property
    def last_point(self):
        return self.points[-1] if self.points else None


@dataclass(frozen=True)
class Bar:
    label: str
    value_label: str
    x: float
    y: float
    width: float
    height: int = BAR_HEIGHT

    @property
    def text_y(self) -> float:
        return self.y + self.height / 2 + 4

    @property
    def value_x(self) -> float:
        """Anschlag für die Beschriftung: knapp hinter dem Balkenende."""
        return round(self.x + self.width + 8, 1)


@dataclass(frozen=True)
class BarChart:
    """Waagerechtes Balkendiagramm — für Vergleiche und die Historie.

    Waagerecht, weil die Beschriftung (Beckenname, Monat) daneben passt und
    nicht gedreht werden muss.
    """

    bars: list[Bar] = field(default_factory=list)
    description: str = "Vergleich"
    width: int = WIDTH
    label_width: int = BAR_LABEL_WIDTH

    @property
    def has_data(self) -> bool:
        return bool(self.bars)

    @property
    def height(self) -> int:
        return max(len(self.bars) * (BAR_HEIGHT + BAR_GAP), BAR_HEIGHT)


def rpm_chart(readings) -> Chart:
    """Verlauf der Drehzahl aus Messwerten (beliebige Reihenfolge)."""
    return _line_chart(
        readings,
        value_of=lambda reading: reading.rpm_percent,
        unit="%",
        description="Verlauf der Drehzahl in Prozent",
        y_max=100,
        y_ticks=PERCENT_TICKS,
    )


def power_chart(readings) -> Chart:
    """Verlauf der Leistungsaufnahme in Watt.

    Anders als bei der Drehzahl gibt es keine feste Obergrenze; die Achse
    richtet sich nach dem höchsten Messwert, aufgerundet auf ein rundes Raster.
    """
    values = [reading.power_w for reading in readings if reading.power_w is not None]
    peak = max((float(value) for value in values), default=0)
    y_max = next((step for step in POWER_STEPS if step >= peak), max(peak, 1))
    return _line_chart(
        readings,
        value_of=lambda reading: reading.power_w,
        unit="W",
        description="Verlauf der Leistungsaufnahme in Watt",
        y_max=y_max,
        y_ticks=(0, y_max / 2, y_max),
    )


def _line_chart(readings, *, value_of, unit, description, y_max, y_ticks) -> Chart:
    """Gemeinsame Geometrie aller Linienbilder.

    Messwerte ohne den gesuchten Wert — etwa eine Steckdose ohne Drehzahl —
    fallen raus; die x-Achse folgt der Zeit, nicht der Position in der Liste,
    damit eine Lücke im Verlauf auch als Lücke sichtbar ist.
    """
    usable = sorted(
        (reading for reading in readings if value_of(reading) is not None),
        key=lambda reading: reading.read_at,
    )
    if not usable:
        return Chart(unit=unit, description=description)

    left, right = PADDING_LEFT, WIDTH - PADDING_RIGHT
    top, bottom = PADDING_TOP, HEIGHT - PADDING_BOTTOM

    start = usable[0].read_at
    span = (usable[-1].read_at - start).total_seconds()

    def x_for(reading):
        if span <= 0:  # nur ein Messwert (oder alle zur selben Sekunde)
            return round((left + right) / 2, 1)
        return round(left + (reading.read_at - start).total_seconds() / span * (right - left), 1)

    def y_for(value):
        clamped = max(0.0, min(float(y_max), float(value)))
        return round(bottom - clamped / float(y_max) * (bottom - top), 1)

    points = [
        ChartPoint(
            x=x_for(reading),
            y=y_for(value_of(reading)),
            value=_trim(value_of(reading)),
            label=f"{reading.read_at:%d.%m. %H:%M} – {_trim(value_of(reading))} {unit}",
        )
        for reading in usable
    ]

    ticks = [(y_for(value), f"{_trim(value)} {unit}") for value in y_ticks]
    x_labels = [(points[0].x, f"{usable[0].read_at:%d.%m. %H:%M}")]
    if len(points) > 1:
        x_labels.append((points[-1].x, f"{usable[-1].read_at:%d.%m. %H:%M}"))

    return Chart(
        points=points, y_ticks=ticks, x_labels=x_labels, unit=unit, description=description
    )


def bar_chart(entries, *, unit: str = "kWh", description: str = "Vergleich") -> BarChart:
    """Balken aus ``(Beschriftung, Wert)``-Paaren.

    Die Skala richtet sich nach dem größten Wert; ein Vergleich soll zeigen,
    wer wie viel verbraucht, nicht wie nah alle an einer gedachten Obergrenze
    liegen. Sind alle Werte null, bleiben die Balken leer statt gleich lang.
    """
    entries = [(str(label), value or 0) for label, value in entries]
    if not entries:
        return BarChart(description=description)

    peak = max(float(value) for _label, value in entries)
    plot_left = BAR_LABEL_WIDTH
    plot_width = WIDTH - BAR_LABEL_WIDTH - BAR_VALUE_WIDTH

    bars = []
    for index, (label, value) in enumerate(entries):
        width = 0.0 if peak <= 0 else round(float(value) / peak * plot_width, 1)
        bars.append(
            Bar(
                label=label,
                value_label=f"{_trim(value)} {unit}",
                x=plot_left,
                y=index * (BAR_HEIGHT + BAR_GAP),
                width=max(width, 1.0),
            )
        )
    return BarChart(bars=bars, description=description)


def _trim(value):
    """Zahl ohne überflüssige Nachkommastellen — 12.00 W liest sich schlecht."""
    if isinstance(value, Decimal):
        normalized = value.normalize()
        if normalized == normalized.to_integral_value():
            return int(normalized)
        return normalized
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else round(value, 2)
    return value
