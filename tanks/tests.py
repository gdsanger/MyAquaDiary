import datetime
import io
import os
import shutil
import tempfile
from decimal import Decimal

from PIL import Image, ExifTags
from PIL.TiffImagePlugin import IFDRational

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import AnimalGroup, CatalogAnimal, CatalogPlant, Difficulty, GrowthForm, Placement

from .models import (
    Event,
    MaintenanceSchedule,
    Measurement,
    MeasurementValue,
    Parameter,
    Photo,
    Tank,
    TankAnimal,
    TankAnimalMovement,
    TankParameterTarget,
    TankPlant,
    WaterType,
    read_exif_taken_at,
)

User = get_user_model()


def make_catalog_animal(**kwargs):
    defaults = dict(
        scientific_name="Mikrogeophagus ramirezi",
        group=AnimalGroup.FISCH,
        difficulty=Difficulty.MEDIUM,
    )
    defaults.update(kwargs)
    return CatalogAnimal.objects.create(**defaults)


def make_catalog_plant(**kwargs):
    defaults = dict(
        scientific_name="Microsorum pteropus",
        growth_form=GrowthForm.EPIPHYTE,
        placement=Placement.MIDGROUND,
        difficulty=Difficulty.EASY,
        growth_rate="slow",
        light_demand="low",
        co2_demand="low",
    )
    defaults.update(kwargs)
    return CatalogPlant.objects.create(**defaults)


def make_tank(owner, **kwargs):
    defaults = dict(
        name="Südamerika",
        water_type=WaterType.SUESSWASSER,
        started_on=datetime.date(2024, 1, 1),
    )
    defaults.update(kwargs)
    return Tank.objects.create(owner=owner, **defaults)


def make_jpeg_bytes(taken_at=None, with_gps=False, size=(20, 20)):
    """Baut ein JPEG mit echten EXIF-Daten für die Photo-Tests — DateTimeOriginal
    und GPS-Koordinaten liegen in Sub-IFDs, die Pillow nur schreibt, wenn der
    jeweilige Sub-IFD-Tag am obersten `Exif`-Objekt selbst gesetzt ist."""

    image = Image.new("RGB", size, color="red")
    exif = image.getexif()
    if taken_at:
        exif[ExifTags.IFD.Exif] = {36867: taken_at.strftime("%Y:%m:%d %H:%M:%S")}
    if with_gps:
        exif[ExifTags.IFD.GPSInfo] = {
            1: "N",
            2: (IFDRational(52, 1), IFDRational(30, 1), IFDRational(0, 1)),
            3: "E",
            4: (IFDRational(13, 1), IFDRational(24, 1), IFDRational(0, 1)),
        }
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


class TankSlugTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )

    def test_slug_is_generated_from_name(self):
        tank = make_tank(self.user_a, name="Südamerika")
        self.assertEqual(tank.slug, "sudamerika")

    def test_slug_collision_for_same_owner_gets_a_numeric_suffix(self):
        first = make_tank(self.user_a, name="Südamerika")
        second = make_tank(self.user_a, name="Südamerika")
        self.assertEqual(first.slug, "sudamerika")
        self.assertEqual(second.slug, "sudamerika-2")

    def test_two_owners_may_share_the_same_slug(self):
        tank_a = make_tank(self.user_a, name="Südamerika")
        tank_b = make_tank(self.user_b, name="Südamerika")
        self.assertEqual(tank_a.slug, tank_b.slug)


class TankIsolationTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank_a = make_tank(self.user_a, name="Beckens A")
        self.tank_b = make_tank(self.user_b, name="Beckens B")

    def test_manager_for_user_only_returns_own_tanks(self):
        self.assertEqual(list(Tank.objects.for_user(self.user_a)), [self.tank_a])
        self.assertEqual(list(Tank.objects.for_user(self.user_b)), [self.tank_b])

    def test_list_view_only_shows_own_tanks(self):
        self.client.force_login(self.user_b)
        response = self.client.get(reverse("tanks:list"))
        self.assertContains(response, "Beckens B")
        self.assertNotContains(response, "Beckens A")

    def test_detail_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            reverse("tanks:detail", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 404)

    def test_detail_view_allows_access_to_own_tank(self):
        self.client.force_login(self.user_a)
        response = self.client.get(
            reverse("tanks:detail", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 200)

    def test_update_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            reverse("tanks:update", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.post(
            reverse("tanks:delete", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Tank.objects.filter(pk=self.tank_a.pk).exists())

    def test_owner_can_delete_their_own_tank(self):
        self.client.force_login(self.user_a)
        response = self.client.post(
            reverse("tanks:delete", kwargs={"slug": self.tank_a.slug})
        )
        self.assertRedirects(response, reverse("tanks:list"))
        self.assertFalse(Tank.objects.filter(pk=self.tank_a.pk).exists())

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("tanks:list"))
        self.assertEqual(response.status_code, 302)


def tank_form_data(**overrides):
    data = dict(
        name="Neues Becken",
        model_name="",
        length_cm="",
        height_cm="",
        depth_cm="",
        volume_gross_l="",
        volume_net_l="",
        water_type=WaterType.SUESSWASSER,
        biotope="",
        started_on="2024-01-01",
        shut_down_on="",
        description="",
        substrate="",
        hardscape="",
        filtration="",
        lighting="",
        co2="",
        fertilization="",
        **{
            "photos-TOTAL_FORMS": "0",
            "photos-INITIAL_FORMS": "0",
            "photos-MIN_NUM_FORMS": "0",
            "photos-MAX_NUM_FORMS": "1000",
        },
    )
    data.update(overrides)
    return data


class TankCreateViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.client.force_login(self.user)

    def test_create_assigns_current_user_as_owner(self):
        response = self.client.post(reverse("tanks:create"), tank_form_data(), follow=True)
        self.assertEqual(response.status_code, 200)
        tank = Tank.objects.get(name="Neues Becken")
        self.assertEqual(tank.owner, self.user)


class TankOverviewSplitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.client.force_login(self.user)
        self.active_tank = make_tank(self.user, name="Aktiv")
        self.dissolved_tank = make_tank(
            self.user, name="Aufgelöst", shut_down_on=datetime.date(2024, 6, 1)
        )

    def test_is_dissolved_property(self):
        self.assertFalse(self.active_tank.is_dissolved)
        self.assertTrue(self.dissolved_tank.is_dissolved)

    def test_list_view_separates_active_and_dissolved_tanks(self):
        response = self.client.get(reverse("tanks:list"))
        self.assertEqual(list(response.context["active_tanks"]), [self.active_tank])
        self.assertEqual(list(response.context["dissolved_tanks"]), [self.dissolved_tank])

    def test_dissolved_tank_stays_readable(self):
        response = self.client.get(
            reverse("tanks:detail", kwargs={"slug": self.dissolved_tank.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aufgelöst")


class TankParameterTargetInlineTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user_a)
        self.parameter = Parameter.objects.create(key="ph", name="pH-Wert", decimals=1)
        self.client.force_login(self.user_a)

    def test_edit_form_is_rendered_for_a_parameter_without_a_target_yet(self):
        response = self.client.get(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pH-Wert")

    def test_post_creates_a_target(self):
        response = self.client.post(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            ),
            {"minimum": "6.5", "target": "7.0", "maximum": "7.5"},
        )
        self.assertEqual(response.status_code, 200)
        target = TankParameterTarget.objects.get(tank=self.tank, parameter=self.parameter)
        self.assertEqual(str(target.minimum), "6.50")
        self.assertEqual(str(target.target), "7.00")
        self.assertEqual(str(target.maximum), "7.50")

    def test_post_updates_an_existing_target(self):
        TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.parameter, minimum=6, target=7, maximum=7.5
        )
        self.client.post(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            ),
            {"minimum": "6.2", "target": "7.0", "maximum": "7.8"},
        )
        self.assertEqual(
            TankParameterTarget.objects.filter(tank=self.tank, parameter=self.parameter).count(),
            1,
        )
        target = TankParameterTarget.objects.get(tank=self.tank, parameter=self.parameter)
        self.assertEqual(str(target.minimum), "6.20")

    def test_cannot_edit_targets_of_another_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            )
        )
        self.assertEqual(response.status_code, 404)


class SeedParametersCommandTests(TestCase):
    def test_seeds_the_full_default_catalog(self):
        call_command("seed_parameters")
        expected_keys = {
            "ph", "kh", "gh", "temp", "lf", "nh4", "no2", "no3", "po4",
            "fe", "k", "mg", "cu", "o2",
        }
        self.assertEqual(set(Parameter.objects.values_list("key", flat=True)), expected_keys)

    def test_command_is_idempotent(self):
        call_command("seed_parameters")
        call_command("seed_parameters")
        self.assertEqual(Parameter.objects.count(), 14)


class MeasurementCo2PropertyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.ph = Parameter.objects.create(key="ph", name="pH-Wert", decimals=1)
        self.kh = Parameter.objects.create(key="kh", name="KH", unit="°dKH", decimals=1)

    def test_co2_is_computed_from_kh_and_ph(self):
        measurement = Measurement.objects.create(tank=self.tank)
        MeasurementValue.objects.create(measurement=measurement, parameter=self.ph, value=Decimal("7.0"))
        MeasurementValue.objects.create(measurement=measurement, parameter=self.kh, value=Decimal("6.0"))
        self.assertEqual(measurement.co2_mg_l, Decimal("18.0"))

    def test_co2_is_none_when_ph_or_kh_missing(self):
        measurement = Measurement.objects.create(tank=self.tank)
        MeasurementValue.objects.create(measurement=measurement, parameter=self.ph, value=Decimal("7.0"))
        self.assertIsNone(measurement.co2_mg_l)

    def test_co2_is_not_stored_and_reflects_later_corrections(self):
        measurement = Measurement.objects.create(tank=self.tank)
        ph_value = MeasurementValue.objects.create(
            measurement=measurement, parameter=self.ph, value=Decimal("7.0")
        )
        MeasurementValue.objects.create(measurement=measurement, parameter=self.kh, value=Decimal("6.0"))
        self.assertEqual(measurement.co2_mg_l, Decimal("18.0"))
        ph_value.value = Decimal("6.7")
        ph_value.save()
        self.assertEqual(measurement.co2_mg_l, Decimal("35.9"))


class MeasurementValueValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.ph = Parameter.objects.create(key="ph", name="pH-Wert", decimals=1)
        self.no3 = Parameter.objects.create(
            key="no3", name="Nitrat (NO3)", decimals=1, supports_below_detection=True
        )
        self.measurement = Measurement.objects.create(tank=self.tank)

    def test_rejects_neither_value_nor_below_detection(self):
        value = MeasurementValue(measurement=self.measurement, parameter=self.ph)
        with self.assertRaises(ValidationError):
            value.full_clean()

    def test_rejects_both_value_and_below_detection(self):
        value = MeasurementValue(
            measurement=self.measurement, parameter=self.no3, value=Decimal("0.1"), below_detection=True
        )
        with self.assertRaises(ValidationError):
            value.full_clean()

    def test_rejects_below_detection_for_parameter_without_support(self):
        value = MeasurementValue(measurement=self.measurement, parameter=self.ph, below_detection=True)
        with self.assertRaises(ValidationError):
            value.full_clean()

    def test_below_detection_is_valid_for_supported_parameter(self):
        value = MeasurementValue(measurement=self.measurement, parameter=self.no3, below_detection=True)
        value.full_clean()  # should not raise
        value.save()
        self.assertIsNone(value.value)
        self.assertTrue(value.below_detection)


class MeasurementValueStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.ph = Parameter.objects.create(key="ph", name="pH-Wert", decimals=1)
        TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.ph, minimum=Decimal("6.5"), maximum=Decimal("7.5")
        )
        self.measurement = Measurement.objects.create(tank=self.tank)

    def test_value_below_target_is_low(self):
        value = MeasurementValue.objects.create(measurement=self.measurement, parameter=self.ph, value=Decimal("6.0"))
        self.assertEqual(value.status, "low")

    def test_value_within_target_is_ok(self):
        value = MeasurementValue.objects.create(measurement=self.measurement, parameter=self.ph, value=Decimal("7.0"))
        self.assertEqual(value.status, "ok")

    def test_value_above_target_is_high(self):
        value = MeasurementValue.objects.create(measurement=self.measurement, parameter=self.ph, value=Decimal("8.0"))
        self.assertEqual(value.status, "high")

    def test_value_without_target_has_no_status(self):
        other = Parameter.objects.create(key="gh", name="GH", decimals=1)
        value = MeasurementValue.objects.create(measurement=self.measurement, parameter=other, value=Decimal("8.0"))
        self.assertIsNone(value.status)


def measurement_post_data(value_rows, **overrides):
    data = {
        "measured_at": "2024-03-01T10:00",
        "note": "",
        "source": "manual",
        "values-TOTAL_FORMS": str(len(value_rows)),
        "values-INITIAL_FORMS": "0",
        "values-MIN_NUM_FORMS": "0",
        "values-MAX_NUM_FORMS": "1000",
        "photos-TOTAL_FORMS": "0",
        "photos-INITIAL_FORMS": "0",
        "photos-MIN_NUM_FORMS": "0",
        "photos-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(value_rows):
        data[f"values-{index}-parameter"] = str(row["parameter"].pk)
        data[f"values-{index}-value"] = row.get("value", "")
        if row.get("below_detection"):
            data[f"values-{index}-below_detection"] = "on"
    data.update(overrides)
    return data


class MeasurementViewTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user_a)
        self.ph = Parameter.objects.create(key="ph", name="pH-Wert", decimals=1)
        self.no3 = Parameter.objects.create(
            key="no3", name="Nitrat (NO3)", decimals=1, supports_below_detection=True
        )
        self.client.force_login(self.user_a)

    def test_create_measurement_stores_below_detection_and_not_zero(self):
        response = self.client.post(
            reverse("tanks:measurement-create", kwargs={"slug": self.tank.slug}),
            measurement_post_data(
                [
                    {"parameter": self.ph, "value": "7.2"},
                    {"parameter": self.no3, "below_detection": True},
                ]
            ),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        measurement = Measurement.objects.get(tank=self.tank)
        no3_value = measurement.values.get(parameter=self.no3)
        self.assertIsNone(no3_value.value)
        self.assertTrue(no3_value.below_detection)
        ph_value = measurement.values.get(parameter=self.ph)
        self.assertEqual(ph_value.value, Decimal("7.200"))

    def test_create_rejects_value_and_below_detection_together(self):
        response = self.client.post(
            reverse("tanks:measurement-create", kwargs={"slug": self.tank.slug}),
            measurement_post_data(
                [{"parameter": self.no3, "value": "0.1", "below_detection": True}]
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Measurement.objects.filter(tank=self.tank).exists())

    def test_list_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(reverse("tanks:measurement-list", kwargs={"slug": self.tank.slug}))
        self.assertEqual(response.status_code, 404)

    def test_owner_can_delete_a_measurement(self):
        measurement = Measurement.objects.create(tank=self.tank)
        response = self.client.post(
            reverse("tanks:measurement-delete", kwargs={"slug": self.tank.slug, "pk": measurement.pk})
        )
        self.assertRedirects(response, reverse("tanks:measurement-list", kwargs={"slug": self.tank.slug}))
        self.assertFalse(Measurement.objects.filter(pk=measurement.pk).exists())

    def test_csv_export_marks_below_detection_and_includes_co2(self):
        kh = Parameter.objects.create(key="kh", name="KH", decimals=1)
        measurement = Measurement.objects.create(tank=self.tank)
        MeasurementValue.objects.create(measurement=measurement, parameter=self.ph, value=Decimal("7.0"))
        MeasurementValue.objects.create(measurement=measurement, parameter=kh, value=Decimal("6.0"))
        MeasurementValue.objects.create(measurement=measurement, parameter=self.no3, below_detection=True)

        response = self.client.get(reverse("tanks:measurement-export", kwargs={"slug": self.tank.slug}))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("n.n.", content)
        self.assertIn("18.0", content)

    def test_chart_data_endpoint_returns_values_for_parameter(self):
        measurement = Measurement.objects.create(
            tank=self.tank,
            measured_at=datetime.datetime(2024, 3, 1, 10, 0, tzinfo=datetime.timezone.utc),
        )
        MeasurementValue.objects.create(measurement=measurement, parameter=self.ph, value=Decimal("7.1"))

        response = self.client.get(
            reverse("tanks:measurement-chart-data", kwargs={"slug": self.tank.slug}),
            {"parameter": "ph"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["values"], [7.1])


def event_post_data(**overrides):
    data = {
        "occurred_at": "2024-03-01T10:00",
        "category": Event.Category.WATER_CHANGE,
        "title": "Wasserwechsel",
        "description": "",
        "water_changed_l": "",
        "photos-TOTAL_FORMS": "0",
        "photos-INITIAL_FORMS": "0",
        "photos-MIN_NUM_FORMS": "0",
        "photos-MAX_NUM_FORMS": "1000",
    }
    data.update(overrides)
    return data


class EventWaterChangePercentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )

    def test_percent_is_computed_from_volume_net(self):
        tank = make_tank(self.user, volume_net_l=Decimal("100.0"))
        event = Event.objects.create(
            tank=tank, title="Wasserwechsel", water_changed_l=Decimal("25.0")
        )
        self.assertEqual(event.water_change_percent, Decimal("25.0"))

    def test_percent_is_none_without_water_changed_l(self):
        tank = make_tank(self.user, volume_net_l=Decimal("100.0"))
        event = Event.objects.create(tank=tank, title="Beobachtung")
        self.assertIsNone(event.water_change_percent)

    def test_percent_is_none_without_tank_volume(self):
        tank = make_tank(self.user)
        event = Event.objects.create(
            tank=tank, title="Wasserwechsel", water_changed_l=Decimal("25.0")
        )
        self.assertIsNone(event.water_change_percent)


class EventViewTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user_a, volume_net_l=Decimal("100.0"))
        self.client.force_login(self.user_a)

    def test_create_event_stores_water_changed_l(self):
        response = self.client.post(
            reverse("tanks:event-create", kwargs={"slug": self.tank.slug}),
            event_post_data(water_changed_l="20.0"),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        event = Event.objects.get(tank=self.tank)
        self.assertEqual(event.water_changed_l, Decimal("20.0"))
        self.assertEqual(event.water_change_percent, Decimal("20.0"))

    def test_create_event_with_photo_attaches_it_to_tank_gallery(self):
        gif_bytes = (
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00\x00"
            b"\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        )
        image = SimpleUploadedFile("test.gif", gif_bytes, content_type="image/gif")
        data = event_post_data(
            **{
                "photos-TOTAL_FORMS": "1",
                "photos-0-image": image,
                "photos-0-caption": "Vorher",
            }
        )
        response = self.client.post(
            reverse("tanks:event-create", kwargs={"slug": self.tank.slug}), data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        event = Event.objects.get(tank=self.tank)
        photo = Photo.objects.get(event=event)
        self.assertEqual(photo.tank, self.tank)

    def test_list_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(reverse("tanks:event-list", kwargs={"slug": self.tank.slug}))
        self.assertEqual(response.status_code, 404)

    def test_owner_can_delete_an_event(self):
        event = Event.objects.create(tank=self.tank, title="Beobachtung")
        response = self.client.post(
            reverse("tanks:event-delete", kwargs={"slug": self.tank.slug, "pk": event.pk})
        )
        self.assertRedirects(response, reverse("tanks:event-list", kwargs={"slug": self.tank.slug}))
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_list_view_filters_by_category(self):
        Event.objects.create(
            tank=self.tank, title="Erster Wasserwechsel", category=Event.Category.WATER_CHANGE
        )
        Event.objects.create(
            tank=self.tank, title="Schnecke entdeckt", category=Event.Category.OBSERVATION
        )

        response = self.client.get(
            reverse("tanks:event-list", kwargs={"slug": self.tank.slug}), {"kategorie": "observation"}
        )
        self.assertContains(response, "Schnecke entdeckt")
        self.assertNotContains(response, "Erster Wasserwechsel")

    def test_list_view_filters_by_date_range(self):
        Event.objects.create(
            tank=self.tank,
            title="Alt",
            occurred_at=datetime.datetime(2024, 1, 1, 10, 0, tzinfo=datetime.timezone.utc),
        )
        Event.objects.create(
            tank=self.tank,
            title="Neu",
            occurred_at=datetime.datetime(2024, 6, 1, 10, 0, tzinfo=datetime.timezone.utc),
        )

        response = self.client.get(
            reverse("tanks:event-list", kwargs={"slug": self.tank.slug}),
            {"von": "2024-05-01", "bis": "2024-12-31"},
        )
        self.assertContains(response, "Neu")
        self.assertNotContains(response, "Alt")


class EventFromScheduleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.client.force_login(self.user)
        self.schedule = MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Filter reinigen",
            event_category=Event.Category.MAINTENANCE,
            interval=MaintenanceSchedule.Interval.CUSTOM,
            interval_days=14,
            next_due_on=datetime.date(2024, 3, 1),
        )

    def test_create_form_prefills_title_and_category_from_schedule(self):
        response = self.client.get(
            reverse("tanks:event-create", kwargs={"slug": self.tank.slug}),
            {"termin": self.schedule.pk},
        )
        self.assertContains(response, "Filter reinigen")

    def test_completing_event_marks_schedule_done_and_reschedules(self):
        response = self.client.post(
            reverse("tanks:event-create", kwargs={"slug": self.tank.slug}),
            event_post_data(
                occurred_at="2024-03-05T12:00",
                category=Event.Category.MAINTENANCE,
                title="Filter reinigen",
                schedule=str(self.schedule.pk),
            ),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        event = Event.objects.get(tank=self.tank)
        self.assertEqual(event.schedule, self.schedule)

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.last_done_on, datetime.date(2024, 3, 5))
        self.assertEqual(self.schedule.next_due_on, datetime.date(2024, 3, 19))

    def test_next_due_on_is_computed_from_completion_not_old_due_date(self):
        """Ein versäumter Termin darf sich nicht vom alten Solltermin aus
        fortschreiben, sonst türmen sich Fälligkeiten übereinander."""

        self.schedule.mark_done(datetime.date(2024, 4, 1))
        self.assertEqual(self.schedule.next_due_on, datetime.date(2024, 4, 15))

    def test_is_due_reflects_next_due_on(self):
        self.assertTrue(self.schedule.is_due)
        self.schedule.mark_done(timezone.localdate())
        self.assertFalse(self.schedule.is_due)

    def test_inactive_schedule_is_never_due_or_upcoming(self):
        self.schedule.is_active = False
        self.schedule.save(update_fields=["is_active"])
        self.assertFalse(self.schedule.is_due)
        self.assertFalse(self.schedule.is_upcoming)


class MaintenanceScheduleIntervalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)

    def make_schedule(self, **overrides):
        defaults = dict(
            tank=self.tank,
            title="Wasserwechsel",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=datetime.date(2024, 1, 31),
        )
        defaults.update(overrides)
        return MaintenanceSchedule.objects.create(**defaults)

    def test_weekly_interval_adds_seven_days(self):
        schedule = self.make_schedule(interval=MaintenanceSchedule.Interval.WEEKLY)
        self.assertEqual(
            schedule.compute_next_due_on(datetime.date(2024, 1, 31)), datetime.date(2024, 2, 7)
        )

    def test_monthly_interval_clamps_to_last_day_of_shorter_month(self):
        schedule = self.make_schedule(interval=MaintenanceSchedule.Interval.MONTHLY)
        self.assertEqual(
            schedule.compute_next_due_on(datetime.date(2024, 1, 31)), datetime.date(2024, 2, 29)
        )

    def test_yearly_interval_adds_twelve_months(self):
        schedule = self.make_schedule(interval=MaintenanceSchedule.Interval.YEARLY)
        self.assertEqual(
            schedule.compute_next_due_on(datetime.date(2024, 1, 31)), datetime.date(2025, 1, 31)
        )

    def test_custom_interval_requires_interval_days(self):
        schedule = self.make_schedule(interval=MaintenanceSchedule.Interval.CUSTOM, interval_days=None)
        with self.assertRaises(ValidationError):
            schedule.full_clean()

    def test_is_upcoming_within_lead_days(self):
        schedule = self.make_schedule(
            next_due_on=timezone.localdate() + datetime.timedelta(days=2), lead_days=2
        )
        self.assertFalse(schedule.is_due)
        self.assertTrue(schedule.is_upcoming)

    def test_is_upcoming_false_beyond_lead_days(self):
        schedule = self.make_schedule(
            next_due_on=timezone.localdate() + datetime.timedelta(days=5), lead_days=2
        )
        self.assertFalse(schedule.is_due)
        self.assertFalse(schedule.is_upcoming)


class ScheduleCrudViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.client.force_login(self.user)

    def test_create_schedule(self):
        response = self.client.post(
            reverse("tanks:schedule-create", kwargs={"slug": self.tank.slug}),
            {
                "title": "Wasserwechsel 30 %",
                "event_category": Event.Category.WATER_CHANGE,
                "description": "",
                "interval": MaintenanceSchedule.Interval.WEEKLY,
                "interval_days": "",
                "next_due_on": "2024-06-01",
                "lead_days": "2",
                "notify_email": "on",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        schedule = MaintenanceSchedule.objects.get(tank=self.tank)
        self.assertEqual(schedule.title, "Wasserwechsel 30 %")

    def test_custom_interval_without_days_is_rejected(self):
        response = self.client.post(
            reverse("tanks:schedule-create", kwargs={"slug": self.tank.slug}),
            {
                "title": "Sonderaufgabe",
                "event_category": Event.Category.MAINTENANCE,
                "description": "",
                "interval": MaintenanceSchedule.Interval.CUSTOM,
                "interval_days": "",
                "next_due_on": "2024-06-01",
                "lead_days": "2",
                "notify_email": "on",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MaintenanceSchedule.objects.filter(tank=self.tank).exists())

    def test_delete_schedule(self):
        schedule = MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Düngen",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=datetime.date(2024, 6, 1),
        )
        response = self.client.post(
            reverse("tanks:schedule-delete", kwargs={"slug": self.tank.slug, "pk": schedule.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MaintenanceSchedule.objects.filter(pk=schedule.pk).exists())

    def test_other_users_tank_is_not_accessible(self):
        other = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        other_tank = make_tank(other, name="Anderes Becken")
        response = self.client.get(
            reverse("tanks:schedule-list", kwargs={"slug": other_tank.slug})
        )
        self.assertEqual(response.status_code, 404)


class DashboardDueSchedulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.client.force_login(self.user)

    def test_due_and_upcoming_schedules_are_listed(self):
        due = MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Fällig",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate(),
        )
        upcoming = MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Bald",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate() + datetime.timedelta(days=1),
            lead_days=2,
        )
        MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Weit weg",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate() + datetime.timedelta(days=30),
        )
        MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Deaktiviert",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate(),
            is_active=False,
        )

        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, due.title)
        self.assertContains(response, upcoming.title)
        self.assertNotContains(response, "Weit weg")
        self.assertNotContains(response, "Deaktiviert")


class SendDueRemindersCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)

    def test_sends_one_mail_per_user_and_sets_last_reminded_on(self):
        schedule = MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Fällig",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate(),
        )
        call_command("send_due_reminders")
        self.assertEqual(len(mail.outbox), 1)
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_reminded_on, timezone.localdate())

    def test_does_not_send_twice_on_the_same_day(self):
        MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Fällig",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate(),
            last_reminded_on=timezone.localdate(),
        )
        call_command("send_due_reminders")
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_schedule_is_not_reminded(self):
        MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Fällig",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate(),
            is_active=False,
        )
        call_command("send_due_reminders")
        self.assertEqual(len(mail.outbox), 0)

    def test_not_yet_due_schedule_is_not_reminded(self):
        MaintenanceSchedule.objects.create(
            tank=self.tank,
            title="Weit weg",
            interval=MaintenanceSchedule.Interval.WEEKLY,
            next_due_on=timezone.localdate() + datetime.timedelta(days=30),
        )
        call_command("send_due_reminders")
        self.assertEqual(len(mail.outbox), 0)


class TankHistoryViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.client.force_login(self.user)

    def test_history_combines_events_and_measurements_in_chronological_order(self):
        Event.objects.create(
            tank=self.tank,
            title="Wasserwechsel",
            occurred_at=datetime.datetime(2024, 2, 1, 10, 0, tzinfo=datetime.timezone.utc),
        )
        Measurement.objects.create(
            tank=self.tank,
            measured_at=datetime.datetime(2024, 3, 1, 10, 0, tzinfo=datetime.timezone.utc),
        )
        Event.objects.create(
            tank=self.tank,
            title="Beobachtung",
            occurred_at=datetime.datetime(2024, 1, 1, 10, 0, tzinfo=datetime.timezone.utc),
        )

        response = self.client.get(reverse("tanks:history", kwargs={"slug": self.tank.slug}))
        self.assertEqual(response.status_code, 200)
        titles_in_order = [entry["object"] for entry in response.context["history"]]
        self.assertEqual(
            [type(obj).__name__ for obj in titles_in_order],
            ["Measurement", "Event", "Event"],
        )


class TankAnimalStockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.animal = make_catalog_animal(min_group_size=6)

    def test_creating_stock_books_an_initial_movement(self):
        tank_animal = TankAnimal.objects.create(
            tank=self.tank,
            animal=self.animal,
            quantity=8,
            added_on=datetime.date(2024, 5, 1),
        )
        self.assertEqual(tank_animal.movements.count(), 1)
        movement = tank_animal.movements.first()
        self.assertEqual(movement.direction, TankAnimalMovement.Direction.IN)
        self.assertEqual(movement.quantity, 8)
        self.assertTrue(tank_animal.stock_matches_movements)

    def test_booking_an_in_movement_increases_quantity(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=6)
        TankAnimalMovement.objects.create(
            tank_animal=tank_animal,
            direction=TankAnimalMovement.Direction.IN,
            reason=TankAnimalMovement.Reason.BREEDING,
            quantity=3,
            occurred_on=datetime.date(2024, 6, 1),
        )
        tank_animal.refresh_from_db()
        self.assertEqual(tank_animal.quantity, 9)
        self.assertTrue(tank_animal.stock_matches_movements)

    def test_booking_an_out_movement_decreases_quantity(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=6)
        TankAnimalMovement.objects.create(
            tank_animal=tank_animal,
            direction=TankAnimalMovement.Direction.OUT,
            reason=TankAnimalMovement.Reason.DIED,
            quantity=2,
            occurred_on=datetime.date(2024, 6, 1),
        )
        tank_animal.refresh_from_db()
        self.assertEqual(tank_animal.quantity, 4)

    def test_stock_cannot_fall_below_zero(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=2)
        with self.assertRaises(ValidationError):
            TankAnimalMovement.objects.create(
                tank_animal=tank_animal,
                direction=TankAnimalMovement.Direction.OUT,
                reason=TankAnimalMovement.Reason.SOLD,
                quantity=5,
                occurred_on=datetime.date(2024, 6, 1),
            )
        tank_animal.refresh_from_db()
        self.assertEqual(tank_animal.quantity, 2)

    def test_deleting_a_movement_reverses_its_effect_on_stock(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=6)
        movement = TankAnimalMovement.objects.create(
            tank_animal=tank_animal,
            direction=TankAnimalMovement.Direction.OUT,
            reason=TankAnimalMovement.Reason.SOLD,
            quantity=2,
            occurred_on=datetime.date(2024, 6, 1),
        )
        tank_animal.refresh_from_db()
        self.assertEqual(tank_animal.quantity, 4)

        movement.delete()
        tank_animal.refresh_from_db()
        self.assertEqual(tank_animal.quantity, 6)

    def test_transfer_out_requires_target_tank(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=6)
        movement = TankAnimalMovement(
            tank_animal=tank_animal,
            direction=TankAnimalMovement.Direction.OUT,
            reason=TankAnimalMovement.Reason.TRANSFER_OUT,
            quantity=2,
            occurred_on=datetime.date(2024, 6, 1),
        )
        with self.assertRaises(ValidationError):
            movement.full_clean()

    def test_is_below_min_group_size_warns_when_present_and_understocked(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=3)
        self.assertTrue(tank_animal.is_below_min_group_size)

    def test_is_below_min_group_size_ignores_planned_status(self):
        tank_animal = TankAnimal.objects.create(
            tank=self.tank, animal=self.animal, quantity=3, status=TankAnimal.Status.PLANNED
        )
        self.assertFalse(tank_animal.is_below_min_group_size)

    def test_is_below_min_group_size_false_when_enough_animals(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=6)
        self.assertFalse(tank_animal.is_below_min_group_size)

    def test_parameter_warnings_flag_values_outside_species_tolerance(self):
        animal = make_catalog_animal(
            scientific_name="Paracheirodon axelrodi", temp_min_c=24, temp_max_c=27
        )
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=animal, quantity=10)
        Parameter.objects.get_or_create(key="temp", defaults={"name": "Temperatur", "unit": "°C"})
        measurement = Measurement.objects.create(tank=self.tank)
        MeasurementValue.objects.create(
            measurement=measurement, parameter=Parameter.objects.get(key="temp"), value=30
        )
        warnings = tank_animal.parameter_warnings
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["value"], Decimal("30"))

    def test_parameter_warnings_empty_without_measurement(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=6)
        self.assertEqual(tank_animal.parameter_warnings, [])


class TankAnimalViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.other_user = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.animal = make_catalog_animal()
        self.client.force_login(self.user)

    def test_create_view_adds_stock_with_initial_movement(self):
        response = self.client.post(
            reverse("tanks:animal-create", kwargs={"slug": self.tank.slug}),
            data={
                "animal": self.animal.pk,
                "label": "Zuchtgruppe",
                "status": TankAnimal.Status.PRESENT,
                "quantity": 5,
                "quantity_male": "",
                "quantity_female": "",
                "added_on": "2024-05-01",
                "origin": "Händler",
                "note": "",
            },
        )
        self.assertRedirects(
            response, reverse("tanks:animal-list", kwargs={"slug": self.tank.slug})
        )
        tank_animal = TankAnimal.objects.get(tank=self.tank)
        self.assertEqual(tank_animal.quantity, 5)
        self.assertEqual(tank_animal.movements.count(), 1)

    def test_list_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("tanks:animal-list", kwargs={"slug": self.tank.slug}))
        self.assertEqual(response.status_code, 404)

    def test_movement_view_books_a_movement(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=6)
        response = self.client.post(
            reverse(
                "tanks:animal-movements", kwargs={"slug": self.tank.slug, "pk": tank_animal.pk}
            ),
            data={
                "direction": TankAnimalMovement.Direction.OUT,
                "reason": TankAnimalMovement.Reason.SOLD,
                "quantity": 2,
                "occurred_on": "2024-06-01",
                "target_tank": "",
                "note": "",
            },
        )
        self.assertRedirects(
            response,
            reverse(
                "tanks:animal-movements", kwargs={"slug": self.tank.slug, "pk": tank_animal.pk}
            ),
        )
        tank_animal.refresh_from_db()
        self.assertEqual(tank_animal.quantity, 4)

    def test_movement_view_rejects_overselling(self):
        tank_animal = TankAnimal.objects.create(tank=self.tank, animal=self.animal, quantity=1)
        response = self.client.post(
            reverse(
                "tanks:animal-movements", kwargs={"slug": self.tank.slug, "pk": tank_animal.pk}
            ),
            data={
                "direction": TankAnimalMovement.Direction.OUT,
                "reason": TankAnimalMovement.Reason.SOLD,
                "quantity": 5,
                "occurred_on": "2024-06-01",
                "target_tank": "",
                "note": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        tank_animal.refresh_from_db()
        self.assertEqual(tank_animal.quantity, 1)


class TankPlantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.other_user = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.plant = make_catalog_plant()
        self.client.force_login(self.user)

    def test_identification_certain_defaults_to_true(self):
        tank_plant = TankPlant.objects.create(tank=self.tank, plant=self.plant)
        self.assertTrue(tank_plant.identification_certain)

    def test_parameter_warnings_flag_values_outside_species_tolerance(self):
        plant = make_catalog_plant(
            scientific_name="Eleocharis acicularis", ph_min=Decimal("6.0"), ph_max=Decimal("7.0")
        )
        tank_plant = TankPlant.objects.create(tank=self.tank, plant=plant)
        Parameter.objects.get_or_create(key="ph", defaults={"name": "pH-Wert", "unit": ""})
        measurement = Measurement.objects.create(tank=self.tank)
        MeasurementValue.objects.create(
            measurement=measurement, parameter=Parameter.objects.get(key="ph"), value=Decimal("8.0")
        )
        warnings = tank_plant.parameter_warnings
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["value"], Decimal("8.0"))

    def test_create_view_adds_planting(self):
        response = self.client.post(
            reverse("tanks:plant-create", kwargs={"slug": self.tank.slug}),
            data={
                "plant": self.plant.pk,
                "status": TankPlant.Status.PRESENT,
                "quantity": 10,
                "placement": "links hinten",
                "attached_to": "",
                "added_on": "2024-05-01",
                "removed_on": "",
                "identification_certain": "on",
                "note": "",
            },
        )
        self.assertRedirects(
            response, reverse("tanks:plant-list", kwargs={"slug": self.tank.slug})
        )
        self.assertTrue(TankPlant.objects.filter(tank=self.tank, plant=self.plant).exists())

    def test_list_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("tanks:plant-list", kwargs={"slug": self.tank.slug}))
        self.assertEqual(response.status_code, 404)


class TempMediaTestCase(TestCase):
    """Basisklasse für Tests, die tatsächlich Dateien schreiben — mit eigenem
    MEDIA_ROOT, damit weder das Repo-`mediafiles`-Verzeichnis vollläuft noch
    Tests sich gegenseitig Dateien unterschieben."""

    def setUp(self):
        super().setUp()
        media_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_dir, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media_dir)
        override.enable()
        self.addCleanup(override.disable)


class PhotoImageProcessingTests(TempMediaTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)

    def test_upload_generates_a_thumbnail(self):
        image = SimpleUploadedFile("photo.jpg", make_jpeg_bytes(), content_type="image/jpeg")
        photo = Photo.objects.create(tank=self.tank, image=image)
        self.assertTrue(photo.thumbnail.name)
        with Image.open(photo.thumbnail.path) as thumbnail:
            self.assertLessEqual(thumbnail.width, 480)
            self.assertLessEqual(thumbnail.height, 480)

    def test_gps_data_is_removed_from_the_stored_image(self):
        image = SimpleUploadedFile(
            "photo.jpg", make_jpeg_bytes(with_gps=True), content_type="image/jpeg"
        )
        photo = Photo.objects.create(tank=self.tank, image=image)
        with Image.open(photo.image.path) as stored_image:
            self.assertNotIn(ExifTags.IFD.GPSInfo, stored_image.getexif())

    def test_deleting_a_photo_removes_image_and_thumbnail_from_storage(self):
        image = SimpleUploadedFile("photo.jpg", make_jpeg_bytes(), content_type="image/jpeg")
        photo = Photo.objects.create(tank=self.tank, image=image)
        image_path = photo.image.path
        thumbnail_path = photo.thumbnail.path
        self.assertTrue(os.path.exists(image_path))

        photo.delete()

        self.assertFalse(os.path.exists(image_path))
        self.assertFalse(os.path.exists(thumbnail_path))

    def test_deleting_the_tank_cascades_and_removes_the_photo_file(self):
        image = SimpleUploadedFile("photo.jpg", make_jpeg_bytes(), content_type="image/jpeg")
        photo = Photo.objects.create(tank=self.tank, image=image)
        image_path = photo.image.path

        self.tank.delete()

        self.assertFalse(os.path.exists(image_path))


class ReadExifTakenAtTests(TestCase):
    def test_extracts_datetime_original(self):
        taken_at = datetime.datetime(2024, 5, 1, 12, 30, 0)
        image = SimpleUploadedFile(
            "photo.jpg", make_jpeg_bytes(taken_at=taken_at), content_type="image/jpeg"
        )
        result = read_exif_taken_at(image)
        self.assertIsNotNone(result)
        self.assertEqual(
            timezone.localtime(result).replace(tzinfo=None) if timezone.is_aware(result) else result,
            taken_at,
        )

    def test_returns_none_without_exif_data(self):
        image = SimpleUploadedFile("photo.jpg", make_jpeg_bytes(), content_type="image/jpeg")
        self.assertIsNone(read_exif_taken_at(image))


class PhotoConstraintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.measurement = Measurement.objects.create(tank=self.tank)
        self.event = Event.objects.create(tank=self.tank, title="Wasserwechsel")

    def test_photo_may_belong_to_measurement_only(self):
        photo = Photo(tank=self.tank, measurement=self.measurement)
        photo.full_clean(exclude=["image"])

    def test_photo_may_belong_to_event_only(self):
        photo = Photo(tank=self.tank, event=self.event)
        photo.full_clean(exclude=["image"])

    def test_photo_cannot_belong_to_measurement_and_event_at_once(self):
        photo = Photo(tank=self.tank, measurement=self.measurement, event=self.event)
        with self.assertRaises(ValidationError):
            photo.full_clean(exclude=["image"])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TankGalleryViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.other_user = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.client.force_login(self.user)

    def test_upload_accepts_multiple_images_at_once(self):
        images = [
            SimpleUploadedFile(f"photo{i}.jpg", make_jpeg_bytes(), content_type="image/jpeg")
            for i in range(2)
        ]
        response = self.client.post(
            reverse("tanks:gallery", kwargs={"slug": self.tank.slug}),
            {"images": images, "caption": "Becken von vorne", "is_full_tank_shot": "on"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Photo.objects.filter(tank=self.tank).count(), 2)
        self.assertTrue(Photo.objects.filter(tank=self.tank, is_full_tank_shot=True).exists())

    def test_upload_prefills_taken_at_from_exif(self):
        taken_at = datetime.datetime(2023, 7, 4, 9, 15, 0)
        image = SimpleUploadedFile(
            "photo.jpg", make_jpeg_bytes(taken_at=taken_at), content_type="image/jpeg"
        )
        self.client.post(
            reverse("tanks:gallery", kwargs={"slug": self.tank.slug}),
            {"images": [image], "caption": "", "is_full_tank_shot": ""},
            follow=True,
        )
        photo = Photo.objects.get(tank=self.tank)
        localized = timezone.localtime(photo.taken_at) if timezone.is_aware(photo.taken_at) else photo.taken_at
        self.assertEqual(localized.replace(tzinfo=None), taken_at)

    def test_filters_by_assignment(self):
        measurement = Measurement.objects.create(tank=self.tank)
        Photo.objects.create(tank=self.tank, image=self._image())
        Photo.objects.create(tank=self.tank, image=self._image(), measurement=measurement)

        response = self.client.get(
            reverse("tanks:gallery", kwargs={"slug": self.tank.slug}), {"zuordnung": "messung"}
        )
        self.assertEqual(len(response.context["photos"]), 1)

    def test_filters_by_period(self):
        Photo.objects.create(
            tank=self.tank,
            image=self._image(),
            taken_at=timezone.make_aware(datetime.datetime(2024, 1, 1, 10, 0)),
        )
        Photo.objects.create(
            tank=self.tank,
            image=self._image(),
            taken_at=timezone.make_aware(datetime.datetime(2024, 6, 1, 10, 0)),
        )

        response = self.client.get(
            reverse("tanks:gallery", kwargs={"slug": self.tank.slug}),
            {"von": "2024-05-01", "bis": "2024-12-31"},
        )
        self.assertEqual(len(response.context["photos"]), 1)

    def test_setting_a_photo_as_cover_photo(self):
        photo = Photo.objects.create(tank=self.tank, image=self._image())
        response = self.client.post(
            reverse("tanks:photo-set-cover", kwargs={"slug": self.tank.slug, "pk": photo.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.tank.refresh_from_db()
        self.assertEqual(self.tank.cover_photo_id, photo.pk)

    def test_deleting_a_photo_via_the_gallery(self):
        photo = Photo.objects.create(tank=self.tank, image=self._image())
        response = self.client.post(
            reverse("tanks:photo-delete", kwargs={"slug": self.tank.slug, "pk": photo.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Photo.objects.filter(pk=photo.pk).exists())

    def test_other_users_tank_is_not_accessible(self):
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("tanks:gallery", kwargs={"slug": self.tank.slug}))
        self.assertEqual(response.status_code, 404)

    def _image(self):
        return SimpleUploadedFile("photo.jpg", make_jpeg_bytes(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TankTimelineViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user)
        self.client.force_login(self.user)

    def test_only_full_tank_shots_are_listed_in_chronological_order(self):
        image = lambda: SimpleUploadedFile("photo.jpg", make_jpeg_bytes(), content_type="image/jpeg")
        Photo.objects.create(tank=self.tank, image=image(), is_full_tank_shot=False)
        older = Photo.objects.create(
            tank=self.tank,
            image=image(),
            is_full_tank_shot=True,
            taken_at=timezone.make_aware(datetime.datetime(2024, 1, 1)),
        )
        newer = Photo.objects.create(
            tank=self.tank,
            image=image(),
            is_full_tank_shot=True,
            taken_at=timezone.make_aware(datetime.datetime(2024, 6, 1)),
        )

        response = self.client.get(reverse("tanks:timeline", kwargs={"slug": self.tank.slug}))

        self.assertEqual(list(response.context["photos"]), [older, newer])
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.enums import Status
from core.testing import (
    create_animal,
    create_measurement,
    create_plant,
    create_tank,
    create_task,
    create_user,
    stock,
)
from tanks.charts import parameter_series
from tanks.models import Parameter, Planting, Tank, classify_value
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
