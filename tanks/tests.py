import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from PIL import Image

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
from tanks import selectors
from tanks.charts import parameter_series
from tanks.models import (
    BOTANICALS_DAYS,
    NUTRIENT_DEPOT_DAYS,
    CareTask,
    Event,
    HardscapeItem,
    Measurement,
    Parameter,
    Planting,
    SubstrateLayer,
    Tank,
    TankParameterTarget,
    TankPhoto,
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
        values = {m.parameter.key: m.value for m in self.tank.measurements.all()}
        self.assertEqual(values, {"ph": Decimal("7.200"), "no2": Decimal("0.000")})

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
