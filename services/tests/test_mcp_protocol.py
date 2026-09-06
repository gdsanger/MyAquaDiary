"""Tests der JSON-RPC-Schicht, der Registry und des Ratelimits."""

import json

from django.core.cache import cache
from django.test import TestCase, override_settings

from services.mcp import protocol, ratelimit, registry
from services.mcp.exceptions import RateLimited, ToolError
from services.mcp.runner import Context
from services.models import MCPToken

from .mcp_stubs import DataModelTestCase, StubTank
from .test_mcp_tokens import make_user


def request(method, message_id=1, **params):
    payload = {"jsonrpc": "2.0", "method": method}
    if message_id is not None:
        payload["id"] = message_id
    if params:
        payload["params"] = params
    return payload


class RegistryTests(TestCase):
    def test_every_tool_has_a_description_and_a_schema(self):
        for definition in registry.definitions(allow_write=True):
            with self.subTest(tool=definition["name"]):
                self.assertTrue(definition["description"])
                self.assertEqual(definition["inputSchema"]["type"], "object")

    def test_required_arguments_are_declared_as_properties(self):
        for definition in registry.definitions(allow_write=True):
            schema = definition["inputSchema"]
            for name in schema.get("required", []):
                with self.subTest(tool=definition["name"], argument=name):
                    self.assertIn(name, schema["properties"])

    def test_a_read_only_token_sees_no_writing_tools(self):
        readable = {item["name"] for item in registry.definitions(allow_write=False)}
        everything = {item["name"] for item in registry.definitions(allow_write=True)}

        self.assertIn("list_tanks", readable)
        self.assertNotIn("create_event", readable)
        self.assertIn("create_event", everything)

    def test_reading_tools_are_marked_as_read_only(self):
        by_name = {item["name"]: item for item in registry.definitions(allow_write=True)}

        self.assertTrue(by_name["list_tanks"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["create_event"]["annotations"]["readOnlyHint"])

    def test_there_is_no_tool_for_devices_deletion_catalog_or_users(self):
        """Was hier auftaucht, ist ein Rückschritt und kein Zufall."""
        forbidden = ["device", "delete", "remove", "token", "user", "catalog_entry_create"]
        for name in registry.names():
            for word in forbidden:
                with self.subTest(tool=name, word=word):
                    self.assertNotIn(word, name)

    def test_an_unknown_tool_is_a_readable_error(self):
        with self.assertRaises(ToolError) as caught:
            registry.get("drop_database")

        self.assertIn("drop_database", str(caught.exception))


class ProtocolTests(DataModelTestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.token, _ = MCPToken.issue(self.user, "Claude Desktop", allow_write=True)
        self.context = Context(token=self.token, user=self.user)
        self.tank = StubTank.objects.create(owner=self.user, name="Südamerika-Becken")

    def handle(self, payload):
        return protocol.handle_message(self.context, payload)

    # -- Handshake ------------------------------------------------------------

    def test_initialize_answers_with_capabilities_and_server_info(self):
        answer = self.handle(request("initialize", protocolVersion=protocol.PROTOCOL_VERSION))

        result = answer["result"]
        self.assertEqual(answer["id"], 1)
        self.assertEqual(result["protocolVersion"], protocol.PROTOCOL_VERSION)
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], protocol.SERVER_NAME)

    def test_a_newer_protocol_version_is_confirmed(self):
        answer = self.handle(request("initialize", protocolVersion="2025-06-18"))

        self.assertEqual(answer["result"]["protocolVersion"], "2025-06-18")

    def test_an_unknown_protocol_version_falls_back_to_the_own_one(self):
        answer = self.handle(request("initialize", protocolVersion="1999-01-01"))

        self.assertEqual(answer["result"]["protocolVersion"], protocol.PROTOCOL_VERSION)

    def test_the_initialized_notification_gets_no_answer(self):
        self.assertIsNone(self.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_an_unknown_notification_is_silently_accepted(self):
        self.assertIsNone(self.handle({"jsonrpc": "2.0", "method": "notifications/erfunden"}))

    def test_ping_is_answered(self):
        self.assertEqual(self.handle(request("ping"))["result"], {})

    # -- Werkzeuge ------------------------------------------------------------

    def test_tools_list_returns_the_tools(self):
        answer = self.handle(request("tools/list"))

        names = [item["name"] for item in answer["result"]["tools"]]
        self.assertIn("list_tanks", names)
        self.assertIn("create_measurement", names)

    def test_tools_list_hides_writing_tools_from_a_read_only_token(self):
        token, _ = MCPToken.issue(self.user, "Nur lesen")
        answer = protocol.handle_message(
            Context(token=token, user=self.user), request("tools/list")
        )

        names = [item["name"] for item in answer["result"]["tools"]]
        self.assertNotIn("create_measurement", names)

    def test_a_tool_call_returns_its_result_as_json_text(self):
        answer = self.handle(request("tools/call", name="list_tanks", arguments={}))

        result = answer["result"]
        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["tanks"][0]["name"], "Südamerika-Becken")

    def test_a_tool_error_is_a_result_and_not_a_protocol_error(self):
        answer = self.handle(request("tools/call", name="get_tank", arguments={"tank_id": 4711}))

        self.assertNotIn("error", answer)
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("keinen Eintrag", answer["result"]["content"][0]["text"])

    def test_calling_an_unknown_tool_is_a_tool_error(self):
        answer = self.handle(request("tools/call", name="drop_database", arguments={}))

        self.assertTrue(answer["result"]["isError"])

    def test_a_call_without_a_tool_name_is_refused(self):
        answer = self.handle(request("tools/call", arguments={}))

        self.assertTrue(answer["result"]["isError"])

    def test_a_call_without_arguments_still_works(self):
        answer = self.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                              "params": {"name": "list_tanks"}})

        self.assertFalse(answer["result"]["isError"])

    # -- Fehlerfälle ----------------------------------------------------------

    def test_an_unknown_method_is_a_protocol_error(self):
        answer = self.handle(request("resources/list"))

        self.assertEqual(answer["error"]["code"], protocol.METHOD_NOT_FOUND)

    def test_a_message_without_a_method_is_refused(self):
        answer = self.handle({"jsonrpc": "2.0", "id": 3})

        self.assertEqual(answer["error"]["code"], protocol.INVALID_REQUEST)
        self.assertEqual(answer["id"], 3)

    def test_something_that_is_not_an_object_is_refused(self):
        answer = self.handle("guten tag")

        self.assertEqual(answer["error"]["code"], protocol.INVALID_REQUEST)

    def test_a_batch_is_answered_in_one_go(self):
        answers = self.handle([request("ping", message_id=1), request("tools/list", message_id=2)])

        self.assertEqual([item["id"] for item in answers], [1, 2])

    def test_a_batch_of_notifications_needs_no_answer(self):
        self.assertIsNone(
            self.handle([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
        )

    def test_an_empty_batch_is_refused(self):
        self.assertEqual(self.handle([])["error"]["code"], protocol.INVALID_REQUEST)


@override_settings(MCP_RATE_LIMIT_PER_MINUTE=3)
class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.token, _ = MCPToken.issue(self.user, "Claude Desktop")

    def test_it_lets_calls_through_up_to_the_limit(self):
        for expected in (1, 2, 3):
            self.assertEqual(ratelimit.check(self.token), expected)

    def test_it_stops_the_call_after_the_limit(self):
        for _ in range(3):
            ratelimit.check(self.token)

        with self.assertRaises(RateLimited) as caught:
            ratelimit.check(self.token)

        self.assertIn("3", str(caught.exception))

    def test_the_limit_counts_per_token(self):
        other, _ = MCPToken.issue(self.user, "Zweiter Client")
        for _ in range(3):
            ratelimit.check(self.token)

        self.assertEqual(ratelimit.check(other), 1)

    @override_settings(MCP_RATE_LIMIT_PER_MINUTE=0)
    def test_zero_switches_the_limit_off(self):
        for _ in range(10):
            ratelimit.check(self.token)
