"""Tests des Endpunkts: SSE-Strom, Nachrichtenkanal, Anmeldung.

Der Strom läuft, bis der Client geht — in den Tests wird deshalb immer nur so
weit gelesen, wie es etwas zu prüfen gibt, und danach geschlossen.
"""

import json

from django.conf import settings
from django.core.signals import request_finished
from django.db import close_old_connections
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from services.mcp import protocol, sessions, views, wsgi
from services.mcp.views import stream
from services.models import MCPToken

from .mcp_stubs import DataModelTestCase, StubTank
from .test_mcp_tokens import make_user

#: Der MCP-Endpunkt hat einen eigenen URL-Baum — genau wie im Betrieb.
MCP_URLS = override_settings(ROOT_URLCONF="config.mcp_urls")


def bearer(key):
    return {"HTTP_AUTHORIZATION": f"Bearer {key}"}


def open_stream(key):
    """Öffnet den Strom über die View statt über den Testclient.

    Der Testclient legt um eine strömende Antwort einen Wrapper, der beim
    Schließen ``request_finished`` auslöst — und daran hängt
    ``close_old_connections``, das dem laufenden Testfall die
    Datenbankverbindung unter den Füßen wegzieht. Der direkte Aufruf der View
    prüft dasselbe ohne diesen Nebeneffekt.
    """
    request = RequestFactory().get(reverse("mcp:sse"), **bearer(key))
    return views.sse(request)


def first_chunk(response):
    """Der erste Abschnitt des Stroms; danach wird der Strom geschlossen."""
    iterator = iter(response.streaming_content)
    try:
        return next(iterator).decode("utf-8")
    finally:
        # Das Schließen sendet ``request_finished``; im Test soll daraus keine
        # geschlossene Datenbankverbindung werden.
        request_finished.disconnect(close_old_connections)
        response.close()
        request_finished.connect(close_old_connections)


@MCP_URLS
class AuthenticationTests(TestCase):
    def setUp(self):
        sessions._sessions.clear()
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop")

    def test_the_stream_needs_a_token(self):
        response = self.client.get(reverse("mcp:sse"))

        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response["WWW-Authenticate"])

    def test_a_wrong_token_is_refused(self):
        response = self.client.get(reverse("mcp:sse"), **bearer("mad_ausgedacht"))

        self.assertEqual(response.status_code, 401)

    def test_another_authentication_scheme_is_refused(self):
        response = self.client.get(
            reverse("mcp:sse"), HTTP_AUTHORIZATION=f"Basic {self.key}"
        )

        self.assertEqual(response.status_code, 401)

    def test_a_revoked_token_is_refused(self):
        self.token.revoke()

        response = self.client.get(reverse("mcp:sse"), **bearer(self.key))

        self.assertEqual(response.status_code, 401)

    def test_the_messages_endpoint_needs_a_token_too(self):
        response = self.client.post(reverse("mcp:messages") + "?session=egal")

        self.assertEqual(response.status_code, 401)

    def test_using_the_stream_marks_the_token_as_used(self):
        first_chunk(open_stream(self.key))

        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)

    def test_the_web_application_is_not_reachable_here(self):
        """Der MCP-Entrypoint kennt nur den Endpunkt — kein Admin, kein Login."""
        self.assertEqual(self.client.get("/admin/").status_code, 404)
        self.assertEqual(self.client.get("/geraete/").status_code, 404)


@MCP_URLS
class StreamTests(TestCase):
    def setUp(self):
        sessions._sessions.clear()
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop")

    def test_the_stream_starts_with_the_message_endpoint(self):
        response = open_stream(self.key)

        chunk = first_chunk(response)

        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertIn("event: endpoint", chunk)
        self.assertIn(reverse("mcp:messages"), chunk)
        self.assertIn("?session=", chunk)

    def test_the_session_is_gone_once_the_stream_ends(self):
        first_chunk(open_stream(self.key))

        self.assertEqual(sessions.count(), 0)

    def test_a_pushed_answer_reaches_the_stream(self):
        session = sessions.open_session(self.token)
        session.push({"jsonrpc": "2.0", "id": 1, "result": {}})

        chunks = stream(session, "/mcp/messages/", keepalive=1)
        self.assertIn("event: endpoint", next(chunks))
        message = next(chunks)
        chunks.close()

        self.assertIn("event: message", message)
        self.assertEqual(json.loads(message.split("data: ", 1)[1])["id"], 1)

    def test_a_quiet_stream_sends_a_keep_alive(self):
        session = sessions.open_session(self.token)

        chunks = stream(session, "/mcp/messages/", keepalive=0.01)
        next(chunks)
        quiet = next(chunks)
        chunks.close()

        self.assertTrue(quiet.startswith(":"))

    def test_closing_the_stream_frees_the_session(self):
        session = sessions.open_session(self.token)
        chunks = stream(session, "/mcp/messages/", keepalive=0.01)
        next(chunks)

        chunks.close()

        self.assertIsNone(sessions.get(session.id))


class EntrypointTests(TestCase):
    """Der eigene Entrypoint: eigener URL-Baum, kürzere Middleware-Kette."""

    def test_the_handler_serves_the_mcp_url_tree(self):
        self.assertEqual(wsgi.MCP_URLCONF, "config.mcp_urls")

    def test_the_debug_toolbar_is_left_out(self):
        """Sie erwartet Adressen, die es im MCP-Baum nicht gibt — und ist dort
        auch sonst fehl am Platz."""
        configured = [
            "django.middleware.common.CommonMiddleware",
            "debug_toolbar.middleware.DebugToolbarMiddleware",
        ]

        self.assertEqual(
            wsgi.usable_middleware(configured),
            ["django.middleware.common.CommonMiddleware"],
        )

    def test_building_the_handler_leaves_the_settings_alone(self):
        before = list(settings.MIDDLEWARE)

        wsgi.MCPHandler()

        self.assertEqual(list(settings.MIDDLEWARE), before)


class SessionRegistryTests(TestCase):
    def setUp(self):
        sessions._sessions.clear()
        self.user = make_user()
        self.token, _ = MCPToken.issue(self.user, "Claude Desktop")

    def test_sessions_have_their_own_identifiers(self):
        first = sessions.open_session(self.token)
        second = sessions.open_session(self.token)

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(sessions.count(), 2)

    def test_an_unknown_identifier_yields_nothing(self):
        self.assertIsNone(sessions.get("ausgedacht"))
        self.assertIsNone(sessions.get(""))

    @override_settings(MCP_SESSION_IDLE_TIMEOUT=0)
    def test_forgotten_sessions_are_cleaned_up_on_the_next_connection(self):
        abandoned = sessions.open_session(self.token)

        sessions.open_session(self.token)

        self.assertIsNone(sessions.get(abandoned.id))


@MCP_URLS
class MessageTests(DataModelTestCase):
    def setUp(self):
        sessions._sessions.clear()
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop", allow_write=True)
        self.session = sessions.open_session(self.token)
        self.url = f"{reverse('mcp:messages')}?session={self.session.id}"
        StubTank.objects.create(owner=self.user, name="Südamerika-Becken")

    def post(self, payload, url=None, **extra):
        return self.client.post(
            url or self.url,
            data=json.dumps(payload),
            content_type="application/json",
            **{**bearer(self.key), **extra},
        )

    def test_a_request_is_accepted_and_answered_through_the_stream(self):
        response = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        self.assertEqual(response.status_code, 202)
        answer = self.session.take(timeout=1)
        self.assertEqual(answer["id"], 1)
        self.assertIn("tools", answer["result"])

    def test_a_tool_call_runs_against_the_own_data(self):
        self.post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_tanks", "arguments": {}},
            }
        )

        answer = self.session.take(timeout=1)
        payload = json.loads(answer["result"]["content"][0]["text"])
        self.assertEqual(payload["tanks"][0]["name"], "Südamerika-Becken")

    def test_a_notification_is_accepted_without_an_answer(self):
        response = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})

        self.assertEqual(response.status_code, 202)
        self.assertIsNone(self.session.take(timeout=0.05))

    def test_broken_json_is_refused(self):
        response = self.client.post(
            self.url, data="{kein json", content_type="application/json", **bearer(self.key)
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], protocol.PARSE_ERROR)

    def test_an_unknown_session_is_refused(self):
        response = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             url=f"{reverse('mcp:messages')}?session=ausgedacht")

        self.assertEqual(response.status_code, 404)

    def test_a_foreign_session_is_refused(self):
        stranger = make_user("hans")
        stranger_token, stranger_key = MCPToken.issue(stranger, "Fremder Client")
        foreign_session = sessions.open_session(stranger_token)

        response = self.post(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            url=f"{reverse('mcp:messages')}?session={foreign_session.id}",
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(foreign_session.take(timeout=0.05))
        self.assertTrue(stranger_key)

    def test_a_revoked_token_stops_a_running_session(self):
        self.token.revoke()

        response = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"})

        self.assertEqual(response.status_code, 401)

    def test_a_get_request_is_not_a_message(self):
        self.assertEqual(self.client.get(self.url, **bearer(self.key)).status_code, 405)
