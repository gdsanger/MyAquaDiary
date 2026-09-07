"""Modelle als JSON-taugliche Wörterbücher.

Was ein Werkzeug zurückgibt, liest ein Sprachmodell — kein Programm. Daraus
folgen drei Entscheidungen:

* **Schlüssel mit Einheit im Namen** (``volume_liters``, ``length_cm``), damit
  aus einer Zahl ohne Kontext keine falsche Aussage wird. Wo das Modellfeld die
  Einheit schon trägt, wird sein Name unverändert übernommen; wo nicht, steht
  die Einheit im Nachbarschlüssel (``unit``, ``display_value``).
* **Berechnetes wird mitgeliefert** (Abgleich mit dem Zielbereich, Fälligkeit
  in Tagen, Gruppengröße): das Modell soll die Regeln der Anwendung nicht
  nachbauen müssen, sonst rechnet es anders als die Oberfläche.
* **Klartext neben dem Schlüssel** (``category`` und ``category_label``): der
  Schlüssel ist für Folgeaufrufe, der Klartext für die Antwort an den Menschen.

Die Schlüssel folgen den Feldnamen aus ``tanks/models.py`` und
``catalog/models.py``. Das ist keine Kosmetik: die frühere Fassung war gegen
Entwurfsnamen geschrieben (``biotope``, ``shut_down_on``, ``volume_net_l``) und
scheiterte deshalb erst beim Aufruf (#1236).

Decimal und Datum überleben ``json.dumps`` nicht — beides wird hier zu Zahl
bzw. ISO-String.
"""

from decimal import Decimal

from django.utils import timezone

from core.enums import Status


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


def _status(value):
    """Statuswert und sein Klartext — ``classify_value`` liefert ein ``Status``."""
    return str(value), Status(value).label


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
        "location": instance.location,
        "length_cm": instance.length_cm,
        "width_cm": instance.width_cm,
        "height_cm": instance.height_cm,
        "volume_liters": number(instance.volume_liters),
        "setup_date": moment(instance.setup_date),
        "dissolved_on": moment(instance.dissolved_on),
        "is_dissolved": instance.is_dissolved,
        "age_display": instance.age_display,
    }


def tank_detail(instance) -> dict:
    """Becken mit allem, was daran hängt: Zielbereiche, Besatz, Bepflanzung.

    Einen Technikblock gibt es nicht: Bodengrund, Filterung, Beleuchtung, CO₂
    und Düngung stehen nicht am Becken. Was an Technik erfasst ist, hängt als
    Gerät daran — und Geräte bleiben über MCP bewusst außen vor (#1226).
    """
    detail = tank(instance)
    detail.update(
        {
            "notes": instance.notes,
            "parameter_targets": [target(item) for item in instance.parameter_targets.all()],
            "animals": [stocking(item) for item in instance.stockings.all()],
            "plants": [planting(item) for item in instance.plantings.all()],
        }
    )
    return detail


def target(instance) -> dict:
    return {
        "parameter": instance.parameter.key,
        "parameter_label": instance.parameter.name,
        "unit": instance.parameter.unit,
        "minimum": number(instance.minimum),
        "maximum": number(instance.maximum),
        "range_label": instance.range_label,
    }


# --------------------------------------------------------------------------
# Messwerte
# --------------------------------------------------------------------------


def measurement(instance, *, targets=None) -> dict:
    """Ein Messwert: eine Messgröße, ein Wert, ein Zeitpunkt.

    Das Modell speichert einen Wert je Zeile — eine „Messreihe“ mit mehreren
    Werten gibt es nicht. Mehrere gleichzeitig erfasste Werte teilen sich
    lediglich ``measured_at``.

    ``targets`` nimmt die vorgeladenen Zielbereiche des Beckens entgegen
    (``{parameter_id: TankParameterTarget}``) und spart damit je Wert eine
    Abfrage; ohne Angabe schlägt das Modell selbst nach.
    """
    minimum, maximum = instance.target_range(targets)
    status, status_label = _status(instance.status(targets))
    return {
        "measurement_id": instance.pk,
        "tank_id": instance.tank_id,
        "tank": instance.tank.name,
        "measured_at": moment(instance.measured_at),
        "parameter": instance.parameter.key,
        "parameter_label": instance.parameter.name,
        "unit": instance.parameter.unit,
        "value": number(instance.value),
        "display_value": instance.display_value,
        "status": status,
        "status_label": status_label,
        "target_minimum": number(minimum),
        "target_maximum": number(maximum),
        "note": instance.note,
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
    }


def task(instance, *, today=None) -> dict:
    """Ein Pflegetermin.

    ``days_until_due`` ist negativ, wenn der Termin überfällig ist — für ein
    Modell die brauchbarere Angabe als zwei Datumsangaben zum Vergleichen.
    """
    today = today or timezone.localdate()
    status, status_label = _status(instance.status(today))
    return {
        "task_id": instance.pk,
        "tank_id": instance.tank_id,
        "tank": instance.tank.name,
        "title": instance.title,
        "category": instance.category,
        "category_label": _display(instance, "category"),
        "interval_days": instance.interval_days,
        "due_on": moment(instance.due_on),
        "days_until_due": instance.days_until_due(today),
        "due_label": instance.due_label,
        "last_completed_on": moment(instance.last_completed_on),
        "status": status,
        "status_label": status_label,
        "is_active": instance.is_active,
        "notes": instance.notes,
    }


def task_completion(instance) -> dict:
    return {
        "completion_id": instance.pk,
        "task_id": instance.task_id,
        "completed_on": moment(instance.completed_on),
        "note": instance.note,
    }


# --------------------------------------------------------------------------
# Besatz und Bepflanzung
# --------------------------------------------------------------------------


def stocking(instance) -> dict:
    """Besatz: eine Tierart in einem Becken.

    Eine Bestandshistorie führt das Modell nicht — eine Änderung ist eine neue
    Stückzahl, ein Abgang ein ``removed_on``.
    """
    status, status_label = _status(instance.group_status)
    return {
        "stocking_id": instance.pk,
        "tank_id": instance.tank_id,
        "species_id": instance.species_id,
        "name": str(instance.species),
        "scientific_name": instance.species.scientific_name,
        "common_name": instance.species.common_name,
        "quantity": instance.quantity,
        "min_group_size": instance.species.min_group_size,
        "group_status": status,
        "group_status_label": status_label,
        "added_on": moment(instance.added_on),
        "removed_on": moment(instance.removed_on),
        "is_active": instance.is_active,
        "note": instance.note,
    }


def planting(instance) -> dict:
    return {
        "planting_id": instance.pk,
        "tank_id": instance.tank_id,
        "species_id": instance.species_id,
        "name": str(instance.species),
        "scientific_name": instance.species.scientific_name,
        "common_name": instance.species.common_name,
        "quantity": instance.quantity,
        "planted_on": moment(instance.planted_on),
        "removed_on": moment(instance.removed_on),
        "is_active": instance.is_active,
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
        "name": instance.display_name,
        "scientific_name": instance.scientific_name,
        "common_name": instance.common_name,
        "summary": instance.summary,
        "water_type": instance.water_type,
        "water_type_label": _display(instance, "water_type"),
        "difficulty": instance.difficulty,
        "difficulty_label": _display(instance, "difficulty"),
    }


#: Steckbrief-Felder je Art. Nur Felder, die es im jeweiligen Katalogmodell
#: gibt — der Rest wird beim Aufbau übersprungen (siehe :func:`catalog_detail`).
CATALOG_FIELDS = {
    "animal": [
        "category", "temperament", "adult_size_cm", "min_group_size",
        "min_tank_volume_l", "temperature_min", "temperature_max",
        "ph_min", "ph_max", "gh_min", "gh_max", "description",
    ],
    "plant": [
        "placement", "growth_rate", "light_demand", "co2_required",
        "max_height_cm", "temperature_min", "temperature_max",
        "ph_min", "ph_max", "gh_min", "gh_max", "description",
    ],
}

#: Felder mit Auswahlliste — zusätzlich zum Schlüssel kommt der Klartext mit.
_CHOICE_FIELDS = {
    "category", "temperament", "placement", "growth_rate", "light_demand",
}

#: Zusammengefasste Bereiche als Text. Das Modell rechnet sie ohnehin für die
#: Oberfläche aus; für ein Sprachmodell ist „22–28 °C“ die klarere Auskunft als
#: zwei Zahlen ohne Einheit.
_RANGE_PROPERTIES = ["temperature_range", "ph_range", "gh_range"]


def catalog_detail(kind: str, instance) -> dict:
    """Der vollständige Steckbrief."""
    detail = catalog_entry(kind, instance)
    for field in CATALOG_FIELDS[kind]:
        if not hasattr(instance, field):
            continue
        detail[field] = number(getattr(instance, field))
        if field in _CHOICE_FIELDS:
            detail[f"{field}_label"] = _display(instance, field)
    for prop in _RANGE_PROPERTIES:
        detail[prop] = getattr(instance, prop)
    return detail
