"""Tests der MCP-Werkzeuge.

Die wichtigste Zusage dieser Schnittstelle ist nicht, dass sie Daten liefert,
sondern **welche**: ausschließlich die des Token-Inhabers. Deshalb steht hier
zu jedem Werkzeug mit einer Kennung im Aufruf auch der Versuch mit einer
fremden — er muss scheitern, und zwar ohne zu verraten, dass es den fremden
Datensatz gibt.

Gearbeitet wird gegen die Ersatzmodelle aus :mod:`services.tests.mcp_stubs`;
das Becken-Modell selbst liegt in einem anderen Schritt des Epics.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from services.mcp import call_tool
from services.mcp.exceptions import ToolError, WriteNotAllowed
from services.models import MCPAccessLog, MCPToken

from .mcp_stubs import (
    DataModelTestCase,
    StubCatalogAnimal,
    StubCatalogPlant,
    StubEvent,
    StubMaintenanceSchedule,
    StubMeasurement,
    StubMeasurementValue,
    StubParameter,
    StubTank,
    StubTankAnimal,
    StubTankParameterTarget,
    StubTankPlant,
)
from .test_mcp_tokens import make_user


class ToolTestCase(DataModelTestCase):
    """Zwei Benutzer, zwei Becken — und ein Token auf genau eines davon."""

    allow_write = True

    def setUp(self):
        self.user = make_user("greta")
        self.stranger = make_user("hans")
        self.token, self.key = MCPToken.issue(
            self.user, "Claude Desktop", allow_write=self.allow_write
        )
        self.context = self._context(self.token)

        self.tank = StubTank.objects.create(
            owner=self.user,
            name="Südamerika-Becken",
            water_type="suess",
            volume_net_l=Decimal("160.0"),
            filtration="Außenfilter",
        )
        self.foreign_tank = StubTank.objects.create(owner=self.stranger, name="Fremdes Becken")

        self.ph = StubParameter.objects.create(key="ph", name="pH-Wert", position=10)
        self.kh = StubParameter.objects.create(key="kh", name="Karbonathärte", unit="°dKH", position=20)
        self.no3 = StubParameter.objects.create(
            key="no3", name="Nitrat", unit="mg/l", position=30, supports_below_detection=True
        )

    @staticmethod
    def _context(token):
        from services.mcp import Context

        return Context(token=token, user=token.user)

    def call(self, name, **arguments):
        return call_tool(self.context, name, arguments)

    # -- Daten ----------------------------------------------------------------

    def measurement(self, tank=None, *, ph="7.2", kh="4.0", when=None):
        measurement = StubMeasurement.objects.create(
            tank=tank or self.tank, measured_at=when or timezone.now()
        )
        StubMeasurementValue.objects.create(
            measurement=measurement, parameter=self.ph, value=Decimal(ph)
        )
        StubMeasurementValue.objects.create(
            measurement=measurement, parameter=self.kh, value=Decimal(kh)
        )
        return measurement

    def guppy(self):
        return StubCatalogAnimal.objects.create(
            scientific_name="Poecilia reticulata",
            common_name="Guppy",
            group="fisch",
            min_group_size=6,
        )

    def moss(self):
        return StubCatalogPlant.objects.create(
            scientific_name="Vesicularia dubyana",
            common_name="Javamoos",
            growth_form="moss",
        )


class ListTanksTests(ToolTestCase):
    def test_it_lists_only_the_own_tanks(self):
        result = self.call("list_tanks")

        names = [tank["name"] for tank in result["tanks"]]
        self.assertEqual(names, ["Südamerika-Becken"])

    def test_dissolved_tanks_are_hidden_unless_asked_for(self):
        StubTank.objects.create(
            owner=self.user, name="Aufgelöst", shut_down_on=timezone.localdate()
        )

        without = self.call("list_tanks")
        with_them = self.call("list_tanks", include_dissolved=True)

        self.assertEqual(len(without["tanks"]), 1)
        self.assertEqual(len(with_them["tanks"]), 2)

    def test_units_are_part_of_the_field_names(self):
        result = self.call("list_tanks")

        self.assertEqual(result["tanks"][0]["volume_net_l"], 160.0)


class GetTankTests(ToolTestCase):
    def test_it_returns_technology_targets_stock_and_plants(self):
        StubTankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.5"), maximum=Decimal("7.5")
        )
        StubTankAnimal.objects.create(tank=self.tank, animal=self.guppy(), quantity=8)
        StubTankPlant.objects.create(tank=self.tank, plant=self.moss())

        result = self.call("get_tank", tank_id=self.tank.pk)

        self.assertEqual(result["technology"]["filtration"], "Außenfilter")
        self.assertEqual(result["parameter_targets"][0]["parameter"], "ph")
        self.assertEqual(result["animals"][0]["common_name"], "Guppy")
        self.assertEqual(result["plants"][0]["common_name"], "Javamoos")

    def test_a_foreign_tank_is_not_found(self):
        with self.assertRaises(ToolError) as caught:
            self.call("get_tank", tank_id=self.foreign_tank.pk)

        # Die Meldung darf nicht verraten, dass es das Becken gibt.
        self.assertNotIn("fremd", str(caught.exception).lower())
        self.assertIn("keinen Eintrag", str(caught.exception))

    def test_an_unknown_tank_fails_exactly_like_a_foreign_one(self):
        with self.assertRaises(ToolError) as unknown:
            self.call("get_tank", tank_id=999999)
        with self.assertRaises(ToolError) as foreign:
            self.call("get_tank", tank_id=self.foreign_tank.pk)

        self.assertEqual(str(unknown.exception), str(foreign.exception))

    def test_the_tank_id_is_required(self):
        with self.assertRaises(ToolError) as caught:
            self.call("get_tank")

        self.assertIn("tank_id", str(caught.exception))

    def test_a_nonsense_tank_id_is_a_readable_error(self):
        with self.assertRaises(ToolError) as caught:
            self.call("get_tank", tank_id="das dritte von links")

        self.assertIn("ganze Zahl", str(caught.exception))


class MeasurementReadTests(ToolTestCase):
    def test_it_lists_the_own_measurements_newest_first(self):
        older = self.measurement(when=timezone.now() - timedelta(days=2))
        newer = self.measurement()
        self.measurement(tank=self.foreign_tank)

        result = self.call("list_measurements")

        ids = [row["measurement_id"] for row in result["measurements"]]
        self.assertEqual(ids, [newer.pk, older.pk])

    def test_it_filters_by_period(self):
        self.measurement(when=timezone.now() - timedelta(days=10))
        recent = self.measurement()

        result = self.call(
            "list_measurements", **{"from": (timezone.localdate() - timedelta(days=1)).isoformat()}
        )

        self.assertEqual([row["measurement_id"] for row in result["measurements"]], [recent.pk])

    def test_the_upper_bound_includes_the_whole_day(self):
        today = self.measurement(when=timezone.now())

        result = self.call("list_measurements", to=timezone.localdate().isoformat())

        self.assertEqual([row["measurement_id"] for row in result["measurements"]], [today.pk])

    def test_it_filters_by_parameter(self):
        with_ph = self.measurement()
        without_ph = StubMeasurement.objects.create(tank=self.tank)
        StubMeasurementValue.objects.create(
            measurement=without_ph, parameter=self.no3, value=Decimal("10")
        )

        result = self.call("list_measurements", parameter="ph")

        self.assertEqual([row["measurement_id"] for row in result["measurements"]], [with_ph.pk])

    def test_a_detail_carries_co2_and_the_comparison_with_the_target(self):
        StubTankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.0"), maximum=Decimal("7.0")
        )
        measurement = self.measurement(ph="7.2", kh="4.0")

        result = self.call("get_measurement", measurement_id=measurement.pk)

        self.assertAlmostEqual(result["co2_mg_l"], 7.6, places=1)
        ph_row = next(row for row in result["values"] if row["parameter"] == "ph")
        self.assertEqual(ph_row["status"], "high")
        self.assertEqual(ph_row["target_maximum"], 7.0)

    def test_a_foreign_measurement_is_not_found(self):
        foreign = self.measurement(tank=self.foreign_tank)

        with self.assertRaises(ToolError):
            self.call("get_measurement", measurement_id=foreign.pk)

    def test_a_foreign_tank_filter_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("list_measurements", tank_id=self.foreign_tank.pk)

    def test_the_number_of_results_stays_bounded(self):
        for _ in range(3):
            self.measurement()

        result = self.call("list_measurements", limit=2)

        self.assertEqual(len(result["measurements"]), 2)

    def test_an_absurd_limit_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("list_measurements", limit=100000)


class EventReadTests(ToolTestCase):
    def test_it_lists_the_own_events_with_the_water_change_in_percent(self):
        StubEvent.objects.create(
            tank=self.tank,
            title="Wasserwechsel",
            category=StubEvent.Category.WATER_CHANGE,
            water_changed_l=Decimal("40.0"),
        )
        StubEvent.objects.create(tank=self.foreign_tank, title="Fremdes Ereignis")

        result = self.call("list_events")

        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["water_change_percent"], 25.0)
        self.assertEqual(result["events"][0]["category_label"], "Wasserwechsel")

    def test_it_filters_by_category(self):
        StubEvent.objects.create(
            tank=self.tank, title="Wasserwechsel", category=StubEvent.Category.WATER_CHANGE
        )
        StubEvent.objects.create(
            tank=self.tank, title="Beobachtung", category=StubEvent.Category.OBSERVATION
        )

        result = self.call("list_events", category="observation")

        self.assertEqual([row["title"] for row in result["events"]], ["Beobachtung"])

    def test_an_unknown_category_lists_the_possible_ones(self):
        with self.assertRaises(ToolError) as caught:
            self.call("list_events", category="urlaub")

        self.assertIn("water_change", str(caught.exception))


class ScheduleReadTests(ToolTestCase):
    def test_it_returns_due_and_upcoming_appointments(self):
        overdue = StubMaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Filter reinigen",
            next_due_on=timezone.localdate() - timedelta(days=3),
        )
        StubMaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Ferne Zukunft",
            next_due_on=timezone.localdate() + timedelta(days=90),
        )
        StubMaintenanceSchedule.objects.create(
            tank=self.foreign_tank,
            title="Fremder Termin",
            next_due_on=timezone.localdate(),
        )

        result = self.call("list_due_schedules")

        self.assertEqual([row["title"] for row in result["schedules"]], ["Filter reinigen"])
        self.assertEqual(result["schedules"][0]["days_until_due"], -3)
        self.assertTrue(result["schedules"][0]["is_due"])
        self.assertEqual(result["schedules"][0]["schedule_id"], overdue.pk)

    def test_the_horizon_can_be_widened(self):
        StubMaintenanceSchedule.objects.create(
            tank=self.tank, title="In zwei Wochen",
            next_due_on=timezone.localdate() + timedelta(days=14),
        )

        narrow = self.call("list_due_schedules")
        wide = self.call("list_due_schedules", days_ahead=30)

        self.assertEqual(narrow["schedules"], [])
        self.assertEqual(len(wide["schedules"]), 1)

    def test_inactive_appointments_stay_out_unless_asked_for(self):
        StubMaintenanceSchedule.objects.create(
            tank=self.tank, title="Stillgelegt", is_active=False,
            next_due_on=timezone.localdate(),
        )

        self.assertEqual(self.call("list_due_schedules")["schedules"], [])
        self.assertEqual(len(self.call("list_due_schedules", include_inactive=True)["schedules"]), 1)


class CatalogReadTests(ToolTestCase):
    def test_it_searches_both_catalogs_by_either_name(self):
        self.guppy()
        self.moss()

        by_science = self.call("search_catalog", query="Poecilia")
        by_common = self.call("search_catalog", query="Javamoos")

        self.assertEqual([entry["kind"] for entry in by_science["entries"]], ["animal"])
        self.assertEqual([entry["kind"] for entry in by_common["entries"]], ["plant"])

    def test_the_search_can_be_narrowed_to_one_kind(self):
        self.guppy()
        StubCatalogPlant.objects.create(scientific_name="Poecilia-Pflanze")

        result = self.call("search_catalog", query="Poecilia", kind="animal")

        self.assertEqual(len(result["entries"]), 1)

    def test_the_profile_carries_the_details(self):
        guppy = self.guppy()

        result = self.call("get_catalog_entry", kind="animal", entry_id=guppy.pk)

        self.assertEqual(result["scientific_name"], "Poecilia reticulata")
        self.assertEqual(result["min_group_size"], 6)
        self.assertEqual(result["group_label"], "Fisch")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("get_catalog_entry", kind="pilz", entry_id=1)

        self.assertIn("animal", str(caught.exception))


class CreateMeasurementTests(ToolTestCase):
    def test_it_creates_a_measurement_marked_as_coming_from_mcp(self):
        result = self.call(
            "create_measurement",
            tank_id=self.tank.pk,
            note="Nach dem Wasserwechsel",
            values=[{"parameter": "ph", "value": 7.1}, {"parameter": "kh", "value": 4}],
        )

        measurement = StubMeasurement.objects.get()
        self.assertEqual(measurement.source, "mcp")
        self.assertEqual(measurement.note, "Nach dem Wasserwechsel")
        self.assertEqual(len(result["values"]), 2)

    def test_below_detection_is_kept_apart_from_a_missing_value(self):
        self.call(
            "create_measurement",
            tank_id=self.tank.pk,
            values=[{"parameter": "no3", "below_detection": True}],
        )

        value = StubMeasurementValue.objects.get()
        self.assertTrue(value.below_detection)
        self.assertIsNone(value.value)

    def test_a_value_and_below_detection_together_are_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call(
                "create_measurement",
                tank_id=self.tank.pk,
                values=[{"parameter": "no3", "value": 5, "below_detection": True}],
            )

        self.assertIn("nicht beides", str(caught.exception))

    def test_below_detection_needs_a_parameter_that_supports_it(self):
        with self.assertRaises(ToolError):
            self.call(
                "create_measurement",
                tank_id=self.tank.pk,
                values=[{"parameter": "ph", "below_detection": True}],
            )

    def test_nothing_is_left_behind_when_one_value_fails(self):
        with self.assertRaises(ToolError):
            self.call(
                "create_measurement",
                tank_id=self.tank.pk,
                values=[{"parameter": "ph", "value": 7.1}, {"parameter": "ph"}],
            )

        self.assertFalse(StubMeasurement.objects.exists())
        self.assertFalse(StubMeasurementValue.objects.exists())

    def test_the_same_parameter_twice_is_refused(self):
        with self.assertRaises(ToolError):
            self.call(
                "create_measurement",
                tank_id=self.tank.pk,
                values=[{"parameter": "ph", "value": 7.1}, {"parameter": "ph", "value": 7.4}],
            )

        self.assertFalse(StubMeasurement.objects.exists())

    def test_an_unknown_parameter_names_the_known_ones(self):
        with self.assertRaises(ToolError) as caught:
            self.call(
                "create_measurement",
                tank_id=self.tank.pk,
                values=[{"parameter": "leitwert", "value": 300}],
            )

        self.assertIn("ph", str(caught.exception))

    def test_values_are_required(self):
        with self.assertRaises(ToolError):
            self.call("create_measurement", tank_id=self.tank.pk, values=[])

    def test_it_cannot_write_into_a_foreign_tank(self):
        with self.assertRaises(ToolError):
            self.call(
                "create_measurement",
                tank_id=self.foreign_tank.pk,
                values=[{"parameter": "ph", "value": 7.1}],
            )

        self.assertFalse(StubMeasurement.objects.exists())


class CreateEventTests(ToolTestCase):
    def test_it_creates_an_event(self):
        result = self.call(
            "create_event",
            tank_id=self.tank.pk,
            title="30 Liter gewechselt",
            category="water_change",
            water_changed_l=30,
        )

        event = StubEvent.objects.get()
        self.assertEqual(event.category, "water_change")
        self.assertEqual(result["water_change_percent"], 18.8)

    def test_without_a_category_it_becomes_other(self):
        self.call("create_event", tank_id=self.tank.pk, title="Irgendwas")

        self.assertEqual(StubEvent.objects.get().category, "other")

    def test_a_title_is_required(self):
        with self.assertRaises(ToolError):
            self.call("create_event", tank_id=self.tank.pk)

    def test_an_invalid_timestamp_is_a_readable_error(self):
        with self.assertRaises(ToolError) as caught:
            self.call(
                "create_event", tank_id=self.tank.pk, title="Test", occurred_at="letzten Dienstag"
            )

        self.assertIn("ISO 8601", str(caught.exception))

    def test_it_cannot_write_into_a_foreign_tank(self):
        with self.assertRaises(ToolError):
            self.call("create_event", tank_id=self.foreign_tank.pk, title="Fremd")

        self.assertFalse(StubEvent.objects.exists())


class CompleteScheduleTests(ToolTestCase):
    def setUp(self):
        super().setUp()
        self.schedule = StubMaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Filter reinigen",
            interval=StubMaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate() - timedelta(days=2),
        )

    def test_it_creates_the_event_and_moves_the_appointment(self):
        today = timezone.localdate()

        result = self.call("complete_schedule", schedule_id=self.schedule.pk, note="Schwämme gespült")

        event = StubEvent.objects.get()
        self.schedule.refresh_from_db()
        self.assertEqual(event.schedule_id, self.schedule.pk)
        self.assertEqual(event.description, "Schwämme gespült")
        self.assertEqual(self.schedule.last_done_on, today)
        self.assertEqual(self.schedule.next_due_on, today + timedelta(days=7))
        self.assertEqual(result["schedule"]["next_due_on"], (today + timedelta(days=7)).isoformat())

    def test_the_next_date_is_calculated_from_the_day_it_was_done(self):
        done_on = timezone.localdate() - timedelta(days=1)

        self.call("complete_schedule", schedule_id=self.schedule.pk, done_on=done_on.isoformat())

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.next_due_on, done_on + timedelta(days=7))

    def test_a_foreign_appointment_is_not_found(self):
        foreign = StubMaintenanceSchedule.objects.create(
            tank=self.foreign_tank, title="Fremder Termin"
        )

        with self.assertRaises(ToolError):
            self.call("complete_schedule", schedule_id=foreign.pk)

        self.assertFalse(StubEvent.objects.exists())


class StockTests(ToolTestCase):
    def test_adding_an_animal_counts_as_an_arrival(self):
        guppy = self.guppy()

        result = self.call(
            "add_tank_animal", tank_id=self.tank.pk, catalog_animal_id=guppy.pk, quantity=10
        )

        stock = StubTankAnimal.objects.get()
        self.assertEqual(stock.quantity, 10)
        self.assertEqual(stock.movements.count(), 1)
        self.assertFalse(result["below_min_group_size"])

    def test_a_group_below_the_minimum_is_visible_in_the_answer(self):
        guppy = self.guppy()

        result = self.call(
            "add_tank_animal", tank_id=self.tank.pk, catalog_animal_id=guppy.pk, quantity=2
        )

        self.assertTrue(result["below_min_group_size"])

    def test_adding_a_plant(self):
        moss = self.moss()

        result = self.call(
            "add_tank_plant",
            tank_id=self.tank.pk,
            catalog_plant_id=moss.pk,
            placement="Wurzel links",
        )

        self.assertEqual(StubTankPlant.objects.get().placement, "Wurzel links")
        self.assertEqual(result["name"], "Vesicularia dubyana")

    def test_stock_cannot_be_added_to_a_foreign_tank(self):
        guppy = self.guppy()

        with self.assertRaises(ToolError):
            self.call(
                "add_tank_animal", tank_id=self.foreign_tank.pk, catalog_animal_id=guppy.pk
            )

        self.assertFalse(StubTankAnimal.objects.exists())

    def test_a_movement_updates_the_stock(self):
        stock = StubTankAnimal.objects.create(tank=self.tank, animal=self.guppy(), quantity=10)

        result = self.call(
            "record_animal_movement",
            tank_animal_id=stock.pk,
            direction="out",
            reason="died",
            quantity=3,
        )

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 7)
        self.assertEqual(result["stock"]["quantity"], 7)
        self.assertEqual(result["movement"]["reason_label"], "Eingegangen")

    def test_a_movement_cannot_push_the_stock_below_zero(self):
        stock = StubTankAnimal.objects.create(tank=self.tank, animal=self.guppy(), quantity=2)

        with self.assertRaises(ToolError) as caught:
            self.call(
                "record_animal_movement",
                tank_animal_id=stock.pk,
                direction="out",
                reason="died",
                quantity=5,
            )

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 2)
        self.assertIn("unter null", str(caught.exception))

    def test_a_transfer_needs_a_target_tank(self):
        stock = StubTankAnimal.objects.create(tank=self.tank, animal=self.guppy(), quantity=10)

        with self.assertRaises(ToolError) as caught:
            self.call(
                "record_animal_movement",
                tank_animal_id=stock.pk,
                direction="out",
                reason="transfer_out",
                quantity=2,
            )

        self.assertIn("Zielbecken", str(caught.exception))

    def test_a_foreign_tank_is_no_valid_transfer_target(self):
        stock = StubTankAnimal.objects.create(tank=self.tank, animal=self.guppy(), quantity=10)

        with self.assertRaises(ToolError):
            self.call(
                "record_animal_movement",
                tank_animal_id=stock.pk,
                direction="out",
                reason="transfer_out",
                quantity=2,
                target_tank_id=self.foreign_tank.pk,
            )

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 10)

    def test_a_foreign_stock_item_is_not_found(self):
        foreign = StubTankAnimal.objects.create(
            tank=self.foreign_tank, animal=self.guppy(), quantity=5
        )

        with self.assertRaises(ToolError):
            self.call(
                "record_animal_movement",
                tank_animal_id=foreign.pk,
                direction="out",
                reason="died",
                quantity=1,
            )

        foreign.refresh_from_db()
        self.assertEqual(foreign.quantity, 5)

    def test_an_unknown_reason_lists_the_possible_ones(self):
        stock = StubTankAnimal.objects.create(tank=self.tank, animal=self.guppy(), quantity=10)

        with self.assertRaises(ToolError) as caught:
            self.call(
                "record_animal_movement",
                tank_animal_id=stock.pk,
                direction="out",
                reason="ausgebüxt",
                quantity=1,
            )

        self.assertIn("died", str(caught.exception))


class ReadOnlyTokenTests(ToolTestCase):
    """Ein Token ohne Schreibrecht liest — und sonst nichts."""

    allow_write = False

    def test_reading_works(self):
        self.assertEqual(len(self.call("list_tanks")["tanks"]), 1)

    def test_writing_is_refused(self):
        with self.assertRaises(WriteNotAllowed):
            self.call("create_event", tank_id=self.tank.pk, title="Versuch")

        self.assertFalse(StubEvent.objects.exists())

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
        event = StubEvent.objects.get()
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


class ResultShapeTests(ToolTestCase):
    def test_every_result_survives_json(self):
        """Decimal und Datum überleben ``json.dumps`` nur umgewandelt."""
        StubTankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.5")
        )
        StubTankAnimal.objects.create(tank=self.tank, animal=self.guppy(), quantity=8)
        measurement = self.measurement()

        for name, arguments in [
            ("list_tanks", {}),
            ("get_tank", {"tank_id": self.tank.pk}),
            ("list_measurements", {}),
            ("get_measurement", {"measurement_id": measurement.pk}),
            ("list_events", {}),
            ("list_due_schedules", {}),
            ("search_catalog", {"query": "Poecilia"}),
        ]:
            with self.subTest(tool=name):
                json.dumps(call_tool(self.context, name, arguments), ensure_ascii=False)


class DataModelMissingTests(DataModelTestCase):
    """Ohne Becken-Modell antwortet jedes Werkzeug sauber statt zu stürzen."""

    def setUp(self):
        self.user = make_user("greta")
        self.token, _ = MCPToken.issue(self.user, "Claude Desktop", allow_write=True)

    def test_a_missing_model_becomes_a_tool_error(self):
        from services.mcp import Context
        from services.mcp.data import MODELS

        context = Context(token=self.token, user=self.user)
        original = dict(MODELS)
        MODELS["tank"] = ("tanks", "Tank")  # gibt es auf diesem Stand noch nicht
        try:
            with self.assertRaises(ToolError) as caught:
                call_tool(context, "list_tanks", {})
        finally:
            MODELS.clear()
            MODELS.update(original)

        self.assertIn("nicht verfügbar", str(caught.exception))


class UserModelTests(ToolTestCase):
    def test_the_token_owner_decides_what_is_visible(self):
        """Zur Sicherheit gegen die andere Richtung: derselbe Aufruf, anderer Token."""
        stranger_token, _ = MCPToken.issue(self.stranger, "Fremder Client")
        stranger_context = self._context(stranger_token)

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
        from services.mcp import registry

        for definition in registry.definitions(allow_write=True):
            with self.subTest(tool=definition["name"]):
                text = f"{definition['name']} {definition['description']}".lower()
                self.assertNotIn("gerät", text)
                self.assertNotIn("device", text)

    def test_the_model_map_knows_no_device(self):
        from services.mcp.data import MODELS

        self.assertEqual(
            [alias for alias, (_app, model) in MODELS.items() if "evice" in model], []
        )

    def test_get_tank_carries_no_device_data(self):
        result = self.call("get_tank", tank_id=self.tank.pk)

        payload = json.dumps(result, ensure_ascii=False).lower()
        self.assertNotIn("device", payload)
        self.assertNotIn("mac", payload)
