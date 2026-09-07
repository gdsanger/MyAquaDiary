"""Geräteseiten: Anzeige, Suche und die schreibenden Aktionen."""

from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from services.eheim import MeshDevice
from services.eheim.devices import KIND_CLASSICVARIO
from services.models import Device, DeviceEvent, DeviceReading
from services.tests.test_devices import fake_service, make_device
from services.tests.test_eheim_client import CLASSICVARIO_PAYLOAD, MAC, FakeResponse


class DeviceViewTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.client.force_login(self.user)
        self.device = make_device(self.user)


class AccessTests(DeviceViewTestCase):
    def urls(self):
        return [
            reverse("services:device_list"),
            reverse("services:device_discover"),
            reverse("services:device_detail", args=[self.device.pk]),
            reverse("services:device_status", args=[self.device.pk]),
            reverse("services:device_edit", args=[self.device.pk]),
            reverse("services:device_credentials", args=[self.device.pk]),
            reverse("services:device_control", args=[self.device.pk, "on"]),
            reverse("services:device_specs", args=[self.device.pk]),
            reverse("services:device_spec_create", args=[self.device.pk]),
            reverse("services:device_documents", args=[self.device.pk]),
            reverse("services:device_document_create", args=[self.device.pk]),
            reverse("services:device_links", args=[self.device.pk]),
            reverse("services:device_link_create", args=[self.device.pk]),
        ]

    def test_every_page_requires_login(self):
        self.client.logout()
        for url in self.urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login/", response["Location"])

    def test_foreign_devices_are_not_reachable(self):
        eva = get_user_model().objects.create_user(
            username="eva", email="eva@example.com", password="geheim-123"
        )
        self.client.force_login(eva)
        for url in self.urls()[2:]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)


class ListAndStatusTests(DeviceViewTestCase):
    def test_list_loads_the_status_via_htmx_instead_of_blocking(self):
        with patch("services.devices.service_for") as service_for:
            response = self.client.get(reverse("services:device_list"))

        self.assertContains(response, reverse("services:device_status", args=[self.device.pk]))
        self.assertContains(response, "hx-trigger=\"load\"")
        service_for.assert_not_called()

    def test_status_fragment_shows_the_current_values(self):
        service, _ = fake_service(self.device, FakeResponse(200, CLASSICVARIO_PAYLOAD))
        with patch("services.devices.service_for", return_value=service):
            response = self.client.get(reverse("services:device_status", args=[self.device.pk]))

        self.assertContains(response, "72 %")
        self.assertContains(response, "Bio-Modus")
        self.assertContains(response, "11:00")
        self.assertEqual(DeviceReading.objects.count(), 1)

    def test_unreachable_device_does_not_break_the_page(self):
        service, _ = fake_service(self.device, requests.Timeout("still"))
        with patch("services.devices.service_for", return_value=service):
            response = self.client.get(reverse("services:device_status", args=[self.device.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nicht erreichbar")

    def test_error_code_is_shown_in_plain_language(self):
        service, _ = fake_service(
            self.device, FakeResponse(200, {**CLASSICVARIO_PAYLOAD, "errorCode": 1})
        )
        with patch("services.devices.service_for", return_value=service):
            response = self.client.get(reverse("services:device_status", args=[self.device.pk]))

        self.assertContains(response, "Rotor blockiert")

    def test_list_without_devices_points_to_the_search(self):
        self.device.delete()
        response = self.client.get(reverse("services:device_list"))
        self.assertContains(response, reverse("services:device_discover"))
        self.assertContains(response, "2.0.1")

    def test_default_password_is_flagged(self):
        response = self.client.get(reverse("services:device_list"))
        self.assertContains(response, "Werkspasswort")

    def test_detail_shows_chart_and_history(self):
        DeviceReading.objects.create(
            device=self.device, read_at=timezone.now(), payload={}, rpm_percent=70
        )
        DeviceReading.objects.create(
            device=self.device,
            read_at=timezone.now() - timezone.timedelta(hours=1),
            payload={},
            rpm_percent=40,
        )

        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))

        self.assertContains(response, "<polyline")
        self.assertContains(response, "Drehzahl im Verlauf")

    def test_detail_without_readings_says_so(self):
        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))
        self.assertContains(response, "Noch keine Messwerte erfasst.")

    def test_old_firmware_gets_a_hint(self):
        self.device.firmware = "2.0.0.9"
        self.device.save()
        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))
        self.assertContains(response, "2.0.1")


class ControlTests(DeviceViewTestCase):
    def url(self, action="on"):
        return reverse("services:device_control", args=[self.device.pk, action])

    def test_get_shows_the_form_without_touching_the_device(self):
        with patch("services.devices.service_for") as service_for:
            response = self.client.get(self.url("manual"))

        self.assertContains(response, "Drehzahl (%)")
        service_for.assert_not_called()

    def test_post_without_confirmation_only_asks(self):
        service, session = fake_service(self.device, FakeResponse(200, {}))
        with patch("services.devices.service_for", return_value=service):
            response = self.client.post(self.url("on"))

        self.assertContains(response, "Änderung bestätigen")
        self.assertContains(response, "Filter eingeschaltet")
        self.assertEqual(session.calls, [])
        self.assertFalse(DeviceEvent.objects.exists())

    def test_confirmed_post_reaches_the_device_and_is_logged(self):
        service, session = fake_service(self.device, FakeResponse(200, {}))
        with patch("services.devices.service_for", return_value=service):
            response = self.client.post(self.url("on"), {"confirm": "1"})

        self.assertRedirects(response, reverse("services:device_detail", args=[self.device.pk]))
        self.assertEqual(session.calls[0]["json"], {"to": MAC, "active": 1})
        self.assertEqual(DeviceEvent.objects.get().title, "Filter eingeschaltet")

    def test_manual_mode_keeps_the_values_across_the_confirmation(self):
        service, session = fake_service(self.device, FakeResponse(200, {}))
        with patch("services.devices.service_for", return_value=service):
            confirm = self.client.post(self.url("manual"), {"speed_percent": "65"})
            self.assertContains(confirm, "Manueller Modus, 65 %")

            self.client.post(self.url("manual"), {"speed_percent": "65", "confirm": "1"})

        self.assertEqual(session.calls[0]["json"]["rel_manual_motor_speed"], 65)

    def test_bio_mode_sends_minutes_since_midnight(self):
        service, session = fake_service(self.device, FakeResponse(200, {}))
        payload = {
            "day_speed": "80",
            "night_speed": "40",
            "day_start": "11:00",
            "night_start": "23:00",
            "confirm": "1",
        }
        with patch("services.devices.service_for", return_value=service):
            self.client.post(self.url("bio"), payload)

        self.assertEqual(session.calls[0]["json"]["start_time_day"], 660)
        self.assertEqual(session.calls[0]["json"]["start_time_night"], 1380)

    def test_invalid_input_never_reaches_the_device(self):
        service, session = fake_service(self.device, FakeResponse(200, {}))
        with patch("services.devices.service_for", return_value=service):
            response = self.client.post(self.url("manual"), {"speed_percent": "500", "confirm": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.calls, [])
        self.assertFalse(DeviceEvent.objects.exists())

    def test_failure_is_reported_to_the_user(self):
        service, _ = fake_service(self.device, requests.Timeout("still"))
        with patch("services.devices.service_for", return_value=service):
            response = self.client.post(self.url("on"), {"confirm": "1"}, follow=True)

        self.assertContains(response, "Zeitüberschreitung")
        self.assertFalse(DeviceEvent.objects.get().succeeded)

    def test_firmware_update_is_not_offered(self):
        for action in ("doupdate", "update", "firmware"):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(self.url(action), {"confirm": "1"}).status_code, 404)

    def test_detail_page_offers_no_update_button(self):
        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))
        self.assertNotContains(response, "doupdate")
        self.assertNotContains(response, "Update")

    def test_devices_without_control_have_no_control_urls(self):
        self.device.kind = Device.Kind.EHEIM_OTHER
        self.device.save()
        self.assertEqual(self.client.get(self.url("on")).status_code, 404)


class CredentialsTests(DeviceViewTestCase):
    def url(self):
        return reverse("services:device_credentials", args=[self.device.pk])

    def test_password_is_changed_on_the_device_and_stored(self):
        service, session = fake_service(self.device, FakeResponse(200, {}))
        with patch("services.devices.service_for", return_value=service):
            response = self.client.post(
                self.url(), {"password": "eigenes-passwort", "password_repeat": "eigenes-passwort"}
            )

        self.assertRedirects(response, reverse("services:device_detail", args=[self.device.pk]))
        self.assertIn("/changeauth", session.calls[0]["url"])
        self.device.refresh_from_db()
        self.assertEqual(self.device.api_password, "eigenes-passwort")

    def test_mismatched_passwords_are_refused(self):
        response = self.client.post(self.url(), {"password": "eins-zwei", "password_repeat": "drei"})
        self.assertContains(response, "stimmen nicht überein")

    def test_factory_password_is_refused(self):
        response = self.client.post(self.url(), {"password": "admin", "password_repeat": "admin"})
        self.assertContains(response, "Werkspasswort")
        self.device.refresh_from_db()
        self.assertTrue(self.device.uses_default_password)

    def test_password_is_never_rendered(self):
        self.device.set_credentials("api", "streng-geheim")
        self.device.save()
        for url in (self.url(), reverse("services:device_edit", args=[self.device.pk])):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "streng-geheim")


class DiscoveryTests(DeviceViewTestCase):
    def setUp(self):
        super().setUp()
        self.tank = self.device.tank
        self.device.delete()
        self.url = reverse("services:device_discover")
        self.found = [
            MeshDevice(mac=MAC, name="Filter", firmware="2.0.1.4", kind=KIND_CLASSICVARIO),
            MeshDevice(mac="AA:BB:CC:00:11:22", name="Alter Filter", firmware="2.0.0.9"),
        ]

    def search(self):
        with patch("services.views.EheimService") as service_class:
            service_class.return_value.discover.return_value = self.found
            return self.client.post(
                self.url, {"host": "192.168.1.50", "username": "api", "password": "admin",
                           "action": "search"}
            )

    def test_mesh_devices_are_listed(self):
        response = self.search()
        self.assertContains(response, MAC)
        self.assertContains(response, "Filter")
        self.assertContains(response, "zu alt")

    def test_unreachable_gateway_is_reported(self):
        with patch("services.views.EheimService") as service_class:
            from services.eheim import EheimUnreachable

            service_class.return_value.discover.side_effect = EheimUnreachable("Zeitüberschreitung")
            response = self.client.post(
                self.url, {"host": "192.168.1.50", "username": "api", "password": "admin",
                           "action": "search"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zeitüberschreitung")

    def create(self, macs, extra=None):
        data = {
            "host": "192.168.1.50",
            "username": "api",
            "password": "admin",
            "action": "create",
            "macs": macs,
            f"name_{MAC}": "Filter Becken 1",
            f"kind_{MAC}": Device.Kind.EHEIM_CLASSICVARIO,
            f"tank_{MAC}": self.tank.pk,
            "tank_AA:BB:CC:00:11:22": self.tank.pk,
            f"firmware_{MAC}": "2.0.1.4",
            "firmware_AA:BB:CC:00:11:22": "2.0.0.9",
            "name_AA:BB:CC:00:11:22": "Alter Filter",
            "kind_AA:BB:CC:00:11:22": Device.Kind.EHEIM_OTHER,
        }
        data.update(extra or {})
        with patch("services.views.EheimService") as service_class:
            service_class.return_value.discover.return_value = self.found
            return self.client.post(self.url, data, follow=True)

    def test_selected_device_is_created(self):
        response = self.create([MAC])

        device = Device.objects.get()
        self.assertEqual(device.owner, self.user)
        self.assertEqual(device.name, "Filter Becken 1")
        self.assertEqual(device.mac_address, MAC)
        self.assertEqual(device.host, "192.168.1.50")
        self.assertEqual(device.firmware, "2.0.1.4")
        self.assertEqual(device.api_password, "admin")
        self.assertContains(response, "Werkspasswort")

    def test_old_firmware_is_not_created_and_explained(self):
        response = self.create(["AA:BB:CC:00:11:22"])

        self.assertFalse(Device.objects.exists())
        self.assertContains(response, "2.0.1")

    def test_known_device_is_not_created_twice(self):
        self.create([MAC])
        self.create([MAC])
        self.assertEqual(Device.objects.count(), 1)
