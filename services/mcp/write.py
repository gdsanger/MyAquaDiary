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
    "Kürzel angegeben (temperatur, ph, no2, no3, nh4, kh, gh, po4, leitwert). "
    "Ein Wert unterhalb der Nachweisgrenze des Tests wird nicht als 0 "
    "eingetragen, sondern mit below_detection: true (dann ohne value) — das "
    "geht nur bei Messgrößen mit Nachweisgrenze (no2, no3, nh4, po4).",
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
                        "below_detection": {
                            "type": "boolean",
                            "description": "true für „nicht nachweisbar“ (n.n.); dann ohne value.",
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
    measured_at = arguments.datetime("measured_at", default=timezone.now())
    note = arguments.text("note", max_length=200)

    model = data.model("measurement")
    created = []
    # Alle Werte eines Aufrufs oder keiner: eine halb eingetragene Messung ist
    # schlimmer als eine abgewiesene, weil sie unauffällig falsch aussieht.
    with transaction.atomic():
        for row in rows:
            below_detection = row.boolean("below_detection")
            measurement = mark_source(
                model(
                    tank=tank,
                    parameter=data.parameter(row.text("parameter", required=True)),
                    # Bei n.n. keinen Wert: das Modell verlangt genau eines von
                    # beidem und weist „beides“ ab.
                    value=None if below_detection else row.decimal("value", required=True),
                    below_detection=below_detection,
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


# --------------------------------------------------------------------------
# Einrichtung: Bodengrund und Hardscape
# --------------------------------------------------------------------------
#
# Anders als beim Besatz gibt es hier keinen Katalog dazwischen: Bodengrund und
# Hardscape sind Freitext (Produkt, Bezeichnung) plus ein paar strukturierte
# Felder. Zwei Grenzen aus #1246 stehen hier fest:
#
# * **Kein Termin.** Aus ``depleted_on`` und ``expected_depletion`` bietet die
#   Oberfläche einen Termin an; über MCP gibt es niemanden, der zustimmt.
#   Deshalb legen diese Werkzeuge keinen Termin an, auch nicht auf Wunsch.
# * **Kein Löschen und, beim Bodengrund, kein ``removed_on``.** Entferntes
#   Hardscape bekommt ``removed_on`` über ``update_hardscape_item``. Eine
#   Bodengrundschicht verschwindet nicht, sie wird ersetzt — das ist ein Umbau,
#   der in der Oberfläche stattfindet.


@tool(
    "add_substrate_layer",
    "Legt eine Bodengrundschicht an einem Becken an. Die Schichten sind von "
    "unten nach oben geordnet (position 0 ist die unterste). Ohne position "
    "kommt die Schicht ganz nach oben — das ist der Normalfall, weil Bodengrund "
    "von unten nach oben entsteht. Mit position rücken die darüberliegenden "
    "Schichten nach. Ein Termin für das Ende der Standzeit wird über diesen Weg "
    "nicht angelegt; wer einen will, legt ihn in der Oberfläche an.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken."},
            "kind": {
                "type": "string",
                "description": "Art der Schicht: nutrient, soil, gravel, sand, lava, "
                "filter_mat, other.",
            },
            "position": {
                "type": "integer",
                "description": "Einbauhöhe, von unten gezählt (0 ist unten). Ohne "
                "Angabe: ganz nach oben.",
            },
            "product": {"type": "string", "description": "Produkt, z. B. „Dennerle Sansibar“."},
            "grain_size": {"type": "string", "description": "Körnung, z. B. „0,5–1 mm“."},
            "depth_cm": {"type": "number", "description": "Mächtigkeit in cm."},
            "added_on": {
                "type": "string",
                "description": "Tag des Einbringens (JJJJ-MM-TT). Ohne Angabe: heute.",
            },
            "depleted_on": {
                "type": "string",
                "description": "Rechnerisches Ende der Standzeit eines Depots (JJJJ-MM-TT).",
            },
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["tank_id", "kind"],
    },
)
def add_substrate_layer(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    model = data.model("substrate_layer")

    # Die vorhandenen Schichten in ihrer Reihenfolge (von unten): daran hängt,
    # wohin die neue kommt und wie die anderen nachrücken.
    layers = list(tank.substrate_layers.all())
    position = arguments.integer("position", minimum=0)
    if position is None or position > len(layers):
        # Ohne Angabe — und bei einer Angabe jenseits des Stapels — ganz oben.
        position = len(layers)

    layer = mark_source(
        model(
            tank=tank,
            position=position,
            kind=arguments.choice("kind", _values_of(model, "kind"), required=True),
            product=arguments.text("product", max_length=160),
            grain_size=arguments.text("grain_size", max_length=60),
            depth_cm=arguments.decimal("depth_cm", minimum=0),
            added_on=arguments.date("added_on", default=timezone.localdate()),
            depleted_on=arguments.date("depleted_on"),
            note=arguments.text("note", max_length=5000),
        )
    )
    layer.full_clean()

    # Einfügen und den ganzen Stapel lückenlos durchnummerieren — wie
    # ``SubstrateLayer.move`` in der Oberfläche: eine Reihenfolge mit Lücken
    # lässt sich nicht zuverlässig erweitern.
    with transaction.atomic():
        layer.save()
        layers.insert(position, layer)
        for index, existing in enumerate(layers):
            existing.position = index
        model.objects.bulk_update(layers, ["position"])

    return Written(serialize.substrate_layer(layer), data.label(layer))


@tool(
    "update_substrate_layer",
    "Schreibt eine Bodengrundschicht fort: Mächtigkeit, Produkt, Körnung, Art "
    "oder das rechnerische Ende der Standzeit (depleted_on). Die layer_id steht "
    "im Bodengrund eines Beckens (get_tank). Eine Schicht lässt sich hierüber "
    "nicht entfernen — sie verschwindet nicht, sie wird ersetzt, und das ist ein "
    "Umbau in der Oberfläche. Auch die Reihenfolge ändert sich dort.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "layer_id": {"type": "integer", "description": "Bodengrundschicht aus get_tank."},
            "kind": {
                "type": "string",
                "description": "Art der Schicht: nutrient, soil, gravel, sand, lava, "
                "filter_mat, other.",
            },
            "product": {"type": "string", "description": "Produkt."},
            "grain_size": {"type": "string", "description": "Körnung."},
            "depth_cm": {"type": "number", "description": "Mächtigkeit in cm."},
            "added_on": {
                "type": "string",
                "description": "Tag des Einbringens (JJJJ-MM-TT).",
            },
            "depleted_on": {
                "type": "string",
                "description": "Rechnerisches Ende der Standzeit (JJJJ-MM-TT).",
            },
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["layer_id"],
    },
)
def update_substrate_layer(context, arguments):
    layer = data.substrate_layer(context.user, arguments.integer("layer_id", required=True))

    kind = arguments.choice("kind", _values_of(type(layer), "kind"))
    if kind is not None:
        layer.kind = kind
    product = arguments.text("product", max_length=160)
    if product:
        layer.product = product
    grain_size = arguments.text("grain_size", max_length=60)
    if grain_size:
        layer.grain_size = grain_size
    depth_cm = arguments.decimal("depth_cm", minimum=0)
    if depth_cm is not None:
        layer.depth_cm = depth_cm
    added_on = arguments.date("added_on")
    if added_on is not None:
        layer.added_on = added_on
    depleted_on = arguments.date("depleted_on")
    if depleted_on is not None:
        layer.depleted_on = depleted_on
    note = arguments.text("note", max_length=5000)
    if note:
        layer.note = note

    layer.full_clean()
    layer.save()
    return Written(serialize.substrate_layer(layer), data.label(layer))


@tool(
    "add_hardscape_item",
    "Legt eine Hardscape-Position an einem Becken an: Wurzel, Stein, "
    "Erlenzapfen und Laub, Rückwand, Höhle. Erfasst wird das wegen der "
    "Wasserwerte — Wurzeln und Laub geben Huminstoffe ab, Kalkgestein hebt KH. "
    "Über affects_water und water_effect lässt sich das festhalten. Ein Termin "
    "für das Ende (etwa Erlenzapfen nach sechs Wochen) wird über diesen Weg "
    "nicht angelegt; wer einen will, legt ihn in der Oberfläche an.",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Becken."},
            "kind": {
                "type": "string",
                "description": "Art: wood, stone, botanicals, background, cave, other.",
            },
            "name": {"type": "string", "description": "Bezeichnung, z. B. „Moorkienwurzel“."},
            "quantity": {"type": "integer", "description": "Anzahl (Standard 1)."},
            "added_on": {
                "type": "string",
                "description": "Tag des Einbringens (JJJJ-MM-TT). Ohne Angabe: heute.",
            },
            "affects_water": {
                "type": "boolean",
                "description": "Wirkt die Position auf die Wasserwerte? Standard false.",
            },
            "water_effect": {
                "type": "string",
                "description": "Wie sie wirkt, z. B. „Huminstoffe, senkt pH“.",
            },
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["tank_id", "kind", "name"],
    },
)
def add_hardscape_item(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    model = data.model("hardscape_item")
    item = mark_source(
        model(
            tank=tank,
            kind=arguments.choice("kind", _values_of(model, "kind"), required=True),
            name=arguments.text("name", required=True, max_length=160),
            quantity=arguments.integer("quantity", default=1, minimum=1),
            added_on=arguments.date("added_on", default=timezone.localdate()),
            affects_water=arguments.boolean("affects_water"),
            water_effect=arguments.text("water_effect", max_length=200),
            note=arguments.text("note", max_length=5000),
        )
    )
    item.full_clean()
    item.save()
    return Written(serialize.hardscape_item(item), data.label(item))


@tool(
    "update_hardscape_item",
    "Schreibt eine Hardscape-Position fort: Anzahl, Bezeichnung, Wirkung auf die "
    "Wasserwerte — und vor allem removed_on, wenn das Stück das Becken verlassen "
    "hat. Gelöscht wird nichts; eine entfernte Position bleibt mit ihrem "
    "Entnahmetag stehen. Die hardscape_id steht in der Einrichtung eines Beckens "
    "(get_tank).",
    writes=True,
    schema={
        "type": "object",
        "properties": {
            "hardscape_id": {"type": "integer", "description": "Hardscape-Position aus get_tank."},
            "kind": {
                "type": "string",
                "description": "Art: wood, stone, botanicals, background, cave, other.",
            },
            "name": {"type": "string", "description": "Bezeichnung."},
            "quantity": {"type": "integer", "description": "Anzahl."},
            "added_on": {
                "type": "string",
                "description": "Tag des Einbringens (JJJJ-MM-TT).",
            },
            "removed_on": {
                "type": "string",
                "description": "Tag der Entnahme (JJJJ-MM-TT). Setzt die Position auf entfernt.",
            },
            "affects_water": {
                "type": "boolean",
                "description": "Wirkt die Position auf die Wasserwerte?",
            },
            "water_effect": {"type": "string", "description": "Wie sie wirkt."},
            "note": {"type": "string", "description": "Bemerkung."},
        },
        "required": ["hardscape_id"],
    },
)
def update_hardscape_item(context, arguments):
    item = data.hardscape_item(context.user, arguments.integer("hardscape_id", required=True))

    kind = arguments.choice("kind", _values_of(type(item), "kind"))
    if kind is not None:
        item.kind = kind
    name = arguments.text("name", max_length=160)
    if name:
        item.name = name
    quantity = arguments.integer("quantity", minimum=1)
    if quantity is not None:
        item.quantity = quantity
    added_on = arguments.date("added_on")
    if added_on is not None:
        item.added_on = added_on
    removed_on = arguments.date("removed_on")
    if removed_on is not None:
        item.removed_on = removed_on
    # ``affects_water`` ist ein Schalter: hier lässt sich nicht unterscheiden,
    # ob „false“ gemeint ist oder das Feld weggelassen wurde. Nur wenn es im
    # Aufruf steht, wird es übernommen — sonst bliebe es nie abzuschalten.
    if "affects_water" in arguments.raw:
        item.affects_water = arguments.boolean("affects_water")
    water_effect = arguments.text("water_effect", max_length=200)
    if water_effect:
        item.water_effect = water_effect
    note = arguments.text("note", max_length=5000)
    if note:
        item.note = note

    item.full_clean()
    item.save()
    return Written(serialize.hardscape_item(item), data.label(item))
