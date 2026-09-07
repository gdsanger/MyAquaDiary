"""Tests der MCP-Werkzeuge.

Die wichtigste Zusage dieser Schnittstelle ist nicht, dass sie Daten liefert,
sondern **welche**: ausschließlich die des Token-Inhabers. Deshalb steht hier
zu jedem Werkzeug mit einer Kennung im Aufruf auch der Versuch mit einer
fremden — er muss scheitern, und zwar ohne zu verraten, dass es den fremden
Datensatz gibt.

Gearbeitet wird gegen die **echten** Modelle aus ``tanks`` und ``catalog``.
Früher standen hier Ersatzmodelle mit den Feldnamen aus dem Entwurf der
Agira-Items; sie haben dazu geführt, dass die Werkzeuge grün getestet waren und
im Betrieb an ``shut_down_on`` und ``biotope`` scheiterten (#1236). Ein Test,
der sein eigenes Datenmodell mitbringt, prüft die Schnittstelle gegen den
Entwurf und nicht gegen die Anwendung.

:class:`EveryToolTests` ist die Lehre daraus: dort wird jedes registrierte
Werkzeug einmal mit gültigen Argumenten aufgerufen. Feldzugriffe löst Django
erst zur Laufzeit auf — ohne einen solchen Aufruf fällt eine Modelländerung
erst dem Benutzer auf.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from catalog.models import AnimalSpecies, PlantSpecies
from services.mcp import Context, call_tool, registry
from services.mcp.exceptions import ToolError, WriteNotAllowed
from services.models import MCPAccessLog, MCPToken
from tanks.models import (
    CareTask,
    Event,
    HardscapeItem,
    Measurement,
    Parameter,
    Planting,
    Stocking,
    SubstrateLayer,
    Tank,
    TankParameterTarget,
)

from .test_mcp_tokens import make_user


def make_tank(owner, name="Südamerika-Becken", **extra):
    """Ein Becken mit allem, was das Modell zwingend verlangt."""
    return Tank.objects.create(
        owner=owner,
        name=name,
        slug=name.lower().replace(" ", "-").replace("ü", "ue"),
        volume_liters=Decimal("160.0"),
        setup_date=timezone.localdate() - timedelta(days=400),
        **extra,
    )


class ToolTestCase(TestCase):
    """Zwei Benutzer, zwei Becken — und ein Token auf genau eines davon."""

    allow_write = True

    def setUp(self):
        self.user = make_user("greta")
        self.stranger = make_user("hans")
        self.token, self.key = MCPToken.issue(
            self.user, "Claude Desktop", allow_write=self.allow_write
        )
        self.context = Context(token=self.token, user=self.user)

        self.tank = make_tank(self.user, location="Wohnzimmer")
        self.foreign_tank = make_tank(self.stranger, "Fremdes Becken")

        # Die Messgrößen bringt ``tanks.0002_default_parameters`` mit; sie hier
        # noch einmal anzulegen, würde am Unique-Schlüssel scheitern — und den
        # Test gegen erfundene statt gegen die ausgelieferten Größen führen.
        self.ph = Parameter.objects.get(key="ph")
        self.kh = Parameter.objects.get(key="kh")
        self.no3 = Parameter.objects.get(key="no3")

    # -- Daten ----------------------------------------------------------------

    def make_measurement(self, tank=None, *, parameter=None, value="7.2", when=None):
        return Measurement.objects.create(
            tank=tank or self.tank,
            parameter=parameter or self.ph,
            value=Decimal(value),
            measured_at=when or timezone.now(),
        )

    def guppy(self):
        return AnimalSpecies.objects.create(
            scientific_name="Poecilia reticulata",
            common_name="Guppy",
            slug="poecilia-reticulata",
            summary="Lebendgebärender Zahnkarpfen",
            min_group_size=6,
            adult_size_cm=Decimal("4.0"),
        )

    def moss(self):
        return PlantSpecies.objects.create(
            scientific_name="Vesicularia dubyana",
            common_name="Javamoos",
            slug="vesicularia-dubyana",
            placement=PlantSpecies.Placement.EPIPHYTE,
        )

    def make_task(self, tank=None, **extra):
        return CareTask.objects.create(
            tank=tank or self.tank,
            title=extra.pop("title", "Wasserwechsel"),
            due_on=extra.pop("due_on", timezone.localdate()),
            **extra,
        )

    def call(self, name, **arguments):
        return call_tool(self.context, name, arguments)


class ListTanksTests(ToolTestCase):
    def test_it_lists_only_the_own_tanks(self):
        result = self.call("list_tanks")

        self.assertEqual([tank["name"] for tank in result["tanks"]], ["Südamerika-Becken"])

    def test_it_carries_the_real_master_data(self):
        """Die Felder heißen wie im Modell — daran ist #1236 gescheitert."""
        entry = self.call("list_tanks")["tanks"][0]

        self.assertEqual(entry["tank_id"], self.tank.pk)
        self.assertEqual(entry["volume_liters"], 160.0)
        self.assertEqual(entry["location"], "Wohnzimmer")
        self.assertEqual(entry["water_type"], "fresh")
        self.assertEqual(entry["water_type_label"], "Süßwasser")
        self.assertEqual(entry["setup_date"], self.tank.setup_date.isoformat())
        self.assertIsNone(entry["dissolved_on"])
        self.assertFalse(entry["is_dissolved"])

    def test_dissolved_tanks_are_hidden_unless_asked_for(self):
        dissolved = make_tank(self.user, "Altes Becken")
        dissolved.dissolved_on = timezone.localdate()
        dissolved.save(update_fields=["dissolved_on"])

        self.assertEqual(len(self.call("list_tanks")["tanks"]), 1)

        both = self.call("list_tanks", include_dissolved=True)["tanks"]
        self.assertEqual(len(both), 2)
        self.assertTrue(any(tank["is_dissolved"] for tank in both))


class GetTankTests(ToolTestCase):
    def test_it_returns_targets_stock_and_plants(self):
        TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.5"), maximum=Decimal("7.5")
        )
        Stocking.objects.create(
            tank=self.tank, species=self.guppy(), quantity=8, added_on=timezone.localdate()
        )
        Planting.objects.create(
            tank=self.tank, species=self.moss(), quantity=3, planted_on=timezone.localdate()
        )

        result = self.call("get_tank", tank_id=self.tank.pk)

        self.assertEqual(result["name"], "Südamerika-Becken")
        self.assertEqual(result["parameter_targets"][0]["parameter"], "ph")
        self.assertEqual(result["parameter_targets"][0]["minimum"], 6.5)
        self.assertEqual(result["animals"][0]["name"], "Guppy")
        self.assertEqual(result["animals"][0]["quantity"], 8)
        self.assertEqual(result["plants"][0]["common_name"], "Javamoos")

    def test_it_returns_the_setup_bottom_up_and_with_its_effect(self):
        """Bodengrund und Hardscape gehören zur Auskunft über ein Becken.

        Sie sind die naheliegendste Erklärung für eine Wertveränderung: die
        Wurzel drückt den pH, das Depot zehrt sich auf.
        """
        today = timezone.localdate()
        SubstrateLayer.objects.create(
            tank=self.tank,
            kind=SubstrateLayer.Kind.NUTRIENT,
            position=0,
            depth_cm=Decimal("2.0"),
            product="JBL AquaBasis",
            added_on=today - timedelta(days=200),
            depleted_on=today - timedelta(days=80),
        )
        SubstrateLayer.objects.create(
            tank=self.tank, kind=SubstrateLayer.Kind.SAND, position=1, depth_cm=Decimal("4.0")
        )
        HardscapeItem.objects.create(
            tank=self.tank,
            kind=HardscapeItem.Kind.WOOD,
            name="Moorkienwurzel",
            quantity=1,
            affects_water=True,
            water_effect="Huminstoffe, senkt pH",
        )

        result = self.call("get_tank", tank_id=self.tank.pk)

        self.assertEqual([layer["kind"] for layer in result["substrate"]], ["nutrient", "sand"])
        self.assertEqual(result["substrate"][0]["position"], 0)
        self.assertEqual(result["substrate"][0]["status"], "warn")
        self.assertEqual(result["substrate_depth_cm"], 6.0)
        self.assertTrue(result["hardscape"][0]["affects_water"])
        self.assertEqual(result["hardscape"][0]["water_effect"], "Huminstoffe, senkt pH")

    def test_a_group_below_the_minimum_is_visible_in_the_answer(self):
        Stocking.objects.create(
            tank=self.tank, species=self.guppy(), quantity=3, added_on=timezone.localdate()
        )

        stock = self.call("get_tank", tank_id=self.tank.pk)["animals"][0]

        self.assertEqual(stock["min_group_size"], 6)
        self.assertEqual(stock["group_status"], "warn")

    def test_a_foreign_tank_is_not_found(self):
        with self.assertRaises(ToolError) as caught:
            self.call("get_tank", tank_id=self.foreign_tank.pk)

        self.assertIn("keinen Eintrag", str(caught.exception))

    def test_an_unknown_tank_fails_exactly_like_a_foreign_one(self):
        with self.assertRaises(ToolError) as unknown:
            self.call("get_tank", tank_id=999_999)
        with self.assertRaises(ToolError) as foreign:
            self.call("get_tank", tank_id=self.foreign_tank.pk)

        self.assertEqual(str(unknown.exception), str(foreign.exception))

    def test_the_tank_id_is_required(self):
        with self.assertRaises(ToolError):
            self.call("get_tank")

    def test_a_nonsense_tank_id_is_a_readable_error(self):
        with self.assertRaises(ToolError) as caught:
            self.call("get_tank", tank_id="das dritte von links")

        self.assertIn("ganze Zahl", str(caught.exception))


class MeasurementReadTests(ToolTestCase):
    def test_it_lists_the_own_measurements_newest_first(self):
        old = self.make_measurement(when=timezone.now() - timedelta(days=3))
        new = self.make_measurement(when=timezone.now())
        self.make_measurement(tank=self.foreign_tank)

        result = self.call("list_measurements")

        self.assertEqual(
            [item["measurement_id"] for item in result["measurements"]], [new.pk, old.pk]
        )

    def test_a_value_carries_its_parameter_and_the_target_comparison(self):
        TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.5"), maximum=Decimal("7.5")
        )
        measurement = self.make_measurement(value="8.9")

        result = self.call("get_measurement", measurement_id=measurement.pk)

        self.assertEqual(result["parameter"], "ph")
        self.assertEqual(result["parameter_label"], "pH-Wert")
        self.assertEqual(result["value"], 8.9)
        self.assertEqual(result["target_minimum"], 6.5)
        self.assertEqual(result["target_maximum"], 7.5)
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["status_label"], "Kritisch")

    def test_a_value_inside_the_target_range_is_in_order(self):
        TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.5"), maximum=Decimal("7.5")
        )
        measurement = self.make_measurement(value="7.0")

        self.assertEqual(
            self.call("get_measurement", measurement_id=measurement.pk)["status"], "ok"
        )

    def test_without_a_tank_target_the_parameter_default_applies(self):
        """Kein Zielbereich am Becken heißt nicht „keine Aussage“.

        Die Messgröße bringt einen Standardbereich mit; erst wenn auch der
        fehlt, ist der Status ``unknown``.
        """
        measurement = self.make_measurement(value="7.0")

        self.assertEqual(
            self.call("get_measurement", measurement_id=measurement.pk)["status"], "ok"
        )

    def test_without_any_target_range_the_status_stays_unknown(self):
        self.ph.default_min = None
        self.ph.default_max = None
        self.ph.save(update_fields=["default_min", "default_max"])
        measurement = self.make_measurement()

        self.assertEqual(
            self.call("get_measurement", measurement_id=measurement.pk)["status"], "unknown"
        )

    def test_it_filters_by_period(self):
        self.make_measurement(when=timezone.now() - timedelta(days=10))
        recent = self.make_measurement(when=timezone.now())

        result = self.call(
            "list_measurements", **{"from": (timezone.localdate() - timedelta(days=1)).isoformat()}
        )

        self.assertEqual(
            [item["measurement_id"] for item in result["measurements"]], [recent.pk]
        )

    def test_the_upper_bound_includes_the_whole_day(self):
        today = self.make_measurement(when=timezone.now())

        result = self.call("list_measurements", to=timezone.localdate().isoformat())

        self.assertIn(today.pk, [item["measurement_id"] for item in result["measurements"]])

    def test_it_filters_by_parameter(self):
        self.make_measurement(parameter=self.ph)
        nitrate = self.make_measurement(parameter=self.no3, value="10")

        result = self.call("list_measurements", parameter="no3")

        self.assertEqual(
            [item["measurement_id"] for item in result["measurements"]], [nitrate.pk]
        )

    def test_a_foreign_measurement_is_not_found(self):
        foreign = self.make_measurement(tank=self.foreign_tank)

        with self.assertRaises(ToolError):
            self.call("get_measurement", measurement_id=foreign.pk)

    def test_a_foreign_tank_filter_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("list_measurements", tank_id=self.foreign_tank.pk)

    def test_the_number_of_results_stays_bounded(self):
        for index in range(5):
            self.make_measurement(when=timezone.now() - timedelta(hours=index))

        result = self.call("list_measurements", limit=2)

        self.assertEqual(len(result["measurements"]), 2)

    def test_an_absurd_limit_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("list_measurements", limit=5000)


class EventReadTests(ToolTestCase):
    def make_event(self, tank=None, **extra):
        return Event.objects.create(
            tank=tank or self.tank,
            title=extra.pop("title", "Wasserwechsel"),
            occurred_at=extra.pop("occurred_at", timezone.now()),
            **extra,
        )

    def test_it_lists_the_own_events(self):
        self.make_event()
        self.make_event(tank=self.foreign_tank, title="Fremd")

        result = self.call("list_events")

        self.assertEqual([item["title"] for item in result["events"]], ["Wasserwechsel"])
        self.assertEqual(result["events"][0]["tank"], "Südamerika-Becken")

    def test_it_filters_by_category(self):
        self.make_event(category=Event.Category.WATER_CHANGE)
        self.make_event(title="Trübung", category=Event.Category.OBSERVATION)

        result = self.call("list_events", category="observation")

        self.assertEqual([item["title"] for item in result["events"]], ["Trübung"])
        self.assertEqual(result["events"][0]["category_label"], "Beobachtung")

    def test_an_unknown_category_lists_the_possible_ones(self):
        with self.assertRaises(ToolError) as caught:
            self.call("list_events", category="vollmond")

        self.assertIn("water_change", str(caught.exception))


class TaskReadTests(ToolTestCase):
    def test_it_returns_due_and_upcoming_appointments(self):
        overdue = self.make_task(title="Filter", due_on=timezone.localdate() - timedelta(days=2))
        self.make_task(title="Düngen", due_on=timezone.localdate() + timedelta(days=30))
        self.make_task(tank=self.foreign_tank, title="Fremd")

        result = self.call("list_due_tasks")

        titles = [item["title"] for item in result["tasks"]]
        self.assertIn("Filter", titles)
        self.assertNotIn("Düngen", titles)
        self.assertNotIn("Fremd", titles)

        entry = next(item for item in result["tasks"] if item["task_id"] == overdue.pk)
        self.assertEqual(entry["days_until_due"], -2)
        self.assertEqual(entry["status"], "critical")
        self.assertEqual(result["as_of"], timezone.localdate().isoformat())

    def test_the_horizon_can_be_widened(self):
        self.make_task(title="Düngen", due_on=timezone.localdate() + timedelta(days=30))

        result = self.call("list_due_tasks", days_ahead=60)

        self.assertIn("Düngen", [item["title"] for item in result["tasks"]])

    def test_inactive_appointments_stay_out_unless_asked_for(self):
        self.make_task(title="Stillgelegt", is_active=False)

        self.assertEqual(self.call("list_due_tasks")["tasks"], [])
        self.assertEqual(len(self.call("list_due_tasks", include_inactive=True)["tasks"]), 1)


class CatalogReadTests(ToolTestCase):
    def test_it_searches_both_catalogs_by_either_name(self):
        self.guppy()
        self.moss()

        by_science = self.call("search_catalog", query="Poecilia")
        by_german = self.call("search_catalog", query="Javamoos")

        self.assertEqual([item["kind"] for item in by_science["entries"]], ["animal"])
        self.assertEqual([item["kind"] for item in by_german["entries"]], ["plant"])

    def test_the_search_can_be_narrowed_to_one_kind(self):
        self.guppy()
        self.moss()

        result = self.call("search_catalog", query="a", kind="plant")

        self.assertTrue(result["entries"])
        self.assertEqual({item["kind"] for item in result["entries"]}, {"plant"})

    def test_the_profile_carries_the_details(self):
        guppy = self.guppy()

        result = self.call("get_catalog_entry", kind="animal", entry_id=guppy.pk)

        self.assertEqual(result["scientific_name"], "Poecilia reticulata")
        self.assertEqual(result["min_group_size"], 6)
        self.assertEqual(result["adult_size_cm"], 4.0)
        self.assertEqual(result["category_label"], "Fisch")
        self.assertIn("temperature_range", result)

    def test_a_plant_profile_carries_its_own_fields(self):
        moss = self.moss()

        result = self.call("get_catalog_entry", kind="plant", entry_id=moss.pk)

        self.assertEqual(result["placement"], "epiphyte")
        self.assertEqual(result["placement_label"], "Aufsitzerpflanze")
        self.assertIn("co2_required", result)

    def test_the_search_finds_a_cultivated_form_and_names_it_as_one(self):
        AnimalSpecies.objects.create(
            scientific_name="Mikrogeophagus ramirezi",
            common_name="Schmetterlingsbuntbarsch",
            slug="mikrogeophagus-ramirezi-electric-blue",
            variant="Electric Blue",
            is_cultivated_form=True,
        )

        entry = self.call("search_catalog", query="Electric Blue")["entries"][0]

        # Ohne diese beiden Felder liesse sich die Zuchtform in der Antwort
        # nicht von der Stammform unterscheiden.
        self.assertEqual(entry["variant"], "Electric Blue")
        self.assertTrue(entry["is_cultivated_form"])
        self.assertEqual(entry["name"], "Schmetterlingsbuntbarsch 'Electric Blue'")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("get_catalog_entry", kind="mineral", entry_id=1)


class CreateMeasurementTests(ToolTestCase):
    def test_it_creates_one_row_per_parameter_sharing_the_moment(self):
        result = self.call(
            "create_measurement",
            tank_id=self.tank.pk,
            values=[{"parameter": "ph", "value": 7.2}, {"parameter": "kh", "value": 4}],
        )

        self.assertEqual(len(result["measurements"]), 2)
        rows = Measurement.objects.filter(tank=self.tank)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(len({row.measured_at for row in rows}), 1)
        self.assertEqual({row.created_by for row in rows}, {self.user})

    def test_nothing_is_left_behind_when_one_value_fails(self):
        with self.assertRaises(ToolError):
            self.call(
                "create_measurement",
                tank_id=self.tank.pk,
                values=[{"parameter": "ph", "value": 7.2}, {"parameter": "gibtsnicht", "value": 1}],
            )

        self.assertFalse(Measurement.objects.exists())

    def test_an_unknown_parameter_names_the_known_ones(self):
        with self.assertRaises(ToolError) as caught:
            self.call(
                "create_measurement",
                tank_id=self.tank.pk,
                values=[{"parameter": "gibtsnicht", "value": 1}],
            )

        self.assertIn("ph", str(caught.exception))

    def test_values_are_required(self):
        with self.assertRaises(ToolError):
            self.call("create_measurement", tank_id=self.tank.pk, values=[])

    def test_a_value_is_required_per_row(self):
        with self.assertRaises(ToolError):
            self.call(
                "create_measurement", tank_id=self.tank.pk, values=[{"parameter": "ph"}]
            )

    def test_it_cannot_write_into_a_foreign_tank(self):
        with self.assertRaises(ToolError):
            self.call(
                "create_measurement",
                tank_id=self.foreign_tank.pk,
                values=[{"parameter": "ph", "value": 7.2}],
            )

        self.assertFalse(Measurement.objects.exists())


class CreateEventTests(ToolTestCase):
    def test_it_creates_an_event(self):
        result = self.call(
            "create_event",
            tank_id=self.tank.pk,
            title="Wasserwechsel",
            category="water_change",
            description="30 Liter",
        )

        event = Event.objects.get()
        self.assertEqual(result["event_id"], event.pk)
        self.assertEqual(event.title, "Wasserwechsel")
        self.assertEqual(event.category, "water_change")
        self.assertEqual(event.created_by, self.user)

    def test_without_a_category_it_becomes_other(self):
        self.call("create_event", tank_id=self.tank.pk, title="Irgendwas")

        self.assertEqual(Event.objects.get().category, "other")

    def test_an_unknown_category_lists_the_possible_ones(self):
        with self.assertRaises(ToolError) as caught:
            self.call("create_event", tank_id=self.tank.pk, title="X", category="vollmond")

        self.assertIn("observation", str(caught.exception))

    def test_a_title_is_required(self):
        with self.assertRaises(ToolError):
            self.call("create_event", tank_id=self.tank.pk)

    def test_an_invalid_timestamp_is_a_readable_error(self):
        with self.assertRaises(ToolError) as caught:
            self.call(
                "create_event", tank_id=self.tank.pk, title="X", occurred_at="neulich"
            )

        self.assertIn("ISO 8601", str(caught.exception))

    def test_it_cannot_write_into_a_foreign_tank(self):
        with self.assertRaises(ToolError):
            self.call("create_event", tank_id=self.foreign_tank.pk, title="Fremd")

        self.assertFalse(Event.objects.exists())


class CompleteTaskTests(ToolTestCase):
    def test_a_recurring_task_moves_on_from_the_day_it_was_done(self):
        task = self.make_task(
            interval_days=7, due_on=timezone.localdate() - timedelta(days=3)
        )
        done_on = timezone.localdate() - timedelta(days=1)

        result = self.call("complete_task", task_id=task.pk, done_on=done_on.isoformat())

        task.refresh_from_db()
        self.assertEqual(task.last_completed_on, done_on)
        self.assertEqual(task.due_on, done_on + timedelta(days=7))
        self.assertTrue(task.is_active)
        self.assertEqual(result["task"]["due_on"], task.due_on.isoformat())
        self.assertEqual(result["completion"]["completed_on"], done_on.isoformat())

    def test_a_one_off_task_is_switched_off(self):
        task = self.make_task()

        self.call("complete_task", task_id=task.pk)

        task.refresh_from_db()
        self.assertFalse(task.is_active)

    def test_the_completion_remembers_who_did_it(self):
        task = self.make_task()

        self.call("complete_task", task_id=task.pk, note="erledigt")

        completion = task.completions.get()
        self.assertEqual(completion.completed_by, self.user)
        self.assertEqual(completion.note, "erledigt")

    def test_a_foreign_appointment_is_not_found(self):
        foreign = self.make_task(tank=self.foreign_tank)

        with self.assertRaises(ToolError):
            self.call("complete_task", task_id=foreign.pk)

        foreign.refresh_from_db()
        self.assertIsNone(foreign.last_completed_on)


class StockTests(ToolTestCase):
    def test_adding_an_animal(self):
        guppy = self.guppy()

        result = self.call(
            "add_stocking", tank_id=self.tank.pk, species_id=guppy.pk, quantity=8
        )

        stocking = Stocking.objects.get()
        self.assertEqual(result["stocking_id"], stocking.pk)
        self.assertEqual(stocking.quantity, 8)
        self.assertEqual(stocking.added_on, timezone.localdate())
        self.assertTrue(result["is_active"])

    def test_adding_a_plant(self):
        moss = self.moss()

        result = self.call(
            "add_planting", tank_id=self.tank.pk, species_id=moss.pk, quantity=3
        )

        planting = Planting.objects.get()
        self.assertEqual(result["planting_id"], planting.pk)
        self.assertEqual(planting.quantity, 3)
        self.assertEqual(result["common_name"], "Javamoos")

    def test_a_group_below_the_minimum_is_visible_in_the_answer(self):
        guppy = self.guppy()

        result = self.call(
            "add_stocking", tank_id=self.tank.pk, species_id=guppy.pk, quantity=3
        )

        self.assertEqual(result["group_status"], "warn")
        self.assertEqual(result["min_group_size"], 6)

    def test_stock_cannot_be_added_to_a_foreign_tank(self):
        guppy = self.guppy()

        with self.assertRaises(ToolError):
            self.call("add_stocking", tank_id=self.foreign_tank.pk, species_id=guppy.pk)

        self.assertFalse(Stocking.objects.exists())

    def test_an_unknown_species_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("add_stocking", tank_id=self.tank.pk, species_id=999_999)

    def test_the_quantity_is_written_forward(self):
        guppy = self.guppy()
        stocking = Stocking.objects.create(
            tank=self.tank, species=guppy, quantity=8, added_on=timezone.localdate()
        )

        result = self.call("update_stocking", stocking_id=stocking.pk, quantity=11)

        stocking.refresh_from_db()
        self.assertEqual(stocking.quantity, 11)
        self.assertEqual(result["quantity"], 11)

    def test_a_removal_ends_the_position_instead_of_deleting_it(self):
        guppy = self.guppy()
        stocking = Stocking.objects.create(
            tank=self.tank, species=guppy, quantity=8, added_on=timezone.localdate()
        )
        removed_on = timezone.localdate()

        result = self.call(
            "update_stocking", stocking_id=stocking.pk, removed_on=removed_on.isoformat()
        )

        stocking.refresh_from_db()
        self.assertEqual(stocking.removed_on, removed_on)
        self.assertFalse(result["is_active"])
        self.assertTrue(Stocking.objects.filter(pk=stocking.pk).exists())

    def test_a_negative_quantity_is_refused(self):
        guppy = self.guppy()
        stocking = Stocking.objects.create(
            tank=self.tank, species=guppy, quantity=8, added_on=timezone.localdate()
        )

        with self.assertRaises(ToolError):
            self.call("update_stocking", stocking_id=stocking.pk, quantity=-1)

        stocking.refresh_from_db()
        self.assertEqual(stocking.quantity, 8)

    def test_a_foreign_stock_item_is_not_found(self):
        foreign = Stocking.objects.create(
            tank=self.foreign_tank,
            species=self.guppy(),
            quantity=8,
            added_on=timezone.localdate(),
        )

        with self.assertRaises(ToolError):
            self.call("update_stocking", stocking_id=foreign.pk, quantity=1)

        foreign.refresh_from_db()
        self.assertEqual(foreign.quantity, 8)


class EveryToolTests(ToolTestCase):
    """Jedes registrierte Werkzeug wird einmal mit gültigen Argumenten gerufen.

    Django löst Feldzugriffe erst zur Laufzeit auf: ein Filter auf ein Feld,
    das es nicht gibt, und ein Zugriff auf ein Attribut, das es nicht gibt,
    fallen beide erst beim Aufruf auf. Genau daran ist #1236 gescheitert —
    ``list_tanks`` und ``get_tank`` waren im Betrieb kaputt, während die Tests
    grün waren.

    Dieser Test ist die Gegenprobe. Er ist bewusst anspruchslos, was das
    Ergebnis angeht: geprüft wird, dass der Aufruf **überhaupt durchläuft** und
    JSON ergibt. Wo es um Inhalte geht, stehen die Tests oben.
    """

    def setUp(self):
        super().setUp()
        self.guppy_species = self.guppy()
        self.moss_species = self.moss()
        self.measurement = self.make_measurement()
        self.task = self.make_task(interval_days=7)
        self.stocking = Stocking.objects.create(
            tank=self.tank,
            species=self.guppy_species,
            quantity=8,
            added_on=timezone.localdate(),
        )
        TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.5"), maximum=Decimal("7.5")
        )
        Event.objects.create(
            tank=self.tank, title="Wasserwechsel", occurred_at=timezone.now()
        )
        SubstrateLayer.objects.create(
            tank=self.tank, kind=SubstrateLayer.Kind.SAND, position=0, depth_cm=Decimal("4.0")
        )
        HardscapeItem.objects.create(
            tank=self.tank, kind=HardscapeItem.Kind.WOOD, name="Moorkienwurzel", quantity=1
        )

    def valid_arguments(self):
        """Gültige Argumente je Werkzeug — die Vorlage für den Aufruf."""
        return {
            "list_tanks": {},
            "get_tank": {"tank_id": self.tank.pk},
            "list_measurements": {},
            "get_measurement": {"measurement_id": self.measurement.pk},
            "list_events": {},
            "list_due_tasks": {},
            "search_catalog": {"query": "Poecilia"},
            "get_catalog_entry": {"kind": "animal", "entry_id": self.guppy_species.pk},
            "create_measurement": {
                "tank_id": self.tank.pk,
                "values": [{"parameter": "ph", "value": 7.1}],
            },
            "create_event": {"tank_id": self.tank.pk, "title": "Beobachtung"},
            "complete_task": {"task_id": self.task.pk},
            "add_stocking": {"tank_id": self.tank.pk, "species_id": self.guppy_species.pk},
            "add_planting": {"tank_id": self.tank.pk, "species_id": self.moss_species.pk},
            "update_stocking": {"stocking_id": self.stocking.pk, "quantity": 9},
        }

    def test_every_registered_tool_is_covered_here(self):
        """Ein neues Werkzeug ohne Aufruf lässt diesen Test scheitern."""
        self.assertEqual(set(registry.names()), set(self.valid_arguments()))

    def test_every_tool_runs_without_an_exception_and_yields_json(self):
        for name, arguments in self.valid_arguments().items():
            with self.subTest(tool=name):
                result = call_tool(self.context, name, arguments)
                json.dumps(result, ensure_ascii=False)

    def test_every_tool_refuses_a_foreign_or_unknown_id(self):
        """Jede Kennung im Aufruf wird eingegrenzt — auch die der Schreibenden."""
        unreachable = {
            "get_tank": {"tank_id": self.foreign_tank.pk},
            "list_measurements": {"tank_id": self.foreign_tank.pk},
            "get_measurement": {"measurement_id": 999_999},
            "list_events": {"tank_id": self.foreign_tank.pk},
            "list_due_tasks": {"tank_id": self.foreign_tank.pk},
            "get_catalog_entry": {"kind": "animal", "entry_id": 999_999},
            "create_measurement": {
                "tank_id": self.foreign_tank.pk,
                "values": [{"parameter": "ph", "value": 7.1}],
            },
            "create_event": {"tank_id": self.foreign_tank.pk, "title": "Fremd"},
            "complete_task": {"task_id": 999_999},
            "add_stocking": {
                "tank_id": self.foreign_tank.pk,
                "species_id": self.guppy_species.pk,
            },
            "add_planting": {
                "tank_id": self.foreign_tank.pk,
                "species_id": self.moss_species.pk,
            },
            "update_stocking": {"stocking_id": 999_999, "quantity": 1},
        }
        for name, arguments in unreachable.items():
            with self.subTest(tool=name):
                with self.assertRaises(ToolError):
                    call_tool(self.context, name, arguments)


class ReadOnlyTokenTests(ToolTestCase):
    """Ein Token ohne Schreibrecht liest — und sonst nichts."""

    allow_write = False

    def test_reading_works(self):
        self.assertEqual(len(self.call("list_tanks")["tanks"]), 1)

    def test_writing_is_refused(self):
        with self.assertRaises(WriteNotAllowed):
            self.call("create_event", tank_id=self.tank.pk, title="Versuch")

        self.assertFalse(Event.objects.exists())

    def test_the_refused_attempt_is_logged(self):
        with self.assertRaises(WriteNotAllowed):
            self.call("create_event", tank_id=self.tank.pk, title="Versuch")

        entry = MCPAccessLog.objects.get()
        self.assertEqual(entry.tool, "create_event")
        self.assertFalse(entry.succeeded)
        self.assertEqual(entry.user, self.user)


class AccessLogTests(ToolTestCase):
    def test_a_write_is_logged_with_token_arguments_and_record(self):
        self.call("create_event", tank_id=self.tank.pk, title="Wasserwechsel")

        entry = MCPAccessLog.objects.get()
        event = Event.objects.get()
        self.assertEqual(entry.token, self.token)
        self.assertEqual(entry.token_name, "Claude Desktop")
        self.assertEqual(entry.tool, "create_event")
        self.assertEqual(entry.arguments["title"], "Wasserwechsel")
        self.assertEqual(entry.object_ref, f"{event._meta.label}:{event.pk}")
        self.assertTrue(entry.succeeded)

    def test_a_failed_write_is_logged_with_its_reason(self):
        with self.assertRaises(ToolError):
            self.call("create_event", tank_id=self.foreign_tank.pk, title="Fremd")

        entry = MCPAccessLog.objects.get()
        self.assertFalse(entry.succeeded)
        self.assertIn("keinen Eintrag", entry.error_message)

    def test_a_call_marks_the_token_as_used(self):
        self.assertIsNone(self.token.last_used_at)

        self.call("list_tanks")

        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)

    def test_reading_is_not_logged(self):
        self.call("list_tanks")
        self.call("get_tank", tank_id=self.tank.pk)

        self.assertFalse(MCPAccessLog.objects.exists())

    def test_the_log_survives_a_deleted_token(self):
        self.call("create_event", tank_id=self.tank.pk, title="Wasserwechsel")
        self.token.delete()

        entry = MCPAccessLog.objects.get()
        self.assertIsNone(entry.token)
        self.assertEqual(entry.token_name, "Claude Desktop")


class UserModelTests(ToolTestCase):
    def test_the_token_owner_decides_what_is_visible(self):
        """Zur Sicherheit gegen die andere Richtung: derselbe Aufruf, anderer Token."""
        stranger_token, _ = MCPToken.issue(self.stranger, "Fremder Client")
        stranger_context = Context(token=stranger_token, user=self.stranger)

        mine = call_tool(self.context, "list_tanks", {})
        theirs = call_tool(stranger_context, "list_tanks", {})

        self.assertEqual([tank["name"] for tank in mine["tanks"]], ["Südamerika-Becken"])
        self.assertEqual([tank["name"] for tank in theirs["tanks"]], ["Fremdes Becken"])
        self.assertNotEqual(get_user_model().objects.count(), 1)


class DeviceDataStaysOutTests(ToolTestCase):
    """Über MCP gibt es keine Gerätedaten — auch nach dem Zusammenführen nicht.

    Ein Gerät hängt jetzt am Becken, ``get_tank`` könnte es also mitliefern.
    Soll es aber nicht (#1226): Zugangsdaten, Adressen und Schaltzustände
    gehören nicht in ein Sprachmodell, und lesen ohne schalten wäre eine
    Auskunft über das Heimnetz.
    """

    def test_no_tool_deals_with_devices(self):
        for definition in registry.definitions(allow_write=True):
            with self.subTest(tool=definition["name"]):
                text = f"{definition['name']} {definition['description']}".lower()
                self.assertNotIn("gerät", text)
                self.assertNotIn("device", text)

    def test_the_model_map_knows_no_device(self):
        from services.mcp.data import MODELS

        self.assertEqual(
            [alias for alias, model in MODELS.items() if "evice" in model.__name__], []
        )

    def test_get_tank_carries_no_device_data(self):
        result = self.call("get_tank", tank_id=self.tank.pk)

        payload = json.dumps(result, ensure_ascii=False).lower()
        self.assertNotIn("device", payload)
        self.assertNotIn("mac", payload)
