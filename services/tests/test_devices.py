"""Gerätemodelle und die Schicht zwischen Modell und Eheim-Anbindung."""

from io import StringIO
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from services import devices as device_service
from services.eheim import ClassicVarioService, EheimClient
from services.models import Device, DeviceEvent, DeviceReading
from services.tests.test_eheim_client import CLASSICVARIO_PAYLOAD, MAC, FakeResponse, FakeSession


def make_device(owner, **kwargs):
    device = Device(
        owner=owner,
        name=kwargs.pop("name", "Filter Becken 1"),
        kind=kwargs.pop("kind", Device.Kind.EHEIM_CLASSICVARIO),
        mac_address=kwargs.pop("mac_address", MAC),
        host=kwargs.pop("host", "192.168.1.50"),
        **kwargs,
    )
    device.set_credentials("api", "admin")
    device.save()
    return device


def fake_service(device, *responses):
    """Echter Service, aber mit vorbereiteten Antworten statt eines Geräts."""
    session = FakeSession(*responses)
    client = EheimClient(
        device.host, device.api_user, device.api_password, mac=device.mac_address, session=session
    )
    return ClassicVarioService(device, client=client), session


class DeviceModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )

    def test_mac_is_stored_normalized(self):
        device = make_device(self.user, mac_address="aabbccddeeff")
        device.refresh_from_db()
        self.assertEqual(device.mac_address, MAC)

    def test_eheim_device_needs_mac_and_host(self):
        device = Device(owner=self.user, name="Filter", kind=Device.Kind.EHEIM_CLASSICVARIO)
        with self.assertRaises(ValidationError) as caught:
            device.full_clean(exclude=["owner"])
        self.assertIn("mac_address", caught.exception.message_dict)
        self.assertIn("host", caught.exception.message_dict)

    def test_other_kinds_do_not_need_a_mac(self):
        device = Device(owner=self.user, name="Steckdose", kind=Device.Kind.SHELLY_PLUG,
                        host="192.168.1.60")
        device.full_clean(exclude=["owner"])

    def test_credentials_are_encrypted_at_rest(self):
        device = make_device(self.user)
        with connection.cursor() as cursor:
            cursor.execute("SELECT credentials FROM services_device WHERE id = %s", [device.pk])
            stored = cursor.fetchone()[0]
        self.assertNotIn("admin", stored)
        self.assertEqual(Device.objects.get(pk=device.pk).api_password, "admin")

    def test_default_password_is_recognised(self):
        device = make_device(self.user)
        self.assertTrue(device.uses_default_password)
        device.set_credentials("api", "eigenes-passwort")
        self.assertFalse(device.uses_default_password)

    def test_unreadable_credentials_do_not_raise(self):
        device = make_device(self.user)
        device.credentials = "kein json"
        self.assertEqual(device.credentials_dict, {})
        self.assertEqual(device.api_user, "api")
        self.assertEqual(device.api_password, "")

    def test_firmware_check(self):
        device = make_device(self.user)
        self.assertIsNone(device.firmware_supported)
        device.firmware = "2.0.0.9"
        self.assertFalse(device.firmware_supported)
        device.firmware = "2.0.1.4"
        self.assertTrue(device.firmware_supported)

    def test_reading_translates_codes(self):
        device = make_device(self.user)
        reading = DeviceReading.objects.create(
            device=device, read_at=timezone.now(), payload={}, error_code=1, pump_mode=16,
            service_due_in=48,
        )
        self.assertEqual(reading.error_text, "Rotor blockiert")
        self.assertEqual(reading.mode_label, "Manueller Modus")
        self.assertEqual(reading.service_due_in_days, 2)
        self.assertTrue(reading.has_error)


class ProbeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.device = make_device(self.user)

    def probe_with(self, *responses):
        service, session = fake_service(self.device, *responses)
        with patch("services.devices.service_for", return_value=service):
            return device_service.probe(self.device), session

    def test_status_is_stored_with_the_raw_answer(self):
        result, _ = self.probe_with(FakeResponse(200, CLASSICVARIO_PAYLOAD))

        self.assertTrue(result)
        reading = DeviceReading.objects.get()
        self.assertEqual(reading.payload, CLASSICVARIO_PAYLOAD)
        self.assertEqual(reading.rpm_percent, 72)
        self.assertEqual(reading.pump_mode, 4)
        self.assertEqual(reading.error_code, 0)
        self.assertEqual(reading.service_due_in, 1200)
        self.assertIs(reading.is_on, True)

    def test_successful_read_updates_last_seen(self):
        self.probe_with(FakeResponse(200, CLASSICVARIO_PAYLOAD))
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_unreachable_device_yields_an_error_not_an_exception(self):
        result, _ = self.probe_with(requests.Timeout("still"))

        self.assertFalse(result)
        self.assertIn("Zeitüberschreitung", result.error)
        self.assertFalse(DeviceReading.objects.exists())
        self.device.refresh_from_db()
        self.assertIsNone(self.device.last_seen)

    def test_inactive_device_is_not_queried(self):
        self.device.is_active = False
        self.device.save()

        result, session = self.probe_with(FakeResponse(200, CLASSICVARIO_PAYLOAD))

        self.assertFalse(result)
        self.assertEqual(session.calls, [])

    def test_absurd_values_do_not_break_the_insert(self):
        result, _ = self.probe_with(FakeResponse(200, {**CLASSICVARIO_PAYLOAD, "rel_speed": -3}))

        self.assertTrue(result)
        self.assertIsNone(DeviceReading.objects.get().rpm_percent)

    def test_firmware_is_read_and_checked(self):
        service, _ = fake_service(self.device, FakeResponse(200, {"version": "2.0.1.4"}))
        with patch("services.devices.service_for", return_value=service):
            device_service.check_firmware(self.device)
        self.device.refresh_from_db()
        self.assertEqual(self.device.firmware, "2.0.1.4")

    def test_old_firmware_is_reported(self):
        service, _ = fake_service(self.device, FakeResponse(200, {"version": "2.0.0.9"}))
        with patch("services.devices.service_for", return_value=service):
            with self.assertRaises(Exception) as caught:
                device_service.check_firmware(self.device)
        self.assertIn("2.0.1", str(caught.exception))


class ExecuteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.device = make_device(self.user)

    def execute(self, action, params=None, *responses):
        service, session = fake_service(self.device, *(responses or [FakeResponse(200, {})]))
        with patch("services.devices.service_for", return_value=service):
            return device_service.execute(self.device, action, params, user=self.user), session

    def test_switching_on_is_sent_and_logged(self):
        result, session = self.execute("on")

        self.assertTrue(result)
        self.assertEqual(session.calls[0]["json"], {"to": MAC, "active": 1})
        event = DeviceEvent.objects.get()
        self.assertEqual(event.title, "Filter eingeschaltet")
        self.assertEqual(event.user, self.user)
        self.assertTrue(event.succeeded)

    def test_bio_mode_is_logged_in_plain_language(self):
        import datetime

        result, _ = self.execute(
            "bio",
            {
                "day_speed": 80,
                "night_speed": 40,
                "day_start": datetime.time(11, 0),
                "night_start": datetime.time(23, 0),
            },
        )

        self.assertTrue(result)
        self.assertEqual(
            DeviceEvent.objects.get().title,
            "Bio-Modus: Tag ab 11:00 mit 80 %, Nacht ab 23:00 mit 40 %",
        )

    def test_failed_command_is_logged_too(self):
        result, _ = self.execute("on", None, requests.Timeout("still"))

        self.assertFalse(result)
        event = DeviceEvent.objects.get()
        self.assertFalse(event.succeeded)
        self.assertIn("Einschalten", event.title)

    def test_unknown_action_is_refused(self):
        result, session = self.execute("selbstzerstoerung")

        self.assertFalse(result)
        self.assertEqual(session.calls, [])

    def test_inactive_device_is_not_controlled(self):
        self.device.is_active = False
        self.device.save()

        result, session = self.execute("on")

        self.assertFalse(result)
        self.assertEqual(session.calls, [])
        self.assertFalse(DeviceEvent.objects.exists())

    def test_device_without_control_is_refused(self):
        self.device.kind = Device.Kind.EHEIM_OTHER
        self.device.save()
        service, session = fake_service(self.device, FakeResponse(200, {}))
        from services.eheim import EheimService

        with patch("services.devices.service_for", return_value=EheimService(self.device, client=service.client)):
            result = device_service.execute(self.device, "on", user=self.user)

        self.assertFalse(result)
        self.assertEqual(session.calls, [])
        self.assertFalse(DeviceEvent.objects.get().succeeded)

    def test_confirmation_text_matches_the_log_entry(self):
        summary = device_service.describe(self.device, "manual", {"speed_percent": 70})
        result, _ = self.execute("manual", {"speed_percent": 70})
        self.assertEqual(summary, DeviceEvent.objects.get().title)
        self.assertEqual(summary, result.message)

    def test_password_change_is_stored_and_logged_without_the_password(self):
        service, session = fake_service(self.device, FakeResponse(200, {}))
        with patch("services.devices.service_for", return_value=service):
            result = device_service.change_password(self.device, "neues-passwort", user=self.user)

        self.assertTrue(result)
        self.device.refresh_from_db()
        self.assertEqual(self.device.api_password, "neues-passwort")
        self.assertFalse(self.device.uses_default_password)
        event = DeviceEvent.objects.get()
        self.assertNotIn("neues-passwort", event.title + event.description)

    def test_failed_password_change_keeps_the_old_credentials(self):
        service, _ = fake_service(self.device, FakeResponse(403, None, text="denied"))
        with patch("services.devices.service_for", return_value=service):
            result = device_service.change_password(self.device, "neues-passwort", user=self.user)

        self.assertFalse(result)
        self.device.refresh_from_db()
        self.assertEqual(self.device.api_password, "admin")


class WarningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.other = get_user_model().objects.create_user(
            username="eva", email="eva@example.com", password="geheim-123"
        )
        self.device = make_device(self.user)

    def add_reading(self, error_code, minutes_ago=0, device=None):
        return DeviceReading.objects.create(
            device=device or self.device,
            read_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
            payload={},
            error_code=error_code,
        )

    def test_error_code_becomes_a_warning_in_plain_language(self):
        self.add_reading(1)
        warnings = device_service.warnings_for(self.user)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].error_text, "Rotor blockiert")
        self.assertIn("Rotor blockiert", warnings[0].message)

    def test_only_the_latest_reading_counts(self):
        self.add_reading(2, minutes_ago=30)
        self.add_reading(0, minutes_ago=1)
        self.assertEqual(device_service.warnings_for(self.user), [])

    def test_no_warning_without_error(self):
        self.add_reading(0)
        self.assertEqual(device_service.warnings_for(self.user), [])

    def test_foreign_devices_are_not_shown(self):
        self.add_reading(1)
        self.assertEqual(device_service.warnings_for(self.other), [])

    def test_inactive_devices_are_not_shown(self):
        self.add_reading(1)
        self.device.is_active = False
        self.device.save()
        self.assertEqual(device_service.warnings_for(self.user), [])


class PollCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.device = make_device(self.user)

    def run_command(self, *responses, **options):
        service, session = fake_service(self.device, *responses)
        out = StringIO()
        with patch("services.devices.service_for", return_value=service):
            call_command("poll_devices", stdout=out, stderr=out, **options)
        return out.getvalue(), session

    def test_reading_is_stored(self):
        output, _ = self.run_command(FakeResponse(200, CLASSICVARIO_PAYLOAD))

        self.assertEqual(DeviceReading.objects.count(), 1)
        self.assertIn("1 Gerät(e) abgefragt", output)

    def test_unreachable_device_does_not_stop_the_run(self):
        output, _ = self.run_command(requests.Timeout("still"))

        self.assertFalse(DeviceReading.objects.exists())
        self.assertIn("nicht erreichbar", output)

    def test_error_code_is_reported(self):
        output, _ = self.run_command(FakeResponse(200, {**CLASSICVARIO_PAYLOAD, "errorCode": 2}))
        self.assertIn("Luft im Filter", output)

    def test_single_device_can_be_selected(self):
        other = make_device(self.user, name="Zweiter Filter", mac_address="AA:BB:CC:00:11:22")
        output, _ = self.run_command(FakeResponse(200, CLASSICVARIO_PAYLOAD), device_id=self.device.pk)

        self.assertEqual(DeviceReading.objects.get().device, self.device)
        self.assertNotIn(other.name, output)

    def test_inactive_devices_are_skipped(self):
        Device.objects.create(
            owner=self.user,
            name="Steckdose",
            kind=Device.Kind.SHELLY_PLUG,
            host="192.168.1.60",
            is_active=False,
        )
        output, _ = self.run_command(FakeResponse(200, CLASSICVARIO_PAYLOAD))
        self.assertNotIn("Steckdose", output)
