"""Tests der MCP-Zugänge: Token-Modell und Verwaltungsseite.

Der Schwerpunkt liegt auf dem, was einen Zugang gefährlich machen würde: ein
gespeicherter Klartext, ein Widerruf ohne Wirkung, ein fremder Token, der sich
widerrufen lässt.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from services.models import MCP_TOKEN_PREFIX, MCPToken


def make_user(username="greta"):
    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", password="geheim"
    )


class MCPTokenModelTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_issuing_returns_a_key_that_is_not_stored(self):
        token, key = MCPToken.issue(self.user, "Claude Desktop")

        self.assertTrue(key.startswith(MCP_TOKEN_PREFIX))
        self.assertNotIn(key, MCPToken.objects.values_list("token_hash", flat=True))
        self.assertEqual(token.token_hash, MCPToken.hash_key(key))
        self.assertTrue(key.startswith(token.hint))

    def test_two_tokens_never_look_alike(self):
        _, first = MCPToken.issue(self.user, "Erster")
        _, second = MCPToken.issue(self.user, "Zweiter")

        self.assertNotEqual(first, second)

    def test_resolving_finds_the_token_behind_a_key(self):
        token, key = MCPToken.issue(self.user, "Claude Desktop")

        self.assertEqual(MCPToken.resolve(key), token)

    def test_an_unknown_or_empty_key_resolves_to_nothing(self):
        MCPToken.issue(self.user, "Claude Desktop")

        self.assertIsNone(MCPToken.resolve("mad_ausgedacht"))
        self.assertIsNone(MCPToken.resolve(""))

    def test_a_revoked_token_stops_working_immediately(self):
        token, key = MCPToken.issue(self.user, "Claude Desktop")
        token.revoke()

        self.assertIsNone(MCPToken.resolve(key))
        self.assertEqual(token.status_label, "widerrufen")

    def test_revoking_twice_keeps_the_first_moment(self):
        token, _ = MCPToken.issue(self.user, "Claude Desktop")
        token.revoke()
        first = token.revoked_at
        token.revoke()

        self.assertEqual(token.revoked_at, first)

    def test_an_expired_token_stops_working(self):
        _, key = MCPToken.issue(
            self.user, "Abgelaufen", expires_at=timezone.now() - timedelta(minutes=1)
        )

        self.assertIsNone(MCPToken.resolve(key))

    def test_a_token_with_a_future_expiry_still_works(self):
        token, key = MCPToken.issue(
            self.user, "Befristet", expires_at=timezone.now() + timedelta(days=1)
        )

        self.assertEqual(MCPToken.resolve(key), token)
        self.assertEqual(token.status_label, "aktiv")

    def test_write_access_is_off_unless_asked_for(self):
        token, _ = MCPToken.issue(self.user, "Nur lesen")

        self.assertFalse(token.allow_write)
        self.assertEqual(token.access_label, "nur lesen")

    def test_touching_records_the_use(self):
        token, _ = MCPToken.issue(self.user, "Claude Desktop")
        self.assertIsNone(token.last_used_at)

        token.touch()
        token.refresh_from_db()

        self.assertIsNotNone(token.last_used_at)


class MCPTokenPageTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse("services:mcp_token_list")

    def test_the_page_needs_a_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_creating_a_token_shows_the_key_exactly_once(self):
        response = self.client.post(self.url, {"name": "Claude Desktop"})

        self.assertEqual(response.status_code, 200)
        key = response.context["issued_key"]
        self.assertTrue(key.startswith(MCP_TOKEN_PREFIX))
        self.assertContains(response, key)

        # Zweiter Aufruf derselben Seite: der Klartext ist weg und bleibt weg.
        again = self.client.get(self.url)
        self.assertEqual(again.context["issued_key"], "")
        self.assertNotContains(again, key)

    def test_the_page_shows_an_example_configuration(self):
        """Mit der Adresse des MCP-Dienstes, nicht der der Web-App."""
        with override_settings(MCP_PUBLIC_URL="https://tagebuch.example.com:8001"):
            response = self.client.post(self.url, {"name": "Claude Desktop"})

        self.assertContains(response, "mcp-remote")
        self.assertContains(response, "https://tagebuch.example.com:8001/mcp/sse/")

    def test_write_access_is_only_granted_when_asked_for(self):
        self.client.post(self.url, {"name": "Nur lesen"})
        self.client.post(self.url, {"name": "Auch schreiben", "allow_write": "on"})

        self.assertFalse(MCPToken.objects.get(name="Nur lesen").allow_write)
        self.assertTrue(MCPToken.objects.get(name="Auch schreiben").allow_write)

    def test_an_expiry_in_the_past_is_rejected(self):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()

        response = self.client.post(self.url, {"name": "Gestern", "expires_at": yesterday})

        self.assertFalse(MCPToken.objects.exists())
        self.assertContains(response, "Vergangenheit")

    def test_an_expiry_lasts_until_the_end_of_that_day(self):
        today = timezone.localdate()

        self.client.post(self.url, {"name": "Heute", "expires_at": today.isoformat()})

        token = MCPToken.objects.get()
        self.assertEqual(timezone.localtime(token.expires_at).date(), today)
        self.assertTrue(token.is_usable)

    def test_the_list_shows_only_the_own_tokens(self):
        MCPToken.issue(self.user, "Eigener")
        MCPToken.issue(make_user("hans"), "Fremder")

        response = self.client.get(self.url)

        self.assertContains(response, "Eigener")
        self.assertNotContains(response, "Fremder")

    def test_revoking_keeps_the_token_for_the_log(self):
        token, _ = MCPToken.issue(self.user, "Claude Desktop")

        response = self.client.post(reverse("services:mcp_token_revoke", args=[token.pk]))

        token.refresh_from_db()
        self.assertRedirects(response, self.url)
        self.assertTrue(token.is_revoked)
        self.assertTrue(MCPToken.objects.filter(pk=token.pk).exists())

    def test_a_foreign_token_cannot_be_revoked(self):
        token, _ = MCPToken.issue(make_user("hans"), "Fremder")

        response = self.client.post(reverse("services:mcp_token_revoke", args=[token.pk]))

        token.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertFalse(token.is_revoked)
