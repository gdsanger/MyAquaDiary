"""Schreibende Werkzeuge.

Alle vier Leitplanken greifen hier gemeinsam:

* **Eingrenzung.** Jede Kennung im Aufruf wird über
  :mod:`services.mcp.data` aufgelöst und gehört damit dem Token-Inhaber — sonst
  ließen sich Werte in fremde Becken schreiben.
* **Kennzeichnung.** Was hier entsteht, trägt ``source="mcp"``, sobald ein
  Modell eine Herkunft führt (:func:`services.mcp.audit.mark_source`; derzeit
  tut das keines). Wer schreibt, steht bis dahin im Zugriffsprotokoll und —
  wo das Modell es vorsieht — in ``created_by``.
* **Prüfung durch das Modell.** Geschrieben wird über ``full_clean()`` und die
  Methoden der Modelle (``CareTask.complete``, das die Quittierung anlegt und
  den Termin fortschreibt) — keine zweite Geschäftslogik neben der Web-App.
* **Protokoll und Schreibrecht** liegen im Rahmen drumherum
  (:mod:`services.mcp.runner`), nicht in jedem Werkzeug einzeln.

Kein ``delete_*``: Was falsch angelegt wurde, wird in der Oberfläche korrigiert.
Ein irrtümlich ausgelöster Löschbefehl aus einem Chatfenster ist nicht
zurückzuholen — die Korrektur eines falschen Werts dagegen ist zwei Klicks
Arbeit. Ebenso wenig gibt es hier Katalogpflege: ein Steckbrief, den alle
Benutzer sehen, entsteht nicht über die Einzelnutzer-Schnittstelle.
"""

from django.db import transaction
from django.utils import timezone

from . import data, serialize
from .audit import mark_source
from .registry import Written, tool


def _values_of(model, field: str) -> list[str]:
    """Die zulässigen Werte einer Auswahlliste am Modell."""
    return [value for value, _ in model._meta.get_field(field).choices]


# --------------------------------------------------------------------------
# Messwerte
# --------------------------------------------------------------------------


@tool(
    "create_measurement",
    "Trägt Messwerte an einem Becken ein. Das Modell speichert einen Wert je "
    "Messgröße; mehrere gleichzeitig gemessene Werte werden in einem Aufruf "
    "übergeben und teilen sich den Zeitpunkt. Die Messgrößen werden über ihr "
    "Kürzel angegeben (temperatur, ph, no2, no3, nh4, kh, gh, po4, leitwert).",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken, zu dem gemessen wurde."},
            "measured_at": {
                "type": "string",
                "description": "Zeitpunkt der Messung nach ISO 8601. Ohne Angabe: jetzt.",
            },
            "note": {"type": "string", "description": "Bemerkung zu den Werten."},
            "values": {
                "type": "array",
                "description": "Die gemessenen Werte. Mindestens einer.",
                "items": {
                    "type": "object",
                    "properties": {
                        "parameter": {"type": "string", "description": "Kürzel der Messgröße."},
                        "value": {"type": "number", "description": "Gemessener Wert."},
                    },
                    "required": ["parameter", "value"],
                },
            },
        },
        "required": ["tank_id", "values"],
    },
)
def create_measurement(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    rows = arguments.objects("values", required=True)
    measured_at = arguments.datetime("measured_at", default=timezone.now())
    note = arguments.text("note", max_length=200)

    model = data.model("measurement")
    created = []
    # Alle Werte eines Aufrufs oder keiner: eine halb eingetragene Messung ist
    # schlimmer als eine abgewiesene, weil sie unauffällig falsch aussieht.
    with transaction.atomic():
        for row in rows:
            measurement = mark_source(
                model(
                    tank=tank,
                    parameter=data.parameter(row.text("parameter", required=True)),
                    value=row.decimal("value", required=True),
                    measured_at=measured_at,
                    note=note,
                    created_by=context.user,
                )
            )
            # full_clean() setzt die Regeln des Modells durch; die Meldung
            # daraus geht unverändert an den Client.
            measurement.full_clean()
            measurement.save()
            created.append(measurement)

    return Written(
        {"measurements": [serialize.measurement(item) for item in created]},
        ", ".join(data.label(item) for item in created),
    )


# --------------------------------------------------------------------------
# Ereignisse
# --------------------------------------------------------------------------


@tool(
    "create_event",
    "Legt ein Ereignis am Becken an: Wasserwechsel, Pflege, Behandlung, "
    "Besatzänderung, Technik, Vorfall, Beobachtung. Bilder nimmt diese "
    "Schnittstelle nicht entgegen; sie entstehen in der Oberfläche.",
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
            title=arguments.text("title", required=True, max_length=160),
            category=arguments.choice(
                "category", _values_of(model, "category"), default=model.Category.OTHER
            ),
            occurred_at=arguments.datetime("occurred_at", default=timezone.now()),
            description=arguments.text("description", max_length=5000),
            created_by=context.user,
        )
    )
    event.full_clean()
    event.save()
    return Written(serialize.event(event), data.label(event))


@tool(
    "complete_task",
    "Quittiert einen fälligen Pflegetermin: hält die Erledigung fest und "
    "rechnet einen wiederkehrenden Termin vom Erledigungstag aus fort. Ein "
    "einmaliger Termin wird dabei stillgelegt. Der neue Stand steht in der "
    "Antwort.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "Kennung aus list_due_tasks."},
            "done_on": {
                "type": "string",
                "description": "Tag der Erledigung (JJJJ-MM-TT). Ohne Angabe: heute.",
            },
            "note": {"type": "string", "description": "Bemerkung zur Quittierung."},
        },
        "required": ["task_id"],
    },
)
def complete_task(context, arguments):
    task = data.task(context.user, arguments.integer("task_id", required=True))
    done_on = arguments.date("done_on", default=timezone.localdate())
    note = arguments.text("note", max_length=200)

    # ``complete`` legt die Quittierung an und schreibt den Termin fort — genau
    # wie der Knopf in der Oberfläche. Ein zweiter Rechenweg dafür wäre eine
    # zweite Wahrheit über den nächsten Fälligkeitstag.
    with transaction.atomic():
        completion = task.complete(on=done_on, user=context.user, note=note)

    return Written(
        {"completion": serialize.task_completion(completion), "task": serialize.task(task)},
        data.label(completion),
    )


# --------------------------------------------------------------------------
# Besatz und Bepflanzung
# --------------------------------------------------------------------------


@tool(
    "add_stocking",
    "Ergänzt den Besatz eines Beckens um eine Tierart aus dem Katalog. Die "
    "species_id kommt aus search_catalog. Spätere Änderungen an Stückzahl oder "
    "Verbleib laufen über update_stocking.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken."},
            "species_id": {"type": "integer", "description": "Kennung aus search_catalog (kind: animal)."},
            "quantity": {"type": "integer", "description": "Anzahl Tiere (Standard 1)."},
            "added_on": {
                "type": "string",
                "description": "Tag des Einsetzens (JJJJ-MM-TT). Ohne Angabe: heute.",
            },
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["tank_id", "species_id"],
    },
)
def add_stocking(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    stocking = data.model("stocking")(
        tank=tank,
        species=data.catalog_entry("animal", arguments.integer("species_id", required=True)),
        quantity=arguments.integer("quantity", default=1, minimum=0),
        added_on=arguments.date("added_on", default=timezone.localdate()),
        note=arguments.text("note", max_length=200),
    )
    stocking.full_clean()
    stocking.save()
    return Written(serialize.stocking(stocking), data.label(stocking))


@tool(
    "add_planting",
    "Ergänzt die Bepflanzung eines Beckens um eine Pflanzenart aus dem Katalog. "
    "Die species_id kommt aus search_catalog.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken."},
            "species_id": {"type": "integer", "description": "Kennung aus search_catalog (kind: plant)."},
            "quantity": {"type": "integer", "description": "Anzahl Töpfe oder Stängel (Standard 1)."},
            "planted_on": {
                "type": "string",
                "description": "Tag des Einpflanzens (JJJJ-MM-TT). Ohne Angabe: heute.",
            },
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["tank_id", "species_id"],
    },
)
def add_planting(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    planting = data.model("planting")(
        tank=tank,
        species=data.catalog_entry("plant", arguments.integer("species_id", required=True)),
        quantity=arguments.integer("quantity", default=1, minimum=0),
        planted_on=arguments.date("planted_on", default=timezone.localdate()),
        note=arguments.text("note", max_length=200),
    )
    planting.full_clean()
    planting.save()
    return Written(serialize.planting(planting), data.label(planting))


@tool(
    "update_stocking",
    "Schreibt einen Besatzposten fort: neue Stückzahl nach Nachwuchs, Zukauf "
    "oder Verlust, oder removed_on, wenn die Art das Becken verlassen hat. Die "
    "stocking_id steht im Besatz eines Beckens (get_tank).",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "stocking_id": {"type": "integer", "description": "Besatzposten aus get_tank."},
            "quantity": {"type": "integer", "description": "Neue Stückzahl (nicht die Differenz)."},
            "removed_on": {
                "type": "string",
                "description": "Tag der Entnahme (JJJJ-MM-TT). Setzt den Posten auf beendet.",
            },
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["stocking_id"],
    },
)
def update_stocking(context, arguments):
    """Ändert einen Besatzposten.

    Übergeben wird die neue Stückzahl, nicht die Veränderung: das Modell führt
    keine Bestandshistorie, aus der sich eine Differenz verrechnen ließe. Wer
    „drei dazu“ meint, muss die Summe nennen — das ist eindeutig, eine
    Differenz auf einen nur vermuteten Ausgangsbestand wäre es nicht.
    """
    stocking = data.stocking(context.user, arguments.integer("stocking_id", required=True))

    quantity = arguments.integer("quantity", minimum=0)
    if quantity is not None:
        stocking.quantity = quantity
    removed_on = arguments.date("removed_on")
    if removed_on is not None:
        stocking.removed_on = removed_on
    note = arguments.text("note", max_length=200)
    if note:
        stocking.note = note

    stocking.full_clean()
    stocking.save()
    return Written(serialize.stocking(stocking), data.label(stocking))
