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
        queryset = queryset.filter(shut_down_on__isnull=True)
    return {"tanks": [serialize.tank(item) for item in queryset]}


@tool(
    "get_tank",
    "Ein Becken im Detail: Stammdaten, Technik (Bodengrund, Filterung, "
    "Beleuchtung, CO2, Düngung), Zielbereiche der Wasserwerte, Besatz und "
    "Bepflanzung.",
    schema={
        "type": "object",
        "properties": {"tank_id": {"type": "integer", "description": "Kennung des Beckens."}},
        "required": ["tank_id"],
    },
)
def get_tank(context, arguments):
    tank = data.tank(context.user, arguments.integer("tank_id", required=True))
    return serialize.tank_detail(tank)


# --------------------------------------------------------------------------
# Messreihen
# --------------------------------------------------------------------------


@tool(
    "list_measurements",
    "Messreihen eines oder aller Becken, neueste zuerst. Eine Messreihe ist "
    "ein Messzeitpunkt mit allen dabei erfassten Werten, nicht ein Einzelwert. "
    "Mit parameter lässt sich auf Messreihen einschränken, die eine bestimmte "
    "Messgröße enthalten (z. B. ph, kh, no3).",
    schema={
        "type": "object",
        "properties": {
            "tank_id": {"type": "integer", "description": "Nur dieses Becken."},
            "parameter": {
                "type": "string",
                "description": "Kürzel einer Messgröße, z. B. ph, kh, gh, no3, temp.",
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
        queryset = queryset.filter(values__parameter__key=parameter).distinct()
    queryset = queryset.prefetch_related("values__parameter")[: arguments.limit()]
    return {"measurements": [serialize.measurement(item) for item in queryset]}


@tool(
    "get_measurement",
    "Eine einzelne Messreihe mit allen Werten, dem daraus berechneten CO2-Gehalt "
    "und dem Abgleich mit den Zielbereichen des Beckens (status: low, ok, high).",
    schema={
        "type": "object",
        "properties": {
            "measurement_id": {"type": "integer", "description": "Kennung der Messreihe."}
        },
        "required": ["measurement_id"],
    },
)
def get_measurement(context, arguments):
    measurement = data.measurement(
        context.user, arguments.integer("measurement_id", required=True)
    )
    return serialize.measurement(measurement, with_targets=True)


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
    "list_due_schedules",
    "Wiederkehrende Termine, die fällig oder in den nächsten Tagen fällig sind "
    "— Wasserwechsel, Filterreinigung, Düngung. Negatives days_until_due heißt "
    "überfällig.",
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
def list_due_schedules(context, arguments):
    days_ahead = arguments.integer("days_ahead", default=7, minimum=0, maximum=365)
    today = timezone.localdate()
    queryset = _tank_filter(context, arguments, data.schedules(context.user))
    if not arguments.boolean("include_inactive"):
        queryset = queryset.filter(is_active=True)
    queryset = queryset.filter(next_due_on__lte=today + timedelta(days=days_ahead))
    return {
        "as_of": today.isoformat(),
        "schedules": [serialize.schedule(item, today=today) for item in queryset],
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
        model = data.catalog_model(current)
        matches = model.objects.filter(scientific_name__icontains=query) | model.objects.filter(
            common_name__icontains=query
        )
        results.extend(
            serialize.catalog_entry(current, entry) for entry in matches.distinct()[:limit]
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
