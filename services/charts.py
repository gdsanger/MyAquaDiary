"""Serverseitig gerechnete Diagramme für die Geräteanzeige.

Bewusst ein handgerechnetes Inline-SVG statt einer Chart-Bibliothek: es geht um
genau eine Linie über der Zeit, und der Stack der Anwendung ist Django + HTMX
ohne Build-Schritt. Das Diagramm ist damit auch ohne JavaScript vollständig da.

Die Farben kommen aus der Palette in ``static/css/main.css``; die Achse steht
fest auf 0–100 %, damit ein Verlauf nicht durch eine mitwandernde Skala
dramatischer aussieht, als er ist.
"""

from dataclasses import dataclass, field

#: Zeichenfläche inklusive Rand für die Achsenbeschriftung.
WIDTH = 680
HEIGHT = 200
PADDING_LEFT = 38
PADDING_RIGHT = 12
PADDING_TOP = 12
PADDING_BOTTOM = 26

Y_TICKS = (0, 50, 100)


@dataclass(frozen=True)
class ChartPoint:
    x: float
    y: float
    value: int
    label: str


@dataclass(frozen=True)
class Chart:
    """Fertig gerechnetes Liniendiagramm — das Template zeichnet nur noch."""

    points: list[ChartPoint] = field(default_factory=list)
    y_ticks: list[tuple[float, str]] = field(default_factory=list)
    x_labels: list[tuple[float, str]] = field(default_factory=list)
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


def rpm_chart(readings) -> Chart:
    """Verlauf der Drehzahl aus Messwerten (beliebige Reihenfolge).

    Messwerte ohne Drehzahl — etwa von einem Gerät ohne Pumpe — fallen raus;
    die x-Achse folgt der Zeit, nicht der Position in der Liste, damit eine
    Lücke im Verlauf auch als Lücke sichtbar ist.
    """
    usable = sorted(
        (reading for reading in readings if reading.rpm_percent is not None),
        key=lambda reading: reading.read_at,
    )
    if not usable:
        return Chart()

    left, right = PADDING_LEFT, WIDTH - PADDING_RIGHT
    top, bottom = PADDING_TOP, HEIGHT - PADDING_BOTTOM

    start = usable[0].read_at
    span = (usable[-1].read_at - start).total_seconds()

    def x_for(reading):
        if span <= 0:  # nur ein Messwert (oder alle zur selben Sekunde)
            return round((left + right) / 2, 1)
        return round(left + (reading.read_at - start).total_seconds() / span * (right - left), 1)

    def y_for(percent):
        clamped = max(0, min(100, percent))
        return round(bottom - clamped / 100 * (bottom - top), 1)

    points = [
        ChartPoint(
            x=x_for(reading),
            y=y_for(reading.rpm_percent),
            value=reading.rpm_percent,
            label=f"{reading.read_at:%d.%m. %H:%M} – {reading.rpm_percent} %",
        )
        for reading in usable
    ]

    y_ticks = [(y_for(value), f"{value} %") for value in Y_TICKS]
    x_labels = [(points[0].x, f"{usable[0].read_at:%d.%m. %H:%M}")]
    if len(points) > 1:
        x_labels.append((points[-1].x, f"{usable[-1].read_at:%d.%m. %H:%M}"))

    return Chart(points=points, y_ticks=y_ticks, x_labels=x_labels)
