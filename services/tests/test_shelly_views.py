"""Seiten rund um die Steckdosen: anbinden, ansehen, schalten, auswerten."""

from decimal import Decimal
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import tank_named
from services.models import Device, DeviceEvent, DeviceReading
from services.shelly import ShellyClient
from services.tests.test_eheim_client import FakeResponse, FakeSession
from services.tests.test_shelly_client import GEN1_STATUS, GEN2_INFO, GEN2_STATUS, HOST
from services.tests.test_shelly_devices import make_plug


class PlugViewTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.client.force_login(self.user)
        self.device = make_plug(self.user, generation=2)

    def with_responses(self, *responses):
        session = FakeSession(*responses)
        api = ShellyClient(HOST, session=session)
        return patch.object(ShellyClient, "for_device", return_value=api), session


class AddPlugTests(PlugViewTestCase):
    def setUp(self):
        super().setUp()
        self.device.delete()
        self.url = reverse("services:shelly_add")

    def post(self, response, **extra):
        session = FakeSession(response)
        data = {
            "name": "Licht",
            "host": HOST,
            "tank": tank_named(self.user, "Becken 1").pk,
            "is_active": "on",
        }
        data.update(extra)
        with patch("services.views.ShellyClient", return_value=ShellyClient(HOST, session=session)):
            return self.client.post(self.url, data, follow=True), session

    def test_plug_is_identified_before_it_is_stored(self):
        response, session = self.post(FakeResponse(200, GEN2_INFO))

        device = Device.objects.get()
        self.assertEqual(device.owner, self.user)
        self.assertEqual(device.kind, Device.Kind.SHELLY_PLUG)
        self.assertEqual(device.generation, 2)
        self.assertEqual(device.firmware, "1.0.3")
        self.assertEqual(device.tank.name, "Becken 1")
        self.assertIn("/shelly", session.calls[0]["url"])
        self.assertContains(response, "Gen2+")

    def test_unreachable_address_creates_nothing_and_says_why(self):
        response, _ = self.post(requests.Timeout("still"))

        self.assertFalse(Device.objects.exists())
        self.assertContains(response, "Zeitüberschreitung")

    def test_a_foreign_device_under_that_address_is_refused(self):
        response, _ = self.post(FakeResponse(200, {"hallo": "welt"}))

        self.assertFalse(Device.objects.exists())
        self.assertContains(response, "kein Shelly-Gerät")

    def test_the_page_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_the_form_says_that_no_cloud_is_involved(self):
        response = self.client.get(self.url)

        self.assertContains(response, "lokalen Netz")
        self.assertContains(response, "Cloud")


class ListTests(PlugViewTestCase):
    def test_the_plug_is_listed_and_loads_its_status_via_htmx(self):
        with patch("services.devices.service_for") as service_for:
            response = self.client.get(reverse("services:device_list"))

        self.assertContains(response, self.device.name)
        self.assertContains(response, "Becken 1")
        self.assertContains(response, reverse("services:device_status", args=[self.device.pk]))
        self.assertContains(response, reverse("services:energy_overview"))
        service_for.assert_not_called()


class StatusTests(PlugViewTestCase):
    def url(self):
        return reverse("services:device_status", args=[self.device.pk])

    def test_status_shows_state_power_and_meter(self):
        patcher, _ = self.with_responses(FakeResponse(200, GEN2_STATUS))
        with patcher:
            response = self.client.get(self.url())

        self.assertContains(response, "an")
        self.assertContains(response, "12,30 W")
        self.assertContains(response, "1,000 kWh")
        self.assertContains(response, "44,4 °C")

    def test_unreachable_plug_does_not_break_the_page(self):
        patcher, _ = self.with_responses(requests.Timeout("still"))
        with patcher:
            response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nicht erreichbar")

    def test_overload_is_shown_as_a_warning(self):
        self.device.generation = 1
        self.device.save()
        payload = {**GEN1_STATUS, "relays": [{"ison": False, "overpower": True}]}
        patcher, _ = self.with_responses(FakeResponse(200, payload))
        with patcher:
            response = self.client.get(self.url())

        self.assertContains(response, "Überlast")


class DetailTests(PlugViewTestCase):
    def url(self):
        return reverse("services:device_detail", args=[self.device.pk])

    def add_reading(self, watt_hours, power, hours_ago=0):
        DeviceReading.objects.create(
            device=self.device,
            read_at=timezone.now() - timezone.timedelta(hours=hours_ago),
            payload={},
            is_on=True,
            power_w=Decimal(str(power)),
            energy_total_wh=Decimal(str(watt_hours)),
        )

    def test_detail_shows_power_chart_and_consumption(self):
        self.add_reading(1000, 12, hours_ago=2)
        self.add_reading(1500, 20, hours_ago=1)

        response = self.client.get(self.url())

        self.assertContains(response, "Leistung im Verlauf")
        self.assertContains(response, "<polyline")
        self.assertContains(response, "Stromverbrauch")
        self.assertContains(response, "0,500 kWh")
        self.assertNotContains(response, "Drehzahl")

    def test_period_can_be_switched(self):
        self.add_reading(1000, 12, hours_ago=30)
        self.add_reading(1500, 20)

        day = self.client.get(self.url(), {"zeitraum": "tag"})
        month = self.client.get(self.url(), {"zeitraum": "monat"})

        self.assertEqual(day.status_code, 200)
        self.assertContains(month, "0,500 kWh")

    def test_switching_is_offered_and_password_change_is_not(self):
        response = self.client.get(self.url())

        self.assertContains(response, "Steckdose einschalten")
        self.assertContains(response, "Steckdose ausschalten")
        self.assertNotContains(response, "Zugangsdaten ändern")

    def test_the_credentials_page_does_not_exist_for_a_plug(self):
        url = reverse("services:device_credentials", args=[self.device.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_detail_page_offers_no_update_button(self):
        response = self.client.get(self.url())
        self.assertNotContains(response, "Update")


class ControlTests(PlugViewTestCase):
    def url(self, action="on"):
        return reverse("services:device_control", args=[self.device.pk, action])

    def test_post_without_confirmation_only_asks(self):
        patcher, session = self.with_responses(FakeResponse(200, {"was_on": False}))
        with patcher:
            response = self.client.post(self.url("off"))

        self.assertContains(response, "Änderung bestätigen")
        self.assertContains(response, "Steckdose Licht Becken 1 ausgeschaltet")
        self.assertEqual(session.calls, [])
        self.assertFalse(DeviceEvent.objects.exists())

    def test_confirmed_post_reaches_the_plug_and_is_logged(self):
        patcher, session = self.with_responses(FakeResponse(200, {"was_on": False}))
        with patcher:
            response = self.client.post(self.url("on"), {"confirm": "1"})

        self.assertRedirects(response, reverse("services:device_detail", args=[self.device.pk]))
        self.assertEqual(session.calls[0]["params"], {"id": 0, "on": "true"})
        self.assertEqual(DeviceEvent.objects.get().title, "Steckdose Licht Becken 1 eingeschaltet")

    def test_filter_modes_are_not_offered_for_a_plug(self):
        for action in ("bio", "pulse", "manual", "doupdate"):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(self.url(action)).status_code, 404)

    def test_failure_is_reported_to_the_user(self):
        patcher, _ = self.with_responses(requests.Timeout("still"))
        with patcher:
            response = self.client.post(self.url("on"), {"confirm": "1"}, follow=True)

        self.assertContains(response, "Zeitüberschreitung")
        self.assertFalse(DeviceEvent.objects.get().succeeded)


class EnergyOverviewTests(PlugViewTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("services:energy_overview")
        self.heater = make_plug(
            self.user, name="Heizung", tank_name="Becken 2", host="192.168.1.61"
        )

    def add_reading(self, device, watt_hours, hours_ago=0):
        DeviceReading.objects.create(
            device=device,
            read_at=timezone.now() - timezone.timedelta(hours=hours_ago),
            payload={},
            energy_total_wh=Decimal(str(watt_hours)),
        )

    def test_tanks_are_compared(self):
        self.add_reading(self.device, 1000, hours_ago=2)
        self.add_reading(self.device, 1400, hours_ago=1)
        self.add_reading(self.heater, 2000, hours_ago=2)
        self.add_reading(self.heater, 4000, hours_ago=1)

        response = self.client.get(self.url)

        self.assertContains(response, "Becken 1")
        self.assertContains(response, "Becken 2")
        self.assertContains(response, "2,000 kWh")
        self.assertContains(response, "0,400 kWh")
        self.assertContains(response, "<rect")

    def test_page_touches_no_device(self):
        with patch("services.devices.service_for") as service_for:
            self.client.get(self.url)
        service_for.assert_not_called()

    def test_period_can_be_switched(self):
        self.add_reading(self.device, 1000, hours_ago=30)
        self.add_reading(self.device, 1600)

        response = self.client.get(self.url, {"zeitraum": "tag"})

        self.assertContains(response, "Tag")
        self.assertEqual(response.status_code, 200)

    def test_without_plugs_the_page_points_to_the_setup(self):
        Device.objects.all().delete()
        response = self.client.get(self.url)
        self.assertContains(response, reverse("services:shelly_add"))

    def test_foreign_devices_are_not_shown(self):
        eva = get_user_model().objects.create_user(
            username="eva", email="eva@example.com", password="geheim-123"
        )
        self.client.force_login(eva)
        response = self.client.get(self.url)

        self.assertNotContains(response, "Becken 1")

    def test_the_page_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
