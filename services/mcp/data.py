"""Zugang zu den Tagebuch-Modellen — eingegrenzt auf den Token-Inhaber.

Zwei Aufgaben, und beide sind der Grund, warum es dieses Modul gibt:

**Eingrenzung.** Jede Abfrage beginnt bei :func:`tanks`, also bei
``Tank.objects.for_user(user)``. Alles, was daran hängt — Messreihen,
Ereignisse, Termine, Besatz, Bepflanzung — wird über ``tank__in=tanks(user)``
gesucht, nicht über eine freie ``pk``-Abfrage. Ein Werkzeug, das eine
``tank_id`` entgegennimmt, kann damit nur Becken finden, die dem Token-Inhaber
gehören; eine fremde Kennung sieht für den Client aus wie eine unbekannte. Wäre
das anders, wäre jeder Token ein Generalschlüssel über alle Benutzer.

**Auflösung der Modelle.** Becken und Katalog liegen in einem anderen Schritt
des Epics. Damit der MCP-Server schon vorher vollständig ist, werden sie über
:func:`django.apps.apps.get_model` aufgelöst statt importiert — genau wie in
:mod:`services.ai.catalog`. Fehlen sie noch, meldet jedes Werkzeug sauber
„Datenmodell nicht verfügbar“ statt beim Import zu scheitern; sobald sie da
sind, greift derselbe Code ohne weitere Änderung.
"""

from django.apps import apps

from .exceptions import DataModelUnavailable, NotFound

#: Kurzname -> (App, Modell). Ein Tippfehler fällt damit an einer Stelle auf
#: und nicht verstreut über zwölf Werkzeuge.
MODELS = {
    "tank": ("tanks", "Tank"),
    "parameter": ("tanks", "Parameter"),
    "measurement": ("tanks", "Measurement"),
    "measurement_value": ("tanks", "MeasurementValue"),
    "event": ("tanks", "Event"),
    "schedule": ("tanks", "MaintenanceSchedule"),
    "tank_animal": ("tanks", "TankAnimal"),
    "tank_animal_movement": ("tanks", "TankAnimalMovement"),
    "tank_plant": ("tanks", "TankPlant"),
    "catalog_animal": ("catalog", "CatalogAnimal"),
    "catalog_plant": ("catalog", "CatalogPlant"),
}

#: Art eines Katalogeintrags -> Kurzname des Modells.
CATALOG_MODELS = {"animal": "catalog_animal", "plant": "catalog_plant"}

#: Meldung bei jeder nicht auflösbaren Kennung. Absichtlich immer dieselbe:
#: „gibt es nicht“ und „gehört jemand anderem“ dürfen sich nicht unterscheiden.
NOT_FOUND = "Dazu gibt es keinen Eintrag. Prüfe die Kennung — sie muss zu einem eigenen Becken gehören."


def model(alias: str):
    """Das Modell zu einem Kurznamen.

    :raises DataModelUnavailable: solange das Modell in dieser Installation
        fehlt. Der Client bekommt daraus einen Werkzeug-Fehler, keinen
        Serverabsturz.
    """
    app_label, name = MODELS[alias]
    try:
        return apps.get_model(app_label, name)
    except LookupError as exc:
        raise DataModelUnavailable(
            f"Das Datenmodell „{app_label}.{name}“ ist in dieser Installation nicht "
            "verfügbar. Ohne Becken-Modell gibt es hier nichts zu lesen und nichts "
            "zu schreiben."
        ) from exc


def is_available() -> bool:
    """True, wenn die Tagebuch-Modelle da sind."""
    try:
        model("tank")
    except DataModelUnavailable:
        return False
    return True


def label(instance) -> str:
    """Verweis auf einen Datensatz für das Protokoll (``tanks.Event:12``)."""
    return f"{instance._meta.label}:{instance.pk}"


# --------------------------------------------------------------------------
# Eingegrenzte Abfragen
# --------------------------------------------------------------------------


def tanks(user):
    """Die Becken des Token-Inhabers — Ausgangspunkt jeder weiteren Abfrage."""
    return model("tank").objects.for_user(user)


def tank(user, tank_id: int):
    """Ein eigenes Becken oder :class:`NotFound`."""
    found = tanks(user).filter(pk=tank_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _owned(alias: str, user):
    """Alle Datensätze eines Modells, die an einem eigenen Becken hängen."""
    return model(alias).objects.filter(tank__in=tanks(user))


def measurements(user):
    return _owned("measurement", user).select_related("tank")


def measurement(user, measurement_id: int):
    found = measurements(user).filter(pk=measurement_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def events(user):
    return _owned("event", user).select_related("tank")


def schedules(user):
    return _owned("schedule", user).select_related("tank")


def schedule(user, schedule_id: int):
    found = schedules(user).filter(pk=schedule_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def tank_animals(user):
    return _owned("tank_animal", user).select_related("tank", "animal")


def tank_animal(user, tank_animal_id: int):
    found = tank_animals(user).filter(pk=tank_animal_id).first()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def tank_plants(user):
    return _owned("tank_plant", user).select_related("tank", "plant")


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
