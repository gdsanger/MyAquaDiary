import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import (
    create_animal,
    create_measurement,
    create_tank,
    create_task,
    create_user,
    stock,
    tank_named,
)
from services.models import Device, DeviceReading
from tanks.models import TaskCompletion


class DashboardWarningTests(TestCase):
    """Fehlercode 1 oder 2 gehört auf das Dashboard — sofort und im Klartext."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            owner=self.user,
            tank=tank_named(self.user),
            name="Filter Becken 1",
            kind=Device.Kind.EHEIM_CLASSICVARIO,
            mac_address="AA:BB:CC:DD:EE:FF",
            host="192.168.1.50",
        )

    def add_reading(self, error_code, minutes_ago=0):
        DeviceReading.objects.create(
            device=self.device,
            read_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
            payload={},
            error_code=error_code,
        )

    def test_error_code_is_shown_in_plain_language(self):
        self.add_reading(1)
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, "Rotor blockiert")
        self.assertContains(response, reverse("services:device_detail", args=[self.device.pk]))

    def test_air_in_filter_is_shown(self):
        self.add_reading(2)
        self.assertContains(self.client.get(reverse("dashboard:index")), "Luft im Filter")

    def test_no_warning_when_the_last_reading_is_clean(self):
        self.add_reading(1, minutes_ago=30)
        self.add_reading(0)
        self.assertNotContains(self.client.get(reverse("dashboard:index")), "Störung")

    def test_dashboard_without_devices_stays_empty(self):
        self.assertNotContains(self.client.get(reverse("dashboard:index")), "Störung")

    def test_dashboard_does_not_query_devices_over_the_network(self):
        # Der Status kommt aus dem letzten Messwert; ein stummes Gerät darf das
        # Dashboard nicht aufhalten. Vier Abfragen: Session, Benutzer, Geräte
        # mit Störung und die KI-Konfiguration für die Navigation.
        self.add_reading(1)
        with self.assertNumQueries(4):
            self.client.get(reverse("dashboard:index"))


TILE_URLS = [
    "dashboard:tile-kpi",
    "dashboard:tile-tasks",
    "dashboard:tile-warnings",
    "dashboard:tile-activity",
    "dashboard:tile-chart",
]


class DashboardAccessTests(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_tiles_require_login(self):
        for name in TILE_URLS:
            with self.subTest(tile=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)


class DashboardShellTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def test_shell_only_contains_lazy_tiles(self):
        """Das Gerüst liefert keine Daten, sondern nur Nachlade-Platzhalter."""
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for name in TILE_URLS:
            self.assertIn(f'hx-get="{reverse(name)}"', content)
        self.assertIn('hx-trigger="load"', content)

    def test_each_tile_renders_independently(self):
        create_tank(self.user)
        for name in TILE_URLS:
            with self.subTest(tile=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                # Fragmente sind Ausschnitte, kein vollständiges Dokument.
                self.assertNotIn("<html", response.content.decode())


class KpiTileTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def test_counts_only_own_active_tanks(self):
        tank = create_tank(self.user, name="Becken A", slug="becken-a")
        create_tank(self.user, name="Alt", slug="alt", dissolved_on=timezone.localdate())
        create_tank(create_user("fremd"), name="Fremd", slug="fremd")
        stock(tank, create_animal(), quantity=12)

        response = self.client.get(reverse("dashboard:tile-kpi"))
        self.assertEqual(response.context["tank_count"], 1)
        self.assertEqual(response.context["animal_count"], 12)
        self.assertEqual(response.context["species_count"], 1)
        self.assertEqual(response.context["total_volume"], tank.volume_liters)


class TaskTileTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

    def test_lists_due_and_upcoming_tasks_only(self):
        create_task(self.tank, title="Überfällig", days_until_due=-3)
        create_task(self.tank, title="Heute", days_until_due=0)
        create_task(self.tank, title="Bald", days_until_due=5)
        create_task(self.tank, title="Später", days_until_due=90)

        response = self.client.get(reverse("dashboard:tile-tasks"))
        titles = [task.title for task in response.context["tasks"]]
        self.assertEqual(titles, ["Überfällig", "Heute", "Bald"])

    def test_tank_colour_marking_is_rendered(self):
        create_task(self.tank, days_until_due=0)
        response = self.client.get(reverse("dashboard:tile-tasks"))
        self.assertContains(response, self.tank.accent_class)
        self.assertContains(response, "mad-tank-dot")


class TaskCompletionTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.task = create_task(self.tank, days_until_due=-2, interval_days=7)
        self.url = reverse("dashboard:task-complete", args=[self.task.pk])

    def test_htmx_acknowledgement_returns_updated_tile(self):
        response = self.client.post(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "quittiert")
        self.assertNotIn("<html", response.content.decode())

    def test_acknowledgement_reschedules_recurring_task(self):
        today = timezone.localdate()
        self.client.post(self.url, HTTP_HX_REQUEST="true")
        self.task.refresh_from_db()
        self.assertEqual(self.task.last_completed_on, today)
        self.assertEqual(self.task.due_on, today + timedelta(days=7))
        self.assertTrue(self.task.is_active)
        self.assertEqual(TaskCompletion.objects.filter(task=self.task).count(), 1)

    def test_acknowledgement_closes_one_off_task(self):
        one_off = create_task(self.tank, title="Einmalig", days_until_due=0, interval_days=None)
        self.client.post(
            reverse("dashboard:task-complete", args=[one_off.pk]), HTTP_HX_REQUEST="true"
        )
        one_off.refresh_from_db()
        self.assertFalse(one_off.is_active)

    def test_without_htmx_it_redirects_to_the_dashboard(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("dashboard:index"))

    def test_foreign_tasks_cannot_be_acknowledged(self):
        stranger = create_user("fremd")
        foreign_task = create_task(create_tank(stranger, slug="fremd"), days_until_due=0)
        response = self.client.post(
            reverse("dashboard:task-complete", args=[foreign_task.pk]), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 404)
        foreign_task.refresh_from_db()
        self.assertIsNone(foreign_task.last_completed_on)

    def test_acknowledgement_needs_a_post(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)


class WarningTileTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

    def test_measurement_outside_target_range_is_reported(self):
        create_measurement(self.tank, "no2", "1.500")
        response = self.client.get(reverse("dashboard:tile-warnings"))
        titles = [warning["title"] for warning in response.context["warnings"]]
        self.assertIn("Nitrit außerhalb des Zielbereichs", titles)

    def test_measurement_inside_target_range_is_silent(self):
        create_measurement(self.tank, "ph", "7.0")
        response = self.client.get(reverse("dashboard:tile-warnings"))
        self.assertEqual(response.context["warnings"], [])
        self.assertContains(response, "keine Auffälligkeiten")

    def test_device_error_and_due_maintenance_are_reported(self):
        Device.objects.create(
            owner=self.user,
            tank=self.tank,
            name="Außenfilter",
            kind=Device.Kind.FILTER,
            status="critical",
            status_message="Keine Verbindung",
            maintenance_interval_days=30,
            last_maintenance_on=timezone.localdate() - timedelta(days=90),
        )
        response = self.client.get(reverse("dashboard:tile-warnings"))
        titles = [warning["title"] for warning in response.context["warnings"]]
        self.assertIn("Außenfilter: Kritisch", titles)
        self.assertIn("Wartung fällig: Außenfilter", titles)

    def test_warranty_running_out_is_reported(self):
        Device.objects.create(
            owner=self.user,
            tank=self.tank,
            name="Beleuchtung",
            kind=Device.Kind.LIGHT,
            warranty_until=timezone.localdate() + timedelta(days=10),
        )
        response = self.client.get(reverse("dashboard:tile-warnings"))
        titles = [warning["title"] for warning in response.context["warnings"]]
        self.assertIn("Garantie läuft ab: Beleuchtung", titles)

    def test_a_warranty_far_out_or_long_gone_is_silent(self):
        Device.objects.create(
            owner=self.user,
            tank=self.tank,
            name="Neuer Filter",
            kind=Device.Kind.FILTER,
            warranty_until=timezone.localdate() + timedelta(days=200),
        )
        Device.objects.create(
            owner=self.user,
            tank=self.tank,
            name="Alter Heizer",
            kind=Device.Kind.HEATER,
            warranty_until=timezone.localdate() - timedelta(days=1),
        )
        response = self.client.get(reverse("dashboard:tile-warnings"))
        self.assertEqual(response.context["warnings"], [])

    def test_group_size_below_minimum_is_reported(self):
        stock(self.tank, create_animal(), quantity=4)
        response = self.client.get(reverse("dashboard:tile-warnings"))
        titles = [warning["title"] for warning in response.context["warnings"]]
        self.assertIn("Gruppengröße unterschritten: Neonsalmler", titles)

    def test_critical_warnings_come_first(self):
        create_measurement(self.tank, "no2", "1.500")
        stock(self.tank, create_animal(), quantity=4)
        response = self.client.get(reverse("dashboard:tile-warnings"))
        statuses = [warning["status"] for warning in response.context["warnings"]]
        self.assertEqual(statuses[0], "critical")


class ChartTileTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def test_chart_uses_last_visited_tank(self):
        first = create_tank(self.user, name="Erstes", slug="erstes")
        second = create_tank(self.user, name="Zweites", slug="zweites")
        create_measurement(first, "ph", "7.0", days_ago=1)
        create_measurement(second, "ph", "7.2", days_ago=2)

        self.client.get(second.get_absolute_url())
        response = self.client.get(reverse("dashboard:tile-chart"))
        self.assertEqual(response.context["tank"], second)

    def test_chart_falls_back_to_most_recently_measured_tank(self):
        older = create_tank(self.user, name="Älter", slug="aelter")
        newer = create_tank(self.user, name="Neuer", slug="neuer")
        create_measurement(older, "ph", "7.0", days_ago=20)
        create_measurement(newer, "ph", "7.1", days_ago=1)

        response = self.client.get(reverse("dashboard:tile-chart"))
        self.assertEqual(response.context["tank"], newer)

    def test_chart_renders_svg_with_accessible_description(self):
        tank = create_tank(self.user)
        for day in range(5):
            create_measurement(tank, "ph", f"7.{day}", days_ago=day)
        response = self.client.get(reverse("dashboard:tile-chart"))
        self.assertContains(response, "<svg")
        self.assertContains(response, 'role="img"')
        self.assertContains(response, "Verlauf pH-Wert")

    def test_svg_coordinates_are_not_localised(self):
        """Die deutsche Lokalisierung würde „12,34" schreiben und das SVG
        damit unbrauchbar machen."""
        tank = create_tank(self.user)
        for day in range(4):
            create_measurement(tank, "ph", f"7.{day}", days_ago=day)
        content = self.client.get(reverse("dashboard:tile-chart")).content.decode()
        svg = re.search(r"<svg.*?</svg>", content, re.DOTALL).group()
        for attribute in re.findall(r'\b(?:cx|cy|x1|y1|x2|y2|width|height)="([^"]+)"', svg):
            with self.subTest(value=attribute):
                self.assertNotIn(",", attribute)
                float(attribute)

    def test_chart_tile_survives_without_any_tank(self):
        response = self.client.get(reverse("dashboard:tile-chart"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Noch kein Becken angelegt")


class ActivityTileTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def test_activity_is_sorted_newest_first(self):
        tank = create_tank(self.user)
        create_measurement(tank, "ph", "7.0", days_ago=5)
        create_measurement(tank, "no3", "10", days_ago=1)
        response = self.client.get(reverse("dashboard:tile-activity"))
        timestamps = [entry["timestamp"] for entry in response.context["entries"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_activity_excludes_other_users(self):
        create_measurement(create_tank(create_user("fremd"), slug="fremd"), "ph", "7.0")
        response = self.client.get(reverse("dashboard:tile-activity"))
        self.assertEqual(response.context["entries"], [])


class CareTaskModelTests(TestCase):
    def setUp(self):
        self.tank = create_tank(create_user())

    def test_next_due_date_is_counted_from_the_completion_day(self):
        """Sonst häufen sich bei einem lange liegen gebliebenen Termin sofort
        mehrere neue Fälligkeiten an."""
        task = create_task(self.tank, days_until_due=-40, interval_days=7)
        task.complete()
        self.assertEqual(task.due_on, timezone.localdate() + timedelta(days=7))

    def test_due_label_wording(self):
        self.assertEqual(create_task(self.tank, days_until_due=0).due_label, "heute fällig")
        self.assertEqual(create_task(self.tank, days_until_due=1).due_label, "morgen fällig")
        self.assertEqual(create_task(self.tank, days_until_due=-1).due_label, "1 Tag überfällig")
        self.assertEqual(create_task(self.tank, days_until_due=-3).due_label, "3 Tage überfällig")

    def test_status_scale(self):
        self.assertEqual(create_task(self.tank, days_until_due=-1).status_value, "critical")
        self.assertEqual(create_task(self.tank, days_until_due=0).status_value, "warn")
        self.assertEqual(create_task(self.tank, days_until_due=9).status_value, "unknown")
