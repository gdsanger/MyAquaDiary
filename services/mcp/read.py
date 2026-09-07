"""Lesende Werkzeuge.

Jedes beginnt bei :func:`services.mcp.data.tanks` und sieht damit nur die
Becken des Token-Inhabers. Eine ``tank_id``, die jemand anderem gehört, ist
hier nicht von einer erfundenen zu unterscheiden.

Geräte fehlen in dieser Liste mit Absicht: Filter und Steckdosen regeln sich
selbst, ihre Werte gehören in die Oberfläche und in die serverseitige
Auswertung — ein externes Modell braucht darauf keinen Zugriff, und ein
Schaltbefehl aus einem Chatfenster erst recht nicht.
"""

from datetime import timedelta

from django.utils import timezone

from . import data, serialize
from .registry import tool

#: Zeitraumfilter, die mehrere Werkzeuge gleich verwenden.
_PERIOD_SCHEMA = {
    "from": {"type": "string", "description": "Frühester Zeitpunkt (JJJJ-MM-TT)."},
    "to": {"type": "string", "description": "Spätester Zeitpunkt (JJJJ-MM-TT)."},
    "limit": {
        "type": "integer",
        "description": "Höchstzahl der Treffer, neueste zuerst (Standard 50).",
    },
}


def _tank_filter(context, arguments, queryset):
    """Wendet einen optionalen ``tank_id``-Filter an — auf eigene Becken."""
    tank_id = arguments.integer("tank_id")
    if tank_id is None:
        return queryset
    return queryset.filter(tank=data.tank(context.user, tank_id))


def _period_filter(arguments, queryset, field):
    """Grenzt auf einen Zeitraum ein.

    Verglichen wird über ``__date`` und nicht gegen einen Zeitpunkt: „bis
    2026-09-01“ meint den ganzen 1. September, nicht dessen Mitternacht.
    """
    start = arguments.date("from")
    end = arguments.date("to")
    if start:
        queryset = queryset.filter(**{f"{field}__date__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__date__lte": end})
    return queryset


# --------------------------------------------------------------------------
# Becken
# --------------------------------------------------------------------------


@tool(
    "list_tanks",
    "Alle Becken des Benutzers mit ihren Stammdaten: Name, Wasserart, Maße, "
    "Volumen, Inbetriebnahme. Der Einstiegspunkt — die tank_id von hier wird "
    "in allen anderen Werkzeugen gebraucht.",
    schema={
        "type": "object",
        "properties": {
            "include_dissolved": {
                "type": "boolean",
                "description": "Aufgelöste Becken mit ausgeben (Standard: nein).",
            }
        },
    },
)
def list_tanks(context, arguments):
    queryset = data.tanks(context.user)
    if not arguments.boolean("include_dissolved"):
        queryset = queryset.active()
    return {"tanks": [serialize.tank(item) for item in queryset]}


@tool(
    "get_tank",
    "Ein Becken im Detail: Stammdaten, Notizen, Zielbereiche der Wasserwerte, "
    "Besatz, Bepflanzung und die Einrichtung — Bodengrund als Schichtung von "
    "unten nach oben samt Standzeit eines Nährstoffdepots, dazu Hardscape "
    "(Wurzeln, Steine, Erlenzapfen, Rückwand) mit dem Hinweis, ob es auf die "
    "Wasserwerte wirkt.",
    schema={
        "type": "object",
        "properties": {"tank_id": {"type": "integer", "description": "Kennung des Beckens."}},
        "required": ["tank_id"],
    },
)
def get_tank(context, arguments):
    # Ohne Vorabladung fragt die Serialisierung je Zielbereich, Besatzposten,
    # Pflanze und Einrichtungsposition einzeln nach — bei einem gut gefüllten
    # Becken sind das dutzende Abfragen für eine Antwort.
    tank = data.tank(
        context.user,
        arguments.integer("tank_id", required=True),
        prefetch=(
            "parameter_targets__parameter",
            "stockings__species",
            "plantings__species",
            "substrate_layers",
            "hardscape",
        ),
    )
    return serialize.tank_detail(tank)


# --------------------------------------------------------------------------
# Messwerte
# --------------------------------------------------------------------------


@tool(
    "list_measurements",
    "Messwerte eines oder aller Becken, neueste zuerst. Je Eintrag eine "
    "Messgröße mit einem Wert; gleichzeitig erfasste Werte teilen sich den "
    "Zeitpunkt. Mit parameter lässt sich auf eine Messgröße einschränken "
    "(z. B. ph, kh, no3). Jeder Wert kommt mit dem Abgleich gegen den "
    "Zielbereich des Beckens (status: ok, warn, critical, unknown).",
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Nur dieses Becken."},
            "parameter": {
                "type": "string",
                "description": "Kürzel einer Messgröße: temperatur, ph, no2, no3, "
                "nh4, kh, gh, po4, leitwert.",
            },
            **_PERIOD_SCHEMA,
        },
    },
)
def list_measurements(context, arguments):
    queryset = _tank_filter(context, arguments, data.measurements(context.user))
    queryset = _period_filter(arguments, queryset, "measured_at")
    parameter = arguments.text("parameter")
    if parameter:
        queryset = queryset.filter(parameter__key=parameter)
    rows = list(queryset[: arguments.limit()])
    targets = data.parameter_targets(context.user, [row.tank_id for row in rows])
    return {
        "measurements": [
            serialize.measurement(row, targets=targets.get(row.tank_id, {})) for row in rows
        ]
    }


@tool(
    "get_measurement",
    "Ein einzelner Messwert mit dem Abgleich gegen den Zielbereich des Beckens "
    "(status: ok, warn, critical, unknown).",
    schema={
        "type": "object",
        "properties": {
            "measurement_id": {"type": "integer", "description": "Kennung des Messwerts."}
        },
        "required": ["measurement_id"],
    },
)
def get_measurement(context, arguments):
    measurement = data.measurement(
        context.user, arguments.integer("measurement_id", required=True)
    )
    return serialize.measurement(measurement)


# --------------------------------------------------------------------------
# Ereignisse und Termine
# --------------------------------------------------------------------------


@tool(
    "list_events",
    "Ereignisse am Becken, neueste zuerst: Wasserwechsel, Pflege, Technik, "
    "Beobachtungen, Probleme, Krankheiten, Nachwuchs.",
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Nur dieses Becken."},
            "category": {
                "type": "string",
                "description": "Kategorie, z. B. water_change, maintenance, disease.",
            },
            **_PERIOD_SCHEMA,
        },
    },
)
def list_events(context, arguments):
    queryset = _tank_filter(context, arguments, data.events(context.user))
    queryset = _period_filter(arguments, queryset, "occurred_at")
    category = arguments.choice("category", _event_categories())
    if category:
        queryset = queryset.filter(category=category)
    return {"events": [serialize.event(item) for item in queryset[: arguments.limit()]]}


def _event_categories() -> list[str]:
    return [value for value, _ in data.model("event").Category.choices]


@tool(
    "list_due_tasks",
    "Pflegetermine, die fällig oder in den nächsten Tagen fällig sind — "
    "Wasserwechsel, Filterreinigung, Düngung, Wassertest. Negatives "
    "days_until_due heißt überfällig. Die task_id wird für complete_task "
    "gebraucht.",
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Nur dieses Becken."},
            "days_ahead": {
                "type": "integer",
                "description": "Wie weit vorausgeschaut wird (Standard 7 Tage).",
            },
            "include_inactive": {
                "type": "boolean",
                "description": "Auch stillgelegte Termine ausgeben (Standard: nein).",
            },
        },
    },
)
def list_due_tasks(context, arguments):
    days_ahead = arguments.integer("days_ahead", default=7, minimum=0, maximum=365)
    today = timezone.localdate()
    queryset = _tank_filter(context, arguments, data.tasks(context.user))
    if not arguments.boolean("include_inactive"):
        queryset = queryset.filter(is_active=True)
    queryset = queryset.filter(due_on__lte=today + timedelta(days=days_ahead))
    return {
        "as_of": today.isoformat(),
        "tasks": [serialize.task(item, today=today) for item in queryset],
    }


# --------------------------------------------------------------------------
# Katalog
# --------------------------------------------------------------------------


@tool(
    "search_catalog",
    "Sucht im Pflanzen- und Tierkatalog nach wissenschaftlichem oder deutschem "
    "Namen. Der Katalog ist für alle Benutzer derselbe und über MCP nur lesbar.",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Namensteil, z. B. „Neocaridina“ oder „Garnele“."},
            "kind": {
                "type": "string",
                "enum": ["animal", "plant"],
                "description": "Nur Tiere oder nur Pflanzen. Ohne Angabe beides.",
            },
            "limit": {"type": "integer", "description": "Höchstzahl der Treffer je Art (Standard 20)."},
        },
        "required": ["query"],
    },
)
def search_catalog(context, arguments):
    query = arguments.text("query", required=True, max_length=160)
    kind = arguments.choice("kind", data.CATALOG_MODELS)
    kinds = [kind] if kind else list(data.CATALOG_MODELS)
    limit = arguments.limit(default=20)

    results = []
    for current in kinds:
        # ``search`` ist dieselbe Suche wie im Katalog der Web-App — sie deckt
        # neben beiden Namen auch die Kurzbeschreibung ab. Eine zweite,
        # abweichende Suchlogik neben der Oberfläche soll es nicht geben.
        matches = data.catalog_model(current).objects.search(query)
        results.extend(
            serialize.catalog_entry(current, entry) for entry in matches[:limit]
        )
    return {"query": query, "entries": results}


@tool(
    "get_catalog_entry",
    "Der vollständige Steckbrief eines Katalogeintrags: Herkunft, Größe, "
    "Ansprüche an Wasserwerte und Becken, Vergesellschaftung, Pflege.",
    schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["animal", "plant"], "description": "Tier oder Pflanze."},
            "entry_id": {"type": "integer", "description": "Kennung aus search_catalog."},
        },
        "required": ["kind", "entry_id"],
    },
)
def get_catalog_entry(context, arguments):
    kind = arguments.choice("kind", data.CATALOG_MODELS, required=True)
    entry = data.catalog_entry(kind, arguments.integer("entry_id", required=True))
    return serialize.catalog_detail(kind, entry)
