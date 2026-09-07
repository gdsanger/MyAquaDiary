"""Verlaufsdiagramme als serverseitig berechnetes SVG.

Bewusst ohne Diagramm-Bibliothek im Browser: die Datenreihen sind kurz, das
Fragment kommt ohnehin per HTMX vom Server, und so bleiben Farben im
Stylesheet (``.mad-chart__*``) statt in JavaScript oder in Hex-Werten im
Template.
"""

from datetime import timedelta

from django.utils import timezone

from core.enums import Status

from . import derived, selectors
from .models import (
    Measurement,
    Parameter,
    TankParameterTarget,
    classify_below_detection,
    classify_value,
)

#: Koordinatensystem des SVG. Die Darstellung skaliert über ``viewBox``,
#: deshalb sind das keine Pixel, sondern Einheiten.
VIEW_WIDTH = 320
VIEW_HEIGHT = 96
PAD_X = 6
PAD_TOP = 8
PAD_BOTTOM = 18

DEFAULT_DAYS = 90


def _x(timestamp, t_lo, t_hi):
    inner = VIEW_WIDTH - 2 * PAD_X
    if t_hi == t_lo:
        return PAD_X + inner / 2
    return PAD_X + (timestamp - t_lo) / (t_hi - t_lo) * inner


def _y(value, lo, hi):
    """Werteachse: unten ``lo``, oben ``hi`` — SVG zählt y von oben."""
    inner = VIEW_HEIGHT - PAD_TOP - PAD_BOTTOM
    if hi == lo:
        return PAD_TOP + inner / 2
    return VIEW_HEIGHT - PAD_BOTTOM - (value - lo) / (hi - lo) * inner


def parameter_series(tank, parameter, days=DEFAULT_DAYS, target=None):
    """Datenreihe eines Parameters, fertig für das SVG-Template.

    Gibt ``None`` zurück, wenn es im Zeitraum keine Messwerte gibt — die
    Kachel zeigt dann einen Hinweis statt eines leeren Diagramms.
    """
    since = timezone.now() - timedelta(days=days)
    measurements = list(
        Measurement.objects.filter(
            tank=tank, parameter=parameter, measured_at__gte=since
        ).order_by("measured_at")
    )
    if target is None:
        target = TankParameterTarget.objects.filter(tank=tank, parameter=parameter).first()
    if target is not None:
        minimum, maximum = target.minimum, target.maximum
    else:
        minimum, maximum = parameter.default_min, parameter.default_max
    return _series(parameter, measurements, minimum, maximum)


def derived_series(parameter, values, minimum, maximum):
    """Datenreihe einer gerechneten Größe.

    Sie sieht im SVG aus wie jede andere; unterschieden wird sie über
    ``is_derived`` in der Beschriftung. Die Werte kommen fertig gerechnet aus
    :mod:`tanks.selectors` — hier wird nichts noch einmal gepaart.
    """
    # Aufsteigend, wie das Diagramm zeichnet; die Auswertung liefert die
    # neuesten zuerst.
    return _series(parameter, sorted(values, key=lambda item: item.measured_at), minimum, maximum)


def _below_detection(measurement):
    """Ist dieser Wert ein n.n.? Gerechnete Werte kennen das Merkmal nicht."""
    return getattr(measurement, "below_detection", False)


def _status_of(parameter, measurement, minimum, maximum):
    """Status eines Punkts — n.n. wird anders bewertet als eine Zahl."""
    if _below_detection(measurement):
        return classify_below_detection(
            minimum, maximum, getattr(parameter, "detection_limit", None)
        )
    return classify_value(measurement.value, minimum, maximum)


def _series(parameter, measurements, minimum, maximum):
    """Der gemeinsame Rechenweg für gemessene und gerechnete Reihen.

    ``measurements`` ist aufsteigend nach ``measured_at`` sortiert und trägt
    ``value``, ``measured_at`` und ``display_value`` — mehr braucht das
    Diagramm nicht, und deshalb passt ein ``DerivedValue`` genauso hinein wie
    ein ``Measurement``.

    Nicht nachweisbare Werte tragen keinen Zahlenwert; sie stehen nicht als
    Punkt bei 0 in der Kurve (das sähe aus wie eine gemessene Null), sondern als
    eigener Marker an der Grundlinie. Die Linie verbindet nur die gemessenen
    Zahlen.
    """
    if not measurements:
        return None

    numeric = [m for m in measurements if not _below_detection(m) and m.value is not None]
    bounds = [float(m.value) for m in numeric]
    if minimum is not None:
        bounds.append(float(minimum))
    if maximum is not None:
        bounds.append(float(maximum))
    if not bounds:
        # Nur n.n.-Werte und kein Zielbereich: eine kleine Spanne bis zur
        # Nachweisgrenze, damit die Grundlinie überhaupt eine Höhe hat.
        limit = getattr(parameter, "detection_limit", None)
        bounds = [0.0, float(limit) if limit else 1.0]

    lo, hi = min(bounds), max(bounds)
    margin = (hi - lo) * 0.15 or max(abs(hi) * 0.1, 0.5)
    lo, hi = lo - margin, hi + margin

    times = [m.measured_at.timestamp() for m in measurements]
    t_lo, t_hi = times[0], times[-1]

    points = []
    nn_points = []
    for measurement, timestamp in zip(measurements, times):
        x = round(_x(timestamp, t_lo, t_hi), 2)
        status = _status_of(parameter, measurement, minimum, maximum)
        if _below_detection(measurement):
            nn_points.append({"x": x, "status": status, "measured_at": measurement.measured_at})
            continue
        points.append(
            {
                "x": x,
                "y": round(_y(float(measurement.value), lo, hi), 2),
                "status": status,
                "value": measurement.value,
                "measured_at": measurement.measured_at,
            }
        )

    band = None
    if minimum is not None and maximum is not None:
        y_top = _y(float(maximum), lo, hi)
        y_bottom = _y(float(minimum), lo, hi)
        band = {"y": round(y_top, 2), "height": round(max(y_bottom - y_top, 1), 2)}

    latest = measurements[-1]
    latest_status = _status_of(parameter, latest, minimum, maximum)
    baseline_y = round(VIEW_HEIGHT - PAD_BOTTOM, 2)

    return {
        "parameter": parameter,
        # Die Beschriftung sagt, woher die Kurve kommt; gezeichnet wird sie
        # wie jede andere.
        "is_derived": getattr(parameter, "is_derived", False),
        "points": points,
        # n.n.-Marker sitzen leicht unterhalb der Grundlinie: „unter der
        # Nachweisgrenze“ liest sich als „unter der Achse“.
        "nn_points": nn_points,
        "nn_marker_y": round(baseline_y + 5, 2),
        "polyline": " ".join(f"{p['x']},{p['y']}" for p in points),
        "band": band,
        "baseline_y": baseline_y,
        "latest": latest,
        "latest_status": latest_status,
        "minimum": minimum,
        "maximum": maximum,
        "target_label": parameter.format_range(minimum, maximum),
        "first_label": f"{measurements[0].measured_at:%d.%m.}",
        "last_label": f"{latest.measured_at:%d.%m.}",
        "view_width": VIEW_WIDTH,
        "view_height": VIEW_HEIGHT,
        "summary": _summary(parameter, measurements, latest, latest_status),
    }


def _summary(parameter, measurements, latest, status):
    """Textfassung des Diagramms für Screenreader (SVG ist ``role="img"``)."""
    label = {
        Status.OK: "im Zielbereich",
        Status.WARN: "leicht außerhalb des Zielbereichs",
        Status.CRITICAL: "deutlich außerhalb des Zielbereichs",
        Status.UNKNOWN: "ohne hinterlegten Zielbereich",
    }[status]
    noun = "berechnete Werte" if getattr(parameter, "is_derived", False) else "Messwerte"
    # ``display_value`` trägt Einheit bzw. „n.n.“ — so steht im Fließtext für
    # Screenreader dasselbe wie in der Kachel.
    return (
        f"Verlauf {parameter.name}: {len(measurements)} {noun}, zuletzt "
        f"{latest.display_value} am "
        f"{latest.measured_at:%d.%m.%Y} — {label}."
    )


def key_parameter_charts(tank, limit=3, days=DEFAULT_DAYS):
    """Verlaufsdiagramme der Leitparameter eines Beckens."""
    if tank is None:
        return []
    targets = {t.parameter_id: t for t in TankParameterTarget.objects.filter(tank=tank)}
    charts = []
    for parameter in Parameter.objects.filter(is_key_parameter=True):
        series = parameter_series(tank, parameter, days=days, target=targets.get(parameter.pk))
        if series is not None:
            charts.append(series)
        if len(charts) >= limit:
            break
    return charts


def derived_charts(tank, days=DEFAULT_DAYS):
    """Verlaufsdiagramme der gerechneten Größen eines Beckens.

    Nicht Teil von :func:`key_parameter_charts`: dessen Zahl ist begrenzt, und
    CO₂ soll dort keinen Leitparameter verdrängen. Wo keine Paare zustande
    kommen, entsteht auch kein Diagramm.
    """
    if tank is None:
        return []
    since = timezone.now() - timedelta(days=days)
    targets = selectors.derived_target_map([tank])
    by_key = {}
    for value in selectors.derived_values(tank, since=since):
        by_key.setdefault(value.parameter.key, []).append(value)

    charts = []
    for key, parameter in derived.DERIVED_PARAMETERS.items():
        minimum, maximum = derived.target_range(parameter, targets.get((tank.pk, key)))
        series = derived_series(parameter, by_key.get(key, []), minimum, maximum)
        if series is not None:
            charts.append(series)
    return charts
