"""Zugang zu den Tagebuch-Modellen — eingegrenzt auf den Token-Inhaber.

Zwei Aufgaben, und beide sind der Grund, warum es dieses Modul gibt:

**Eingrenzung.** Jede Abfrage beginnt bei :func:`tanks`, also bei
``Tank.objects.for_user(user)``. Alles, was daran hängt — Messreihen,
Ereignisse, Termine, Besatz, Bepflanzung — wird über ``tank__in=tanks(user)``
gesucht, nicht über eine freie ``pk``-Abfrage. Ein Werkzeug, das eine
``tank_id`` entgegennimmt, kann damit nur Becken finden, die dem Token-Inhaber
gehören; eine fremde Kennung sieht für den Client aus wie eine unbekannte. Wäre
das anders, wäre jeder Token ein Generalschlüssel über alle Benutzer.

**Auflösung der Modelle.** Die Modelle werden importiert. Früher standen hier
Namen für :func:`django.apps.apps.get_model`, weil diese Schicht vor den
Modellen entstand; heute wäre ein Tippfehler im Namen von „Modell gibt es in
dieser Installation nicht“ nicht zu unterscheiden und fiele erst dem Client
auf. Ein Fehler beim Start ist besser als ein Werkzeug, das im Betrieb sagt,
es gebe die Daten nicht.

Diese Tabelle ist die einzige Stelle, an der die MCP-Schicht Modelle kennt. Sie
muss zu ``tanks/models.py`` und ``catalog/models.py`` passen und zu nichts
sonst — insbesondere nicht zu den Feldnamen aus dem Entwurf der Agira-Items,
gegen die diese Schicht ursprünglich geschrieben war (#1236).
"""

from catalog.models import AnimalSpecies, PlantSpecies
from tanks import derived, selectors
from tanks.models import (
    CareTask,
    Event,
    Measurement,
    Parameter,
    Planting,
    Stocking,
    Tank,
    TankDerivedTarget,
    TankParameterTarget,
)

from .exceptions import NotFound

#: Kurzname -> Modell. Ein Tippfehler fällt damit an einer Stelle auf und nicht
#: verstreut über zwölf Werkzeuge.
MODELS = {
    "tank": Tank,
    "parameter": Parameter,
    "parameter_target": TankParameterTarget,
    "derived_target": TankDerivedTarget,
    "measurement": Measurement,
    "event": Event,
    "task": CareTask,
    "stocking": Stocking,
    "planting": Planting,
    "catalog_animal": AnimalSpecies,
    "catalog_plant": PlantSpecies,
}

#: Art eines Katalogeintrags -> Kurzname des Modells.
CATALOG_MODELS = {"animal": "catalog_animal", "plant": "catalog_plant"}

#: Meldung bei jeder nicht auflösbaren Kennung. Absichtlich immer dieselbe:
#: „gibt es nicht“ und „gehört jemand anderem“ dürfen sich nicht unterscheiden.
NOT_FOUND = "Dazu gibt es keinen Eintrag. Prüfe die Kennung — sie muss zu einem eigenen Becken gehören."


def model(alias: str):
    """Das Modell zu einem Kurznamen."""
    return MODELS[alias]


def label(instance) -> str:
    """Verweis auf einen Datensatz für das Protokoll (``tanks.Event:12``)."""
    return f"{instance._meta.label}:{instance.pk}"


# --------------------------------------------------------------------------
# Eingegrenzte Abfragen
# --------------------------------------------------------------------------


def tanks(user):
    """Die Becken des Token-Inhabers — Ausgangspunkt jeder weiteren Abfrage."""
    return model("tank").objects.for_user(user)


def tank(user, tank_id: int, *, prefetch=()):
    """Ein eigenes Becken oder :class:`NotFound`.

    ``prefetch`` reicht Pfade an ``prefetch_related`` durch — für die
    Detailansicht, die Zielbereiche, Besatz und Bepflanzung ohnehin alle
    braucht.
    """
    queryset = tanks(user)
    if prefetch:
        queryset = queryset.prefetch_related(*prefetch)
    found = queryset.filter(pk=tank_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _owned(alias: str, user):
    """Alle Datensätze eines Modells, die an einem eigenen Becken hängen."""
    return model(alias).objects.filter(tank__in=tanks(user))


def measurements(user):
    """Messwerte — je Zeile ein Wert einer Messgröße, nicht eine Messreihe."""
    return _owned("measurement", user).select_related("tank", "parameter")


def measurement(user, measurement_id: int):
    found = measurements(user).filter(pk=measurement_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def events(user):
    return _owned("event", user).select_related("tank")


def tasks(user):
    return _owned("task", user).select_related("tank")


def task(user, task_id: int):
    found = tasks(user).filter(pk=task_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def stockings(user):
    return _owned("stocking", user).select_related("tank", "species")


def stocking(user, stocking_id: int):
    found = stockings(user).filter(pk=stocking_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def plantings(user):
    return _owned("planting", user).select_related("tank", "species")


def parameter_targets(user, tank_ids):
    """Zielbereiche mehrerer Becken auf einmal — ``{tank_id: {parameter_id: Ziel}}``.

    Ohne diese Vorabladung fragt jeder Messwert seinen Zielbereich einzeln ab;
    bei 200 Treffern sind das 200 Abfragen.
    """
    found: dict[int, dict[int, object]] = {}
    if not tank_ids:
        return found
    rows = model("parameter_target").objects.filter(tank_id__in=set(tank_ids))
    for row in rows:
        found.setdefault(row.tank_id, {})[row.parameter_id] = row
    return found


def derived_values(rows):
    """CO₂ zu einer Messwertmenge — gerechnet, nie gespeichert.

    ``rows`` sind Messwerte beliebiger Größen und stammen aus :func:`measurements`
    und damit bereits aus eigenen Becken — hier wird nicht noch einmal
    eingegrenzt, sondern nur gerechnet. Gepaart wird je Becken getrennt: eine
    KH aus dem einen und ein pH aus dem anderen Becken ergeben keinen Wert.

    Der Statusabgleich läuft über dieselbe Funktion wie in der Oberfläche: ein
    Modell soll nicht anders über den Zielbereich urteilen als die Anwendung.
    """
    window = derived.pairing_window()
    by_tank: dict[int, dict[str, list]] = {}
    for row in rows:
        if row.parameter.key in derived.CO2.sources:
            per_key = by_tank.setdefault(row.tank_id, {key: [] for key in derived.CO2.sources})
            per_key[row.parameter.key].append(row)

    values = []
    for per_key in by_tank.values():
        for group in per_key.values():
            group.sort(key=lambda item: item.measured_at)
        values.extend(derived.co2_values(per_key["kh"], per_key["ph"], window))
    values.sort(key=lambda item: item.measured_at, reverse=True)

    targets = selectors.derived_target_map(list(by_tank))
    return selectors.annotate_derived_status(values, targets)


# --------------------------------------------------------------------------
# Userübergreifende Daten
# --------------------------------------------------------------------------


def catalog_model(kind: str):
    """Katalogmodell zu ``animal``/``plant``.

    Der Katalog gehört allen — hier wird deshalb nicht eingegrenzt. Er ist
    ausschließlich lesbar: einen Eintrag, den alle Benutzer sehen, legt niemand
    über eine Einzelnutzer-Schnittstelle an.
    """
    return model(CATALOG_MODELS[kind])


def catalog_entry(kind: str, entry_id: int):
    found = catalog_model(kind).objects.filter(pk=entry_id).first()
    if found is None:
        raise NotFound("Diesen Katalogeintrag gibt es nicht.")
    return found


def parameter(key: str):
    """Messgröße über ihr Kürzel (``ph``, ``kh``, ``no3`` …)."""
    found = model("parameter").objects.filter(key=key).first()
    if found is None:
        known = ", ".join(
            model("parameter").objects.values_list("key", flat=True)[:40]
        )
        raise NotFound(f"Unbekannte Messgröße „{key}“. Bekannt sind: {known or 'keine'}.")
    return found
