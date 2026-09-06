"""Die Schicht zwischen Gerätemodell und Shelly-Anbindung."""

from io import StringIO
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from services import devices as device_service
from services.models import Device, DeviceEvent, DeviceReading
from services.shelly import Gen1Service, Gen2Service, ShellyClient
from services.tests.test_eheim_client import FakeResponse, FakeSession
from services.tests.test_shelly_client import (
    GEN1_INFO,
    GEN1_STATUS,
    GEN2_INFO,
    GEN2_STATUS,
    HOST,
)


def make_plug(owner, **kwargs):
    device = Device(
        owner=owner,
        name=kwargs.pop("name", "Licht Becken 1"),
        kind=Device.Kind.SHELLY_PLUG,
        host=kwargs.pop("host", HOST),
        tank_label=kwargs.pop("tank_label", "Becken 1"),
        **kwargs,
    )
    device.save()
    return device


class ShellyDeviceTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.device = make_plug(self.user)

    def with_responses(self, *responses):
        """Echter Client, aber mit vorbereiteten Antworten statt eines Geräts."""
        session = FakeSession(*responses)
        api = ShellyClient(self.device.host, session=session)
        return patch.object(ShellyClient, "for_device", return_value=api), session


class DispatchTests(ShellyDeviceTestCase):
    def test_known_generation_needs_no_extra_request(self):
        self.device.generation = 2
        patcher, session = self.with_responses()
        with patcher:
            service = device_service.service_for(self.device)

        self.assertIsInstance(service, Gen2Service)
        self.assertEqual(session.calls, [])

    def test_unknown_generation_is_detected_via_shelly(self):
        patcher, session = self.with_responses(FakeResponse(200, GEN1_INFO))
        with patcher:
            service = device_service.service_for(self.device)

        self.assertIsInstance(service, Gen1Service)
        self.assertEqual(session.calls[0]["url"], f"http://{HOST}/shelly")


class ProbeTests(ShellyDeviceTestCase):
    def probe_with(self, *responses):
        patcher, session = self.with_responses(*responses)
        with patcher:
            return device_service.probe(self.device), session

    def test_gen1_reading_is_stored_with_the_raw_answer(self):
        result, _ = self.probe_with(FakeResponse(200, GEN1_INFO), FakeResponse(200, GEN1_STATUS))

        self.assertTrue(result)
        reading = DeviceReading.objects.get()
        self.assertEqual(reading.payload, GEN1_STATUS)
        self.assertIs(reading.is_on, True)
        self.assertEqual(str(reading.power_w), "12.34")
        self.assertEqual(str(reading.energy_total_wh), "1000.00")
        self.assertEqual(str(reading.temperature_c), "24.4")

    def test_gen2_reading_is_stored(self):
        result, _ = self.probe_with(FakeResponse(200, GEN2_INFO), FakeResponse(200, GEN2_STATUS))

        self.assertTrue(result)
        reading = DeviceReading.objects.get()
        self.assertEqual(str(reading.energy_total_wh), "1000.00")
        self.assertEqual(str(reading.temperature_c), "44.4")

    def test_detected_generation_is_kept_on_the_device(self):
        _result, session = self.probe_with(
            FakeResponse(200, GEN2_INFO), FakeResponse(200, GEN2_STATUS)
        )
        self.device.refresh_from_db()
        self.assertEqual(self.device.generation, 2)
        self.assertEqual(len(session.calls), 2)

        # Beim zweiten Lauf entfällt der Umweg über /shelly.
        _result, session = self.probe_with(FakeResponse(200, GEN2_STATUS))
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(DeviceReading.objects.count(), 2)

    def test_unreachable_plug_yields_an_error_not_an_exception(self):
        result, _ = self.probe_with(requests.Timeout("still"))

        self.assertFalse(result)
        self.assertIn("Zeitüberschreitung", result.error)
        self.assertFalse(DeviceReading.objects.exists())
        self.device.refresh_from_db()
        self.assertIsNone(self.device.last_seen)

    def test_inactive_plug_is_not_queried(self):
        self.device.is_active = False
        self.device.save()

        result, session = self.probe_with(FakeResponse(200, GEN1_INFO))

        self.assertFalse(result)
        self.assertEqual(session.calls, [])


class SwitchTests(ShellyDeviceTestCase):
    def setUp(self):
        super().setUp()
        self.device.generation = 2
        self.device.save()

    def execute(self, action, *responses):
        patcher, session = self.with_responses(*(responses or [FakeResponse(200, {"was_on": False})]))
        with patcher:
            return device_service.execute(self.device, action, user=self.user), session

    def test_switching_on_is_sent_and_logged(self):
        result, session = self.execute("on")

        self.assertTrue(result)
        self.assertEqual(session.calls[0]["params"], {"id": 0, "on": "true"})
        event = DeviceEvent.objects.get()
        self.assertEqual(event.title, "Steckdose Licht Becken 1 eingeschaltet")
        self.assertEqual(event.user, self.user)
        self.assertTrue(event.succeeded)

    def test_confirmation_text_matches_the_log_entry(self):
        summary = device_service.describe(self.device, "off")
        result, _ = self.execute("off")

        self.assertEqual(summary, DeviceEvent.objects.get().title)
        self.assertEqual(summary, result.message)

    def test_failed_command_is_logged_too(self):
        result, _ = self.execute("on", requests.Timeout("still"))

        self.assertFalse(result)
        event = DeviceEvent.objects.get()
        self.assertFalse(event.succeeded)
        self.assertIn("Einschalten", event.title)

    def test_the_plug_knows_no_other_actions(self):
        result, session = self.execute("bio")

        self.assertFalse(result)
        self.assertEqual(session.calls, [])
        self.assertFalse(DeviceEvent.objects.get().succeeded)

    def test_credentials_cannot_be_changed_on_a_plug(self):
        result = device_service.change_password(self.device, "neu", user=self.user)
        self.assertFalse(result)


class PlugModelTests(ShellyDeviceTestCase):
    def test_a_plug_needs_an_address(self):
        device = Device(owner=self.user, name="Steckdose", kind=Device.Kind.SHELLY_PLUG)
        with self.assertRaises(ValidationError) as caught:
            device.full_clean(exclude=["owner"])
        self.assertIn("host", caught.exception.message_dict)

    def test_a_plug_needs_no_mac(self):
        self.device.full_clean(exclude=["owner"])

    def test_the_eheim_factory_password_is_no_topic_for_a_plug(self):
        self.device.set_credentials("admin", "admin")
        self.assertFalse(self.device.uses_default_password)

    def test_tank_defaults_to_a_readable_label(self):
        self.assertEqual(self.device.tank_name, "Becken 1")
        self.device.tank_label = "  "
        self.assertEqual(self.device.tank_name, "ohne Becken")


class PollCommandTests(ShellyDeviceTestCase):
    def test_plug_is_polled_along_with_the_filters(self):
        patcher, _ = self.with_responses(
            FakeResponse(200, GEN2_INFO), FakeResponse(200, GEN2_STATUS)
        )
        out = StringIO()
        with patcher:
            call_command("poll_devices", stdout=out, stderr=out, device_id=self.device.pk)

        output = out.getvalue()
        self.assertIn(self.device.name, output)
        self.assertIn("12.30 W", output)
        self.assertIn("1.000 kWh", output)
        self.assertEqual(DeviceReading.objects.count(), 1)

    def test_unreachable_plug_does_not_stop_the_run(self):
        patcher, _ = self.with_responses(requests.Timeout("still"))
        out = StringIO()
        with patcher:
            call_command("poll_devices", stdout=out, stderr=out, device_id=self.device.pk)

        self.assertIn("nicht erreichbar", out.getvalue())
        self.assertFalse(DeviceReading.objects.exists())
