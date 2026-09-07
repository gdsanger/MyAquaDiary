"""Tests des Endpunkts: Streamable HTTP, Anmeldung, Herkunft — und der alte
SSE-Transport, solange es ihn noch gibt.

Der Strom läuft, bis der Client geht — in den Tests wird deshalb immer nur so
weit gelesen, wie es etwas zu prüfen gibt, und danach geschlossen.
"""

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.signals import request_finished
from django.db import close_old_connections
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from services.masking import MaskTokens, mask
from services.mcp import auth, protocol, sessions, views, wsgi
from services.mcp.audit import access_log
from services.mcp.runner import Context
from services.mcp.views import stream
from services.models import MCPToken

from .test_mcp_tools import make_tank
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
class MessageTests(TestCase):
    def setUp(self):
        sessions._sessions.clear()
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop", allow_write=True)
        self.session = sessions.open_session(self.token)
        self.url = f"{reverse('mcp:messages')}?session={self.session.id}"
        make_tank(self.user)

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


@MCP_URLS
class StreamableHttpTests(TestCase):
    """``POST /mcp/`` — eine Nachricht rein, die Antwort direkt zurück."""

    def setUp(self):
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop", allow_write=True)
        self.url = reverse("mcp:endpoint")
        make_tank(self.user)

    def post(self, payload, url=None, **extra):
        return self.client.post(
            url or self.url,
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )

    def test_a_request_is_answered_in_the_same_response(self):
        response = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, **bearer(self.key))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        answer = response.json()
        self.assertEqual(answer["id"], 1)
        self.assertIn("tools", answer["result"])

    def test_a_tool_call_runs_against_the_own_data(self):
        response = self.post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_tanks", "arguments": {}},
            },
            **bearer(self.key),
        )

        payload = json.loads(response.json()["result"]["content"][0]["text"])
        self.assertEqual(payload["tanks"][0]["name"], "Südamerika-Becken")

    def test_the_answer_needs_no_session_of_any_kind(self):
        """Zustandslos: kein Mcp-Session-Id, nichts, was im Prozess liegt."""
        self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, **bearer(self.key))

        self.assertNotIn("Mcp-Session-Id", self.post(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"}, **bearer(self.key)
        ))
        self.assertEqual(sessions.count(), 0)

    def test_a_notification_is_accepted_without_an_answer(self):
        response = self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, **bearer(self.key)
        )

        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.content)

    def test_a_batch_comes_back_as_a_batch(self):
        response = self.post(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            ],
            **bearer(self.key),
        )

        self.assertEqual([answer["id"] for answer in response.json()], [1, 2])

    def test_broken_json_is_refused(self):
        response = self.client.post(
            self.url, data="{kein json", content_type="application/json", **bearer(self.key)
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], protocol.PARSE_ERROR)

    def test_the_endpoint_does_not_offer_a_stream(self):
        """Kein GET-Strom: wir verschicken nichts Unaufgefordertes."""
        response = self.client.get(self.url, **bearer(self.key))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "POST")

    def test_deleting_a_session_is_not_a_thing_here(self):
        response = self.client.delete(self.url, **bearer(self.key))

        self.assertEqual(response.status_code, 405)

    def test_the_address_works_without_a_trailing_slash(self):
        """Sonst machte APPEND_SLASH aus dem POST eine Umleitung ohne Rumpf."""
        response = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             url="/mcp", **bearer(self.key))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 1)


@MCP_URLS
class QueryTokenTests(TestCase):
    """Der Token in der Adresse — der Weg, der ohne mcp-remote auskommt."""

    def setUp(self):
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop")
        self.url = reverse("mcp:endpoint")

    def ping(self, url=None, **extra):
        return self.client.post(
            url or self.url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            **extra,
        )

    def test_a_token_in_the_query_string_is_enough(self):
        response = self.ping(f"{self.url}?{auth.TOKEN_PARAM}={self.key}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 1)

    def test_the_header_wins_over_the_query_string(self):
        """Schickt ein Client beides, zählt das Geheimnis aus dem Kopf."""
        stranger, stranger_key = MCPToken.issue(make_user("hans"), "Fremder")

        self.client.post(
            f"{self.url}?{auth.TOKEN_PARAM}={stranger_key}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            content_type="application/json",
            **bearer(self.key),
        )

        self.token.refresh_from_db()
        stranger.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)
        self.assertIsNone(stranger.last_used_at)

    def test_an_empty_header_falls_back_to_the_query_string(self):
        """Manche Clients setzen den Kopf leer statt gar nicht."""
        response = self.ping(
            f"{self.url}?{auth.TOKEN_PARAM}={self.key}", HTTP_AUTHORIZATION="Bearer   "
        )

        self.assertEqual(response.status_code, 200)

    def test_another_authentication_scheme_is_not_a_token(self):
        response = self.ping(HTTP_AUTHORIZATION=f"Basic {self.key}")

        self.assertEqual(response.status_code, 401)

    def test_a_revoked_token_is_refused_in_the_query_string_too(self):
        self.token.revoke()

        response = self.ping(f"{self.url}?{auth.TOKEN_PARAM}={self.key}")

        self.assertEqual(response.status_code, 401)

    def test_an_expired_token_is_refused_in_the_query_string_too(self):
        self.token.expires_at = timezone.now() - timedelta(minutes=1)
        self.token.save(update_fields=["expires_at"])

        response = self.ping(f"{self.url}?{auth.TOKEN_PARAM}={self.key}")

        self.assertEqual(response.status_code, 401)

    def test_without_any_token_there_is_nothing(self):
        response = self.ping()

        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response["WWW-Authenticate"])

    def test_connecting_marks_the_token_as_used(self):
        self.client.post(
            f"{self.url}?{auth.TOKEN_PARAM}={self.key}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            content_type="application/json",
        )

        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)


@MCP_URLS
class OriginTests(TestCase):
    """DNS-Rebinding: eine fremde Webseite spricht den Dienst im Namen des
    Browsers an. Dagegen — und nur dagegen — richtet sich die Prüfung."""

    def setUp(self):
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop")
        self.url = reverse("mcp:endpoint")

    def ping(self, **extra):
        return self.client.post(
            self.url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            **{**bearer(self.key), **extra},
        )

    def test_a_missing_origin_is_fine(self):
        """curl und native Clients schicken keinen — die sollen sich verbinden."""
        self.assertEqual(self.ping().status_code, 200)

    def test_a_foreign_origin_is_refused(self):
        response = self.ping(HTTP_ORIGIN="https://boese.example.com")

        self.assertEqual(response.status_code, 403)

    @override_settings(MCP_ALLOWED_ORIGINS=["https://tagebuch.example.com"])
    def test_a_configured_origin_gets_through(self):
        response = self.ping(HTTP_ORIGIN="https://tagebuch.example.com")

        self.assertEqual(response.status_code, 200)

    def test_the_check_happens_before_the_token_is_looked_at(self):
        """Sonst wäre der 403 ein Orakel darüber, ob ein Token gilt."""
        response = self.client.post(
            self.url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            HTTP_ORIGIN="https://boese.example.com",
        )

        self.assertEqual(response.status_code, 403)

    def test_the_old_endpoints_are_protected_too(self):
        response = self.client.get(
            reverse("mcp:sse"), HTTP_ORIGIN="https://boese.example.com", **bearer(self.key)
        )

        self.assertEqual(response.status_code, 403)


@MCP_URLS
class DeprecationTests(TestCase):
    """Der alte Transport läuft weiter — und sagt, dass er verschwindet."""

    def setUp(self):
        sessions._sessions.clear()
        self.user = make_user()
        self.token, self.key = MCPToken.issue(self.user, "Claude Desktop")

    def test_the_stream_announces_its_own_end(self):
        response = open_stream(self.key)
        first_chunk(response)

        self.assertTrue(response["Deprecation"].startswith("@"))
        self.assertTrue(response["Sunset"])
        self.assertIn(reverse("mcp:endpoint"), response["Link"])

    def test_the_message_channel_announces_it_too(self):
        session = sessions.open_session(self.token)

        response = self.client.post(
            f"{reverse('mcp:messages')}?session={session.id}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            **bearer(self.key),
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response["Sunset"])

    @override_settings(MCP_LEGACY_SSE=False)
    def test_the_old_transport_can_be_switched_off(self):
        """Erst dann ist der Dienst zustandslos und darf mehrere Worker haben."""
        response = self.client.get(reverse("mcp:sse"), **bearer(self.key))

        self.assertEqual(response.status_code, 410)
        self.assertIn(reverse("mcp:endpoint"), response.json()["error"])
        self.assertEqual(sessions.count(), 0)

    @override_settings(MCP_LEGACY_SSE=False)
    def test_switching_it_off_closes_the_message_channel_as_well(self):
        session = sessions.open_session(self.token)

        response = self.client.post(
            f"{reverse('mcp:messages')}?session={session.id}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            **bearer(self.key),
        )

        self.assertEqual(response.status_code, 410)


class MaskingTests(TestCase):
    """Der Token darf in der Adresse stehen — im Protokoll nicht."""

    def test_a_token_is_shortened_to_its_hint(self):
        _, key = MCPToken.issue(make_user(), "Claude Desktop")

        masked = mask(f"Not Found: /mcp/?token={key}")

        self.assertNotIn(key, masked)
        self.assertIn(key[:10], masked)

    def test_text_without_a_token_is_left_alone(self):
        self.assertEqual(mask("Not Found: /geraete/"), "Not Found: /geraete/")
        self.assertEqual(mask(""), "")

    def test_the_log_filter_reaches_message_and_arguments(self):
        """Die gefährliche Zeile kommt von django.request, nicht von uns."""
        _, key = MCPToken.issue(make_user(), "Claude Desktop")
        record = logging.LogRecord(
            "django.request", logging.WARNING, __file__, 1, "Not Found: %s", (f"/mcp/?token={key}",), None
        )

        MaskTokens().filter(record)

        self.assertNotIn(key, record.getMessage())

    def test_the_access_log_never_keeps_a_token(self):
        """Ein Token, den jemand als Werkzeugparameter mitschickt, bleibt nicht
        in der Datenbank stehen."""
        user = make_user()
        token, key = MCPToken.issue(user, "Claude Desktop", allow_write=True)

        entry = access_log(
            Context(token=token, user=user),
            tool="create_event",
            arguments={"note": f"mein Zugang ist {key}"},
            succeeded=True,
        )

        self.assertNotIn(key, entry.arguments["note"])
