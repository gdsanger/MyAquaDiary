import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Event,
    MaintenanceSchedule,
    Measurement,
    MeasurementValue,
    Parameter,
    Photo,
    Tank,
    TankParameterTarget,
    WaterType,
)

User = get_user_model()


def make_tank(owner, **kwargs):
    defaults = dict(
        name="Südamerika",
        water_type=WaterType.SUESSWASSER,
        started_on=datetime.date(2024, 1, 1),
    )
    defaults.update(kwargs)
    return Tank.objects.create(owner=owner, **defaults)


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
                "photos-0-position": "0",
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
