"""KI-Auswertung einer Messreihe.

Hier wird der Kontext zusammengetragen, den die Auswertung braucht, das
Ergebnis festgehalten und der Aufruf aus dem Anfrage-Weg herausgehalten.

Warum der Kontext so breit ist: ein einzelner Wert sagt fast nichts. Ein
KH-Anstieg ist Verdunstung oder ein sich auflösender Stein — das entscheidet
sich am Ereignis „Osmosewasser nachgefüllt". Nitrit „n.n." an Tag 3 bedeutet
etwas anderes als an Tag 30. Und ob 27 °C in Ordnung sind, hängt daran, welche
Arten im Becken schwimmen. Deshalb gehen Verlauf, Ereignisse, Stammdaten und
Besatz mit in den Prompt und nicht nur die frisch erfassten Zahlen.

Die Arbeitsteilung mit :mod:`services.ai` bleibt, wie sie im Epic angelegt ist:
dort kennt niemand das ORM, die Prompt-Bausteine nehmen einfache Datensätze
entgegen. Gefüllt werden sie hier, wo die Becken zu Hause sind.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from core.enums import Status
from services import ai
from services.models import MeasurementAnalysis

from .models import Measurement, Tank

logger = logging.getLogger(__name__)

#: So weit reicht der mitgeschickte Verlauf zurück.
HISTORY_DAYS = 10
#: So viele Ereignisse gehen mit — genug, um eine Veränderung zu erklären,
#: wenig genug, dass sie nicht den halben Prompt füllen.
EVENT_LIMIT = 10
#: Parameterschlüssel, aus denen sich CO₂ rechnen lässt (siehe :func:`co2`).
KH_KEY = "kh"
PH_KEY = "ph"


# --------------------------------------------------------------------------
# Rechnen
# --------------------------------------------------------------------------


def co2(kh_dh, ph):
    """CO₂ in mg/l aus Karbonathärte (°dH) und pH.

    Die in der Aquaristik übliche Näherung ``3 · KH · 10^(7 − pH)``; sie deckt
    sich mit den gedruckten CO₂-Tabellen. Sie gilt nur für Wasser, dessen
    Säurekapazität aus Karbonat stammt — bei Huminsäuren oder Torf liegt sie
    daneben, weshalb der Wert im Prompt ausdrücklich als gerechnet auftaucht
    und nicht als gemessen.

    ``None``, wenn einer der beiden Werte fehlt.
    """
    if kh_dh is None or ph is None:
        return None
    try:
        kh = Decimal(kh_dh)
        acidity = Decimal(ph)
    except (TypeError, ValueError, InvalidOperation):  # pragma: no cover - defensiv
        return None
    if kh <= 0:
        return None
    return float(3 * kh) * float(10 ** (7 - acidity))


# --------------------------------------------------------------------------
# Die Messreihe finden
# --------------------------------------------------------------------------


def series_of(anchor):
    """Alle Messwerte, die zum selben Zeitpunkt erfasst wurden."""
    return (
        Measurement.objects.filter(tank_id=anchor.tank_id, measured_at=anchor.measured_at)
        .select_related("parameter")
        .order_by("parameter__sort_order", "parameter__name")
    )


def anchor_of(measurement):
    """Der Messwert, der seine Reihe vertritt — der älteste Eintrag daraus.

    Eine Messreihe ist kein eigenes Modell: sie ist die Menge der Messwerte
    eines Beckens zu einem Zeitpunkt. Damit eine Auswertung trotzdem irgendwo
    hängen kann, bekommt die Reihe einen festen Vertreter, und der ist
    unabhängig von der Reihenfolge des Erfassens immer derselbe.
    """
    return (
        Measurement.objects.filter(
            tank_id=measurement.tank_id, measured_at=measurement.measured_at
        )
        .order_by("pk")
        .first()
    )


def latest_anchor(tank):
    """Vertreter der jüngsten Messreihe eines Beckens."""
    return Measurement.objects.filter(tank=tank).order_by("-measured_at", "pk").first()


# --------------------------------------------------------------------------
# Kontext zusammentragen
# --------------------------------------------------------------------------


def _targets(tank):
    return {
        target.parameter_id: target
        for target in tank.parameter_targets.select_related("parameter")
    }


def _range_of(measurement, targets):
    target = targets.get(measurement.parameter_id)
    if target is not None:
        return target.minimum, target.maximum
    return measurement.parameter.default_min, measurement.parameter.default_max


def _reading(measurement, targets):
    minimum, maximum = _range_of(measurement, targets)
    status = Status(measurement.status(targets))
    return ai.Reading(
        name=measurement.parameter.name,
        value=measurement.parameter.format_value(measurement.value),
        unit=measurement.parameter.unit,
        target=measurement.parameter.format_range(minimum, maximum)
        if (minimum is not None or maximum is not None)
        else "",
        status="" if status == Status.UNKNOWN else status.label,
    )


def _series(measurements, targets, *, note=""):
    """Baut eine Messreihe aus ihren Messwerten, inklusive gerechnetem CO₂."""
    values = {row.parameter.key: row.value for row in measurements}
    carbon = co2(values.get(KH_KEY), values.get(PH_KEY))
    return ai.Series(
        measured_at=timezone.localtime(measurements[0].measured_at).strftime("%d.%m.%Y %H:%M"),
        readings=tuple(_reading(row, targets) for row in measurements),
        co2="" if carbon is None else f"{carbon:.0f}",
        note=note,
    )


def _history(tank, anchor, targets):
    """Die Messreihen der letzten Tage vor der ausgewerteten, älteste zuerst."""
    since = anchor.measured_at - timedelta(days=HISTORY_DAYS)
    rows = (
        Measurement.objects.filter(
            tank=tank, measured_at__gte=since, measured_at__lt=anchor.measured_at
        )
        .select_related("parameter")
        .order_by("measured_at", "parameter__sort_order", "parameter__name")
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(row.measured_at, []).append(row)
    return tuple(_series(group, targets) for group in grouped.values())


def _events(tank, anchor):
    recent = tank.events.filter(occurred_at__lte=anchor.measured_at).order_by("-occurred_at")[
        :EVENT_LIMIT
    ]
    return tuple(
        ai.DiaryEvent(
            occurred_at=timezone.localtime(event.occurred_at).strftime("%d.%m.%Y %H:%M"),
            title=event.title,
            category=event.get_category_display(),
            description=event.description,
        )
        for event in reversed(list(recent))
    )


def _span(low, high, unit=""):
    """Toleranzbereich als Text; leer, wenn nichts hinterlegt ist.

    Über ``float`` formatiert, damit aus einem Katalogwert ``23.0`` auch
    „23" wird — ``Decimal`` behielte die Nachkommastelle bei.
    """
    unit = f" {unit}" if unit else ""
    if low is None and high is None:
        return ""
    if low is not None and high is not None:
        return f"{float(low):g}–{float(high):g}{unit}"
    if low is not None:
        return f"ab {float(low):g}{unit}"
    return f"bis {float(high):g}{unit}"


def _demands(species, extra=()):
    """Ansprüche einer Art, so wie sie im Katalog stehen."""
    ph = _span(species.ph_min, species.ph_max)
    gh = _span(species.gh_min, species.gh_max, "°dH")
    parts = [
        _span(species.temperature_min, species.temperature_max, "°C"),
        f"pH {ph}" if ph else "",
        f"GH {gh}" if gh else "",
        *extra,
    ]
    known = [part for part in parts if part]
    return ", ".join(known) if known else "keine Ansprüche im Katalog hinterlegt"


def _stock(tank):
    """Aktueller Besatz und Bestand an Pflanzen, jeweils mit Ansprüchen."""
    items = []
    stockings = tank.stockings.filter(removed_on__isnull=True).select_related("species")
    for stocking in stockings:
        extra = []
        if stocking.species.min_group_size > 1:
            extra.append(f"Gruppe ab {stocking.species.min_group_size}")
        if stocking.species.min_tank_volume_l:
            extra.append(f"ab {stocking.species.min_tank_volume_l} l")
        items.append(
            ai.StockItem(
                name=f"{stocking.species.display_name} (Tier)",
                count=stocking.quantity,
                note=_demands(stocking.species, extra),
            )
        )
    plantings = tank.plantings.filter(removed_on__isnull=True).select_related("species")
    for planting in plantings:
        extra = ["CO₂ nötig"] if planting.species.co2_required else []
        items.append(
            ai.StockItem(
                name=f"{planting.species.display_name} (Pflanze)",
                count=planting.quantity,
                note=_demands(planting.species, extra),
            )
        )
    return tuple(items)


def _facts(tank, targets):
    devices = tank.devices.filter(is_active=True).order_by("kind", "name")
    technic = ", ".join(f"{device.name} ({device.get_kind_display()})" for device in devices)
    ranges = ", ".join(
        f"{target.parameter.name} {target.range_label}"
        for target in sorted(
            targets.values(), key=lambda t: (t.parameter.sort_order, t.parameter.name)
        )
    )
    return ai.TankFacts(
        name=tank.name,
        volume_liters=float(tank.volume_liters) if tank.volume_liters is not None else None,
        length_cm=tank.length_cm,
        water_type=tank.get_water_type_display(),
        started_on=tank.setup_date.strftime("%d.%m.%Y"),
        age=tank.age_display,
        technic=technic,
        targets=ranges,
        notes=tank.notes,
    )


def build_context(anchor) -> ai.MeasurementContext:
    """Trägt alles zusammen, was die Auswertung dieser Messreihe braucht."""
    tank = anchor.tank
    targets = _targets(tank)
    measurements = list(series_of(anchor))
    note = next((row.note for row in measurements if row.note), "")
    return ai.MeasurementContext(
        tank=_facts(tank, targets),
        current=_series(measurements, targets, note=note),
        history=_history(tank, anchor, targets),
        events=_events(tank, anchor),
        stock=_stock(tank),
    )


# --------------------------------------------------------------------------
# Auswertung anstoßen und festhalten
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisState:
    """Was die Messwerte-Seite über die Auswertung wissen muss."""

    tank: Tank
    anchor: Measurement | None = None
    analysis: MeasurementAnalysis | None = None
    #: Die Datenlage hat sich seit der gespeicherten Auswertung geändert.
    stale: bool = False
    #: Hinweis für den Benutzer, wenn ein Auslösen nichts bewirkt hat.
    notice: str = ""

    @property
    def available(self) -> bool:
        """Sichtbar nur mit eingerichteter KI, am Becken erlaubt und mit Messwerten."""
        return bool(self.anchor) and self.tank.ai_analysis_wanted and ai.ai_enabled()

    @property
    def can_start(self) -> bool:
        """Auslösen geht immer — außer, es läuft gerade schon eine Auswertung.

        Bewusst nicht auf „hat sich etwas geändert" verengt: der Knopf, der
        einmal da war, soll nicht verschwinden. Bei unveränderter Datenlage
        kostet der Klick nichts und beantwortet sich mit einem Satz (siehe
        :func:`start`).
        """
        return self.available and not (self.analysis and self.analysis.is_running)


def latest_analysis(anchor):
    if anchor is None:
        return None
    return MeasurementAnalysis.objects.filter(measurement=anchor).first()


def state(tank, *, notice="") -> AnalysisState:
    """Aktueller Stand der Auswertung zur jüngsten Messreihe des Beckens."""
    anchor = latest_anchor(tank)
    analysis = latest_analysis(anchor)
    stale = False
    if analysis is not None and analysis.is_ready:
        stale = build_context(anchor).fingerprint() != analysis.context_hash
    return AnalysisState(
        tank=tank, anchor=anchor, analysis=analysis, stale=stale, notice=notice
    )


def start(anchor, *, user=None) -> AnalysisState:
    """Stößt die Auswertung der Messreihe an — ohne auf sie zu warten.

    Bei unveränderter Datenlage wird die vorhandene Auswertung wiederverwendet:
    dieselben Zahlen ein zweites Mal auszuwerten kostet Token und liefert
    lediglich einen anders formulierten Text.
    """
    tank = anchor.tank
    if not tank.ai_analysis_wanted or not ai.ai_enabled():
        return state(tank)

    latest = latest_analysis(anchor)
    if latest is not None and latest.is_running:
        return state(tank)

    context = build_context(anchor)
    fingerprint = context.fingerprint()
    # Nur die jüngste Auswertung zählt: nach einem Fehlschlag soll ein Klick
    # es wirklich noch einmal versuchen, auch wenn davor schon einmal etwas
    # zu denselben Daten gelungen war.
    if latest is not None and latest.is_ready and latest.context_hash == fingerprint:
        return state(
            tank,
            notice="An der Datenlage hat sich nichts geändert — die vorhandene "
            "Auswertung gilt weiter.",
        )

    analysis = MeasurementAnalysis.objects.create(
        measurement=anchor,
        status=MeasurementAnalysis.Status.RUNNING,
        context_hash=fingerprint,
    )
    _dispatch(run, analysis.pk, context, getattr(user, "pk", None))
    return state(tank)


def run(analysis_pk, context, user_pk=None) -> None:
    """Führt den Aufruf aus und schreibt das Ergebnis an die Auswertung.

    Läuft im Hintergrund und darf deshalb nichts nach außen werfen: was hier
    scheitert, steht danach als Fehler am Datensatz und nicht in einer Antwort,
    die längst beim Benutzer ist.
    """
    users = get_user_model().objects
    user = users.filter(pk=user_pk).first() if user_pk else None
    answer = ai.analyse_series(context, user=user)

    analysis = MeasurementAnalysis.objects.filter(pk=analysis_pk).first()
    if analysis is None:  # pragma: no cover - Messreihe wurde inzwischen gelöscht
        return
    result = getattr(answer, "result", None)
    analysis.usage_log = getattr(result, "usage_log", None)
    analysis.model_name = getattr(result, "model_name", "")[:100]
    analysis.completed_at = timezone.now()
    if answer:
        analysis.status = MeasurementAnalysis.Status.READY
        analysis.text = answer.text
    else:
        analysis.status = MeasurementAnalysis.Status.FAILED
        analysis.error_message = answer.error
    analysis.save(
        update_fields=[
            "status",
            "text",
            "model_name",
            "error_message",
            "usage_log",
            "completed_at",
        ]
    )


def _dispatch(func, *args) -> None:
    """Startet die Auswertung neben der Anfrage her.

    Ein eigener Thread und keine Aufgabenwarteschlange: die Anwendung bringt
    keinen Broker mit, und ein Aufruf, der eine Minute dauert und dessen
    Ergebnis in der Datenbank landet, braucht auch keinen. Geht der Prozess
    unterdessen unter, bleibt die Auswertung auf „läuft" stehen — sichtbar,
    und mit einem Klick neu anstoßbar.

    In Tests wird ``AI_ANALYSIS_BACKGROUND`` abgeschaltet: dort liefe der
    Thread außerhalb der Testtransaktion und fände die Messreihe nicht.
    """
    if not getattr(settings, "AI_ANALYSIS_BACKGROUND", True):
        func(*args)
        return
    threading.Thread(target=_guarded, args=(func, *args), daemon=True).start()


def _guarded(func, *args) -> None:
    try:
        func(*args)
    except Exception:  # pragma: no cover - der Thread hat niemanden, dem er das sagen könnte
        logger.exception("KI-Auswertung der Messreihe fehlgeschlagen")
    finally:
        connection.close()
