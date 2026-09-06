"""Modelle als JSON-taugliche Wörterbücher.

Was ein Werkzeug zurückgibt, liest ein Sprachmodell — kein Programm. Daraus
folgen drei Entscheidungen:

* **Schlüssel mit Einheit im Namen** (``volume_net_l``, ``temp_min_c``), damit
  aus einer Zahl ohne Kontext keine falsche Aussage wird.
* **Berechnetes wird mitgeliefert** (CO₂, Wasserwechsel in Prozent, Abgleich
  mit dem Zielbereich): das Modell soll die Regeln der Anwendung nicht
  nachbauen müssen, sonst rechnet es anders als die Oberfläche.
* **Klartext neben dem Schlüssel** (``category`` und ``category_label``): der
  Schlüssel ist für Folgeaufrufe, der Klartext für die Antwort an den Menschen.

Decimal und Datum überleben ``json.dumps`` nicht — beides wird hier zu Zahl
bzw. ISO-String.
"""

from decimal import Decimal


def number(value):
    """Decimal -> float, alles andere unverändert.

    Float ist für Messwerte gut genug: hier werden Wasserwerte gelesen, keine
    Beträge gebucht.
    """
    if isinstance(value, Decimal):
        return float(value)
    return value


def moment(value):
    """Datum oder Zeitpunkt als ISO-String, ``None`` bleibt ``None``."""
    return value.isoformat() if value is not None else None


def _display(instance, field):
    """Der Klartext einer Auswahlliste (``get_<field>_display``)."""
    getter = getattr(instance, f"get_{field}_display", None)
    return getter() if getter else getattr(instance, field, "")


# --------------------------------------------------------------------------
# Becken
# --------------------------------------------------------------------------


def tank(instance) -> dict:
    """Stammdaten eines Beckens — was in eine Liste gehört."""
    return {
        "tank_id": instance.pk,
        "name": instance.name,
        "water_type": instance.water_type,
        "water_type_label": _display(instance, "water_type"),
        "biotope": instance.biotope,
        "model_name": instance.model_name,
        "length_cm": instance.length_cm,
        "height_cm": instance.height_cm,
        "depth_cm": instance.depth_cm,
        "volume_gross_l": number(instance.volume_gross_l),
        "volume_net_l": number(instance.volume_net_l),
        "started_on": moment(instance.started_on),
        "shut_down_on": moment(instance.shut_down_on),
        "is_dissolved": instance.is_dissolved,
    }


def tank_detail(instance) -> dict:
    """Becken mit allem, was daran hängt: Technik, Zielbereiche, Besatz, Pflanzen."""
    detail = tank(instance)
    detail.update(
        {
            "description": instance.description,
            "technology": {
                "substrate": instance.substrate,
                "hardscape": instance.hardscape,
                "filtration": instance.filtration,
                "lighting": instance.lighting,
                "co2": instance.co2,
                "fertilization": instance.fertilization,
            },
            "parameter_targets": [target(item) for item in instance.targets.all()],
            "animals": [tank_animal(item) for item in instance.animals.all()],
            "plants": [tank_plant(item) for item in instance.plants.all()],
        }
    )
    return detail


def target(instance) -> dict:
    return {
        "parameter": instance.parameter.key,
        "parameter_label": instance.parameter.name,
        "unit": instance.parameter.unit,
        "target": number(instance.target),
        "minimum": number(instance.minimum),
        "maximum": number(instance.maximum),
    }


# --------------------------------------------------------------------------
# Messreihen
# --------------------------------------------------------------------------


def measurement_value(instance, *, with_target=False) -> dict:
    """Ein Einzelwert einer Messreihe.

    ``below_detection`` ist nicht dasselbe wie „kein Wert“: „n. n.“ ist die
    Aussage, dass gemessen und nichts gefunden wurde.
    """
    data = {
        "parameter": instance.parameter.key,
        "parameter_label": instance.parameter.name,
        "unit": instance.parameter.unit,
        "value": number(instance.value),
        "below_detection": instance.below_detection,
    }
    if with_target:
        bounds = instance.target
        data["status"] = instance.status
        data["target_minimum"] = number(bounds.minimum) if bounds else None
        data["target_maximum"] = number(bounds.maximum) if bounds else None
    return data


def measurement(instance, *, with_targets=False) -> dict:
    return {
        "measurement_id": instance.pk,
        "tank_id": instance.tank_id,
        "tank": instance.tank.name,
        "measured_at": moment(instance.measured_at),
        "source": instance.source,
        "note": instance.note,
        "co2_mg_l": number(instance.co2_mg_l),
        "values": [
            measurement_value(value, with_target=with_targets)
            for value in instance.values.all()
        ],
    }


# --------------------------------------------------------------------------
# Ereignisse und Termine
# --------------------------------------------------------------------------


def event(instance) -> dict:
    return {
        "event_id": instance.pk,
        "tank_id": instance.tank_id,
        "tank": instance.tank.name,
        "occurred_at": moment(instance.occurred_at),
        "category": instance.category,
        "category_label": _display(instance, "category"),
        "title": instance.title,
        "description": instance.description,
        "water_changed_l": number(instance.water_changed_l),
        "water_change_percent": number(instance.water_change_percent),
        "schedule_id": instance.schedule_id,
    }


def schedule(instance, *, today=None) -> dict:
    """Ein wiederkehrender Termin.

    ``days_until_due`` ist negativ, wenn der Termin überfällig ist — für ein
    Modell die brauchbarere Angabe als zwei Datumsangaben zum Vergleichen.
    """
    data = {
        "schedule_id": instance.pk,
        "tank_id": instance.tank_id,
        "tank": instance.tank.name,
        "title": instance.title,
        "description": instance.description,
        "category": instance.event_category,
        "category_label": _display(instance, "event_category"),
        "interval": instance.interval,
        "interval_label": _display(instance, "interval"),
        "next_due_on": moment(instance.next_due_on),
        "last_done_on": moment(instance.last_done_on),
        "is_due": instance.is_due,
        "is_upcoming": instance.is_upcoming,
        "is_active": instance.is_active,
    }
    if today is not None:
        data["days_until_due"] = (instance.next_due_on - today).days
    return data


# --------------------------------------------------------------------------
# Besatz und Bepflanzung
# --------------------------------------------------------------------------


def tank_animal(instance) -> dict:
    return {
        "tank_animal_id": instance.pk,
        "tank_id": instance.tank_id,
        "catalog_animal_id": instance.animal_id,
        "name": str(instance.animal),
        "common_name": instance.animal.common_name,
        "label": instance.label,
        "status": instance.status,
        "status_label": _display(instance, "status"),
        "quantity": instance.quantity,
        "quantity_male": instance.quantity_male,
        "quantity_female": instance.quantity_female,
        "added_on": moment(instance.added_on),
        "origin": instance.origin,
        "note": instance.note,
        "below_min_group_size": instance.is_below_min_group_size,
    }


def tank_plant(instance) -> dict:
    return {
        "tank_plant_id": instance.pk,
        "tank_id": instance.tank_id,
        "catalog_plant_id": instance.plant_id,
        "name": str(instance.plant),
        "common_name": instance.plant.common_name,
        "status": instance.status,
        "status_label": _display(instance, "status"),
        "quantity": instance.quantity,
        "placement": instance.placement,
        "attached_to": instance.attached_to,
        "added_on": moment(instance.added_on),
        "removed_on": moment(instance.removed_on),
        "note": instance.note,
    }


def movement(instance) -> dict:
    return {
        "movement_id": instance.pk,
        "tank_animal_id": instance.tank_animal_id,
        "direction": instance.direction,
        "direction_label": _display(instance, "direction"),
        "reason": instance.reason,
        "reason_label": _display(instance, "reason"),
        "quantity": instance.quantity,
        "occurred_on": moment(instance.occurred_on),
        "note": instance.note,
    }


# --------------------------------------------------------------------------
# Katalog
# --------------------------------------------------------------------------


def catalog_entry(kind: str, instance) -> dict:
    """Kurzform für die Suche — Name, Identität, Anspruch."""
    return {
        "kind": kind,
        "entry_id": instance.pk,
        "name": str(instance),
        "scientific_name": instance.scientific_name,
        "common_name": instance.common_name,
        "difficulty": instance.difficulty,
        "difficulty_label": _display(instance, "difficulty"),
        "verified": instance.verified,
    }


#: Steckbrief-Felder je Art. Nur Felder, die es im jeweiligen Katalogmodell
#: gibt — der Rest wird beim Aufbau übersprungen (siehe :func:`catalog_detail`).
CATALOG_FIELDS = {
    "animal": [
        "variety", "group", "family", "origin", "size_max_cm", "min_tank_liters",
        "min_tank_length_cm", "min_group_size", "social_behavior", "zone",
        "lifespan_years", "temp_min_c", "temp_max_c", "ph_min", "ph_max",
        "kh_min", "kh_max", "gh_min", "gh_max", "diet", "breeding_type",
        "breeding_notes", "description", "care_notes", "compatibility_notes",
        "warning", "is_line_bred_variant", "source_url",
    ],
    "plant": [
        "cultivar", "family", "origin", "growth_form", "placement", "growth_rate",
        "light_demand", "co2_demand", "height_min_cm", "height_max_cm",
        "temp_min_c", "temp_max_c", "ph_min", "ph_max", "kh_min", "kh_max",
        "propagation", "description", "care_notes", "warning", "source_url",
    ],
}

#: Felder mit Auswahlliste — zusätzlich zum Schlüssel kommt der Klartext mit.
_CHOICE_FIELDS = {
    "group", "social_behavior", "zone", "diet", "breeding_type", "growth_form",
    "placement", "growth_rate", "light_demand", "co2_demand",
}


def catalog_detail(kind: str, instance) -> dict:
    """Der vollständige Steckbrief."""
    detail = catalog_entry(kind, instance)
    for field in CATALOG_FIELDS[kind]:
        if not hasattr(instance, field):
            continue
        detail[field] = number(getattr(instance, field))
        if field in _CHOICE_FIELDS:
            detail[f"{field}_label"] = _display(instance, field)
    return detail
