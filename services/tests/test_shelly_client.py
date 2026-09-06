"""Client, Umrechnung und Geräteklassen der Shelly-Anbindung — ohne echtes Gerät."""

import requests
from django.test import SimpleTestCase, override_settings
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from services.shelly import (
    Gen1Service,
    Gen2Service,
    ShellyAuthError,
    ShellyClient,
    ShellyNotConfigured,
    ShellyResponseError,
    ShellyService,
    ShellyUnknownGeneration,
    ShellyUnreachable,
    detect_generation,
    generation_label,
    service_class_for,
    watt_hours_to_kilowatt_hours,
    watt_minutes_to_watt_hours,
)
from services.tests.test_eheim_client import FakeResponse, FakeSession

HOST = "192.168.1.60"

#: Antwort eines Shelly Plug S (Gen1) auf /shelly.
GEN1_INFO = {"type": "SHPLG-S", "mac": "A4CF12345678", "auth": False, "fw": "20230913-112003/v1.14.0"}
#: Antwort eines Shelly Plus Plug S (Gen2) auf /shelly.
GEN2_INFO = {
    "name": "Becken 1 Licht",
    "id": "shellyplusplugs-a1b2c3",
    "mac": "A1B2C3D4E5F6",
    "model": "SNPL-00112EU",
    "gen": 2,
    "ver": "1.0.3",
    "auth_en": False,
}

#: /status eines Plug S. `total` steht in Wattminuten.
GEN1_STATUS = {
    "relays": [{"ison": True, "overpower": False}],
    "meters": [{"power": 12.34, "is_valid": True, "total": 60000}],
    "temperature": 24.4,
    "tmp": {"tC": 24.4, "is_valid": True},
    "overtemperature": False,
}

#: /rpc/Switch.GetStatus eines Plus Plug S. `aenergy.total` steht in Wattstunden.
GEN2_STATUS = {
    "id": 0,
    "output": True,
    "apower": 12.3,
    "voltage": 232.9,
    "aenergy": {"total": 1000.0},
    "temperature": {"tC": 44.4, "tF": 111.9},
}


def client(*responses, **kwargs):
    session = FakeSession(*responses)
    return ShellyClient(HOST, session=session, **kwargs), session


class ConvertTests(SimpleTestCase):
    def test_gen1_counts_in_watt_minutes(self):
        self.assertEqual(str(watt_minutes_to_watt_hours(60000)), "1000.00")
        self.assertEqual(str(watt_minutes_to_watt_hours(90)), "1.50")

    def test_watt_hours_become_kilowatt_hours(self):
        self.assertEqual(str(watt_hours_to_kilowatt_hours(1500)), "1.500")

    def test_unreadable_values_do_not_raise(self):
        for value in (None, "", "keine Zahl", [1]):
            with self.subTest(value=value):
                self.assertIsNone(watt_minutes_to_watt_hours(value))


class GenerationTests(SimpleTestCase):
    def test_gen2_reports_its_generation(self):
        self.assertEqual(detect_generation(GEN2_INFO), 2)

    def test_gen1_is_recognised_without_a_gen_field(self):
        self.assertEqual(detect_generation(GEN1_INFO), 1)

    def test_a_foreign_device_is_refused(self):
        with self.assertRaises(ShellyUnknownGeneration):
            detect_generation({"hallo": "welt"})

    def test_newer_generations_use_the_rpc_service(self):
        for generation in (2, 3, 4):
            with self.subTest(generation=generation):
                self.assertIs(service_class_for(generation), Gen2Service)
        self.assertIs(service_class_for(1), Gen1Service)

    def test_generation_is_shown_in_plain_language(self):
        self.assertEqual(generation_label(1), "Gen1")
        self.assertEqual(generation_label(2), "Gen2+")
        self.assertEqual(generation_label(None), "")


class ClientTests(SimpleTestCase):
    def test_request_goes_to_the_local_address(self):
        api, session = client(FakeResponse(200, GEN1_STATUS))
        api.get("/status")
        self.assertEqual(session.calls[0]["url"], f"http://{HOST}/status")

    def test_without_a_password_no_authentication_is_sent(self):
        api, session = client(FakeResponse(200, {}))
        api.get("/shelly")
        self.assertIsNone(session.calls[0]["auth"])

    def test_gen1_uses_basic_and_gen2_digest(self):
        api, session = client(FakeResponse(200, {}), password="geheim", generation=1)
        api.get("/status")
        self.assertIsInstance(session.calls[0]["auth"], HTTPBasicAuth)

        api, session = client(FakeResponse(200, {}), password="geheim", generation=2)
        api.get("/rpc/Switch.GetStatus")
        self.assertIsInstance(session.calls[0]["auth"], HTTPDigestAuth)

    def test_timeout_becomes_an_unreachable_error(self):
        api, _ = client(requests.Timeout("still"))
        with self.assertRaises(ShellyUnreachable) as caught:
            api.get("/status")
        self.assertIn("Zeitüberschreitung", str(caught.exception))

    def test_rejected_credentials_are_reported_as_such(self):
        api, _ = client(FakeResponse(401, None, text="denied"))
        with self.assertRaises(ShellyAuthError):
            api.get("/status")

    def test_broken_json_is_reported(self):
        api, _ = client(FakeResponse(200, None, text="<html>"))
        with self.assertRaises(ShellyResponseError):
            api.get("/status")

    def test_device_without_address_is_not_requested(self):
        api = ShellyClient("", session=FakeSession())
        with self.assertRaises(ShellyNotConfigured):
            api.get("/status")

    def test_password_never_appears_in_error_messages(self):
        api, _ = client(FakeResponse(500, None, text="passwort streng-geheim"), password="streng-geheim")
        with self.assertRaises(ShellyResponseError) as caught:
            api.get("/status")
        self.assertNotIn("streng-geheim", str(caught.exception))

    def test_firmware_update_and_reset_are_blocked(self):
        for path in ("/ota", "/reset", "/rpc/Shelly.Update", "/rpc/Shelly.FactoryReset"):
            with self.subTest(path=path):
                api, session = client(FakeResponse(200, {}))
                with self.assertRaises(ShellyResponseError):
                    api.get(path)
                self.assertEqual(session.calls, [])

    @override_settings(SHELLY_TIMEOUT=2)
    def test_timeout_comes_from_the_settings(self):
        api, session = client(FakeResponse(200, {}))
        api.get("/shelly")
        self.assertEqual(session.calls[0]["timeout"], 2)


class IdentifyTests(SimpleTestCase):
    def service(self, *responses):
        api, session = client(*responses)
        return ShellyService(client=api), session

    def test_gen1_is_identified(self):
        service, session = self.service(FakeResponse(200, GEN1_INFO))
        info = service.identify()

        self.assertEqual(info.generation, 1)
        self.assertEqual(info.model, "SHPLG-S")
        self.assertEqual(info.generation_label, "Gen1")
        self.assertEqual(session.calls[0]["url"], f"http://{HOST}/shelly")

    def test_gen2_is_identified_with_name_and_version(self):
        service, _ = self.service(FakeResponse(200, GEN2_INFO))
        info = service.identify()

        self.assertEqual(info.generation, 2)
        self.assertEqual(info.label, "Becken 1 Licht")
        self.assertEqual(info.firmware, "1.0.3")
        self.assertFalse(info.auth_required)

    def test_unknown_generation_cannot_read_a_status(self):
        service, _ = self.service()
        with self.assertRaises(ShellyUnknownGeneration):
            service.read_status()


class Gen1Tests(SimpleTestCase):
    def service(self, *responses):
        api, session = client(*responses)
        return Gen1Service(client=api), session

    def test_status_is_read_and_converted(self):
        service, session = self.service(FakeResponse(200, GEN1_STATUS))
        status = service.read_status()

        self.assertEqual(session.calls[0]["url"], f"http://{HOST}/status")
        self.assertIs(status.is_on, True)
        self.assertEqual(str(status.power_w), "12.34")
        # 60000 Wattminuten sind 1000 Wattstunden.
        self.assertEqual(str(status.energy_total_wh), "1000.00")
        self.assertEqual(str(status.energy_total_kwh), "1.000")
        self.assertEqual(str(status.temperature_c), "24.4")
        self.assertEqual(status.payload, GEN1_STATUS)

    def test_overload_is_reported_in_plain_language(self):
        payload = {**GEN1_STATUS, "relays": [{"ison": False, "overpower": True}]}
        service, _ = self.service(FakeResponse(200, payload))
        status = service.read_status()

        self.assertTrue(status.has_alert)
        self.assertIn("Überlast", status.alerts[0])

    def test_switching_uses_the_relay_endpoint(self):
        service, session = self.service(FakeResponse(200, {"ison": True}))
        status = service.set_output(True)

        self.assertEqual(session.calls[0]["params"], {"turn": "on"})
        self.assertIs(status.is_on, True)

    def test_switching_off_sends_off(self):
        service, session = self.service(FakeResponse(200, {"ison": False}))
        service.set_output(False)
        self.assertEqual(session.calls[0]["params"], {"turn": "off"})

    def test_missing_meter_does_not_break_the_reading(self):
        service, _ = self.service(FakeResponse(200, {"relays": [{"ison": True}]}))
        status = service.read_status()

        self.assertIs(status.is_on, True)
        self.assertIsNone(status.power_w)
        self.assertIsNone(status.energy_total_wh)


class Gen2Tests(SimpleTestCase):
    def service(self, *responses):
        api, session = client(*responses)
        return Gen2Service(client=api), session

    def test_status_is_read_from_the_rpc_endpoint(self):
        service, session = self.service(FakeResponse(200, GEN2_STATUS))
        status = service.read_status()

        self.assertEqual(session.calls[0]["url"], f"http://{HOST}/rpc/Switch.GetStatus")
        self.assertEqual(session.calls[0]["params"], {"id": 0})
        self.assertIs(status.is_on, True)
        self.assertEqual(str(status.power_w), "12.30")
        # Gen2 zählt bereits in Wattstunden — hier wird nichts umgerechnet.
        self.assertEqual(str(status.energy_total_wh), "1000.00")
        self.assertEqual(str(status.temperature_c), "44.4")
        self.assertEqual(str(status.voltage_v), "232.9")

    def test_switching_sends_the_new_state(self):
        service, session = self.service(FakeResponse(200, {"was_on": False}))
        status = service.set_output(True)

        self.assertEqual(session.calls[0]["params"], {"id": 0, "on": "true"})
        self.assertIs(status.is_on, True)

    def test_rpc_error_is_raised_even_with_status_200(self):
        service, _ = self.service(FakeResponse(200, {"error": {"code": -105, "message": "no such id"}}))
        with self.assertRaises(ShellyResponseError) as caught:
            service.read_status()
        self.assertIn("no such id", str(caught.exception))
