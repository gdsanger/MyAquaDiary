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
from tanks import derived as derived_module


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
    """Becken mit allem, was daran hängt: Zielbereiche, Besatz, Bepflanzung
    und die Einrichtung (Bodengrund, Hardscape).

    Einen Technikblock gibt es weiterhin nicht: Filterung, Beleuchtung, CO₂ und
    Düngung hängen als Gerät am Becken — und Geräte bleiben über MCP bewusst
    außen vor (#1226). Bodengrund und Hardscape sind keine Technik: sie haben
    keinen Betriebszustand, sondern eine Standzeit und eine Wirkung auf die
    Wasserwerte. Genau die wird gebraucht, wenn ein Verlauf erklärt werden soll.
    """
    overrides = {row.key: row for row in instance.derived_targets.all()}
    detail = tank(instance)
    detail.update(
        {
            "notes": instance.notes,
            "parameter_targets": [target(item) for item in instance.parameter_targets.all()],
            # Immer vollständig: was gerechnet wird, hat keinen Katalogeintrag,
            # in dem die Vorgabe stünde.
            "derived_targets": [
                derived_target(parameter, overrides.get(key))
                for key, parameter in derived_module.DERIVED_PARAMETERS.items()
            ],
            "animals": [stocking(item) for item in instance.stockings.all()],
            "plants": [planting(item) for item in instance.plantings.all()],
            # Von unten nach oben, wie der Bodengrund eingefüllt wurde.
            "substrate": [substrate_layer(item) for item in instance.substrate_layers.all()],
            "substrate_depth_cm": number(instance.substrate_depth_cm),
            "hardscape": [hardscape_item(item) for item in instance.hardscape.all()],
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


def derived_target(parameter, instance=None) -> dict:
    """Zielbereich einer gerechneten Größe.

    Anders als bei den gemessenen Größen steht hier auch dann etwas, wenn
    nichts überschrieben wurde: die Vorgabe liegt im Code und nicht in einer
    Tabelle, die ein Modell nachschlagen könnte. ``is_default`` sagt, welcher
    der beiden Fälle vorliegt.
    """
    minimum, maximum = derived_module.target_range(parameter, instance)
    return {
        "parameter": parameter.key,
        "parameter_label": parameter.name,
        "unit": parameter.unit,
        "minimum": number(minimum),
        "maximum": number(maximum),
        "range_label": parameter.format_range(minimum, maximum),
        "is_default": instance is None,
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
        # ``value`` ist ``null`` bei n.n. — nicht 0. ``below_detection`` sagt,
        # dass unterhalb der Nachweisgrenze durchaus etwas sein kann, der Test
        # es aber nicht auflöst; ``display_value`` steht dann auf „n.n.“.
        "value": number(instance.value),
        "below_detection": instance.below_detection,
        "detection_limit": number(instance.parameter.detection_limit),
        "display_value": instance.display_value,
        "status": status,
        "status_label": status_label,
        "target_minimum": number(minimum),
        "target_maximum": number(maximum),
        "note": instance.note,
    }


def derived_measurement(instance) -> dict:
    """Ein gerechneter Wert (CO₂) samt den Messwerten, aus denen er stammt.

    **Ohne ``measurement_id``**, und das ist keine Auslassung: den Wert gibt es
    in der Datenbank nicht. Er entsteht bei jeder Abfrage neu aus KH und pH und
    zieht deshalb mit, wenn einer der beiden korrigiert wird. Ein Werkzeug, das
    ihn ändern oder abrufen könnte, gibt es folglich auch nicht.

    ``formula`` und ``sources`` stehen dabei, damit ein Modell die Zahl
    nachvollziehen kann, statt sie für eine Messung zu halten — und damit es
    sieht, wie weit die beiden Messwerte auseinanderliegen.
    """
    status, status_label = _status(instance.status_code)
    return {
        "tank_id": instance.tank_id,
        "tank": instance.tank.name,
        # Der spätere der beiden Zeitpunkte: erst dann ist das Paar vollständig.
        "measured_at": moment(instance.measured_at),
        "parameter": instance.parameter.key,
        "parameter_label": instance.parameter.name,
        "unit": instance.parameter.unit,
        "value": number(instance.value),
        "display_value": instance.display_value,
        "status": status,
        "status_label": status_label,
        "target_minimum": number(instance.target_minimum),
        "target_maximum": number(instance.target_maximum),
        "is_derived": True,
        "formula": instance.parameter.formula,
        "sources": [
            {
                "measurement_id": source.pk,
                "parameter": source.parameter.key,
                "value": number(source.value),
                "measured_at": moment(source.measured_at),
            }
            for source in instance.sources
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

    ``social_structure`` steht hier neben der Stückzahl, obwohl es eine Angabe
    des Katalogeintrags ist: ohne sie ist eine Zwei nicht von einem Paar zu
    unterscheiden, und der Abgleich wäre nur mit einem zweiten Aufruf möglich.
    ``social_hints`` ist derselbe Abgleich, den die Oberfläche zeigt — ein
    Modell soll die Regel nicht nachbauen.
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
        # ``null`` heißt „nicht erfasst“ und nicht „keine“: bei einem Schwarm
        # zählt die Geschlechter niemand.
        "quantity_male": instance.quantity_male,
        "quantity_female": instance.quantity_female,
        "min_group_size": instance.species.min_group_size,
        "group_status": status,
        "group_status_label": status_label,
        "social_structure": instance.species.social_structure,
        "social_structure_label": _display(instance.species, "social_structure"),
        "social_hints": instance.social_hints,
        "added_on": moment(instance.added_on),
        "removed_on": moment(instance.removed_on),
        "is_active": instance.is_active,
        # Bezugsquelle, nicht Verbreitungsgebiet: woher diese Tiere stammen,
        # steht am Besatz — das Herkunftsgebiet der Art im Katalog.
        "provenance": instance.provenance,
        "provenance_label": _display(instance, "provenance"),
        "provenance_detail": instance.provenance_detail,
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
        # Wie vorgezogen — InVitro, submers, emers: der Unterschied, der beim
        # Anwachsen zählt.
        "provenance": instance.provenance,
        "provenance_label": _display(instance, "provenance"),
        "provenance_detail": instance.provenance_detail,
        "note": instance.note,
    }


def transfer(instance) -> dict:
    """Ein Umzug zwischen zwei eigenen Becken.

    Beide Becken stehen mit Kennung **und** Namen darin: ein Modell, das den
    Umzug nacherzählt, hat sonst nur zwei Zahlen und muss nachfragen.
    """
    return {
        "transfer_id": instance.pk,
        "kind": instance.kind,
        "kind_label": _display(instance, "kind"),
        "source_tank_id": instance.source_tank_id,
        "source_tank": instance.source_tank.name,
        "target_tank_id": instance.target_tank_id,
        "target_tank": instance.target_tank.name,
        "species_id": instance.species.pk,
        "name": str(instance.species),
        "scientific_name": instance.species.scientific_name,
        "quantity": instance.quantity,
        "moved_on": moment(instance.moved_on),
        "note": instance.note,
    }


# --------------------------------------------------------------------------
# Einrichtung
# --------------------------------------------------------------------------


def substrate_layer(instance) -> dict:
    """Eine Bodengrundschicht.

    ``position`` zählt von unten: 0 ist die unterste Schicht. Ohne diese Angabe
    wäre die Liste eine Menge und keine Schichtung.
    """
    status, status_label = _status(instance.depletion_status())
    return {
        "layer_id": instance.pk,
        "tank_id": instance.tank_id,
        "position": instance.position,
        "kind": instance.kind,
        "kind_label": _display(instance, "kind"),
        "product": instance.product,
        "grain_size": instance.grain_size,
        "depth_cm": number(instance.depth_cm),
        "added_on": moment(instance.added_on),
        "depleted_on": moment(instance.depleted_on),
        "status": status,
        "status_label": status_label,
        "note": instance.note,
    }


def hardscape_item(instance) -> dict:
    """Eine Einrichtungsposition: Wurzel, Stein, Botanik, Rückwand.

    ``affects_water`` kommt aus der Erfassung und nicht aus ``kind``: ob ein
    Stein auslaugt, hängt vom Gestein ab. ``water_effect`` sagt, wie.
    """
    return {
        "hardscape_id": instance.pk,
        "tank_id": instance.tank_id,
        "kind": instance.kind,
        "kind_label": _display(instance, "kind"),
        "name": instance.name,
        "quantity": instance.quantity,
        "added_on": moment(instance.added_on),
        "removed_on": moment(instance.removed_on),
        "expected_depletion": moment(instance.expected_depletion),
        "is_active": instance.is_active,
        "affects_water": instance.affects_water,
        "water_effect": instance.water_effect,
        "note": instance.note,
    }


# --------------------------------------------------------------------------
# Katalog
# --------------------------------------------------------------------------


def catalog_entry(kind: str, instance) -> dict:
    """Kurzform für die Suche — Name, Identität, Herkunft, Anspruch.

    Das Verbreitungsgebiet steht schon in der Kurzform: „nur Südamerika“ ist
    der häufigste Grund, im Katalog zu suchen, und dafür sollte niemand je
    Treffer einen zweiten Aufruf brauchen.
    """
    return {
        "kind": kind,
        "entry_id": instance.pk,
        "name": instance.display_name,
        "scientific_name": instance.scientific_name,
        # Wild- und Zuchtform sind getrennte Einträge; ohne diese beiden
        # Felder wäre für ein Sprachmodell nicht erkennbar, welcher von
        # zwei gleich benannten Treffern gemeint ist.
        "variant": instance.variant,
        "is_cultivated_form": instance.is_cultivated_form,
        "common_name": instance.common_name,
        "summary": instance.summary,
        # Natürliche Verbreitung der Art — nicht die Bezugsquelle eines
        # einzelnen Bestands, die steht am Besatzeintrag. Leer heißt „nicht
        # erfasst“, ``cultivar`` dagegen „kein Wildvorkommen“.
        "origin_region": instance.origin_region,
        "origin_region_label": _display(instance, "origin_region"),
        "water_type": instance.water_type,
        "water_type_label": _display(instance, "water_type"),
        "difficulty": instance.difficulty,
        "difficulty_label": _display(instance, "difficulty"),
    }


#: Steckbrief-Felder je Art. Nur Felder, die es im jeweiligen Katalogmodell
#: gibt — der Rest wird beim Aufbau übersprungen (siehe :func:`catalog_detail`).
CATALOG_FIELDS = {
    "animal": [
        "category", "temperament", "zone", "diet", "social_structure",
        "adult_size_cm", "min_group_size", "min_tank_volume_l",
        "origin_detail", "temperature_min", "temperature_max",
        "ph_min", "ph_max", "gh_min", "gh_max", "description",
    ],
    "plant": [
        "placement", "growth_rate", "light_demand", "co2_required",
        "max_height_cm", "origin_detail", "temperature_min", "temperature_max",
        "ph_min", "ph_max", "gh_min", "gh_max", "description",
    ],
}

#: Felder mit Auswahlliste — zusätzlich zum Schlüssel kommt der Klartext mit.
_CHOICE_FIELDS = {
    "category", "temperament", "placement", "growth_rate", "light_demand",
    "zone", "diet", "social_structure",
}

#: Zusammengefasste Bereiche als Text. Das Modell rechnet sie ohnehin für die
#: Oberfläche aus; für ein Sprachmodell ist „22–28 °C“ die klarere Auskunft als
#: zwei Zahlen ohne Einheit.
_RANGE_PROPERTIES = ["temperature_range", "ph_range", "gh_range"]


def catalog_detail(kind: str, instance) -> dict:
    """Der vollständige Steckbrief samt den Quellen, auf die er verweist."""
    detail = catalog_entry(kind, instance)
    for field in CATALOG_FIELDS[kind]:
        if not hasattr(instance, field):
            continue
        detail[field] = number(getattr(instance, field))
        if field in _CHOICE_FIELDS:
            detail[f"{field}_label"] = _display(instance, field)
    for prop in _RANGE_PROPERTIES:
        detail[prop] = getattr(instance, prop)
    detail["links"] = [species_link(link) for link in instance.links.all()]
    return detail


def species_link(instance) -> dict:
    """Ein Verweis auf eine fremde Wissensquelle.

    Die Adresse wird mitgegeben, nicht ihr Inhalt: abgerufen wird hier nichts,
    und was hinter dem Link steht, gehört jemand anderem.
    """
    return {
        "kind": instance.kind,
        "kind_label": _display(instance, "kind"),
        "title": instance.title,
        "url": instance.url,
    }
