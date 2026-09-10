import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from PIL import Image

from catalog.models import AnimalSpecies
from core.enums import Status, WaterType
from core.images import GPS_IFD
from core.testing import (
    create_animal,
    create_measurement,
    create_plant,
    create_tank,
    create_task,
    create_user,
    image_upload,
    photo_upload,
    stock,
)
from tanks import derived, selectors, transfers
from tanks.charts import derived_charts, parameter_series
from tanks.models import (
    BOTANICALS_DAYS,
    NUTRIENT_DEPOT_DAYS,
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
    TankPhoto,
    Transfer,
    classify_below_detection,
    classify_value,
)
from tanks.views import TAB_KEYS


class ClassifyValueTests(TestCase):
    """Statuslogik: grün im Zielbereich, orange knapp daneben, rot deutlich."""

    def test_inside_range_is_ok(self):
        self.assertEqual(classify_value(Decimal("7.0"), Decimal("6.5"), Decimal("7.5")), Status.OK)

    def test_slightly_outside_range_is_a_warning(self):
        # Bereichsbreite 1.0, Toleranz 0.2 — 7.6 liegt 0.1 daneben.
        self.assertEqual(
            classify_value(Decimal("7.6"), Decimal("6.5"), Decimal("7.5")), Status.WARN
        )

    def test_clearly_outside_range_is_critical(self):
        self.assertEqual(
            classify_value(Decimal("8.5"), Decimal("6.5"), Decimal("7.5")), Status.CRITICAL
        )

    def test_open_ended_range_uses_the_known_bound(self):
        self.assertEqual(classify_value(Decimal("0.05"), None, Decimal("0.10")), Status.OK)
        self.assertEqual(classify_value(Decimal("0.11"), None, Decimal("0.10")), Status.WARN)
        self.assertEqual(classify_value(Decimal("1.50"), None, Decimal("0.10")), Status.CRITICAL)

    def test_without_a_target_range_no_statement_is_made(self):
        self.assertEqual(classify_value(Decimal("7.0"), None, None), Status.UNKNOWN)


class TankModelTests(TestCase):
    def setUp(self):
        self.user = create_user()

    def test_accent_class_refers_to_the_stylesheet(self):
        tank = create_tank(self.user, accent=5)
        self.assertEqual(tank.accent_class, "mad-tank-accent-5")

    def test_age_display(self):
        today = timezone.localdate()
        self.assertEqual(create_tank(self.user, slug="a", setup_date=today).age_display, "0 Tage")
        self.assertEqual(
            create_tank(self.user, slug="b", setup_date=today - timedelta(days=200)).age_display,
            "6 Monate",
        )
        self.assertEqual(
            create_tank(self.user, slug="c", setup_date=today - timedelta(days=1200)).age_display,
            "3 Jahre",
        )

    def test_dissolved_tank_age_stops_at_the_dissolution_date(self):
        today = timezone.localdate()
        tank = create_tank(
            self.user,
            setup_date=today - timedelta(days=800),
            dissolved_on=today - timedelta(days=400),
        )
        self.assertTrue(tank.is_dissolved)
        self.assertEqual(tank.age.days, 400)

    def test_overview_counts_are_computed_without_cross_joins(self):
        """Mehrere Zähler über verschiedene Beziehungen dürfen sich nicht
        gegenseitig multiplizieren."""
        tank = create_tank(self.user)
        stock(tank, create_animal(), quantity=10)
        stock(tank, create_animal("Corydoras paleatus", common_name="Panzerwels"), quantity=6)
        Planting.objects.create(
            tank=tank, species=create_plant(), quantity=4, planted_on=timezone.localdate()
        )
        create_task(tank, days_until_due=-1)
        create_measurement(tank, "ph", "7.0")

        overview = Tank.objects.for_user(self.user).with_overview().get(pk=tank.pk)
        self.assertEqual(overview.animal_count, 16)
        self.assertEqual(overview.plant_count, 4)
        self.assertEqual(overview.open_task_count, 1)
        self.assertIsNotNone(overview.last_measured_at)


class TankListViewTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("tanks:list"))
        self.assertEqual(response.status_code, 302)

    def test_dissolved_tanks_are_listed_separately(self):
        active = create_tank(self.user, name="Aktiv", slug="aktiv")
        dissolved = create_tank(
            self.user, name="Aufgelöst", slug="aufgeloest", dissolved_on=timezone.localdate()
        )
        response = self.client.get(reverse("tanks:list"))
        self.assertEqual(list(response.context["active_tanks"]), [active])
        self.assertEqual(list(response.context["dissolved_tanks"]), [dissolved])
        self.assertContains(response, "Aufgelöste Becken")

    def test_other_users_tanks_are_invisible(self):
        create_tank(create_user("fremd"), name="Fremdbecken", slug="fremd")
        response = self.client.get(reverse("tanks:list"))
        self.assertNotContains(response, "Fremdbecken")

    def test_card_shows_the_required_facts(self):
        tank = create_tank(self.user)
        stock(tank, create_animal(), quantity=12)
        create_measurement(tank, "ph", "7.0")
        response = self.client.get(reverse("tanks:list"))
        self.assertContains(response, tank.name)
        self.assertContains(response, "240 l")
        self.assertContains(response, "Letzte Messung")
        self.assertContains(response, tank.accent_class)


class TankDetailViewTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

    def test_all_tabs_render(self):
        for tab in TAB_KEYS:
            with self.subTest(tab=tab):
                response = self.client.get(f"{self.tank.get_absolute_url()}?reiter={tab}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["active_tab"], tab)

    def test_unknown_tab_falls_back_to_the_overview(self):
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=gibtsnicht")
        self.assertEqual(response.context["active_tab"], "uebersicht")

    def test_tab_fragment_is_a_partial(self):
        url = reverse("tanks:tab", args=[self.tank.slug, "messwerte"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("<html", content)
        self.assertIn('id="tab-area"', content)

    def test_unknown_tab_fragment_is_not_found(self):
        response = self.client.get(reverse("tanks:tab", args=[self.tank.slug, "unsinn"]))
        self.assertEqual(response.status_code, 404)

    def test_foreign_tank_is_not_reachable(self):
        foreign = create_tank(create_user("fremd"), slug="fremd")
        self.assertEqual(self.client.get(foreign.get_absolute_url()).status_code, 404)

    def test_visiting_the_detail_remembers_the_tank(self):
        self.client.get(self.tank.get_absolute_url())
        self.assertEqual(self.client.session["last_tank_id"], self.tank.pk)

    def test_measurement_tab_shows_target_range_and_status(self):
        create_measurement(self.tank, "no2", "1.500")
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=messwerte")
        self.assertContains(response, "mad-status--critical")
        self.assertContains(response, "bis 0,10 mg/l")

    def test_measurement_tab_shows_nn_not_zero(self):
        create_measurement(self.tank, "no2", below_detection=True)
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=messwerte")
        self.assertContains(response, "n.n.")


class ChartTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        self.parameter = Parameter.objects.get(key="ph")

    def test_without_measurements_there_is_no_series(self):
        self.assertIsNone(parameter_series(self.tank, self.parameter))

    def test_coordinates_stay_inside_the_view_box(self):
        for day in range(6):
            create_measurement(self.tank, "ph", f"6.{day}", days_ago=day)
        series = parameter_series(self.tank, self.parameter)
        for point in series["points"]:
            self.assertGreaterEqual(point["x"], 0)
            self.assertLessEqual(point["x"], series["view_width"])
            self.assertGreaterEqual(point["y"], 0)
            self.assertLessEqual(point["y"], series["view_height"])

    def test_points_are_ordered_chronologically(self):
        for day in (10, 5, 1):
            create_measurement(self.tank, "ph", "7.0", days_ago=day)
        series = parameter_series(self.tank, self.parameter)
        x_values = [point["x"] for point in series["points"]]
        self.assertEqual(x_values, sorted(x_values))

    def test_a_single_measurement_is_centred(self):
        create_measurement(self.tank, "ph", "7.0")
        series = parameter_series(self.tank, self.parameter)
        self.assertEqual(series["points"][0]["x"], series["view_width"] / 2)

    def test_higher_values_sit_higher_in_the_chart(self):
        create_measurement(self.tank, "ph", "6.0", days_ago=2)
        create_measurement(self.tank, "ph", "8.0", days_ago=1)
        low, high = parameter_series(self.tank, self.parameter)["points"]
        self.assertGreater(low["y"], high["y"])

    def test_target_band_covers_the_target_range(self):
        create_measurement(self.tank, "ph", "7.0")
        series = parameter_series(self.tank, self.parameter)
        self.assertIsNotNone(series["band"])
        self.assertGreater(series["band"]["height"], 0)
        self.assertEqual(series["target_label"], "6,5–7,5")

    def test_below_detection_is_no_point_at_zero_but_its_own_marker(self):
        no2 = Parameter.objects.get(key="no2")
        create_measurement(self.tank, "no2", days_ago=2, below_detection=True)
        create_measurement(self.tank, "no2", "0.05", days_ago=1)
        series = parameter_series(self.tank, no2)
        # Die Zahl steht als Punkt, das n.n. als eigener Marker — kein Punkt bei 0.
        self.assertEqual(len(series["points"]), 1)
        self.assertEqual(len(series["nn_points"]), 1)

    def test_a_series_of_only_nn_still_renders(self):
        no2 = Parameter.objects.get(key="no2")
        create_measurement(self.tank, "no2", below_detection=True)
        series = parameter_series(self.tank, no2)
        self.assertIsNotNone(series)
        self.assertEqual(series["points"], [])
        self.assertEqual(len(series["nn_points"]), 1)
        self.assertIn("n.n.", series["summary"])


class MeasurementModelTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)

    def test_below_detection_carries_no_value(self):
        measurement = create_measurement(self.tank, "no2", below_detection=True)
        self.assertIsNone(measurement.value)
        self.assertEqual(measurement.display_value, "n.n.")

    def test_a_value_and_below_detection_together_are_invalid(self):
        measurement = Measurement(
            tank=self.tank,
            parameter=Parameter.objects.get(key="no2"),
            value=Decimal("0.05"),
            below_detection=True,
            measured_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            measurement.full_clean()

    def test_neither_a_value_nor_below_detection_is_invalid(self):
        measurement = Measurement(
            tank=self.tank,
            parameter=Parameter.objects.get(key="no2"),
            measured_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            measurement.full_clean()

    def test_below_detection_needs_a_parameter_with_a_detection_limit(self):
        measurement = Measurement(
            tank=self.tank,
            parameter=Parameter.objects.get(key="ph"),
            below_detection=True,
            measured_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            measurement.full_clean()

    def test_nn_counts_as_in_order_for_a_pollutant_with_only_an_upper_limit(self):
        # Nitrit: nur eine Obergrenze — n.n. hält sie zwangsläufig ein.
        measurement = create_measurement(self.tank, "no2", below_detection=True)
        self.assertEqual(measurement.status(), Status.OK)

    def test_nn_below_a_target_minimum_is_flagged_as_low(self):
        # Phosphat als Nährstoff hat eine Untergrenze; die Nachweisgrenze liegt
        # darunter, n.n. ist damit erkennbar zu niedrig.
        measurement = create_measurement(self.tank, "po4", below_detection=True)
        self.assertIn(measurement.status(), (Status.WARN, Status.CRITICAL))

    def test_classify_below_detection_without_a_target_is_unknown(self):
        self.assertEqual(classify_below_detection(None, None), Status.UNKNOWN)


class TankScopeTests(TestCase):
    """Fremde Daten gibt es nicht — auch nicht als 403.

    Ein 403 wäre die Auskunft „diesen Datensatz gibt es, er gehört nur jemand
    anderem". Genau die soll niemand bekommen.
    """

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

        self.stranger = create_user("fremd")
        self.foreign_tank = create_tank(self.stranger, name="Fremdbecken", slug="fremdbecken")
        self.foreign_measurement = create_measurement(self.foreign_tank)
        self.foreign_event = Event.objects.create(
            tank=self.foreign_tank, title="Fremd", occurred_at=timezone.now()
        )
        self.foreign_task = create_task(self.foreign_tank)
        self.foreign_layer = SubstrateLayer.objects.create(
            tank=self.foreign_tank, kind=SubstrateLayer.Kind.SAND
        )
        self.foreign_hardscape = HardscapeItem.objects.create(
            tank=self.foreign_tank, kind=HardscapeItem.Kind.WOOD, name="Fremde Wurzel"
        )

    def foreign_slug_urls(self):
        slug = self.foreign_tank.slug
        return [
            reverse("tanks:update", args=[slug]),
            reverse("tanks:dissolve", args=[slug]),
            reverse("tanks:delete", args=[slug]),
            reverse("tanks:measurement-create", args=[slug]),
            reverse("tanks:event-create", args=[slug]),
            reverse("tanks:stocking-create", args=[slug]),
            reverse("tanks:planting-create", args=[slug]),
            reverse("tanks:task-create", args=[slug]),
            reverse("tanks:target-create", args=[slug]),
            reverse("tanks:photo-create", args=[slug]),
            reverse("tanks:substrate-create", args=[slug]),
            reverse("tanks:hardscape-create", args=[slug]),
        ]

    def test_foreign_slug_is_not_found(self):
        for url in self.foreign_slug_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, {}).status_code, 404)

    def test_foreign_pk_under_the_own_slug_is_not_found(self):
        """Der Slug gehört mir, die pk nicht — auch das ist ein 404."""
        urls = [
            reverse("tanks:measurement-update", args=[self.tank.slug, self.foreign_measurement.pk]),
            reverse("tanks:measurement-delete", args=[self.tank.slug, self.foreign_measurement.pk]),
            reverse("tanks:event-update", args=[self.tank.slug, self.foreign_event.pk]),
            reverse("tanks:event-delete", args=[self.tank.slug, self.foreign_event.pk]),
            reverse("tanks:task-update", args=[self.tank.slug, self.foreign_task.pk]),
            reverse("tanks:task-toggle", args=[self.tank.slug, self.foreign_task.pk]),
            reverse("tanks:substrate-update", args=[self.tank.slug, self.foreign_layer.pk]),
            reverse("tanks:substrate-delete", args=[self.tank.slug, self.foreign_layer.pk]),
            reverse("tanks:substrate-reminder", args=[self.tank.slug, self.foreign_layer.pk]),
            reverse("tanks:hardscape-update", args=[self.tank.slug, self.foreign_hardscape.pk]),
            reverse("tanks:hardscape-remove", args=[self.tank.slug, self.foreign_hardscape.pk]),
            reverse("tanks:hardscape-reminder", args=[self.tank.slug, self.foreign_hardscape.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, {}).status_code, 404)

        # Das Verschieben einer Schicht gibt es nur als POST.
        move = reverse("tanks:substrate-move", args=[self.tank.slug, self.foreign_layer.pk])
        self.assertEqual(self.client.post(move, {}).status_code, 404)

    def test_foreign_data_survives_the_attempt(self):
        self.client.post(
            reverse("tanks:event-delete", args=[self.tank.slug, self.foreign_event.pk])
        )
        self.assertTrue(Event.objects.filter(pk=self.foreign_event.pk).exists())

    def test_anonymous_is_sent_to_the_login(self):
        self.client.logout()
        response = self.client.get(reverse("tanks:measurement-create", args=[self.tank.slug]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class TankWriteViewTests(TestCase):
    """Becken anlegen, bearbeiten, auflösen, löschen."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def payload(self, **overrides):
        data = {
            "name": "Nano am Fenster",
            "water_type": WaterType.FRESHWATER,
            "volume_liters": "54",
            "setup_date": timezone.localdate().isoformat(),
            "accent": "2",
            "notes": "",
            "location": "",
        }
        data.update(overrides)
        return data

    def test_create_derives_the_slug_and_the_owner(self):
        response = self.client.post(reverse("tanks:create"), self.payload())
        tank = Tank.objects.get(name="Nano am Fenster")
        self.assertEqual(tank.owner, self.user)
        self.assertEqual(tank.slug, "nano-am-fenster")
        self.assertRedirects(response, tank.get_absolute_url())

    def test_second_tank_with_the_same_name_gets_its_own_address(self):
        self.client.post(reverse("tanks:create"), self.payload())
        self.client.post(reverse("tanks:create"), self.payload())
        self.assertEqual(
            sorted(Tank.objects.values_list("slug", flat=True)),
            ["nano-am-fenster", "nano-am-fenster-2"],
        )

    def test_another_owner_may_use_the_same_address(self):
        create_tank(create_user("fremd"), name="Nano am Fenster", slug="nano-am-fenster")
        self.client.post(reverse("tanks:create"), self.payload())
        self.assertEqual(Tank.objects.filter(owner=self.user).get().slug, "nano-am-fenster")

    def test_invalid_form_does_not_create_anything(self):
        response = self.client.post(reverse("tanks:create"), self.payload(volume_liters=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Tank.objects.exists())

    def test_update_changes_the_facts(self):
        tank = create_tank(self.user)
        self.client.post(
            reverse("tanks:update", args=[tank.slug]),
            self.payload(name=tank.name, volume_liters="300"),
        )
        tank.refresh_from_db()
        self.assertEqual(tank.volume_liters, Decimal("300.0"))

    def test_renaming_keeps_the_address(self):
        """Ein Lesezeichen auf das eigene Becken überlebt eine Namenskorrektur."""
        tank = create_tank(self.user)
        self.client.post(
            reverse("tanks:update", args=[tank.slug]), self.payload(name="Neuer Name")
        )
        tank.refresh_from_db()
        self.assertEqual(tank.name, "Neuer Name")
        self.assertEqual(tank.slug, "gesellschaftsbecken")

    def test_dissolving_keeps_the_history(self):
        tank = create_tank(self.user)
        create_measurement(tank)
        today = timezone.localdate()
        self.client.post(
            reverse("tanks:dissolve", args=[tank.slug]), {"dissolved_on": today.isoformat()}
        )
        tank.refresh_from_db()
        self.assertEqual(tank.dissolved_on, today)
        self.assertEqual(tank.measurements.count(), 1)

    def test_dissolution_cannot_precede_the_setup(self):
        tank = create_tank(self.user)
        before = (tank.setup_date - timedelta(days=1)).isoformat()
        self.client.post(reverse("tanks:dissolve", args=[tank.slug]), {"dissolved_on": before})
        tank.refresh_from_db()
        self.assertIsNone(tank.dissolved_on)

    def test_a_dissolved_tank_can_be_put_back_into_service(self):
        tank = create_tank(self.user, dissolved_on=timezone.localdate())
        self.client.post(reverse("tanks:dissolve", args=[tank.slug]), {"reaktivieren": "1"})
        tank.refresh_from_db()
        self.assertIsNone(tank.dissolved_on)

    def test_an_empty_tank_can_be_deleted(self):
        tank = create_tank(self.user)
        response = self.client.post(reverse("tanks:delete", args=[tank.slug]))
        self.assertRedirects(response, reverse("tanks:list"))
        self.assertFalse(Tank.objects.exists())

    def test_a_tank_with_history_is_dissolved_instead(self):
        tank = create_tank(self.user)
        create_measurement(tank)
        response = self.client.post(reverse("tanks:delete", args=[tank.slug]))
        self.assertRedirects(response, reverse("tanks:dissolve", args=[tank.slug]))
        self.assertTrue(Tank.objects.filter(pk=tank.pk).exists())

    def test_the_confirmation_page_offers_the_dissolution(self):
        tank = create_tank(self.user)
        create_measurement(tank)
        response = self.client.get(reverse("tanks:delete", args=[tank.slug]))
        self.assertTrue(response.context["has_history"])
        self.assertContains(response, reverse("tanks:dissolve", args=[tank.slug]))


class MeasurementWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.ph = Parameter.objects.get(key="ph")
        self.no2 = Parameter.objects.get(key="no2")
        self.url = reverse("tanks:measurement-create", args=[self.tank.slug])

    def field(self, parameter):
        return f"parameter_{parameter.pk}"

    def test_a_series_is_recorded_in_one_go(self):
        moment = timezone.localtime() - timedelta(days=3)
        self.client.post(
            self.url,
            {
                "measured_at": moment.strftime("%Y-%m-%dT%H:%M"),
                self.field(self.ph): "7,2",
                self.field(self.no2): "n.n.",
                "note": "Tröpfchentest",
            },
        )
        by_key = {m.parameter.key: m for m in self.tank.measurements.all()}
        self.assertEqual(by_key["ph"].value, Decimal("7.200"))
        self.assertFalse(by_key["ph"].below_detection)
        # „n.n.“ wird nicht als 0 abgelegt, sondern als „nicht nachweisbar“.
        self.assertIsNone(by_key["no2"].value)
        self.assertTrue(by_key["no2"].below_detection)

    def test_nn_via_the_switch_is_below_detection(self):
        self.client.post(
            self.url,
            {
                "measured_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                f"{self.field(self.no2)}_nn": "on",
            },
        )
        measurement = self.tank.measurements.get()
        self.assertIsNone(measurement.value)
        self.assertTrue(measurement.below_detection)

    def test_a_value_and_nn_together_are_rejected(self):
        response = self.client.post(
            self.url,
            {
                "measured_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                self.field(self.no2): "0,05",
                f"{self.field(self.no2)}_nn": "on",
            },
        )
        self.assertFalse(self.tank.measurements.exists())
        self.assertContains(response, "nicht beides")

    def test_nn_without_a_detection_limit_is_rejected(self):
        response = self.client.post(
            self.url,
            {
                "measured_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                self.field(self.ph): "n.n.",
            },
        )
        self.assertFalse(self.tank.measurements.exists())
        self.assertContains(response, "keine Nachweisgrenze")

    def test_historic_timestamps_are_kept(self):
        moment = timezone.localtime() - timedelta(days=30)
        self.client.post(
            self.url,
            {"measured_at": moment.strftime("%Y-%m-%dT%H:%M"), self.field(self.ph): "7,0"},
        )
        measured_at = self.tank.measurements.get().measured_at
        self.assertEqual(timezone.localtime(measured_at).date(), moment.date())

    def test_the_author_is_remembered(self):
        self.client.post(
            self.url,
            {
                "measured_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                self.field(self.ph): "7,0",
            },
        )
        self.assertEqual(self.tank.measurements.get().created_by, self.user)

    def test_a_series_without_a_single_value_is_rejected(self):
        response = self.client.post(
            self.url, {"measured_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M")}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.tank.measurements.exists())
        self.assertContains(response, "mindestens einen Wert")

    def test_the_future_is_no_measurement(self):
        moment = timezone.localtime() + timedelta(days=2)
        response = self.client.post(
            self.url,
            {"measured_at": moment.strftime("%Y-%m-%dT%H:%M"), self.field(self.ph): "7,0"},
        )
        self.assertFalse(self.tank.measurements.exists())
        self.assertContains(response, "Zukunft")

    def test_htmx_gets_the_refreshed_tab_and_no_page(self):
        response = self.client.post(
            self.url,
            {
                "measured_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                self.field(self.ph): "7,0",
            },
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        self.assertNotIn("<html", content)
        self.assertIn('id="tab-area"', content)
        self.assertEqual(response.context["active_tab"], "messwerte")

    def test_the_form_loads_as_a_fragment(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertNotIn("<html", response.content.decode())
        self.assertContains(response, "Messreihe erfassen")
        self.assertContains(response, "n.n.")

    def test_without_htmx_the_whole_page_comes_back(self):
        response = self.client.get(self.url)
        self.assertContains(response, "<html")
        self.assertContains(response, "Messreihe erfassen")

    def test_a_single_value_can_be_corrected(self):
        measurement = create_measurement(self.tank, "ph", "7.0")
        self.client.post(
            reverse("tanks:measurement-update", args=[self.tank.slug, measurement.pk]),
            {
                "parameter": self.ph.pk,
                "value": "6,8",
                "measured_at": timezone.localtime(measurement.measured_at).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "note": "",
            },
        )
        measurement.refresh_from_db()
        self.assertEqual(measurement.value, Decimal("6.800"))

    def test_deleting_asks_first(self):
        measurement = create_measurement(self.tank)
        url = reverse("tanks:measurement-delete", args=[self.tank.slug, measurement.pk])
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Messwert löschen")
        self.assertTrue(Measurement.objects.filter(pk=measurement.pk).exists())

        self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertFalse(Measurement.objects.filter(pk=measurement.pk).exists())


class EventWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

    def test_event_is_recorded(self):
        self.client.post(
            reverse("tanks:event-create", args=[self.tank.slug]),
            {
                "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "category": Event.Category.WATER_CHANGE,
                "title": "50 % Wasserwechsel",
                "description": "",
            },
        )
        self.assertEqual(self.tank.events.get().title, "50 % Wasserwechsel")

    def test_event_is_edited_and_deleted(self):
        event = Event.objects.create(
            tank=self.tank, title="Alt", occurred_at=timezone.now(), category=Event.Category.OTHER
        )
        self.client.post(
            reverse("tanks:event-update", args=[self.tank.slug, event.pk]),
            {
                "occurred_at": timezone.localtime(event.occurred_at).strftime("%Y-%m-%dT%H:%M"),
                "category": Event.Category.INCIDENT,
                "title": "Neu",
                "description": "",
            },
        )
        event.refresh_from_db()
        self.assertEqual(event.title, "Neu")

        self.client.post(reverse("tanks:event-delete", args=[self.tank.slug, event.pk]))
        self.assertFalse(Event.objects.exists())

    def test_the_category_named_in_the_mcp_schema_exists(self):
        """``services.mcp.write`` nennt ``observation`` als Beispiel.

        Fehlte sie im Modell, verwürfe ``arguments.choice()`` den Wert still
        und legte ein „Sonstiges“ an — Beschreibung und Modell liefen
        auseinander, ohne dass es jemand merkt.
        """
        self.assertIn("observation", Event.Category.values)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ObservationWriteTests(TestCase):
    """Beobachtung erfassen: Kategorie, Bilder und Zeitpunkt in einem Vorgang."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

    def record(self, **extra):
        payload = {
            "occurred_at": "",
            "category": Event.Category.OBSERVATION,
            "title": "Cryptocoryne schiebt neue Blätter",
            "description": "",
        }
        payload.update(extra)
        return self.client.post(
            reverse("tanks:observation-create", args=[self.tank.slug]), payload
        )

    def test_the_form_offers_the_category_and_the_images(self):
        response = self.client.get(reverse("tanks:observation-create", args=[self.tank.slug]))
        self.assertContains(response, 'value="observation" selected')
        self.assertContains(response, 'name="images"')
        self.assertContains(response, "multipart/form-data")

    def test_observation_and_images_are_recorded_in_one_step(self):
        self.record(images=[photo_upload("eins.jpg"), photo_upload("zwei.jpg")])

        event = self.tank.events.get()
        self.assertEqual(event.category, Event.Category.OBSERVATION)
        self.assertEqual(event.photos.count(), 2)
        # Die Fotos hängen weiterhin am Becken und stehen damit in der Galerie.
        self.assertEqual(self.tank.photos.count(), 2)

    def test_the_shot_time_comes_from_the_exif(self):
        shot = (timezone.localtime() - timedelta(days=3)).replace(microsecond=0)
        self.record(images=[photo_upload("alt.jpg", taken_at=shot.replace(tzinfo=None))])

        photo = self.tank.photos.get()
        self.assertEqual(photo.taken_on, shot.date())
        # Ohne eigene Angabe leitet sich der Zeitpunkt des Ereignisses daraus ab.
        self.assertEqual(timezone.localtime(photo.event.occurred_at), shot)

    def test_the_earliest_shot_wins_for_the_event(self):
        early = (timezone.localtime() - timedelta(days=5)).replace(microsecond=0)
        late = (timezone.localtime() - timedelta(days=1)).replace(microsecond=0)
        self.record(
            images=[
                photo_upload("spaet.jpg", taken_at=late.replace(tzinfo=None)),
                photo_upload("frueh.jpg", taken_at=early.replace(tzinfo=None)),
            ]
        )

        self.assertEqual(timezone.localtime(self.tank.events.get().occurred_at), early)

    def test_without_exif_the_date_comes_from_the_event(self):
        moment = (timezone.localtime() - timedelta(days=2)).replace(microsecond=0)
        self.record(occurred_at=moment.strftime("%Y-%m-%dT%H:%M"), images=[photo_upload()])

        self.assertEqual(self.tank.photos.get().taken_on, moment.date())

    def test_a_camera_clock_in_the_future_is_ignored(self):
        """Eine falsch gestellte Kameradatum soll kein Foto in der Zukunft ablegen."""
        future = (timezone.localtime() + timedelta(days=30)).replace(tzinfo=None)
        before = timezone.now()

        self.record(images=[photo_upload("zukunft.jpg", taken_at=future)])

        self.assertGreaterEqual(self.tank.events.get().occurred_at, before)
        self.assertEqual(self.tank.photos.get().taken_on, timezone.localdate())

    def test_without_images_and_without_a_time_it_is_now(self):
        before = timezone.now()
        self.record()

        self.assertGreaterEqual(self.tank.events.get().occurred_at, before)

    def test_a_plain_event_still_takes_images(self):
        """Die Bilder hängen am Ereignis, nicht an der Kategorie."""
        self.client.post(
            reverse("tanks:event-create", args=[self.tank.slug]),
            {
                "occurred_at": "",
                "category": Event.Category.TREATMENT,
                "title": "Zweite Gabe",
                "description": "",
                "images": [photo_upload("verlauf.jpg")],
            },
        )

        self.assertEqual(self.tank.events.get().photos.count(), 1)

    def test_the_event_list_shows_the_previews(self):
        self.record(images=[photo_upload("vorschau.jpg")])

        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=ereignisse")
        photo = self.tank.photos.get()
        self.assertContains(response, "mad-thumbs")
        # Die Liste lädt die Kachel; das Original hängt nur am Verweis dahinter.
        self.assertContains(response, photo.thumbnail.url)
        self.assertNotContains(response, photo.image.url)

    def test_the_gallery_names_the_event(self):
        self.record(images=[photo_upload("galerie.jpg")])

        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=galerie")
        self.assertContains(response, "Cryptocoryne schiebt neue Blätter")

    def test_deleting_the_event_keeps_the_photos(self):
        self.record(images=[photo_upload("bleibt.jpg")])
        event = self.tank.events.get()

        self.client.post(reverse("tanks:event-delete", args=[self.tank.slug, event.pk]))

        photo = self.tank.photos.get()
        self.assertIsNone(photo.event_id)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhotoAssignmentTests(TestCase):
    """Nachträgliche Zuordnung eines vorhandenen Fotos zu einem Ereignis."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.photo = TankPhoto.objects.create(
            tank=self.tank, image=image_upload(), taken_on=timezone.localdate()
        )
        self.event = Event.objects.create(
            tank=self.tank,
            title="Trübung nach dem Aufwirbeln",
            occurred_at=timezone.now(),
            category=Event.Category.INCIDENT,
        )

    def assign(self, event_pk):
        return self.client.post(
            reverse("tanks:photo-update", args=[self.tank.slug, self.photo.pk]),
            {
                "caption": "",
                "taken_on": self.photo.taken_on.isoformat(),
                "event": event_pk,
            },
        )

    def test_an_existing_photo_finds_its_event(self):
        self.assign(self.event.pk)

        self.photo.refresh_from_db()
        self.assertEqual(self.photo.event_id, self.event.pk)

    def test_the_assignment_can_be_undone(self):
        self.photo.event = self.event
        self.photo.save(update_fields=["event"])

        self.assign("")

        self.photo.refresh_from_db()
        self.assertIsNone(self.photo.event_id)

    def test_only_events_of_the_same_tank_are_offered(self):
        other = create_tank(self.user, name="Nano", slug="nano")
        elsewhere = Event.objects.create(
            tank=other, title="Woanders", occurred_at=timezone.now()
        )

        response = self.client.get(
            reverse("tanks:photo-update", args=[self.tank.slug, self.photo.pk])
        )
        self.assertContains(response, "Trübung nach dem Aufwirbeln")
        self.assertNotContains(response, "Woanders")

        self.assign(elsewhere.pk)
        self.photo.refresh_from_db()
        self.assertIsNone(self.photo.event_id)

    def test_a_foreign_event_is_refused(self):
        foreign = Event.objects.create(
            tank=create_tank(create_user("fremd"), slug="fremd"),
            title="Fremd",
            occurred_at=timezone.now(),
        )

        self.assign(foreign.pk)

        self.photo.refresh_from_db()
        self.assertIsNone(self.photo.event_id)

    def test_the_model_itself_refuses_a_foreign_event(self):
        """Auch am Admin vorbei — die Regel steht am Modell, nicht im Formular."""
        elsewhere = Event.objects.create(
            tank=create_tank(self.user, name="Nano", slug="nano"),
            title="Woanders",
            occurred_at=timezone.now(),
        )
        self.photo.event = elsewhere

        with self.assertRaises(ValidationError):
            self.photo.full_clean()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ActivityGroupingTests(TestCase):
    """Ein Ereignis mit Fotos ist **ein** Eintrag der Zeitleiste."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)

    def event_with_photos(self, count):
        event = Event.objects.create(
            tank=self.tank,
            title="Balzende Panzerwelse",
            occurred_at=timezone.now(),
            category=Event.Category.OBSERVATION,
        )
        for index in range(count):
            TankPhoto.objects.create(
                tank=self.tank,
                event=event,
                image=image_upload(f"{index}.png"),
                taken_on=timezone.localdate(),
            )
        return event

    def test_an_event_with_photos_stays_one_entry(self):
        self.event_with_photos(3)

        entries = selectors.recent_activity(self.user)

        self.assertEqual([entry["kind"] for entry in entries], ["event"])
        self.assertEqual(entries[0]["photo_count"], 3)

    def test_five_images_do_not_flood_the_tile(self):
        self.event_with_photos(5)

        entry = selectors.recent_activity(self.user)[0]

        self.assertEqual(entry["photo_count"], 5)
        self.assertEqual(len(entry["photos"]), selectors.ACTIVITY_PREVIEW_PHOTOS)

    def test_a_photo_without_an_event_remains_its_own_entry(self):
        TankPhoto.objects.create(
            tank=self.tank, image=image_upload(), taken_on=timezone.localdate()
        )

        entries = selectors.recent_activity(self.user)

        self.assertEqual([entry["kind"] for entry in entries], ["photo"])


class StockingWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.species = create_animal()

    def test_stocking_is_recorded(self):
        self.client.post(
            reverse("tanks:stocking-create", args=[self.tank.slug]),
            {
                "species": self.species.pk,
                "quantity": "12",
                "added_on": timezone.localdate().isoformat(),
                "note": "",
            },
        )
        self.assertEqual(self.tank.stockings.get().quantity, 12)

    def test_removal_is_booked_instead_of_deleted(self):
        stocking = stock(self.tank, self.species)
        today = timezone.localdate()
        self.client.post(
            reverse("tanks:stocking-remove", args=[self.tank.slug, stocking.pk]),
            {"removed_on": today.isoformat(), "note": "abgegeben"},
        )
        stocking.refresh_from_db()
        self.assertEqual(stocking.removed_on, today)
        self.assertFalse(stocking.is_active)

    def test_a_removal_before_the_stocking_is_rejected(self):
        stocking = stock(self.tank, self.species, days_ago=10)
        too_early = (stocking.added_on - timedelta(days=1)).isoformat()
        self.client.post(
            reverse("tanks:stocking-remove", args=[self.tank.slug, stocking.pk]),
            {"removed_on": too_early, "note": ""},
        )
        stocking.refresh_from_db()
        self.assertIsNone(stocking.removed_on)

    def test_there_is_no_delete_route_for_stockings(self):
        with self.assertRaises(NoReverseMatch):
            reverse("tanks:stocking-delete", args=[self.tank.slug, 1])


class PlantingWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.species = create_plant()

    def test_planting_is_recorded_and_removed(self):
        self.client.post(
            reverse("tanks:planting-create", args=[self.tank.slug]),
            {
                "species": self.species.pk,
                "quantity": "5",
                "planted_on": timezone.localdate().isoformat(),
                "removed_on": "",
                "note": "",
            },
        )
        planting = self.tank.plantings.get()
        self.assertEqual(planting.quantity, 5)

        self.client.post(reverse("tanks:planting-delete", args=[self.tank.slug, planting.pk]))
        self.assertFalse(Planting.objects.exists())


class ProvenanceTests(TestCase):
    """Bezugsquelle am Eintrag, nicht im Katalog."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.animal = create_animal("Mikrogeophagus ramirezi", slug="mikrogeophagus-ramirezi")
        self.plant = create_plant()

    def test_the_source_of_a_stocking_is_recorded(self):
        self.client.post(
            reverse("tanks:stocking-create", args=[self.tank.slug]),
            {
                "species": self.animal.pk,
                "quantity": "2",
                "added_on": timezone.localdate().isoformat(),
                "provenance": Stocking.Provenance.BRED_DE,
                "provenance_detail": "Nachzucht Müller, Landshut",
                "note": "",
            },
        )
        stocking = self.tank.stockings.get()
        self.assertEqual(stocking.provenance, Stocking.Provenance.BRED_DE)
        self.assertEqual(
            stocking.provenance_label,
            "Deutsche / europäische Nachzucht · Nachzucht Müller, Landshut",
        )

    def test_without_a_source_nothing_is_claimed(self):
        stocking = stock(self.tank, self.animal)
        self.assertEqual(stocking.provenance, "")
        self.assertEqual(stocking.provenance_label, "")

    def test_the_stocking_tab_names_the_source(self):
        stock(self.tank, self.animal)
        self.tank.stockings.update(provenance=Stocking.Provenance.BRED_ASIA)
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=besatz")
        self.assertContains(response, "Asiatische Nachzucht")

    def test_plants_have_their_own_choices(self):
        self.client.post(
            reverse("tanks:planting-create", args=[self.tank.slug]),
            {
                "species": self.plant.pk,
                "quantity": "5",
                "planted_on": timezone.localdate().isoformat(),
                "removed_on": "",
                "provenance": Planting.Provenance.IN_VITRO,
                "provenance_detail": "Tropica 1-2-Grow",
                "note": "",
            },
        )
        planting = self.tank.plantings.get()
        self.assertEqual(planting.provenance_label, "InVitro · Tropica 1-2-Grow")
        # Was bei der Pflanze zählt, sagt beim Tier nichts — und umgekehrt.
        self.assertNotIn("in_vitro", Stocking.Provenance.values)
        self.assertNotIn("wild", Planting.Provenance.values)

    def test_the_plant_tab_names_the_source(self):
        Planting.objects.create(
            tank=self.tank,
            species=self.plant,
            quantity=5,
            planted_on=self.tank.setup_date,
            provenance=Planting.Provenance.EMERSED,
        )
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=pflanzen")
        self.assertContains(response, "Emers vorgezogen")


class SexDistributionTests(TestCase):
    """Geschlechterverteilung — optional, aber nicht widersprüchlich."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.species = create_animal(
            "Apistogramma cacatuoides",
            slug="apistogramma-cacatuoides",
            min_group_size=1,
            social_structure=AnimalSpecies.Social.HAREM,
        )

    def payload(self, **overrides):
        data = {
            "species": self.species.pk,
            "quantity": "4",
            "quantity_male": "1",
            "quantity_female": "3",
            "added_on": timezone.localdate().isoformat(),
            "note": "",
        }
        data.update(overrides)
        return data

    def test_the_distribution_is_recorded(self):
        self.client.post(
            reverse("tanks:stocking-create", args=[self.tank.slug]), self.payload()
        )
        stocking = self.tank.stockings.get()
        self.assertEqual((stocking.quantity_male, stocking.quantity_female), (1, 3))
        self.assertEqual(stocking.sex_label, "1 ♂ · 3 ♀")

    def test_more_sexed_animals_than_animals_is_refused(self):
        response = self.client.post(
            reverse("tanks:stocking-create", args=[self.tank.slug]),
            self.payload(quantity="2"),
        )
        self.assertFalse(self.tank.stockings.exists())
        self.assertContains(response, "mehr als die erfasste Anzahl")

    def test_fewer_is_fine_because_juveniles_are_not_sexable(self):
        self.client.post(
            reverse("tanks:stocking-create", args=[self.tank.slug]),
            self.payload(quantity="10", quantity_male="1", quantity_female="3"),
        )
        self.assertEqual(self.tank.stockings.get().quantity, 10)

    def test_nothing_recorded_means_no_label(self):
        stocking = stock(self.tank, self.species)
        self.assertEqual(stocking.sex_label, "")

    def test_the_stocking_tab_shows_the_distribution(self):
        self.client.post(
            reverse("tanks:stocking-create", args=[self.tank.slug]), self.payload()
        )
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=besatz")
        self.assertContains(response, "1 ♂")


class SocialStructureHintTests(TestCase):
    """Paar, Harem, Einzelhaltung: Hinweise — keine Sperre.

    ``min_group_size`` allein trägt nicht: bei einem Paar steht dort 2, was
    auch „mindestens zwei Tiere" heißen könnte.
    """

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)

    def species(self, structure, **kwargs):
        return create_animal(
            f"Testart {structure}",
            slug=f"testart-{structure}",
            # Ohne deutschen Namen ist der Anzeigename der wissenschaftliche —
            # so steht die geprüfte Sozialstruktur im Meldungstext.
            common_name="",
            min_group_size=1,
            social_structure=structure,
            **kwargs,
        )

    def stocking(self, structure, quantity=2, male=None, female=None):
        return Stocking.objects.create(
            tank=self.tank,
            species=self.species(structure),
            quantity=quantity,
            quantity_male=male,
            quantity_female=female,
            added_on=self.tank.setup_date,
        )

    def test_a_pair_of_two_males_is_pointed_out(self):
        stocking = self.stocking(AnimalSpecies.Social.PAIR, male=2, female=0)
        self.assertEqual(
            stocking.social_hints, ["Ein Paar braucht ein Männchen und ein Weibchen."]
        )

    def test_a_real_pair_is_quiet(self):
        self.assertEqual(
            self.stocking(AnimalSpecies.Social.PAIR, male=1, female=1).social_hints, []
        )

    def test_a_pair_without_a_recorded_distribution_is_quiet(self):
        # Ein Hinweis auf eine fehlende Angabe stünde sonst an jedem Posten.
        self.assertEqual(self.stocking(AnimalSpecies.Social.PAIR).social_hints, [])

    def test_a_harem_with_several_males_is_pointed_out(self):
        stocking = self.stocking(AnimalSpecies.Social.HAREM, quantity=5, male=2, female=3)
        self.assertEqual(len(stocking.social_hints), 1)
        self.assertIn("nur ein Männchen", stocking.social_hints[0])

    def test_a_harem_with_one_male_is_quiet(self):
        self.assertEqual(
            self.stocking(AnimalSpecies.Social.HAREM, quantity=4, male=1, female=3).social_hints,
            [],
        )

    def test_a_solitary_species_in_company_is_pointed_out(self):
        stocking = self.stocking(AnimalSpecies.Social.SOLITARY, quantity=3)
        self.assertIn("einzeln", stocking.social_hints[0])

    def test_a_single_solitary_animal_is_quiet(self):
        self.assertEqual(
            self.stocking(AnimalSpecies.Social.SOLITARY, quantity=1).social_hints, []
        )

    def test_a_shoal_is_never_asked_about_sexes(self):
        self.assertEqual(
            self.stocking(AnimalSpecies.Social.SHOAL, quantity=12).social_hints, []
        )

    def test_a_species_without_a_structure_is_quiet(self):
        stocking = Stocking.objects.create(
            tank=self.tank,
            species=create_animal(),
            quantity=3,
            added_on=self.tank.setup_date,
        )
        self.assertEqual(stocking.social_hints, [])

    def test_a_removed_stocking_is_no_longer_judged(self):
        stocking = self.stocking(AnimalSpecies.Social.SOLITARY, quantity=3)
        stocking.removed_on = timezone.localdate()
        stocking.save()
        self.assertEqual(stocking.social_hints, [])

    def test_the_dashboard_warns_about_the_structure(self):
        self.stocking(AnimalSpecies.Social.HAREM, quantity=5, male=3, female=2)
        titles = [item["title"] for item in selectors.warnings(self.user)]
        self.assertIn("Sozialstruktur: Testart harem", titles)

    def test_a_too_small_group_is_still_reported(self):
        # Die bestehende Prüfung bleibt, sie wird nur ergänzt.
        Stocking.objects.create(
            tank=self.tank,
            species=create_animal(min_group_size=10),
            quantity=3,
            added_on=self.tank.setup_date,
        )
        titles = [item["title"] for item in selectors.warnings(self.user)]
        self.assertTrue(any("Gruppengröße unterschritten" in title for title in titles))

    def test_the_stocking_tab_shows_the_hint(self):
        self.stocking(AnimalSpecies.Social.SOLITARY, quantity=3)
        self.client.force_login(self.user)
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=besatz")
        self.assertContains(response, "im Becken stehen 3 Tiere")


class TransferTestCase(TestCase):
    """Zwei eigene Becken und zehn Panzerwelse im ersten davon.

    Die Ausgangslage ist die aus #1247: vier von zehn *Corydoras* ziehen um.
    Mit einer Mindestgruppengröße von sechs bleibt genau eine vertretbare
    Gruppe zurück — dieser Fall soll ohne Hinweis durchlaufen, damit die Tests
    zum Umzug nicht versehentlich den Hinweisweg testen.
    """

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.source = create_tank(self.user, name="Suedamerikabecken")
        self.target = create_tank(self.user, name="Grosses Becken")
        self.species = create_animal(
            "Corydoras aeneus",
            common_name="Bronze-Panzerwels",
            slug="corydoras-aeneus",
            min_group_size=6,
        )
        self.stocking = stock(self.source, self.species, quantity=10)

    def transfer_url(self, entry=None):
        entry = entry or self.stocking
        return reverse("tanks:stocking-transfer", args=[self.source.slug, entry.pk])

    def post_transfer(self, quantity=4, **extra):
        payload = {
            "target_tank": self.target.pk,
            "quantity": str(quantity),
            "moved_on": timezone.localdate().isoformat(),
            "note": "",
        }
        payload.update(extra)
        return self.client.post(self.transfer_url(), payload)


class TransferTests(TransferTestCase):
    """Der Umzug selbst: Quellbecken, Zielbecken, Ereignisse, Nachweis."""

    def test_a_part_of_the_stock_moves_and_the_rest_stays(self):
        self.post_transfer(quantity=4)

        self.stocking.refresh_from_db()
        self.assertEqual(self.stocking.quantity, 6)
        self.assertIsNone(self.stocking.removed_on)
        self.assertEqual(self.target.stockings.get().quantity, 4)

    def test_the_new_entry_starts_on_the_day_of_the_move(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self.post_transfer(quantity=4, moved_on=yesterday.isoformat())

        self.assertEqual(self.target.stockings.get().added_on, yesterday)

    def test_moving_everything_books_the_departure_in_the_source_tank(self):
        """Bleibt nichts zurück, ist der Posten kein aktiver Besatz mehr.

        Gelöscht wird er trotzdem nicht: dass hier einmal Panzerwelse
        schwammen, gehört zur Geschichte des Beckens.
        """
        today = timezone.localdate()
        self.post_transfer(quantity=10)

        self.stocking.refresh_from_db()
        self.assertEqual(self.stocking.quantity, 0)
        self.assertEqual(self.stocking.removed_on, today)
        self.assertFalse(self.stocking.is_active)

    def test_the_target_tank_merges_instead_of_duplicating(self):
        existing = stock(self.target, self.species, quantity=3)
        self.post_transfer(quantity=4)

        existing.refresh_from_db()
        self.assertEqual(existing.quantity, 7)
        self.assertEqual(self.target.stockings.count(), 1)

    def test_a_departed_entry_in_the_target_tank_is_not_revived(self):
        """Zusammengeführt wird nur mit aktivem Besatz.

        Ein Posten mit Abgangsdatum steht für Tiere, die das Becken verlassen
        haben; sie kehren nicht dadurch zurück, dass neue ankommen.
        """
        gone = stock(self.target, self.species, quantity=3)
        gone.removed_on = timezone.localdate() - timedelta(days=5)
        gone.save(update_fields=["removed_on"])

        self.post_transfer(quantity=4)

        gone.refresh_from_db()
        self.assertEqual(gone.quantity, 3)
        self.assertEqual(self.target.stockings.filter(removed_on__isnull=True).get().quantity, 4)

    def test_both_tanks_get_an_event_naming_the_other(self):
        self.post_transfer(quantity=4)

        out = self.source.events.get()
        arrival = self.target.events.get()
        self.assertEqual(out.category, Event.Category.STOCKING)
        self.assertEqual(arrival.category, Event.Category.STOCKING)
        self.assertIn(self.target.name, out.title)
        self.assertIn(self.source.name, arrival.title)

    def test_the_transfer_stays_as_evidence(self):
        self.post_transfer(quantity=4, note="Nach der Einfahrphase")

        transfer = Transfer.objects.get()
        self.assertEqual(transfer.kind, Transfer.Kind.ANIMAL)
        self.assertEqual(transfer.animal, self.species)
        self.assertIsNone(transfer.plant)
        self.assertEqual(transfer.source_tank, self.source)
        self.assertEqual(transfer.target_tank, self.target)
        self.assertEqual(transfer.quantity, 4)
        self.assertEqual(transfer.note, "Nach der Einfahrphase")
        self.assertEqual(transfer.created_by, self.user)

    def test_everything_or_nothing(self):
        """Ein Umzug, der auf halbem Weg scheitert, hinterlässt keine Spur.

        Tiere im Quellbecken abgezogen und im Zielbecken nie angekommen wäre
        schlimmer als gar keine Funktion — deshalb hängen alle Schritte in
        einer Transaktion.
        """
        move = transfers.Move(
            entry=self.stocking,
            target_tank=self.target,
            quantity=4,
            moved_on=timezone.localdate(),
        )
        with mock.patch.object(Event.objects, "bulk_create", side_effect=RuntimeError("Bruch")):
            with self.assertRaises(RuntimeError):
                transfers.perform(move)

        self.stocking.refresh_from_db()
        self.assertEqual(self.stocking.quantity, 10)
        self.assertFalse(self.target.stockings.exists())
        self.assertFalse(Transfer.objects.exists())
        self.assertFalse(Event.objects.exists())


class TransferRuleTests(TransferTestCase):
    """Was abgewiesen wird — im Unterschied zu dem, was nur angemerkt wird."""

    def test_more_than_the_stock_is_refused(self):
        response = self.post_transfer(quantity=11)

        self.assertContains(response, "Im Quellbecken sind nur 10.")
        self.stocking.refresh_from_db()
        self.assertEqual(self.stocking.quantity, 10)
        self.assertFalse(Transfer.objects.exists())

    def test_a_date_before_the_stocking_is_refused(self):
        too_early = (self.stocking.added_on - timedelta(days=1)).isoformat()
        self.post_transfer(quantity=4, moved_on=too_early)

        self.assertFalse(Transfer.objects.exists())

    def test_a_date_in_the_future_is_refused(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.post_transfer(quantity=4, moved_on=tomorrow)

        self.assertFalse(Transfer.objects.exists())

    def test_a_foreign_tank_is_no_target(self):
        """Eine Abgabe an Dritte ist kein Umzug, sondern ein Abgang."""
        stranger = create_user("fremder")
        foreign = create_tank(stranger, name="Fremdes Becken")

        self.post_transfer(quantity=4, target_tank=foreign.pk)

        self.assertFalse(Transfer.objects.exists())
        self.assertFalse(foreign.stockings.exists())

    def test_a_dissolved_tank_is_no_target(self):
        self.target.dissolved_on = timezone.localdate()
        self.target.save(update_fields=["dissolved_on"])

        self.post_transfer(quantity=4)

        self.assertFalse(Transfer.objects.exists())

    def test_the_source_tank_is_no_target(self):
        self.post_transfer(quantity=4, target_tank=self.source.pk)

        self.assertFalse(Transfer.objects.exists())

    def test_a_departed_entry_cannot_be_moved(self):
        self.stocking.removed_on = timezone.localdate()
        self.stocking.save(update_fields=["removed_on"])

        self.assertEqual(self.client.get(self.transfer_url()).status_code, 404)

    def test_a_foreign_entry_is_not_reachable(self):
        stranger = create_user("fremder")
        foreign = create_tank(stranger, name="Fremdes Becken")
        foreign_stocking = stock(foreign, self.species)

        response = self.client.get(
            reverse("tanks:stocking-transfer", args=[foreign.slug, foreign_stocking.pk])
        )
        self.assertEqual(response.status_code, 404)


class TransferHintTests(TransferTestCase):
    """Hinweise: sie erscheinen vor dem Umzug und halten ihn nicht auf."""

    def move(self, quantity=4):
        return transfers.Move(
            entry=self.stocking,
            target_tank=self.target,
            quantity=quantity,
            moved_on=timezone.localdate(),
        )

    def test_water_values_far_apart_are_pointed_out(self):
        create_measurement(self.source, "kh", "18")
        create_measurement(self.target, "kh", "5")

        hints = transfers.hints(self.move())
        self.assertTrue(any("Karbonathärte" in hint for hint in hints))

    def test_a_small_difference_is_not_worth_a_hint(self):
        create_measurement(self.source, "kh", "8")
        create_measurement(self.target, "kh", "6")

        self.assertEqual(transfers.hints(self.move()), [])

    def test_a_value_measured_in_only_one_tank_is_no_deviation(self):
        """„Nie gemessen“ ist keine Abweichung.

        Ein Hinweis ohne Grundlage lernt man schnell zu überlesen — und dann
        auch den mit Grundlage.
        """
        create_measurement(self.source, "kh", "18")

        self.assertEqual(transfers.hints(self.move()), [])

    def test_a_target_outside_the_species_range_is_pointed_out(self):
        self.species.temperature_min = Decimal("24.0")
        self.species.temperature_max = Decimal("28.0")
        self.species.save(update_fields=["temperature_min", "temperature_max"])
        create_measurement(self.target, "temperatur", "21.0")

        hints = transfers.hints(self.move())
        self.assertTrue(any("Temperatur" in hint for hint in hints))

    def test_a_group_left_too_small_is_pointed_out(self):
        """Vier von zehn zurückzulassen ist vertretbar, acht wegzunehmen nicht."""
        self.assertEqual(transfers.hints(self.move(quantity=4)), [])

        hints = transfers.hints(self.move(quantity=8))
        self.assertTrue(any("Mindestgruppengröße" in hint for hint in hints))

    def test_moving_the_whole_group_leaves_no_group_to_be_too_small(self):
        self.assertEqual(transfers.hints(self.move(quantity=10)), [])

    def test_plants_have_no_group_size(self):
        planting = Planting.objects.create(
            tank=self.source,
            species=create_plant(),
            quantity=10,
            planted_on=timezone.localdate() - timedelta(days=30),
        )
        move = transfers.Move(
            entry=planting,
            target_tank=self.target,
            quantity=8,
            moved_on=timezone.localdate(),
        )
        self.assertEqual(transfers.hints(move), [])

    def test_the_first_attempt_shows_the_hints_and_books_nothing(self):
        response = self.post_transfer(quantity=8)

        self.assertContains(response, "Mindestgruppengröße")
        self.assertContains(response, "Trotzdem umsetzen")
        self.assertFalse(Transfer.objects.exists())
        self.stocking.refresh_from_db()
        self.assertEqual(self.stocking.quantity, 10)

    def test_a_hint_is_no_lock(self):
        self.post_transfer(quantity=8)
        self.post_transfer(quantity=8, bestaetigt="1")

        self.assertEqual(Transfer.objects.get().quantity, 8)
        self.stocking.refresh_from_db()
        self.assertEqual(self.stocking.quantity, 2)


class TransferProvenanceTests(TransferTestCase):
    """Die Bezugsquelle zieht mit — es sind dieselben Tiere."""

    def setUp(self):
        super().setUp()
        Stocking.objects.filter(pk=self.stocking.pk).update(
            provenance=Stocking.Provenance.WILD, provenance_detail="Importeur Rio"
        )
        self.stocking.refresh_from_db()

    def test_a_new_target_entry_keeps_the_source(self):
        self.post_transfer(quantity=4)
        arrived = self.target.stockings.get()
        self.assertEqual(arrived.provenance, Stocking.Provenance.WILD)
        self.assertEqual(arrived.provenance_detail, "Importeur Rio")

    def test_merging_leaves_the_target_as_it_stands(self):
        """Zwei Quellen in einem Feld wären eine Behauptung."""
        existing = stock(self.target, self.species, quantity=5)
        Stocking.objects.filter(pk=existing.pk).update(
            provenance=Stocking.Provenance.BRED_LOCAL, provenance_detail=""
        )
        self.post_transfer(quantity=4)
        existing.refresh_from_db()
        self.assertEqual(existing.quantity, 9)
        self.assertEqual(existing.provenance, Stocking.Provenance.BRED_LOCAL)

    def test_a_recorded_sex_distribution_is_pointed_out(self):
        Stocking.objects.filter(pk=self.stocking.pk).update(quantity_male=4, quantity_female=6)
        self.stocking.refresh_from_db()
        hints = transfers.hints(self.move(quantity=4))
        self.assertTrue(any("Geschlechterverteilung" in hint for hint in hints))

    def test_without_a_distribution_the_move_stays_quiet(self):
        self.assertEqual(transfers.hints(self.move(quantity=4)), [])

    def move(self, quantity=4):
        return transfers.Move(
            entry=self.stocking,
            target_tank=self.target,
            quantity=quantity,
            moved_on=timezone.localdate(),
        )


class TransferDisplayTests(TransferTestCase):
    """Wo der Umzug hinterher zu sehen ist."""

    def test_the_origin_is_visible_on_the_target_entry(self):
        self.post_transfer(quantity=4)

        response = self.client.get(f"{self.target.get_absolute_url()}?reiter=besatz")
        self.assertContains(response, f"aus {self.source.name}")

    def test_an_entry_without_a_transfer_has_no_origin(self):
        stock(self.target, self.species, quantity=5)

        response = self.client.get(f"{self.target.get_absolute_url()}?reiter=besatz")
        self.assertNotContains(response, "aus ")

    def test_the_overview_lists_transfers_across_tanks_and_species(self):
        self.post_transfer(quantity=4)

        response = self.client.get(reverse("tanks:transfer-list"))
        self.assertContains(response, self.species.display_name)
        self.assertContains(response, self.source.name)
        self.assertContains(response, self.target.name)

    def test_the_overview_shows_only_own_transfers(self):
        stranger = create_user("fremder")
        first = create_tank(stranger, name="Fremd eins")
        second = create_tank(stranger, name="Fremd zwei")
        Transfer.objects.create(
            kind=Transfer.Kind.ANIMAL,
            source_tank=first,
            target_tank=second,
            animal=self.species,
            quantity=3,
            moved_on=timezone.localdate(),
        )

        response = self.client.get(reverse("tanks:transfer-list"))
        self.assertNotContains(response, "Fremd eins")

    def test_a_tank_that_gave_animals_away_is_dissolved_instead_of_deleted(self):
        """``PROTECT`` am Umzug und ``has_history`` müssen dasselbe sagen.

        Sagen sie es nicht, versucht die Löschansicht ein Becken zu löschen,
        das die Datenbank nicht hergibt — und der Benutzer sieht einen Fehler
        statt der Auflösung.
        """
        self.post_transfer(quantity=4)
        self.target.events.all().delete()
        self.target.stockings.all().delete()

        self.assertTrue(self.target.has_history)
        response = self.client.post(reverse("tanks:delete", args=[self.target.slug]))
        self.assertRedirects(response, reverse("tanks:dissolve", args=[self.target.slug]))
        self.assertTrue(Tank.objects.filter(pk=self.target.pk).exists())


class PlantingTransferTests(TestCase):
    """Pflanzen ziehen genauso um — nur heißt das Datum anders."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.source = create_tank(self.user, name="Mutterbecken")
        self.target = create_tank(self.user, name="Ableger")
        self.species = create_plant()
        self.planting = Planting.objects.create(
            tank=self.source,
            species=self.species,
            quantity=12,
            planted_on=timezone.localdate() - timedelta(days=60),
        )

    def test_plants_move_between_tanks(self):
        self.client.post(
            reverse("tanks:planting-transfer", args=[self.source.slug, self.planting.pk]),
            {
                "target_tank": self.target.pk,
                "quantity": "5",
                "moved_on": timezone.localdate().isoformat(),
                "note": "vermehrt",
            },
        )

        self.planting.refresh_from_db()
        self.assertEqual(self.planting.quantity, 7)

        arrived = self.target.plantings.get()
        self.assertEqual(arrived.quantity, 5)
        # ``planted_on`` statt ``added_on`` — beim Schreiben von Code der
        # häufigste Griff daneben.
        self.assertEqual(arrived.planted_on, timezone.localdate())

        transfer = Transfer.objects.get()
        self.assertEqual(transfer.kind, Transfer.Kind.PLANT)
        self.assertEqual(transfer.plant, self.species)
        self.assertIsNone(transfer.animal)


class SubstrateModelTests(TestCase):
    """Schichtung, Gesamthöhe und die Standzeit eines Depots."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        self.today = timezone.localdate()

    def layer(self, kind=SubstrateLayer.Kind.SAND, position=0, depth="4.0", **extra):
        return SubstrateLayer.objects.create(
            tank=self.tank,
            kind=kind,
            position=position,
            depth_cm=Decimal(depth) if depth is not None else None,
            **extra,
        )

    def test_the_total_is_the_sum_of_the_layers(self):
        self.layer(SubstrateLayer.Kind.NUTRIENT, position=0, depth="2.0")
        self.layer(SubstrateLayer.Kind.SAND, position=1, depth="4.5")
        self.assertEqual(self.tank.substrate_depth_cm, Decimal("6.5"))

    def test_a_layer_without_a_depth_is_not_counted_as_zero(self):
        self.layer(depth=None)
        self.assertIsNone(self.tank.substrate_depth_cm)

    def test_the_summary_names_substrate_and_hardscape(self):
        self.layer(depth="6.0")
        HardscapeItem.objects.create(
            tank=self.tank, kind=HardscapeItem.Kind.WOOD, name="Moorkienwurzel", quantity=3
        )
        HardscapeItem.objects.create(
            tank=self.tank, kind=HardscapeItem.Kind.STONE, name="Lavastein", quantity=5
        )
        self.assertEqual(self.tank.setup_summary, "6 cm Bodengrund · 3 Wurzeln · 5 Steine")

    def test_removed_hardscape_leaves_the_summary(self):
        HardscapeItem.objects.create(
            tank=self.tank,
            kind=HardscapeItem.Kind.WOOD,
            name="Moorkienwurzel",
            quantity=1,
            removed_on=self.today,
        )
        self.assertEqual(self.tank.setup_summary, "")

    def test_only_a_depot_gets_a_calculated_end(self):
        depot = SubstrateLayer(kind=SubstrateLayer.Kind.NUTRIENT, added_on=self.today)
        self.assertEqual(
            depot.default_depleted_on(), self.today + timedelta(days=NUTRIENT_DEPOT_DAYS)
        )
        sand = SubstrateLayer(kind=SubstrateLayer.Kind.SAND, added_on=self.today)
        self.assertIsNone(sand.default_depleted_on())

    def test_an_expired_depot_is_a_warning(self):
        expired = self.layer(
            SubstrateLayer.Kind.NUTRIENT, depleted_on=self.today - timedelta(days=1)
        )
        running = self.layer(
            SubstrateLayer.Kind.NUTRIENT, position=1, depleted_on=self.today + timedelta(days=30)
        )
        self.assertEqual(expired.depletion_status(), Status.WARN)
        self.assertEqual(running.depletion_status(), Status.OK)
        self.assertEqual(self.layer(position=2).depletion_status(), Status.UNKNOWN)

    def test_moving_a_layer_renumbers_the_whole_stack(self):
        """Lücken in den Positionen dürfen das Verschieben nicht stören."""
        bottom = self.layer(position=0)
        middle = self.layer(position=5)
        top = self.layer(position=9)

        self.assertTrue(middle.move(1))

        self.assertEqual(
            list(self.tank.substrate_layers.values_list("pk", flat=True)),
            [bottom.pk, top.pk, middle.pk],
        )
        self.assertEqual(
            sorted(self.tank.substrate_layers.values_list("position", flat=True)), [0, 1, 2]
        )

    def test_at_the_edge_of_the_stack_nothing_happens(self):
        bottom = self.layer(position=0)
        self.layer(position=1)
        self.assertFalse(bottom.move(-1))
        bottom.refresh_from_db()
        self.assertEqual(bottom.position, 0)


class SubstrateWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.today = timezone.localdate()

    def payload(self, **overrides):
        data = {
            "kind": SubstrateLayer.Kind.SAND,
            "product": "Dennerle Sansibar",
            "grain_size": "0,5–1 mm",
            "depth_cm": "4",
            "added_on": self.today.isoformat(),
            "depleted_on": "",
            "note": "",
        }
        data.update(overrides)
        return data

    def create(self, **overrides):
        return self.client.post(
            reverse("tanks:substrate-create", args=[self.tank.slug]), self.payload(**overrides)
        )

    def test_a_new_layer_lands_on_top_of_the_stack(self):
        self.create(kind=SubstrateLayer.Kind.NUTRIENT, depth_cm="2")
        self.create(kind=SubstrateLayer.Kind.SAND, depth_cm="4")
        self.assertEqual(
            list(self.tank.substrate_layers.values_list("kind", "position")),
            [("nutrient", 0), ("sand", 1)],
        )

    def test_a_depot_is_prefilled_with_four_months(self):
        self.create(kind=SubstrateLayer.Kind.NUTRIENT, product="JBL AquaBasis")
        layer = self.tank.substrate_layers.get()
        self.assertEqual(layer.depleted_on, self.today + timedelta(days=NUTRIENT_DEPOT_DAYS))

    def test_an_entered_end_beats_the_default(self):
        own = self.today + timedelta(days=200)
        self.create(kind=SubstrateLayer.Kind.NUTRIENT, depleted_on=own.isoformat())
        self.assertEqual(self.tank.substrate_layers.get().depleted_on, own)

    def test_an_end_before_the_start_is_refused(self):
        self.create(
            kind=SubstrateLayer.Kind.NUTRIENT,
            depleted_on=(self.today - timedelta(days=1)).isoformat(),
        )
        self.assertFalse(SubstrateLayer.objects.exists())

    def test_a_layer_without_a_depth_is_refused(self):
        self.create(depth_cm="0")
        self.assertFalse(SubstrateLayer.objects.exists())

    def test_the_tab_shows_the_stack_top_down_with_the_total(self):
        self.create(kind=SubstrateLayer.Kind.NUTRIENT, product="Depot", depth_cm="2")
        self.create(kind=SubstrateLayer.Kind.SAND, product="Sansibar", depth_cm="4")

        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=einrichtung")

        self.assertEqual(
            [layer.product for layer in response.context["layers"]], ["Sansibar", "Depot"]
        )
        self.assertContains(response, "6 cm gesamt")

    def test_a_layer_is_moved_and_deleted(self):
        self.create(kind=SubstrateLayer.Kind.NUTRIENT, depth_cm="2")
        self.create(kind=SubstrateLayer.Kind.SAND, depth_cm="4")
        sand = self.tank.substrate_layers.get(kind=SubstrateLayer.Kind.SAND)

        self.client.post(
            reverse("tanks:substrate-move", args=[self.tank.slug, sand.pk]), {"richtung": "runter"}
        )
        self.assertEqual(
            list(self.tank.substrate_layers.values_list("kind", flat=True)), ["sand", "nutrient"]
        )

        self.client.post(reverse("tanks:substrate-delete", args=[self.tank.slug, sand.pk]))
        self.assertEqual(self.tank.substrate_layers.count(), 1)

    def test_a_foreign_layer_is_not_reachable(self):
        foreign = create_tank(create_user("fremd"), slug="fremd")
        layer = SubstrateLayer.objects.create(tank=foreign, kind=SubstrateLayer.Kind.SAND)
        url = reverse("tanks:substrate-update", args=[self.tank.slug, layer.pk])
        self.assertEqual(self.client.get(url).status_code, 404)


class SetupReminderTests(TestCase):
    """Termine aus der Einrichtung werden angeboten, nicht angelegt."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.today = timezone.localdate()

    def create_depot(self):
        return self.client.post(
            reverse("tanks:substrate-create", args=[self.tank.slug]),
            {
                "kind": SubstrateLayer.Kind.NUTRIENT,
                "product": "JBL AquaBasis",
                "grain_size": "",
                "depth_cm": "2",
                "added_on": self.today.isoformat(),
                "depleted_on": "",
                "note": "",
            },
        )

    def create_botanicals(self):
        return self.client.post(
            reverse("tanks:hardscape-create", args=[self.tank.slug]),
            {
                "kind": HardscapeItem.Kind.BOTANICALS,
                "name": "Erlenzapfen",
                "quantity": "10",
                "added_on": self.today.isoformat(),
                "removed_on": "",
                "water_effect": "Huminstoffe, senkt pH",
                "note": "",
            },
        )

    def test_a_depot_offers_a_reminder_without_creating_one(self):
        response = self.create_depot()

        self.assertContains(response, "Termin anlegen?")
        self.assertContains(response, "Nährstoffdepot erschöpft")
        self.assertFalse(CareTask.objects.exists())

    def test_the_offer_becomes_a_task_only_on_confirmation(self):
        self.create_depot()
        layer = self.tank.substrate_layers.get()

        self.client.post(
            reverse("tanks:substrate-reminder", args=[self.tank.slug, layer.pk]),
            {
                "title": "Nährstoffdepot erschöpft: JBL AquaBasis",
                "category": CareTask.Category.FERTILIZER,
                "interval_days": "",
                "due_on": layer.depleted_on.isoformat(),
                "notes": "",
            },
        )

        task = self.tank.tasks.get()
        self.assertEqual(task.due_on, layer.depleted_on)
        self.assertEqual(task.category, CareTask.Category.FERTILIZER)

    def test_botanicals_offer_a_reminder_six_weeks_out(self):
        response = self.create_botanicals()
        item = self.tank.hardscape.get()

        self.assertContains(response, "Termin anlegen?")
        self.assertEqual(item.expected_depletion, self.today + timedelta(days=BOTANICALS_DAYS))
        self.assertFalse(CareTask.objects.exists())

    def test_a_stone_is_offered_nothing(self):
        response = self.client.post(
            reverse("tanks:hardscape-create", args=[self.tank.slug]),
            {
                "kind": HardscapeItem.Kind.STONE,
                "name": "Lavastein",
                "quantity": "5",
                "added_on": self.today.isoformat(),
                "removed_on": "",
                "water_effect": "",
                "note": "",
            },
        )
        self.assertNotContains(response, "Termin anlegen?")

    def test_the_offer_is_reachable_again_from_the_row(self):
        self.create_botanicals()
        item = self.tank.hardscape.get()
        response = self.client.get(
            reverse("tanks:hardscape-reminder", args=[self.tank.slug, item.pk])
        )
        self.assertContains(response, "Erlenzapfen erneuern")


class HardscapeWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.today = timezone.localdate()

    def payload(self, **overrides):
        data = {
            "kind": HardscapeItem.Kind.WOOD,
            "name": "Moorkienwurzel",
            "quantity": "1",
            "added_on": self.today.isoformat(),
            "removed_on": "",
            "water_effect": "",
            "note": "",
        }
        data.update(overrides)
        return data

    def create(self, **overrides):
        return self.client.post(
            reverse("tanks:hardscape-create", args=[self.tank.slug]), self.payload(**overrides)
        )

    def test_an_item_is_recorded(self):
        self.create(affects_water="on", water_effect="Huminstoffe, senkt pH")
        item = self.tank.hardscape.get()
        self.assertTrue(item.affects_water)
        self.assertEqual(item.water_effect, "Huminstoffe, senkt pH")

    def test_a_described_effect_sets_the_flag_by_itself(self):
        """Wer die Wirkung beschreibt, meint auch, dass sie eintritt."""
        self.create(water_effect="hebt KH und Leitwert")
        self.assertTrue(self.tank.hardscape.get().affects_water)

    def test_it_is_marked_as_removed_instead_of_deleted(self):
        self.create()
        item = self.tank.hardscape.get()

        self.client.post(
            reverse("tanks:hardscape-remove", args=[self.tank.slug, item.pk]),
            {"removed_on": self.today.isoformat(), "note": "gegen Steine getauscht"},
        )

        item.refresh_from_db()
        self.assertEqual(item.removed_on, self.today)
        self.assertFalse(item.is_active)
        self.assertTrue(HardscapeItem.objects.exists())

    def test_a_removal_before_the_arrival_is_rejected(self):
        self.create(added_on=(self.today - timedelta(days=5)).isoformat())
        item = self.tank.hardscape.get()
        self.client.post(
            reverse("tanks:hardscape-remove", args=[self.tank.slug, item.pk]),
            {"removed_on": (self.today - timedelta(days=10)).isoformat(), "note": ""},
        )
        item.refresh_from_db()
        self.assertIsNone(item.removed_on)

    def test_there_is_no_delete_route_for_hardscape(self):
        with self.assertRaises(NoReverseMatch):
            reverse("tanks:hardscape-delete", args=[self.tank.slug, 1])

    def test_the_tab_lists_the_item_with_its_effect(self):
        self.create(water_effect="Huminstoffe, senkt pH")
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=einrichtung")
        self.assertContains(response, "Moorkienwurzel")
        self.assertContains(response, "Huminstoffe, senkt pH")


class SetupContextTests(TestCase):
    """Die Einrichtung im Kontext der KI-Auswertung (#1227)."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        self.today = timezone.localdate()

    def test_substrate_and_hardscape_reach_the_prompt(self):
        SubstrateLayer.objects.create(
            tank=self.tank,
            kind=SubstrateLayer.Kind.NUTRIENT,
            position=0,
            depth_cm=Decimal("2.0"),
            product="JBL AquaBasis",
            added_on=self.today - timedelta(days=200),
            depleted_on=self.today - timedelta(days=80),
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

        text = selectors.tank_facts(self.tank).as_text()

        # Von unten nach oben, mit Standzeit — und die Wirkung des Hardscapes.
        self.assertIn("2 cm Nährstoffdepot (JBL AquaBasis), erschöpft seit", text)
        self.assertIn("4 cm Sand", text)
        self.assertIn("Moorkienwurzel — Huminstoffe, senkt pH", text)

    def test_removed_hardscape_stays_in_the_context_with_its_date(self):
        HardscapeItem.objects.create(
            tank=self.tank,
            kind=HardscapeItem.Kind.WOOD,
            name="Moorkienwurzel",
            removed_on=self.today,
        )
        self.assertIn("(entfernt ", selectors.tank_facts(self.tank).as_text())

    def test_without_a_setup_nothing_is_claimed(self):
        text = selectors.tank_facts(self.tank).as_text()
        self.assertNotIn("Bodengrund", text)
        self.assertNotIn("Einrichtung", text)


class CareTaskWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

    def test_task_is_created(self):
        self.client.post(
            reverse("tanks:task-create", args=[self.tank.slug]),
            {
                "title": "Filter reinigen",
                "category": CareTask.Category.FILTER,
                "interval_days": "30",
                "due_on": timezone.localdate().isoformat(),
                "notes": "",
            },
        )
        self.assertEqual(self.tank.tasks.get().title, "Filter reinigen")

    def test_a_task_is_deactivated_instead_of_deleted(self):
        task = create_task(self.tank)
        task.complete(user=self.user)
        url = reverse("tanks:task-toggle", args=[self.tank.slug, task.pk])

        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertContains(response, "deaktiviert")

        self.client.post(url)
        task.refresh_from_db()
        self.assertFalse(task.is_active)
        self.assertEqual(task.completions.count(), 1)

    def test_a_deactivated_task_is_switched_back_on_without_a_question(self):
        task = create_task(self.tank)
        task.is_active = False
        task.save(update_fields=["is_active"])
        self.client.post(reverse("tanks:task-toggle", args=[self.tank.slug, task.pk]))
        task.refresh_from_db()
        self.assertTrue(task.is_active)


class ParameterTargetWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        self.ph = Parameter.objects.get(key="ph")

    def test_the_form_sits_in_the_overview(self):
        response = self.client.get(self.tank.get_absolute_url())
        self.assertContains(response, reverse("tanks:target-create", args=[self.tank.slug]))
        self.assertContains(response, "Zielbereiche")

    def test_target_is_created(self):
        self.client.post(
            reverse("tanks:target-create", args=[self.tank.slug]),
            {"parameter": self.ph.pk, "minimum": "6.8", "maximum": "7.2"},
        )
        target = self.tank.parameter_targets.get()
        self.assertEqual(target.range_label, "6,8–7,2")

    def test_a_second_target_for_the_same_parameter_is_refused(self):
        TankParameterTarget.objects.create(tank=self.tank, parameter=self.ph, minimum=6, maximum=7)
        response = self.client.post(
            reverse("tanks:target-create", args=[self.tank.slug]),
            {"parameter": self.ph.pk, "minimum": "6.8", "maximum": "7.2"},
        )
        self.assertEqual(self.tank.parameter_targets.count(), 1)
        self.assertContains(response, "bereits einen Zielbereich")

    def test_a_reversed_range_is_refused(self):
        self.client.post(
            reverse("tanks:target-create", args=[self.tank.slug]),
            {"parameter": self.ph.pk, "minimum": "7.5", "maximum": "6.5"},
        )
        self.assertFalse(self.tank.parameter_targets.exists())

    def test_target_is_edited_and_deleted(self):
        target = TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=6, maximum=7
        )
        self.client.post(
            reverse("tanks:target-update", args=[self.tank.slug, target.pk]),
            {"parameter": self.ph.pk, "minimum": "6.5", "maximum": "7.5"},
        )
        target.refresh_from_db()
        self.assertEqual(target.maximum, Decimal("7.500"))

        self.client.post(reverse("tanks:target-delete", args=[self.tank.slug, target.pk]))
        self.assertFalse(TankParameterTarget.objects.exists())


class Co2TestCase(TestCase):
    """Gemeinsamer Aufbau für alles, was mit CO₂ zu tun hat.

    Die Zeitpunkte sind hier auf Minuten genau nötig — das Paarungsfenster
    misst Stunden, und ``create_measurement`` kennt nur ganze Tage.
    """

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        self.kh = Parameter.objects.get(key="kh")
        self.ph = Parameter.objects.get(key="ph")
        self.now = timezone.now()

    def measure(self, parameter, value, minutes_ago=0):
        return Measurement.objects.create(
            tank=self.tank,
            parameter=parameter,
            value=Decimal(value),
            measured_at=self.now - timedelta(minutes=minutes_ago),
        )

    def pair(self, kh="5", ph="6.9", apart_minutes=0):
        """Ein KH- und ein pH-Wert, ``apart_minutes`` auseinander."""
        return (
            self.measure(self.kh, kh, minutes_ago=apart_minutes),
            self.measure(self.ph, ph),
        )


class Co2FormulaTests(Co2TestCase):
    """Die Werte aus der Betriebstabelle des Items, Toleranz ±0,5 mg/l."""

    #: KH, pH, erwartetes CO₂ in mg/l.
    CASES = [
        ("5", "6.9", 19),
        ("5", "7.0", 15),
        ("6", "7.0", 18),
        ("5", "6.8", 24),
        ("6", "6.7", 36),
    ]

    def test_the_formula_matches_the_operating_table(self):
        for kh, ph, expected in self.CASES:
            with self.subTest(kh=kh, ph=ph):
                self.assertAlmostEqual(
                    float(derived.co2_from(kh, ph)), expected, delta=0.5
                )

    def test_the_calculated_value_reaches_the_overview(self):
        for kh, ph, expected in self.CASES:
            with self.subTest(kh=kh, ph=ph):
                Measurement.objects.all().delete()
                self.pair(kh=kh, ph=ph)
                value = selectors.latest_derived(self.tank)[0]
                self.assertAlmostEqual(float(value.value), expected, delta=0.5)

    def test_an_impossible_ph_yields_nothing_instead_of_an_error(self):
        """Ein vertippter pH darf die Beckenseite nicht umwerfen.

        Das Erfassungsfeld nimmt acht Stellen entgegen, und 10^(7 − pH) wächst
        exponentiell — aus „-9999" entstünde eine Zahl mit zehntausend Stellen.
        """
        self.measure(self.kh, "5")
        self.measure(self.ph, "-9999")
        self.assertEqual(selectors.derived_values(self.tank), [])

    def test_it_is_displayed_without_decimals(self):
        """Ein Tröpfchentest gibt die zweite Stelle nicht her.

        0,1 pH daneben sind ein Viertel des Ergebnisses — „18,9 mg/l" verspräche
        eine Genauigkeit, die die Eingangswerte nicht haben.
        """
        self.pair(kh="5", ph="6.9")
        self.assertEqual(selectors.latest_derived(self.tank)[0].display_value, "19 mg/l")


class Co2PairingTests(Co2TestCase):
    """Welche KH gehört zu welchem pH — der eigentliche Punkt der Sache."""

    def test_values_with_the_same_timestamp_are_paired(self):
        self.pair(apart_minutes=0)
        self.assertEqual(len(selectors.derived_values(self.tank)), 1)

    def test_values_recorded_in_two_steps_are_paired(self):
        """Erst die KH tropfen, zehn Minuten später den pH ablesen."""
        self.pair(apart_minutes=10)
        self.assertEqual(len(selectors.derived_values(self.tank)), 1)

    def test_without_a_partner_in_the_window_there_is_no_value(self):
        self.pair(apart_minutes=60 * 8)
        self.assertEqual(selectors.derived_values(self.tank), [])

    def test_a_lonely_kh_value_yields_nothing(self):
        self.measure(self.kh, "5")
        self.assertEqual(selectors.derived_values(self.tank), [])

    def test_no_fallback_to_a_two_week_old_partner(self):
        """Ein CO₂ aus weit auseinanderliegenden Messungen sieht echt aus.

        Genau deshalb entsteht es nicht: lieber keine Zahl als eine, der man
        die Herkunft nicht ansieht.
        """
        self.measure(self.kh, "5", minutes_ago=60 * 24 * 14)
        self.measure(self.ph, "6.9")
        self.assertEqual(selectors.derived_values(self.tank), [])

    @override_settings(CO2_PAIR_WINDOW_HOURS=12)
    def test_the_window_size_is_configurable(self):
        self.pair(apart_minutes=60 * 8)
        self.assertEqual(len(selectors.derived_values(self.tank)), 1)

    def test_the_nearest_partner_wins(self):
        near = self.measure(self.kh, "5", minutes_ago=10)
        self.measure(self.kh, "9", minutes_ago=120)
        self.measure(self.ph, "7.0")

        values = selectors.derived_values(self.tank)
        newest = values[0]
        self.assertIn(near, newest.sources)
        self.assertAlmostEqual(float(newest.value), 15, delta=0.5)

    def test_the_pair_carries_the_later_of_the_two_timestamps(self):
        kh, ph = self.pair(apart_minutes=30)
        value = selectors.derived_values(self.tank)[0]
        self.assertEqual(value.measured_at, ph.measured_at)
        self.assertGreater(value.measured_at, kh.measured_at)

    def test_two_ph_values_around_one_kh_give_two_results(self):
        """Gepaart wird in beide Richtungen — sonst fiele der zweite pH aus."""
        self.measure(self.kh, "5", minutes_ago=60)
        self.measure(self.ph, "6.9", minutes_ago=120)
        self.measure(self.ph, "7.0")

        self.assertEqual(len(selectors.derived_values(self.tank)), 2)

    def test_values_of_another_tank_are_not_paired(self):
        other = create_tank(self.user, name="Zweitbecken", slug="zweitbecken")
        self.measure(self.kh, "5")
        Measurement.objects.create(
            tank=other, parameter=self.ph, value=Decimal("6.9"), measured_at=self.now
        )
        self.assertEqual(selectors.derived_values(self.tank), [])
        self.assertEqual(selectors.derived_values(other), [])


class Co2IsNeverStoredTests(Co2TestCase):
    def test_there_is_no_catalog_entry_to_record_it_by_hand(self):
        """Ein ``Parameter`` mit Schlüssel ``co2`` stünde im Erfassungsformular."""
        self.assertFalse(Parameter.objects.filter(key="co2").exists())

    def test_the_entry_form_does_not_offer_it(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("tanks:measurement-create", args=[self.tank.slug])
        )
        self.assertNotContains(response, "CO₂")

    def test_no_measurement_row_is_created(self):
        self.pair()
        selectors.derived_values(self.tank)
        self.assertEqual(Measurement.objects.count(), 2)

    def test_a_corrected_source_value_moves_the_result(self):
        """Der Grund, aus dem nicht gespeichert wird.

        Ein abgelegter Wert bliebe stehen und sähe dabei aus wie eine Messung.
        """
        _, ph = self.pair(kh="5", ph="7.0")
        self.assertAlmostEqual(float(selectors.latest_derived(self.tank)[0].value), 15, delta=0.5)

        ph.value = Decimal("6.8")
        ph.save()
        self.assertAlmostEqual(float(selectors.latest_derived(self.tank)[0].value), 24, delta=0.5)


class Co2DisplayTests(Co2TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_it_appears_in_the_measurement_tab_marked_as_calculated(self):
        self.pair()
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=messwerte")
        self.assertContains(response, "CO₂")
        self.assertContains(response, "berechnet")

    def test_it_appears_in_the_current_values_tile(self):
        self.pair()
        response = self.client.get(self.tank.get_absolute_url())
        self.assertContains(response, "CO₂")

    def test_without_a_pair_nothing_is_shown(self):
        """Kein Wert — weder im Reiter noch in der Kachel.

        In der Übersicht bleibt allein die Zeile im Zielbereichsblock stehen:
        die gehört zur Einstellung und ist keine Auskunft über das Wasser.
        """
        self.measure(self.kh, "5")

        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=messwerte")
        self.assertNotContains(response, "CO₂")

        overview = selectors.tank_measurement_overview(self.tank)
        self.assertEqual([row for row in overview if getattr(row, "is_derived", False)], [])

    def test_the_row_offers_no_edit_or_delete(self):
        """Zu ändern gibt es an einer gerechneten Zeile nichts."""
        self.pair()
        rows = selectors.tank_measurement_rows(self.tank)
        calculated = [row for row in rows if getattr(row, "is_derived", False)]
        self.assertEqual(len(calculated), 1)
        self.assertFalse(hasattr(calculated[0], "pk"))

    def test_the_measurement_list_mixes_both_in_chronological_order(self):
        self.pair()
        rows = selectors.tank_measurement_rows(self.tank)
        timestamps = [row.measured_at for row in rows]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
        self.assertEqual(len(rows), 3)

    def test_it_gets_its_own_curve(self):
        for minutes in (0, 60 * 24, 60 * 48):
            self.measure(self.kh, "5", minutes_ago=minutes)
            self.measure(self.ph, "6.9", minutes_ago=minutes)

        charts = derived_charts(self.tank)
        self.assertEqual(len(charts), 1)
        self.assertTrue(charts[0]["is_derived"])
        self.assertEqual(len(charts[0]["points"]), 3)
        self.assertIn("berechnete Werte", charts[0]["summary"])

    def test_without_a_pair_there_is_no_curve(self):
        self.measure(self.kh, "5")
        self.assertEqual(derived_charts(self.tank), [])


class Co2TargetTests(Co2TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse("tanks:derived-target-update", args=[self.tank.slug, "co2"])

    def test_the_default_range_is_15_to_25(self):
        self.pair(kh="5", ph="6.9")  # ≈ 19 mg/l
        value = selectors.latest_derived(self.tank)[0]
        self.assertEqual(value.target_label, "15–25 mg/l")
        self.assertEqual(value.status_code, Status.OK)

    def test_a_value_above_the_range_is_flagged(self):
        self.pair(kh="6", ph="6.7")  # ≈ 36 mg/l
        self.assertEqual(selectors.latest_derived(self.tank)[0].status_code, Status.CRITICAL)

    def test_the_range_can_be_overridden_per_tank(self):
        self.client.post(self.url, {"minimum": "10", "maximum": "18"})

        target = self.tank.derived_targets.get()
        self.assertEqual(target.key, "co2")
        self.assertEqual(target.range_label, "10–18 mg/l")

        self.pair(kh="6", ph="6.7")  # ≈ 36 mg/l, jetzt weit daneben
        self.assertEqual(selectors.latest_derived(self.tank)[0].status_code, Status.CRITICAL)

    def test_the_form_starts_from_the_default(self):
        response = self.client.get(self.url)
        self.assertContains(response, "15")
        self.assertContains(response, "25")

    def test_a_reversed_range_is_refused(self):
        self.client.post(self.url, {"minimum": "25", "maximum": "15"})
        self.assertFalse(self.tank.derived_targets.exists())

    def test_the_override_can_be_reset(self):
        self.client.post(self.url, {"minimum": "10", "maximum": "18"})
        self.client.post(reverse("tanks:derived-target-reset", args=[self.tank.slug, "co2"]))

        self.assertFalse(self.tank.derived_targets.exists())
        self.pair(kh="5", ph="6.9")
        self.assertEqual(selectors.latest_derived(self.tank)[0].target_label, "15–25 mg/l")

    def test_the_overview_lists_the_range(self):
        response = self.client.get(self.tank.get_absolute_url())
        self.assertContains(response, "15–25 mg/l")

    def test_an_unknown_derived_key_is_a_404(self):
        response = self.client.get(
            reverse("tanks:derived-target-update", args=[self.tank.slug, "kalium"])
        )
        self.assertEqual(response.status_code, 404)

    def test_a_foreign_tank_stays_out_of_reach(self):
        stranger = create_user("fremder")
        foreign = create_tank(stranger, name="Fremdbecken", slug="fremdbecken")
        response = self.client.get(
            reverse("tanks:derived-target-update", args=[foreign.slug, "co2"])
        )
        self.assertEqual(response.status_code, 404)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhotoWriteTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)

    def test_several_photos_are_uploaded_at_once(self):
        self.client.post(
            reverse("tanks:photo-create", args=[self.tank.slug]),
            {
                "images": [image_upload("eins.png"), image_upload("zwei.png")],
                "taken_on": timezone.localdate().isoformat(),
                "caption": "Neuer Aufbau",
            },
        )
        self.assertEqual(self.tank.photos.count(), 2)
        self.assertEqual(self.tank.photos.first().caption, "Neuer Aufbau")

    def test_the_caption_can_be_changed(self):
        photo = TankPhoto.objects.create(
            tank=self.tank, image=image_upload(), taken_on=timezone.localdate()
        )
        self.client.post(
            reverse("tanks:photo-update", args=[self.tank.slug, photo.pk]),
            {"caption": "Nach dem Rückschnitt", "taken_on": photo.taken_on.isoformat()},
        )
        photo.refresh_from_db()
        self.assertEqual(photo.caption, "Nach dem Rückschnitt")

    def test_deleting_asks_first(self):
        photo = TankPhoto.objects.create(
            tank=self.tank, image=image_upload(), taken_on=timezone.localdate()
        )
        url = reverse("tanks:photo-delete", args=[self.tank.slug, photo.pk])
        self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertTrue(TankPhoto.objects.exists())

        self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertFalse(TankPhoto.objects.exists())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhotoLightboxTests(TestCase):
    """Die Großansicht: was sie zeigt, wie geblättert wird, was sie nicht lädt."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tank = create_tank(self.user)
        # Absteigend nach Datum sortiert (``TankPhoto.Meta.ordering``): das
        # jüngste Foto steht vorn.
        self.oldest, self.middle, self.newest = (
            TankPhoto.objects.create(
                tank=self.tank,
                image=photo_upload(f"{days}.jpg", size=(1200, 800)),
                caption=f"Bild {days}",
                taken_on=timezone.localdate() - timedelta(days=days),
            )
            for days in (2, 1, 0)
        )

    def url(self, photo):
        return reverse("tanks:photo-detail", args=[self.tank.slug, photo.pk])

    def open(self, photo):
        return self.client.get(self.url(photo), HTTP_HX_REQUEST="true")

    def test_the_overlay_shows_the_preview_variant(self):
        response = self.open(self.middle)

        self.assertTemplateUsed(response, "tanks/partials/photo_lightbox.html")
        self.assertContains(response, self.middle.preview.url)

    def test_the_overlay_names_caption_date_and_event(self):
        event = Event.objects.create(
            tank=self.tank,
            category=Event.Category.OBSERVATION,
            title="Cryptocoryne schiebt Blätter",
            occurred_at=timezone.now(),
        )
        self.middle.event = event
        self.middle.save(update_fields=["event"])

        response = self.open(self.middle)

        self.assertContains(response, "Bild 1")
        self.assertContains(response, self.middle.taken_on.strftime("%d.%m.%Y"))
        self.assertContains(response, "Cryptocoryne schiebt Blätter")

    def test_the_original_is_linked_in_full_resolution(self):
        self.assertContains(self.open(self.middle), self.middle.image.url)

    def test_editing_and_deleting_are_reachable(self):
        response = self.open(self.middle)

        self.assertContains(
            response, reverse("tanks:photo-update", args=[self.tank.slug, self.middle.pk])
        )
        self.assertContains(
            response, reverse("tanks:photo-delete", args=[self.tank.slug, self.middle.pk])
        )

    def test_the_middle_photo_has_both_neighbours(self):
        response = self.open(self.middle)

        self.assertContains(response, self.url(self.newest))
        self.assertContains(response, self.url(self.oldest))

    def test_the_first_photo_has_no_predecessor(self):
        """Kein Rundlauf: am Ende der Reihe fehlt der Pfeil, statt zu springen."""
        response = self.open(self.newest)

        self.assertNotContains(response, 'data-lightbox-prev')
        self.assertContains(response, 'data-lightbox-next')

    def test_the_last_photo_has_no_successor(self):
        response = self.open(self.oldest)

        self.assertContains(response, 'data-lightbox-prev')
        self.assertNotContains(response, 'data-lightbox-next')

    def test_a_single_photo_has_no_arrows_at_all(self):
        alone = create_tank(self.user, name="Nano", slug="nano")
        photo = TankPhoto.objects.create(
            tank=alone, image=image_upload(), taken_on=timezone.localdate()
        )

        response = self.client.get(
            reverse("tanks:photo-detail", args=[alone.slug, photo.pk]), HTTP_HX_REQUEST="true"
        )

        self.assertNotContains(response, 'data-lightbox-prev')
        self.assertNotContains(response, 'data-lightbox-next')

    def test_without_htmx_the_same_address_is_a_page(self):
        """Ohne Skript gibt es keine Überlagerung — dafür eine ganze Seite."""
        response = self.client.get(self.url(self.middle))

        self.assertTemplateUsed(response, "tanks/photo_detail.html")
        self.assertContains(response, self.middle.preview.url)

    def test_a_foreign_photo_stays_hidden(self):
        stranger = create_tank(create_user("fremd"), name="Fremdbecken", slug="fremdbecken")
        photo = TankPhoto.objects.create(
            tank=stranger, image=image_upload(), taken_on=timezone.localdate()
        )

        response = self.client.get(reverse("tanks:photo-detail", args=[stranger.slug, photo.pk]))

        self.assertEqual(response.status_code, 404)

    def test_the_gallery_links_to_the_large_view_without_loading_it(self):
        """Der Sinn der Kacheln: die Vorschauen kommen erst auf Klick."""
        response = self.client.get(
            reverse("tanks:tab", args=[self.tank.slug, "galerie"]), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, self.url(self.middle))
        self.assertContains(response, 'id="mad-lightbox"')
        self.assertContains(response, self.middle.thumbnail.url)
        self.assertNotContains(response, self.middle.preview.url)


class PhotoNeighbourTests(TestCase):
    """Die Reihenfolge beim Blättern ist die der Galerie — auch am selben Tag."""

    def setUp(self):
        self.tank = create_tank(create_user())
        today = timezone.localdate()
        self.first, self.second = (
            TankPhoto.objects.create(tank=self.tank, image=image_upload(), taken_on=today)
            for _ in range(2)
        )

    def test_photos_of_the_same_day_are_ordered_by_key(self):
        """``-taken_on, -pk``: das zuletzt hochgeladene Bild steht vorn."""
        self.assertEqual(
            selectors.photo_neighbours(self.tank, self.second), (None, self.first.pk)
        )
        self.assertEqual(
            selectors.photo_neighbours(self.tank, self.first), (self.second.pk, None)
        )

    def test_a_photo_of_another_tank_has_no_neighbours(self):
        other = create_tank(create_user("zweiter"), name="Anderes", slug="anderes")

        self.assertEqual(selectors.photo_neighbours(other, self.first), (None, None))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhotoVariantTests(TestCase):
    """Was beim Speichern eines Fotos entsteht — und was verschwindet."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)

    def photo(self, **kwargs):
        upload = kwargs.pop("upload", None) or photo_upload(size=(1200, 800))
        return TankPhoto.objects.create(
            tank=self.tank, image=upload, taken_on=timezone.localdate(), **kwargs
        )

    def opened(self, field):
        field.open("rb")
        try:
            return Image.open(BytesIO(field.read()))
        finally:
            field.close()

    def test_both_variants_are_created(self):
        photo = self.photo()

        self.assertTrue(photo.thumbnail)
        self.assertTrue(photo.preview)
        self.assertEqual(self.opened(photo.thumbnail).size, (400, 267))

    def test_the_dimensions_of_the_original_are_recorded(self):
        photo = self.photo()

        self.assertEqual((photo.width, photo.height), (1200, 800))

    def test_a_small_image_is_not_scaled_up(self):
        """Die Vorschau ist hier nur eine Kopie — größer als das Original wird nichts."""
        photo = self.photo(upload=photo_upload(size=(300, 200)))

        self.assertEqual(self.opened(photo.preview).size, (300, 200))

    def test_a_portrait_stands_upright(self):
        """EXIF-Orientierung 6 heißt: um 90° gedreht aufgenommen."""
        photo = self.photo(upload=photo_upload(size=(1200, 800), orientation=6))

        self.assertEqual((photo.width, photo.height), (800, 1200))
        self.assertEqual(self.opened(photo.thumbnail).size, (267, 400))

    def test_the_location_is_removed_everywhere(self):
        photo = self.photo(upload=photo_upload(size=(1200, 800), located=True))

        for field in (photo.image, photo.thumbnail, photo.preview):
            with self.subTest(field=field.name):
                self.assertNotIn(GPS_IFD, self.opened(field).getexif())

    def test_the_original_keeps_its_size(self):
        """Nur die Koordinaten fallen weg, nicht die Auflösung."""
        photo = self.photo(upload=photo_upload(size=(1200, 800), located=True))

        self.assertEqual(self.opened(photo.image).size, (1200, 800))

    def test_a_replaced_image_gets_new_variants(self):
        """Sonst zeigte die Kachel weiter das alte Bild."""
        photo = self.photo()
        before = Path(photo.thumbnail.path)

        photo.image = photo_upload("anders.jpg", size=(800, 1200))
        photo.save()

        self.assertEqual((photo.width, photo.height), (800, 1200))
        self.assertEqual(self.opened(photo.thumbnail).size, (267, 400))
        self.assertFalse(before.exists())

    def test_saving_again_does_not_rebuild(self):
        photo = self.photo()
        before = photo.thumbnail.name

        photo.caption = "Nach dem Rückschnitt"
        photo.save()

        self.assertEqual(photo.thumbnail.name, before)

    def test_deleting_removes_every_file(self):
        photo = self.photo()
        paths = [Path(field.path) for field in (photo.image, photo.thumbnail, photo.preview)]
        self.assertTrue(all(path.exists() for path in paths))

        photo.delete()

        self.assertFalse(any(path.exists() for path in paths))

    def test_the_variant_carries_the_scaled_dimensions(self):
        photo = self.photo()

        self.assertEqual(photo.thumb.url, photo.thumbnail.url)
        self.assertEqual((photo.thumb.width, photo.thumb.height), (400, 267))

    def test_without_a_variant_the_original_stands_in(self):
        """Bestandsdaten bleiben sichtbar, solange der Befehl noch nicht lief."""
        photo = self.photo()
        photo.thumbnail = ""
        photo.save(update_fields=["thumbnail"])

        self.assertEqual(photo.thumb.url, photo.image.url)
        self.assertEqual((photo.thumb.width, photo.thumb.height), (1200, 800))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TankCoverTests(TestCase):
    """Das Titelbild des Beckens — seit dem Herauslösen von ``CoverImageMixin``
    dieselbe Verarbeitung wie am Gerät, und derselbe Ablageort wie zuvor."""

    def setUp(self):
        self.tank = create_tank(create_user())

    def test_the_variants_are_created(self):
        self.tank.cover_image = photo_upload(size=(1200, 800))
        self.tank.save()

        self.assertTrue(self.tank.cover_thumbnail)
        self.assertTrue(self.tank.cover_preview)
        self.assertEqual((self.tank.cover_width, self.tank.cover_height), (1200, 800))

    def size_of(self, field):
        field.open("rb")
        try:
            return Image.open(BytesIO(field.read())).size
        finally:
            field.close()

    def test_a_rotated_cover_stands_upright(self):
        """EXIF-Orientierung 6 heißt: um 90° gedreht aufgenommen.

        Die Varianten tragen keinen EXIF-Block mehr — die Drehung muss also in
        den Pixeln stecken, sonst läge das Titelbild in der Kachel quer, während
        das Original aufrecht steht.
        """
        self.tank.cover_image = photo_upload(size=(1200, 800), orientation=6)
        self.tank.save()

        self.assertEqual((self.tank.cover_width, self.tank.cover_height), (800, 1200))
        self.assertEqual(self.size_of(self.tank.cover_thumbnail), (267, 400))

    def test_the_recorded_dimensions_match_the_delivered_file(self):
        """Was im ``<img>`` steht, muss zu der Datei passen, die geladen wird."""
        self.tank.cover_image = photo_upload(size=(1200, 800))
        self.tank.save()

        for variant, field in (
            (self.tank.thumb, self.tank.cover_thumbnail),
            (self.tank.large, self.tank.cover_preview),
        ):
            with self.subTest(variant=field.name):
                self.assertEqual(variant.url, field.url)
                self.assertEqual((variant.width, variant.height), self.size_of(field))

    def test_the_files_still_lie_under_tanks(self):
        """Der Ordner kommt jetzt aus dem Mixin — der Pfad bleibt derselbe."""
        self.tank.cover_image = photo_upload(size=(300, 200))
        self.tank.save()

        self.assertTrue(self.tank.cover_image.name.startswith(f"tanks/{self.tank.pk}/cover/"))
        self.assertIn("/cover/thumbs/", self.tank.cover_thumbnail.name)
        self.assertIn("/cover/preview/", self.tank.cover_preview.name)

    def test_the_location_is_removed(self):
        self.tank.cover_image = photo_upload(size=(1200, 800), located=True)
        self.tank.save()

        self.assertNotIn(GPS_IFD, Image.open(BytesIO(self.tank.cover_image.read())).getexif())

    def test_clearing_removes_every_file(self):
        self.tank.cover_image = photo_upload(size=(1200, 800))
        self.tank.save()
        paths = [
            Path(field.path)
            for field in (self.tank.cover_image, self.tank.cover_thumbnail, self.tank.cover_preview)
        ]

        self.tank.clear_cover()

        self.assertFalse(any(path.exists() for path in paths))
        self.assertFalse(self.tank.has_cover)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class GenerateThumbnailsCommandTests(TestCase):
    """Der Befehl für die Bestandsdaten."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        self.photo = TankPhoto.objects.create(
            tank=self.tank, image=photo_upload(size=(1200, 800)), taken_on=timezone.localdate()
        )

    def strip(self):
        """Zustand vor der Umstellung: nur das Original, keine Varianten."""
        TankPhoto.objects.filter(pk=self.photo.pk).update(
            thumbnail="", preview="", width=None, height=None
        )
        self.photo.refresh_from_db()

    def run_command(self, *args):
        output = StringIO()
        call_command("generate_thumbnails", *args, stdout=output, stderr=StringIO())
        self.photo.refresh_from_db()
        return output.getvalue()

    def test_missing_variants_are_created(self):
        self.strip()

        self.run_command()

        self.assertTrue(self.photo.thumbnail)
        self.assertTrue(self.photo.preview)
        self.assertEqual((self.photo.width, self.photo.height), (1200, 800))

    def test_a_second_run_changes_nothing(self):
        self.strip()
        self.run_command()
        before = self.photo.thumbnail.name

        self.run_command()

        self.assertEqual(self.photo.thumbnail.name, before)

    def test_force_rebuilds_an_existing_variant(self):
        """Erst am Datensatz vorbei löschen, dann sieht man den Unterschied."""
        path = Path(self.photo.thumbnail.path)
        path.unlink()

        self.run_command()
        self.assertFalse(path.exists())

        self.run_command("--force")
        self.assertTrue(Path(self.photo.thumbnail.path).exists())

    def test_a_single_model_can_be_named(self):
        self.strip()

        self.run_command("--model", "catalog.PlantImage")

        self.assertFalse(self.photo.thumbnail)
