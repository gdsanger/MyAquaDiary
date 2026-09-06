"""Das Gerät am Becken — Zuordnung, Warnungen, Ereignisse, Verbrauch.

Hier steht, was der Fremdschlüssel gebracht hat: der Gerätefehler erreicht das
Becken, die Schaltaktion landet in der Beckenhistorie, und die Auswertung
gruppiert über eine Kennung statt über einen Namen, den jemand ändern kann.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.enums import Status
from core.testing import create_tank, create_user, tank_named
from services import devices as device_service
from services import energy
from services.forms import DeviceForm, ManualDeviceForm, ShellyDeviceForm, controls_for
from services.models import Device, DeviceEvent, DeviceReading
from services.tests.test_devices import fake_service, make_device
from services.tests.test_eheim_client import CLASSICVARIO_PAYLOAD, FakeResponse
from services.tests.test_shelly_devices import make_plug
from tanks import selectors
from tanks.models import Event


class DeviceTankTestCase(TestCase):
    def setUp(self):
        self.user = create_user("max")
        self.client.force_login(self.user)
        self.tank = tank_named(self.user, "Becken 1")


class StatusFromReadingTests(DeviceTankTestCase):
    """Der Fehlercode steht nicht nur im Messwert, sondern auch am Gerät."""

    def setUp(self):
        super().setUp()
        self.device = make_device(self.user, tank=self.tank)

    def probe_with(self, payload):
        service, _ = fake_service(self.device, FakeResponse(200, payload))
        with patch("services.devices.service_for", return_value=service):
            return device_service.probe(self.device)

    def test_an_error_code_makes_the_device_critical(self):
        self.probe_with({**CLASSICVARIO_PAYLOAD, "errorCode": 1})

        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Status.CRITICAL)
        self.assertEqual(self.device.status_message, "Rotor blockiert")

    def test_a_clean_reading_clears_the_status_again(self):
        self.probe_with({**CLASSICVARIO_PAYLOAD, "errorCode": 2})
        self.probe_with({**CLASSICVARIO_PAYLOAD, "errorCode": 0})

        self.device.refresh_from_db()
        self.assertEqual(self.device.status, Status.OK)
        self.assertEqual(self.device.status_message, "")

    def test_a_device_without_connection_keeps_its_hand_written_status(self):
        manual = Device.objects.create(
            owner=self.user,
            tank=self.tank,
            name="Heizstab",
            kind=Device.Kind.HEATER,
            status=Status.WARN,
            status_message="Regelt zu warm",
        )
        reading = DeviceReading.objects.create(
            device=manual, read_at=timezone.now(), payload={}, error_code=0
        )

        self.assertEqual(manual.update_status_from(reading), [])
        self.assertEqual(manual.status, Status.WARN)


class TankWarningTests(DeviceTankTestCase):
    """Was am Gerät kritisch ist, gehört auf das Dashboard — als Beckenwarnung."""

    def test_the_eheim_error_code_appears_as_a_tank_warning(self):
        device = make_device(self.user, tank=self.tank, name="Außenfilter")
        service, _ = fake_service(device, FakeResponse(200, {**CLASSICVARIO_PAYLOAD, "errorCode": 1}))
        with patch("services.devices.service_for", return_value=service):
            device_service.probe(device)

        warnings = selectors.warnings(self.user)

        self.assertEqual(
            [(warning["status"], warning["title"], warning["tank"]) for warning in warnings],
            [(Status.CRITICAL, "Außenfilter: Kritisch", self.tank)],
        )
        self.assertEqual(warnings[0]["detail"], "Rotor blockiert")

    def test_it_is_shown_on_the_dashboard(self):
        device = make_device(self.user, tank=self.tank, name="Außenfilter")
        service, _ = fake_service(device, FakeResponse(200, {**CLASSICVARIO_PAYLOAD, "errorCode": 2}))
        with patch("services.devices.service_for", return_value=service):
            device_service.probe(device)

        response = self.client.get(reverse("dashboard:tile-warnings"))

        self.assertContains(response, "Luft im Filter")
        self.assertContains(response, self.tank.name)

    def test_due_maintenance_of_a_documented_device_is_a_tank_warning(self):
        Device.objects.create(
            owner=self.user,
            tank=self.tank,
            name="CO₂-Anlage",
            kind=Device.Kind.CO2,
            maintenance_interval_days=30,
            last_maintenance_on=timezone.localdate() - timedelta(days=90),
        )

        titles = [warning["title"] for warning in selectors.warnings(self.user)]

        self.assertIn("Wartung fällig: CO₂-Anlage", titles)

    def test_foreign_devices_stay_out(self):
        stranger = create_user("eva")
        make_device(
            stranger,
            tank=create_tank(stranger, name="Fremdes Becken", slug="fremdes-becken"),
            name="Fremder Filter",
            status=Status.CRITICAL,
        )

        self.assertEqual(selectors.warnings(self.user), [])


class TankEventTests(DeviceTankTestCase):
    """Jede Schaltaktion steht zusätzlich in der Beckenhistorie."""

    def setUp(self):
        super().setUp()
        self.device = make_device(self.user, tank=self.tank, name="Außenfilter")

    def execute(self, action="on", *, responses=(FakeResponse(200, {}),)):
        service, _ = fake_service(self.device, *responses)
        with patch("services.devices.service_for", return_value=service):
            return device_service.execute(self.device, action, user=self.user)

    def test_switching_creates_an_equipment_event_at_the_tank(self):
        self.execute()

        event = Event.objects.get()
        self.assertEqual(event.tank, self.tank)
        self.assertEqual(event.category, Event.Category.EQUIPMENT)
        self.assertEqual(event.title, "Filter eingeschaltet")
        self.assertIn("Außenfilter", event.description)
        self.assertEqual(event.created_by, self.user)
        self.assertEqual(event.occurred_at, DeviceEvent.objects.get().occurred_at)

    def test_a_failed_command_is_in_the_history_too(self):
        import requests

        self.execute(responses=(requests.Timeout("still"),))

        event = Event.objects.get()
        self.assertIn("fehlgeschlagen", event.title)


class ConnectedKindTests(DeviceTankTestCase):
    """Nicht jedes Gerät ist anbindbar — erfassbar ist trotzdem jedes."""

    def test_a_documented_device_offers_no_controls(self):
        device = Device.objects.create(
            owner=self.user, tank=self.tank, name="Beleuchtung", kind=Device.Kind.LIGHT
        )

        self.assertFalse(device.is_connected)
        self.assertEqual(controls_for(device), {})
        self.assertFalse(device_service.probe(device))
        self.assertFalse(device_service.execute(device, "on", user=self.user))

    def test_it_can_be_created_through_the_page(self):
        response = self.client.post(
            reverse("services:device_add"),
            {
                "name": "Heizstab",
                "kind": Device.Kind.HEATER,
                "tank": self.tank.pk,
                "status": Status.OK,
                "maintenance_interval_days": 180,
                "is_active": "on",
            },
            follow=True,
        )

        device = Device.objects.get()
        self.assertEqual(device.owner, self.user)
        self.assertEqual(device.tank, self.tank)
        self.assertEqual(device.kind, Device.Kind.HEATER)
        self.assertContains(response, "Heizstab")

    def test_the_form_offers_no_connected_kinds(self):
        kinds = [value for value, _label in ManualDeviceForm(user=self.user).fields["kind"].choices]

        self.assertNotIn(Device.Kind.SHELLY_PLUG, kinds)
        self.assertIn(Device.Kind.HEATER, kinds)

    def test_the_detail_page_shows_no_status_query(self):
        device = Device.objects.create(
            owner=self.user, tank=self.tank, name="Beleuchtung", kind=Device.Kind.LIGHT
        )

        response = self.client.get(reverse("services:device_detail", args=[device.pk]))

        self.assertNotContains(response, reverse("services:device_status", args=[device.pk]))
        self.assertContains(response, "nicht angebunden")


class TankChoiceTests(DeviceTankTestCase):
    """Das Becken ist Pflicht — und es ist eines des Benutzers."""

    def setUp(self):
        super().setUp()
        self.stranger = create_user("eva")
        self.foreign_tank = create_tank(self.stranger, name="Fremdes Becken", slug="fremd")

    def test_every_device_form_offers_only_own_tanks(self):
        for form_class in (DeviceForm, ShellyDeviceForm, ManualDeviceForm):
            with self.subTest(form=form_class.__name__):
                tanks = form_class(user=self.user).fields["tank"].queryset
                self.assertEqual(list(tanks), [self.tank])

    def test_a_foreign_tank_is_refused(self):
        form = ManualDeviceForm(
            {"name": "Heizstab", "kind": Device.Kind.HEATER, "tank": self.foreign_tank.pk,
             "status": Status.OK},
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("tank", form.errors)

    def test_a_device_without_a_tank_is_refused(self):
        form = ManualDeviceForm(
            {"name": "Heizstab", "kind": Device.Kind.HEATER, "status": Status.OK},
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("tank", form.errors)


class TankTabTests(DeviceTankTestCase):
    """Der Beckenreiter zeigt dieselben Geräte wie der Bereich /geraete/."""

    def test_both_pages_list_the_same_devices(self):
        make_plug(self.user, tank=self.tank, name="Licht")
        Device.objects.create(
            owner=self.user, tank=self.tank, name="Heizstab", kind=Device.Kind.HEATER
        )

        tab = self.client.get(f"{self.tank.get_absolute_url()}?reiter=geraete")
        area = self.client.get(reverse("services:device_list"))

        for name in ("Licht", "Heizstab"):
            with self.subTest(device=name):
                self.assertContains(tab, name)
                self.assertContains(area, name)
        self.assertContains(tab, reverse("services:device_detail", args=[Device.objects.first().pk]))


class UsageGroupingTests(DeviceTankTestCase):
    """Gruppiert wird über die Beckenkennung, nicht über den Namen."""

    def add_reading(self, device, watt_hours, hours_ago=0):
        DeviceReading.objects.create(
            device=device,
            read_at=timezone.now() - timedelta(hours=hours_ago),
            payload={},
            energy_total_wh=Decimal(str(watt_hours)),
        )

    def test_renaming_the_tank_does_not_split_the_group(self):
        first = make_plug(self.user, tank=self.tank, name="Licht")
        second = make_plug(self.user, tank=self.tank, name="Filter", host="192.168.1.62")
        for device in (first, second):
            self.add_reading(device, 1000, hours_ago=2)
            self.add_reading(device, 1500, hours_ago=1)

        self.tank.name = "Gesellschaftsbecken"
        self.tank.save(update_fields=["name"])
        usages = energy.usage_by_tank(self.user, energy.PERIOD_MONTH)

        self.assertEqual([usage.label for usage in usages], ["Gesellschaftsbecken"])
        self.assertEqual(usages[0].kwh, Decimal("1.000"))

    def test_two_tanks_with_the_same_name_stay_apart(self):
        twin = create_tank(self.user, name="Becken 1", slug="becken-1-zwei")
        first = make_plug(self.user, tank=self.tank, name="Licht")
        second = make_plug(self.user, tank=twin, name="Licht 2", host="192.168.1.62")
        self.add_reading(first, 1000, hours_ago=2)
        self.add_reading(first, 1200, hours_ago=1)
        self.add_reading(second, 5000, hours_ago=2)
        self.add_reading(second, 5300, hours_ago=1)

        usages = energy.usage_by_tank(self.user, energy.PERIOD_MONTH)

        self.assertEqual([usage.kwh for usage in usages], [Decimal("0.300"), Decimal("0.200")])
        self.assertEqual({usage.tank for usage in usages}, {self.tank, twin})


class OwnershipTests(DeviceTankTestCase):
    def test_a_tank_of_another_user_is_no_valid_home(self):
        stranger = create_user("eva")
        device = Device(
            owner=self.user,
            tank=create_tank(stranger, name="Fremdes Becken", slug="fremd"),
            name="Heizstab",
            kind=Device.Kind.HEATER,
        )

        with self.assertRaises(Exception) as caught:
            device.full_clean()

        self.assertIn("tank", getattr(caught.exception, "message_dict", {}))

    def test_deleting_the_tank_takes_its_devices(self):
        device = make_plug(self.user, tank=self.tank)

        self.tank.delete()

        self.assertFalse(Device.objects.filter(pk=device.pk).exists())


class UserModelTests(TestCase):
    def test_the_user_still_reaches_the_devices(self):
        user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        device = make_plug(user)

        self.assertEqual(list(user.devices.all()), [device])
        self.assertEqual(list(device.tank.devices.all()), [device])
