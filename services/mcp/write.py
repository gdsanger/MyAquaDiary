"""Schreibende Werkzeuge.

Alle vier Leitplanken greifen hier gemeinsam:

* **Eingrenzung.** Jede Kennung im Aufruf wird über
  :mod:`services.mcp.data` aufgelöst und gehört damit dem Token-Inhaber. Auch
  das Zielbecken einer Umsetzung — sonst ließen sich Tiere in fremde Becken
  buchen.
* **Kennzeichnung.** Was hier entsteht, trägt ``source="mcp"``, sofern das
  Modell eine Herkunft führt (:func:`services.mcp.audit.mark_source`).
* **Prüfung durch das Modell.** Geschrieben wird über ``full_clean()`` und die
  Methoden der Modelle (``mark_done``, die Bestandsfortschreibung der
  Bewegungen) — keine zweite Geschäftslogik neben der Web-App.
* **Protokoll und Schreibrecht** liegen im Rahmen drumherum
  (:mod:`services.mcp.runner`), nicht in jedem Werkzeug einzeln.

Kein ``delete_*``: Was falsch angelegt wurde, wird in der Oberfläche korrigiert.
Ein irrtümlich ausgelöster Löschbefehl aus einem Chatfenster ist nicht
zurückzuholen — die Korrektur eines falschen Werts dagegen ist zwei Klicks
Arbeit. Ebenso wenig gibt es hier Katalogpflege: ein Steckbrief, den alle
Benutzer sehen, entsteht nicht über die Einzelnutzer-Schnittstelle.
"""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from . import data, serialize
from .audit import mark_source
from .registry import Written, tool


def _values_of(model, field: str) -> list[str]:
    """Die zulässigen Werte einer Auswahlliste am Modell."""
    return [value for value, _ in model._meta.get_field(field).choices]


# --------------------------------------------------------------------------
# Messreihen
# --------------------------------------------------------------------------


@tool(
    "create_measurement",
    "Legt eine Messreihe an: ein Messzeitpunkt mit beliebig vielen Werten. "
    "Die Messgrößen werden über ihr Kürzel angegeben (ph, kh, gh, no3, temp …); "
    "ein Wert, der unter der Nachweisgrenze liegt, bekommt below_detection "
    "statt einer Zahl.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken, zu dem gemessen wurde."},
            "measured_at": {
                "type": "string",
                "description": "Zeitpunkt der Messung nach ISO 8601. Ohne Angabe: jetzt.",
            },
            "note": {"type": "string", "description": "Bemerkung zur Messreihe."},
            "values": {
                "type": "array",
                "description": "Die gemessenen Werte. Mindestens einer.",
                "items": {
                    "type": "object",
                    "properties": {
                        "parameter": {"type": "string", "description": "Kürzel der Messgröße."},
                        "value": {"type": "number", "description": "Gemessener Wert."},
                        "below_detection": {
                            "type": "boolean",
                            "description": "Wahr, wenn nichts nachweisbar war („n. n.“).",
                        },
                    },
                    "required": ["parameter"],
                },
            },
        },
        "required": ["tank_id", "values"],
    },
)
def create_measurement(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    rows = arguments.objects("values", required=True)

    model = data.model("measurement")
    value_model = data.model("measurement_value")
    measurement = mark_source(
        model(
            tank=tank,
            measured_at=arguments.datetime("measured_at", default=timezone.now()),
            note=arguments.text("note", max_length=2000),
        )
    )

    with transaction.atomic():
        measurement.save()
        for row in rows:
            value = value_model(
                measurement=measurement,
                parameter=data.parameter(row.text("parameter", required=True)),
                value=row.decimal("value"),
                below_detection=row.boolean("below_detection"),
            )
            # full_clean() setzt die Regeln des Modells durch — dass ein Wert
            # und „n. n.“ sich ausschließen ebenso wie „jede Messgröße nur
            # einmal je Messreihe“. Die Meldung daraus geht unverändert an den
            # Client.
            value.full_clean()
            value.save()

    measurement.refresh_from_db()
    return Written(
        serialize.measurement(measurement, with_targets=True), data.label(measurement)
    )


# --------------------------------------------------------------------------
# Ereignisse
# --------------------------------------------------------------------------


@tool(
    "create_event",
    "Legt ein Ereignis am Becken an: Wasserwechsel, Pflege, Technikänderung, "
    "Beobachtung, Problem, Nachwuchs. Bei einem Wasserwechsel gehört die "
    "gewechselte Menge in water_changed_l — der Prozentsatz wird daraus "
    "berechnet.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken, an dem es passiert ist."},
            "title": {"type": "string", "description": "Kurze Überschrift."},
            "category": {
                "type": "string",
                "description": "Kategorie, z. B. water_change, maintenance, observation. "
                "Ohne Angabe: other.",
            },
            "occurred_at": {
                "type": "string",
                "description": "Zeitpunkt nach ISO 8601. Ohne Angabe: jetzt.",
            },
            "description": {"type": "string", "description": "Ausführlicher Text."},
            "water_changed_l": {
                "type": "number",
                "description": "Gewechselte Wassermenge in Litern.",
            },
        },
        "required": ["tank_id", "title"],
    },
)
def create_event(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    model = data.model("event")
    event = mark_source(
        model(
            tank=tank,
            title=arguments.text("title", required=True, max_length=200),
            category=arguments.choice(
                "category", _values_of(model, "category"), default=model.Category.OTHER
            ),
            occurred_at=arguments.datetime("occurred_at", default=timezone.now()),
            description=arguments.text("description", max_length=5000),
            water_changed_l=arguments.decimal("water_changed_l", minimum=0),
        )
    )
    event.save()
    return Written(serialize.event(event), data.label(event))


@tool(
    "complete_schedule",
    "Quittiert einen fälligen Termin: legt das zugehörige Ereignis an und "
    "rechnet den Termin vom Erledigungstag aus fort. Der nächste Termin steht "
    "in der Antwort.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "schedule_id": {"type": "integer", "description": "Kennung aus list_due_schedules."},
            "done_on": {
                "type": "string",
                "description": "Tag der Erledigung (JJJJ-MM-TT). Ohne Angabe: heute.",
            },
            "note": {"type": "string", "description": "Bemerkung für das Ereignis."},
            "water_changed_l": {
                "type": "number",
                "description": "Bei einem Wasserwechsel: gewechselte Menge in Litern.",
            },
        },
        "required": ["schedule_id"],
    },
)
def complete_schedule(context, arguments):
    schedule = data.schedule(context.user, arguments.integer("schedule_id", required=True))
    done_on = arguments.date("done_on", default=timezone.localdate())
    event_model = data.model("event")

    event = mark_source(
        event_model(
            tank=schedule.tank,
            schedule=schedule,
            title=schedule.title,
            category=schedule.event_category,
            # Der Termin gilt an dem Tag als erledigt, den der Client nennt —
            # die Uhrzeit ist die des Eintrags, denn eine andere kennt niemand.
            occurred_at=timezone.make_aware(
                datetime.combine(done_on, timezone.localtime().time()),
                timezone.get_current_timezone(),
            ),
            description=arguments.text("note", max_length=5000) or schedule.description,
            water_changed_l=arguments.decimal("water_changed_l", minimum=0),
        )
    )
    with transaction.atomic():
        event.save()
        schedule.mark_done(done_on)

    return Written(
        {"event": serialize.event(event), "schedule": serialize.schedule(schedule)},
        data.label(event),
    )


# --------------------------------------------------------------------------
# Besatz und Bepflanzung
# --------------------------------------------------------------------------


@tool(
    "add_tank_animal",
    "Ergänzt den Besatz eines Beckens um eine Art aus dem Tierkatalog. Die "
    "Kennung kommt aus search_catalog. Die Ersterfassung zählt selbst als "
    "Zugang; spätere Veränderungen laufen über record_animal_movement.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken."},
            "catalog_animal_id": {"type": "integer", "description": "Kennung aus search_catalog."},
            "quantity": {"type": "integer", "description": "Anzahl Tiere (Standard 1)."},
            "quantity_male": {"type": "integer", "description": "Davon männlich."},
            "quantity_female": {"type": "integer", "description": "Davon weiblich."},
            "label": {"type": "string", "description": "Eigene Bezeichnung, z. B. „Zuchtgruppe“."},
            "status": {
                "type": "string",
                "description": "planned, present, temporary oder gone. Standard: present.",
            },
            "added_on": {"type": "string", "description": "Tag des Einzugs (JJJJ-MM-TT)."},
            "origin": {"type": "string", "description": "Herkunft, z. B. Händler oder Züchter."},
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["tank_id", "catalog_animal_id"],
    },
)
def add_tank_animal(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    model = data.model("tank_animal")
    animal = model(
        tank=tank,
        animal=data.catalog_entry("animal", arguments.integer("catalog_animal_id", required=True)),
        quantity=arguments.integer("quantity", default=1, minimum=0),
        quantity_male=arguments.integer("quantity_male", minimum=0),
        quantity_female=arguments.integer("quantity_female", minimum=0),
        label=arguments.text("label", max_length=120),
        status=arguments.choice(
            "status", _values_of(model, "status"), default=model.Status.PRESENT
        ),
        added_on=arguments.date("added_on"),
        origin=arguments.text("origin", max_length=160),
        note=arguments.text("note", max_length=2000),
    )
    animal.save()
    return Written(serialize.tank_animal(animal), data.label(animal))


@tool(
    "add_tank_plant",
    "Ergänzt die Bepflanzung eines Beckens um eine Art aus dem Pflanzenkatalog. "
    "Die Kennung kommt aus search_catalog.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken."},
            "catalog_plant_id": {"type": "integer", "description": "Kennung aus search_catalog."},
            "quantity": {"type": "integer", "description": "Anzahl Töpfe oder Stängel."},
            "status": {
                "type": "string",
                "description": "planned, present, temporary oder gone. Standard: present.",
            },
            "placement": {"type": "string", "description": "Wo im Becken, z. B. „Hintergrund links“."},
            "attached_to": {"type": "string", "description": "Aufgebunden auf, z. B. „Wurzel“."},
            "added_on": {"type": "string", "description": "Tag des Einpflanzens (JJJJ-MM-TT)."},
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["tank_id", "catalog_plant_id"],
    },
)
def add_tank_plant(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    model = data.model("tank_plant")
    plant = model(
        tank=tank,
        plant=data.catalog_entry("plant", arguments.integer("catalog_plant_id", required=True)),
        quantity=arguments.integer("quantity", minimum=0),
        status=arguments.choice(
            "status", _values_of(model, "status"), default=model.Status.PRESENT
        ),
        placement=arguments.text("placement", max_length=120),
        attached_to=arguments.text("attached_to", max_length=120),
        added_on=arguments.date("added_on"),
        note=arguments.text("note", max_length=2000),
    )
    plant.save()
    return Written(serialize.tank_plant(plant), data.label(plant))


@tool(
    "record_animal_movement",
    "Bucht einen Zu- oder Abgang beim Besatz: Zukauf, Nachzucht, Umsetzung, "
    "Abgabe, Verlust. Der Bestand wird dabei fortgeschrieben. Die "
    "tank_animal_id steht im Besatz eines Beckens (get_tank).",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_animal_id": {"type": "integer", "description": "Besatzposten aus get_tank."},
            "direction": {"type": "string", "enum": ["in", "out"], "description": "Zugang oder Abgang."},
            "reason": {
                "type": "string",
                "description": "Grund: purchase, breeding, transfer_in, transfer_out, sold, "
                "died, predation, jumped, unknown.",
            },
            "quantity": {"type": "integer", "description": "Anzahl Tiere, mindestens 1."},
            "occurred_on": {"type": "string", "description": "Tag der Buchung (JJJJ-MM-TT). Standard: heute."},
            "target_tank_id": {
                "type": "integer",
                "description": "Bei Umsetzung in ein anderes eigenes Becken: dessen Kennung.",
            },
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["tank_animal_id", "direction", "reason", "quantity"],
    },
)
def record_animal_movement(context, arguments):
    stock = data.tank_animal(context.user, arguments.integer("tank_animal_id", required=True))
    model = data.model("tank_animal_movement")

    target_tank_id = arguments.integer("target_tank_id")
    # Auch das Zielbecken gehört geprüft: eine Umsetzung ist ein Schreibvorgang
    # an zwei Becken, und beide müssen dem Token-Inhaber gehören.
    target_tank = data.tank(context.user, target_tank_id) if target_tank_id else None

    movement = model(
        tank_animal=stock,
        direction=arguments.choice("direction", _values_of(model, "direction"), required=True),
        reason=arguments.choice("reason", _values_of(model, "reason"), required=True),
        quantity=arguments.integer("quantity", required=True, minimum=1),
        occurred_on=arguments.date("occurred_on", default=timezone.localdate()),
        target_tank=target_tank,
        note=arguments.text("note", max_length=2000),
    )
    movement.full_clean()
    # save() schreibt den Bestand fort und lehnt einen Abgang ab, der ihn unter
    # null drücken würde — dieselbe Prüfung wie in der Oberfläche.
    movement.save()

    stock.refresh_from_db()
    return Written(
        {"movement": serialize.movement(movement), "stock": serialize.tank_animal(stock)},
        data.label(movement),
    )
