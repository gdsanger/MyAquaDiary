"""Leseabfragen rund um Becken.

Hier liegen die Abfragen, die mehrere Ansichten teilen (Dashboard, Becken-
übersicht, Beckendetail). Sie geben fertig ausgewertete Objekte zurück, damit
die Templates keine Logik und keine zusätzlichen Abfragen brauchen.
"""

from datetime import timedelta

from django.db.models import OuterRef, Subquery
from django.utils import timezone

from core.enums import STATUS_SEVERITY, Status
from services.models import Device

from .models import (
    UPCOMING_DAYS,
    CareTask,
    Event,
    Measurement,
    Stocking,
    Tank,
    TankParameterTarget,
    TankPhoto,
    classify_value,
)


def target_map(tanks):
    """``{(tank_id, parameter_id): TankParameterTarget}`` für eine Beckenmenge."""
    targets = TankParameterTarget.objects.filter(tank__in=tanks).select_related("parameter")
    return {(t.tank_id, t.parameter_id): t for t in targets}


def latest_measurements(tanks):
    """Je Becken und Parameter der jüngste Messwert."""
    newest = (
        Measurement.objects.filter(tank=OuterRef("tank"), parameter=OuterRef("parameter"))
        .order_by("-measured_at")
        .values("pk")[:1]
    )
    return list(
        Measurement.objects.filter(tank__in=tanks)
        .filter(pk=Subquery(newest))
        .select_related("parameter", "tank")
        .order_by("tank__name", "parameter__sort_order", "parameter__name")
    )


def annotate_status(measurements, targets):
    """Hängt jedem Messwert seinen Status an — ohne Abfrage pro Zeile."""
    for measurement in measurements:
        target = targets.get((measurement.tank_id, measurement.parameter_id))
        if target is not None:
            minimum, maximum = target.minimum, target.maximum
        else:
            minimum = measurement.parameter.default_min
            maximum = measurement.parameter.default_max
        measurement.target_minimum = minimum
        measurement.target_maximum = maximum
        measurement.target_label = measurement.parameter.format_range(minimum, maximum)
        measurement.status_code = classify_value(measurement.value, minimum, maximum)
        measurement.status_label = Status(measurement.status_code).label
    return measurements


def tank_measurement_overview(tank):
    """Jüngster Messwert je Parameter eines Beckens, inklusive Status."""
    tanks = [tank]
    return annotate_status(latest_measurements(tanks), target_map(tanks))


def tank_measurements(tank, limit=50):
    """Jüngste Messwerte eines Beckens mit ausgewertetem Status."""
    measurements = list(
        Measurement.objects.filter(tank=tank)
        .select_related("parameter", "tank")
        .order_by("-measured_at")[:limit]
    )
    return annotate_status(measurements, target_map([tank]))


def open_tasks_for_tank(tank, horizon_days=UPCOMING_DAYS):
    today = timezone.localdate()
    return list(
        tank.tasks.filter(is_active=True, due_on__lte=today + timedelta(days=horizon_days)).order_by(
            "due_on"
        )
    )


def open_tasks(user, limit=None, horizon_days=UPCOMING_DAYS):
    """Fällige und anstehende Termine über alle aktiven Becken."""
    tasks = (
        CareTask.objects.for_user(user)
        .open(horizon_days=horizon_days)
        .select_related("tank")
        .order_by("due_on", "tank__name", "title")
    )
    return list(tasks[:limit] if limit else tasks)


def _warning(status, title, detail, tank):
    return {"status": status, "title": title, "detail": detail, "tank": tank}


def warnings(user, limit=None):
    """Alle Auffälligkeiten über alle aktiven Becken, kritischste zuerst.

    Vier Quellen: Messwerte außerhalb des Zielbereichs, Gerätefehler, fällige
    Wartung und unterschrittene Gruppengrößen.

    Der Gerätestatus wird gelesen, wie er am Gerät steht — bei angebundenen
    Geräten hat ihn die letzte Abfrage geschrieben (ein Eheim-Fehlercode wird
    dort zu ``CRITICAL``), bei den übrigen ein Mensch. Genau deshalb ist es ein
    Feld und nicht zwei: hier muss niemand beide Wege kennen.
    """
    today = timezone.localdate()
    tanks = list(Tank.objects.for_user(user).active())
    if not tanks:
        return []

    items = []

    for measurement in annotate_status(latest_measurements(tanks), target_map(tanks)):
        if measurement.status_code in (Status.WARN, Status.CRITICAL):
            items.append(
                _warning(
                    measurement.status_code,
                    f"{measurement.parameter.name} außerhalb des Zielbereichs",
                    f"{measurement.display_value} · Ziel "
                    f"{measurement.parameter.format_range(measurement.target_minimum, measurement.target_maximum)}",
                    measurement.tank,
                )
            )

    for device in Device.objects.filter(tank__in=tanks, is_active=True).select_related("tank"):
        if device.status in (Status.WARN, Status.CRITICAL):
            items.append(
                _warning(
                    device.status,
                    f"{device.name}: {device.get_status_display()}",
                    device.status_message or device.get_kind_display(),
                    device.tank,
                )
            )
        maintenance = device.maintenance_status(today)
        if maintenance in (Status.WARN, Status.CRITICAL):
            due = device.maintenance_due_on
            items.append(
                _warning(
                    maintenance,
                    f"Wartung fällig: {device.name}",
                    f"{device.get_kind_display()} · fällig am {due:%d.%m.%Y}",
                    device.tank,
                )
            )

    stockings = (
        Stocking.objects.filter(tank__in=tanks, removed_on__isnull=True)
        .select_related("species", "tank")
        .filter(species__min_group_size__gt=1)
    )
    for stocking in stockings:
        if stocking.quantity < stocking.species.min_group_size:
            items.append(
                _warning(
                    Status.WARN,
                    f"Gruppengröße unterschritten: {stocking.species.display_name}",
                    f"{stocking.quantity} statt mindestens {stocking.species.min_group_size} Tiere",
                    stocking.tank,
                )
            )

    items.sort(key=lambda item: (STATUS_SEVERITY[item["status"]], item["tank"].name, item["title"]))
    return items[:limit] if limit else items


def recent_activity(user, limit=12, days=90):
    """Messungen, Ereignisse und Fotos aller Becken in einer Zeitleiste."""
    since = timezone.now() - timedelta(days=days)
    tanks = list(Tank.objects.for_user(user))
    if not tanks:
        return []

    entries = []

    measurements = (
        Measurement.objects.filter(tank__in=tanks, measured_at__gte=since)
        .select_related("parameter", "tank")
        .order_by("-measured_at")[:limit]
    )
    for measurement in measurements:
        entries.append(
            {
                "kind": "measurement",
                "kind_label": "Messung",
                "timestamp": measurement.measured_at,
                "title": f"{measurement.parameter.name}: {measurement.display_value}",
                "tank": measurement.tank,
            }
        )

    events = (
        Event.objects.filter(tank__in=tanks, occurred_at__gte=since)
        .select_related("tank")
        .order_by("-occurred_at")[:limit]
    )
    for event in events:
        entries.append(
            {
                "kind": "event",
                "kind_label": event.get_category_display(),
                "timestamp": event.occurred_at,
                "title": event.title,
                "tank": event.tank,
            }
        )

    photos = (
        TankPhoto.objects.filter(tank__in=tanks, created_at__gte=since)
        .select_related("tank")
        .order_by("-created_at")[:limit]
    )
    for photo in photos:
        entries.append(
            {
                "kind": "photo",
                "kind_label": "Foto",
                "timestamp": photo.created_at,
                "title": photo.caption or "Neues Foto",
                "tank": photo.tank,
            }
        )

    entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return entries[:limit]


def last_used_tank(user, preferred_id=None):
    """Zuletzt genutztes Becken: zuletzt besucht, sonst zuletzt gemessen.

    Die Beckenkennung kommt aus der Session (im Beckendetail gesetzt). Fehlt
    sie oder passt sie nicht mehr, gewinnt das Becken mit der jüngsten Messung.
    """
    tanks = Tank.objects.for_user(user).active()
    if preferred_id:
        tank = tanks.filter(pk=preferred_id).first()
        if tank is not None:
            return tank
    latest = (
        tanks.with_overview()
        .exclude(last_measured_at=None)
        .order_by("-last_measured_at")
        .first()
    )
    return latest or tanks.first()
