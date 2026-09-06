"""Client und Geräteklassen der Eheim-Anbindung — ohne echtes Gerät."""

import base64

import requests
from django.test import SimpleTestCase, override_settings

from services.eheim import (
    ClassicVarioService,
    EheimAuthError,
    EheimClient,
    EheimEndpointBlocked,
    EheimNotConfigured,
    EheimResponseError,
    EheimService,
    EheimUnreachable,
    is_supported_firmware,
    normalize_mac,
    parse_version,
    service_class_for,
)
from services.eheim.devices import KIND_CLASSICVARIO, KIND_EHEIM_OTHER, classify

MAC = "AA:BB:CC:DD:EE:FF"

CLASSICVARIO_PAYLOAD = {
    "title": "CLASSIC_VARIO_DATA",
    "filterActive": 1,
    "rel_speed": 72,
    "pumpMode": 4,
    "errorCode": 0,
    "serviceHour": 1200,
    "rel_manual_motor_speed": 70,
    "rel_motor_speed_day": 80,
    "rel_motor_speed_night": 40,
    "start_time_day": 660,
    "start_time_night": 1380,
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ("{}" if payload is None else "json")

    def json(self):
        if self._payload is None:
            raise ValueError("kein JSON")
        return self._payload


class FakeSession:
    """Minimaler Ersatz für ``requests`` — liefert vorbereitete Antworten."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0) if self.responses else FakeResponse(200, {})
        if isinstance(response, Exception):
            raise response
        return response


def client(*responses, **kwargs):
    session = FakeSession(*responses)
    kwargs.setdefault("mac", MAC)
    return EheimClient("192.168.1.50", "api", "admin", session=session, **kwargs), session


class MacAndVersionTests(SimpleTestCase):
    def test_mac_is_normalized(self):
        for value in ("aabbccddeeff", "aa-bb-cc-dd-ee-ff", "AA:BB:CC:DD:EE:FF", "aabb.ccdd.eeff"):
            with self.subTest(value=value):
                self.assertEqual(normalize_mac(value), MAC)

    def test_unusable_mac_is_left_alone(self):
        self.assertEqual(normalize_mac("keine mac"), "keine mac")
        self.assertEqual(normalize_mac(""), "")

    def test_version_parsing(self):
        self.assertEqual(parse_version("2.0.1.4"), (2, 0, 1, 4))
        self.assertEqual(parse_version(""), ())

    def test_firmware_below_two_zero_one_is_unsupported(self):
        self.assertTrue(is_supported_firmware("2.0.1"))
        self.assertTrue(is_supported_firmware("2.1.0.0"))
        self.assertFalse(is_supported_firmware("2.0.0.9"))
        self.assertFalse(is_supported_firmware("1.9.9"))
        self.assertFalse(is_supported_firmware(""))


class ClientTests(SimpleTestCase):
    def test_get_sends_basic_auth_and_mac(self):
        api, session = client(FakeResponse(200, {"rel_speed": 50}))

        api.get("/classicvario")

        call = session.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "http://192.168.1.50/classicvario")
        self.assertEqual(call["params"]["to"], MAC)
        expected = base64.b64encode(b"api:admin").decode()
        self.assertEqual(call["headers"]["Authorization"], f"Basic {expected}")

    def test_post_sends_mac_in_the_body(self):
        api, session = client(FakeResponse(200, {}))

        api.post("/classic-vario-active", {"active": 1})

        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["json"], {"to": MAC, "active": 1})

    def test_request_without_mac_is_refused(self):
        api, session = client(mac="")
        with self.assertRaises(EheimNotConfigured):
            api.get("/classicvario")
        self.assertEqual(session.calls, [])

    def test_general_endpoints_work_without_mac(self):
        api, session = client(FakeResponse(200, {"clientList": []}), mac="")
        api.get("/mesh-liste", require_mac=False)
        self.assertNotIn("to", session.calls[0]["params"])

    def test_request_without_host_is_refused(self):
        api = EheimClient("", "api", "admin", mac=MAC, session=FakeSession())
        with self.assertRaises(EheimNotConfigured):
            api.get("/classicvario")

    def test_doupdate_is_blocked(self):
        api, session = client()
        for path in ("/doupdate", "doupdate", "/DoUpdate", "/doupdate?force=1"):
            with self.subTest(path=path):
                with self.assertRaises(EheimEndpointBlocked):
                    api.post(path, {})
        self.assertEqual(session.calls, [])

    def test_timeout_is_passed_and_reported(self):
        api, session = client(requests.Timeout("zu langsam"), timeout=3)
        with self.assertRaises(EheimUnreachable):
            api.get("/classicvario")
        self.assertEqual(session.calls[0]["timeout"], 3)

    @override_settings(EHEIM_TIMEOUT=9)
    def test_timeout_comes_from_settings(self):
        api, session = client(FakeResponse(200, {}))
        api.get("/classicvario")
        self.assertEqual(session.calls[0]["timeout"], 9)

    def test_connection_error_becomes_unreachable(self):
        api, _ = client(requests.ConnectionError("kein Netz"))
        with self.assertRaises(EheimUnreachable):
            api.get("/classicvario")

    def test_rejected_credentials_are_reported_separately(self):
        for status in (401, 403):
            with self.subTest(status=status):
                api, _ = client(FakeResponse(status, None, text="denied"))
                with self.assertRaises(EheimAuthError):
                    api.get("/classicvario")

    def test_server_error_becomes_response_error(self):
        api, _ = client(FakeResponse(500, None, text="kaputt"))
        with self.assertRaises(EheimResponseError):
            api.get("/classicvario")

    def test_broken_json_becomes_response_error(self):
        api, _ = client(FakeResponse(200, None, text="kein json"))
        with self.assertRaises(EheimResponseError):
            api.get("/classicvario")

    def test_empty_body_is_an_empty_answer(self):
        api, _ = client(FakeResponse(200, None, text=""))
        self.assertEqual(api.post("/classic-vario-active", {"active": 1}), {})

    def test_password_never_appears_in_error_messages(self):
        session = FakeSession(FakeResponse(500, None, text="Passwort geheim-123 abgelehnt"))
        api = EheimClient("host", "api", "geheim-123", mac=MAC, session=session)
        with self.assertRaises(EheimResponseError) as caught:
            api.get("/classicvario")
        self.assertNotIn("geheim-123", str(caught.exception))
        self.assertIn("***", str(caught.exception))


class ServiceRegistryTests(SimpleTestCase):
    def test_kind_selects_the_service_class(self):
        self.assertIs(service_class_for(KIND_CLASSICVARIO), ClassicVarioService)
        self.assertIs(service_class_for(KIND_EHEIM_OTHER), EheimService)

    def test_unknown_kind_falls_back_to_the_base_class(self):
        self.assertIs(service_class_for("gibt-es-nicht"), EheimService)

    def test_classify_recognises_the_vario_by_name(self):
        self.assertEqual(classify({"title": "classicVARIO+e"}), KIND_CLASSICVARIO)
        self.assertEqual(classify({"name": "Heizer"}), KIND_EHEIM_OTHER)
        self.assertEqual(classify({}), KIND_EHEIM_OTHER)


class MeshDiscoveryTests(SimpleTestCase):
    def service(self, *responses):
        api, session = client(*responses, mac="")
        return EheimService(client=api), session

    def test_mesh_list_returns_normalized_macs(self):
        service, _ = self.service(FakeResponse(200, {"clientList": ["aabbccddeeff", "AA:BB:CC:00:11:22"]}))
        self.assertEqual(service.mesh_list(), [MAC, "AA:BB:CC:00:11:22"])

    def test_discover_reads_userdata_per_device(self):
        service, session = self.service(
            FakeResponse(200, {"clientList": ["aabbccddeeff"]}),
            FakeResponse(200, {"title": "classicVARIO+e", "name": "Filter", "version": "2.0.1.4"}),
        )

        found = service.discover()

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].mac, MAC)
        self.assertEqual(found[0].name, "Filter")
        self.assertEqual(found[0].kind, KIND_CLASSICVARIO)
        self.assertTrue(found[0].firmware_supported)
        self.assertEqual(session.calls[1]["params"]["to"], MAC)

    def test_silent_node_does_not_break_the_search(self):
        service, _ = self.service(
            FakeResponse(200, {"clientList": ["aabbccddeeff", "aabbccddee11"]}),
            requests.Timeout("still"),
            FakeResponse(200, {"name": "Zweites", "version": "2.0.1.4"}),
        )

        found = service.discover()

        self.assertEqual([item.mac for item in found], [MAC, "AA:BB:CC:DD:EE:11"])
        self.assertEqual(found[0].name, "")
        self.assertEqual(found[1].name, "Zweites")

    def test_firmware_is_read_from_userdata(self):
        service, _ = self.service(FakeResponse(200, {"version": "2.0.1.4"}))
        self.assertEqual(service.firmware(), "2.0.1.4")


class ClassicVarioTests(SimpleTestCase):
    def service(self, *responses):
        api, session = client(*responses)
        return ClassicVarioService(client=api), session

    def test_status_is_parsed(self):
        service, session = self.service(FakeResponse(200, CLASSICVARIO_PAYLOAD))

        status = service.read_status()

        self.assertEqual(session.calls[0]["url"], "http://192.168.1.50/classicvario")
        self.assertIs(status.is_on, True)
        self.assertEqual(status.rpm_percent, 72)
        self.assertEqual(status.pump_mode, 4)
        self.assertEqual(status.mode_label, "Bio-Modus")
        self.assertEqual(status.error_code, 0)
        self.assertFalse(status.has_error)
        self.assertEqual(status.service_due_in, 1200)
        self.assertEqual(status.service_due_in_days, 50)
        self.assertEqual(status.day_speed, 80)
        self.assertEqual(status.night_speed, 40)
        self.assertEqual(status.day_start_label, "11:00")
        self.assertEqual(status.night_start_label, "23:00")

    def test_raw_answer_is_kept(self):
        service, _ = self.service(FakeResponse(200, CLASSICVARIO_PAYLOAD))
        self.assertEqual(service.read_status().payload, CLASSICVARIO_PAYLOAD)

    def test_error_codes_are_translated(self):
        for code, text in ((1, "Rotor blockiert"), (2, "Luft im Filter"), (7, "unbekannter Fehler (7)")):
            with self.subTest(code=code):
                service, _ = self.service(FakeResponse(200, {**CLASSICVARIO_PAYLOAD, "errorCode": code}))
                status = service.read_status()
                self.assertTrue(status.has_error)
                self.assertEqual(status.error_text, text)

    def test_unknown_fields_do_not_break_the_status(self):
        service, _ = self.service(FakeResponse(200, {"title": "CLASSIC_VARIO_DATA"}))

        status = service.read_status()

        self.assertIsNone(status.rpm_percent)
        self.assertIsNone(status.is_on)
        self.assertEqual(status.error_text, "")

    def test_switching_uses_one_and_zero(self):
        service, session = self.service(FakeResponse(200, {}), FakeResponse(200, {}))

        service.set_active(True)
        service.set_active(False)

        self.assertEqual(session.calls[0]["json"], {"to": MAC, "active": 1})
        self.assertEqual(session.calls[1]["json"], {"to": MAC, "active": 0})

    def test_manual_mode_sends_the_speed(self):
        service, session = self.service(FakeResponse(200, {}))
        service.set_manual(70)
        self.assertEqual(session.calls[0]["url"], "http://192.168.1.50/classic-vario-manual")
        self.assertEqual(session.calls[0]["json"]["rel_manual_motor_speed"], 70)

    def test_bio_mode_sends_minutes_since_midnight(self):
        service, session = self.service(FakeResponse(200, {}))

        service.set_bio(80, 40, "11:00", "23:00")

        self.assertEqual(
            session.calls[0]["json"],
            {
                "to": MAC,
                "rel_motor_speed_day": 80,
                "rel_motor_speed_night": 40,
                "start_time_day": 660,
                "start_time_night": 1380,
            },
        )

    def test_pulse_mode_sends_speeds_and_durations(self):
        service, session = self.service(FakeResponse(200, {}))

        service.set_pulse(80, 40, 30, 20)

        self.assertEqual(
            session.calls[0]["json"],
            {
                "to": MAC,
                "rel_motor_speed_high": 80,
                "rel_motor_speed_low": 40,
                "pulse_time_high": 30,
                "pulse_time_low": 20,
            },
        )

    def test_impossible_speeds_never_reach_the_device(self):
        service, session = self.service()
        for value in (-5, 101, "schnell"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    service.set_manual(value)
        self.assertEqual(session.calls, [])

    def test_impossible_durations_never_reach_the_device(self):
        service, session = self.service()
        with self.assertRaises(ValueError):
            service.set_pulse(80, 40, 0, 30)
        self.assertEqual(session.calls, [])

    def test_change_auth_posts_new_credentials(self):
        service, session = self.service(FakeResponse(200, {}))

        service.change_auth("neues-passwort")

        self.assertEqual(session.calls[0]["url"], "http://192.168.1.50/changeauth")
        self.assertEqual(session.calls[0]["json"]["password"], "neues-passwort")

    def test_empty_password_is_refused(self):
        service, session = self.service()
        with self.assertRaises(ValueError):
            service.change_auth("")
        self.assertEqual(session.calls, [])

    def test_status_led_brightness(self):
        service, session = self.service(FakeResponse(200, {}))
        service.set_status_led_brightness(40)
        self.assertEqual(session.calls[0]["url"], "http://192.168.1.50/brightness-status-led")
        self.assertEqual(session.calls[0]["json"]["brightness"], 40)

    def test_service_offers_no_firmware_update(self):
        self.assertFalse(
            [name for name in dir(ClassicVarioService) if "update" in name.lower()]
        )
