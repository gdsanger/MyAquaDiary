import base64
from email import message_from_bytes
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from services.graph import (
    GraphMailService,
    MailResult,
    mail_enabled,
    render_mail,
    reset_token_cache,
    send_template_mail,
)
from services.models import MailConfig, MailLog

CONFIGURED = {
    "tenant_id": "tenant-123",
    "client_id": "client-abc",
    "client_secret": "sehr-geheimes-client-secret",
    "sender_address": "aquarium@example.com",
    "sender_name": "MyAquaDiary",
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("kein JSON")
        return self._payload


class FakeSession:
    """Minimaler Ersatz für ``requests`` — liefert vorbereitete Antworten."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0) if self.responses else FakeResponse(202)
        if isinstance(response, Exception):
            raise response
        return response


def token_response(expires_in=3600):
    return FakeResponse(200, {"access_token": "token-xyz", "expires_in": expires_in})


class MailConfigTests(TestCase):
    def test_is_a_singleton(self):
        config = MailConfig.objects.create(**CONFIGURED)
        self.assertEqual(config.pk, 1)
        config.sender_name = "Anders"
        config.save()
        self.assertEqual(MailConfig.objects.count(), 1)

    def test_secret_is_encrypted_at_rest(self):
        MailConfig.objects.create(**CONFIGURED)
        with connection.cursor() as cursor:
            cursor.execute("SELECT client_secret FROM services_mailconfig WHERE id = 1")
            stored = cursor.fetchone()[0]
        self.assertNotIn("sehr-geheimes-client-secret", stored)
        self.assertEqual(MailConfig.load().client_secret, "sehr-geheimes-client-secret")

    def test_str_does_not_leak_secret(self):
        config = MailConfig.objects.create(**CONFIGURED)
        self.assertNotIn("sehr-geheimes-client-secret", str(config))

    @override_settings(
        GRAPH_MAIL={
            "TENANT_ID": "env-tenant",
            "CLIENT_ID": "env-client",
            "CLIENT_SECRET": "env-secret",
            "SENDER_ADDRESS": "env@example.com",
            "SENDER_NAME": "Env",
            "REPLY_TO": "",
        }
    )
    def test_load_falls_back_to_environment(self):
        config = MailConfig.load()
        self.assertIsNone(config._state.db)
        self.assertEqual(config.tenant_id, "env-tenant")
        self.assertTrue(config.is_configured)

    @override_settings(GRAPH_MAIL={})
    def test_load_without_config_is_not_configured(self):
        self.assertFalse(MailConfig.load().is_configured)
        self.assertFalse(mail_enabled())

    def test_stored_config_wins_over_environment(self):
        MailConfig.objects.create(**CONFIGURED)
        with override_settings(GRAPH_MAIL={"TENANT_ID": "env-tenant"}):
            self.assertEqual(MailConfig.load().tenant_id, "tenant-123")

    def test_inactive_config_is_not_configured(self):
        config = MailConfig.objects.create(**CONFIGURED, is_active=False)
        self.assertFalse(config.is_configured)

    def test_incomplete_config_is_not_configured(self):
        config = MailConfig.objects.create(**{**CONFIGURED, "sender_address": ""})
        self.assertFalse(config.is_configured)


@override_settings(GRAPH_MAIL={})
class GraphMailServiceTests(TestCase):
    def setUp(self):
        reset_token_cache()
        self.addCleanup(reset_token_cache)
        self.config = MailConfig.objects.create(**CONFIGURED)

    def service(self, *responses):
        session = FakeSession(*responses)
        return GraphMailService(self.config, session=session), session

    def test_send_returns_success_and_logs(self):
        service, session = self.service(token_response(), FakeResponse(202))

        result = service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        self.assertTrue(result)
        self.assertEqual(len(session.calls), 2)
        self.assertIn("/users/aquarium%40example.com/sendMail", session.calls[1]["url"])
        log = MailLog.objects.get()
        self.assertEqual(log.status, MailLog.Status.SENT)
        self.assertEqual(log.recipients, "max@example.com")
        self.assertEqual(log.error, "")

    def test_send_uses_client_credentials_flow(self):
        service, session = self.service(token_response(), FakeResponse(202))

        service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        token_call = session.calls[0]
        self.assertIn("tenant-123/oauth2/v2.0/token", token_call["url"])
        self.assertEqual(token_call["data"]["grant_type"], "client_credentials")
        self.assertEqual(token_call["data"]["scope"], "https://graph.microsoft.com/.default")
        self.assertEqual(session.calls[1]["headers"]["Authorization"], "Bearer token-xyz")

    def test_message_carries_html_and_plaintext(self):
        service, session = self.service(token_response(), FakeResponse(202))

        service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        raw = base64.b64decode(session.calls[1]["data"])
        message = message_from_bytes(raw)
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        parts = [part.get_content_type() for part in message.walk() if not part.is_multipart()]
        self.assertEqual(parts, ["text/plain", "text/html"])
        self.assertEqual(message["To"], "max@example.com")
        self.assertIn("aquarium@example.com", message["From"])

    def test_plaintext_is_derived_when_missing(self):
        service, session = self.service(token_response(), FakeResponse(202))

        service.send("max@example.com", "Betreff", "<p>Hallo Welt</p>")

        message = message_from_bytes(base64.b64decode(session.calls[1]["data"]))
        text_part = next(p for p in message.walk() if p.get_content_type() == "text/plain")
        self.assertIn("Hallo Welt", text_part.get_payload(decode=True).decode())

    def test_token_is_cached_between_sends(self):
        service, session = self.service(
            token_response(), FakeResponse(202), FakeResponse(202)
        )

        service.send("max@example.com", "Eins", "<p>1</p>", "1")
        service.send("max@example.com", "Zwei", "<p>2</p>", "2")

        self.assertEqual(len(session.calls), 3)

    def test_expiring_token_is_refetched(self):
        service, session = self.service(
            token_response(expires_in=0), FakeResponse(202), token_response(), FakeResponse(202)
        )

        service.send("max@example.com", "Eins", "<p>1</p>", "1")
        service.send("max@example.com", "Zwei", "<p>2</p>", "2")

        self.assertEqual(len(session.calls), 4)

    def test_saving_config_invalidates_token_cache(self):
        service, session = self.service(token_response(), FakeResponse(202))
        service.send("max@example.com", "Eins", "<p>1</p>", "1")

        self.config.sender_name = "Neu"
        self.config.save()

        service, session = self.service(token_response(), FakeResponse(202))
        service.send("max@example.com", "Zwei", "<p>2</p>", "2")
        self.assertIn("oauth2/v2.0/token", session.calls[0]["url"])

    def test_failed_send_is_logged_without_raising(self):
        service, _ = self.service(
            token_response(),
            FakeResponse(403, {"error": {"code": "ErrorAccessDenied", "message": "kein Zugriff"}}),
        )

        result = service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        self.assertFalse(result)
        self.assertIn("ErrorAccessDenied", result.error)
        log = MailLog.objects.get()
        self.assertEqual(log.status, MailLog.Status.FAILED)
        self.assertIn("403", log.error)

    def test_failed_token_request_is_logged_without_raising(self):
        service, _ = self.service(
            FakeResponse(401, {"error": "invalid_client", "error_description": "AADSTS7000215"})
        )

        result = service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        self.assertFalse(result)
        self.assertIn("AADSTS7000215", result.error)
        self.assertEqual(MailLog.objects.get().status, MailLog.Status.FAILED)

    def test_network_error_is_logged_without_raising(self):
        service, _ = self.service(requests.ConnectionError("kein Netz"))

        result = service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        self.assertFalse(result)
        self.assertEqual(MailLog.objects.get().status, MailLog.Status.FAILED)

    def test_secret_is_redacted_from_error_messages(self):
        service, _ = self.service(FakeResponse(400, {"error_description": "secret sehr-geheimes-client-secret ungültig"}))

        result = service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        self.assertNotIn("sehr-geheimes-client-secret", result.error)
        self.assertIn("***", result.error)
        self.assertNotIn("sehr-geheimes-client-secret", MailLog.objects.get().error)

    def test_send_without_configuration_is_skipped(self):
        self.config.is_active = False
        service, session = self.service()

        result = service.send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")

        self.assertFalse(result)
        self.assertEqual(session.calls, [])
        self.assertEqual(MailLog.objects.get().status, MailLog.Status.SKIPPED)

    def test_send_without_recipient_is_skipped(self):
        service, session = self.service()

        result = service.send("", "Betreff", "<p>Hallo</p>", "Hallo")

        self.assertFalse(result)
        self.assertEqual(session.calls, [])
        self.assertFalse(MailLog.objects.exists())

    def test_recipients_accept_string_and_iterable(self):
        service, session = self.service(token_response(), FakeResponse(202), FakeResponse(202))

        service.send("a@example.com, b@example.com", "Betreff", "<p>x</p>", "x")
        service.send(["c@example.com"], "Betreff", "<p>x</p>", "x")

        first = message_from_bytes(base64.b64decode(session.calls[1]["data"]))
        second = message_from_bytes(base64.b64decode(session.calls[2]["data"]))
        self.assertEqual(first["To"], "a@example.com, b@example.com")
        self.assertEqual(second["To"], "c@example.com")

    def test_subject_newlines_are_stripped(self):
        service, session = self.service(token_response(), FakeResponse(202))

        service.send("max@example.com", "Betreff\nBcc: boese@example.com", "<p>x</p>", "x")

        message = message_from_bytes(base64.b64decode(session.calls[1]["data"]))
        self.assertIsNone(message["Bcc"])
        self.assertNotIn("\n", str(message["Subject"]))


@override_settings(GRAPH_MAIL={})
class MailTemplateTests(TestCase):
    TEMPLATES = [
        ("test_mail", {"sender_address": "aquarium@example.com"}),
        (
            "appointment_reminder",
            {
                "recipient_name": "Max",
                "appointments": [{"title": "Wasserwechsel", "tank_name": "Becken 1"}],
            },
        ),
        (
            "measurement_alert",
            {"recipient_name": "Max", "tank_name": "Becken 1", "parameter": "NO2", "value": "0,8"},
        ),
        ("account_activation", {"recipient_name": "Max", "activation_url": "https://x.invalid/a"}),
        ("password_reset", {"recipient_name": "Max", "reset_url": "https://x.invalid/p"}),
    ]

    def test_every_template_renders_subject_html_and_text(self):
        for name, context in self.TEMPLATES:
            with self.subTest(template=name):
                rendered = render_mail(name, context)
                self.assertTrue(rendered.subject.strip())
                self.assertNotIn("\n", rendered.subject)
                self.assertIn("<table", rendered.html)
                self.assertNotIn("<table", rendered.text)
                self.assertTrue(rendered.text.strip())

    def test_html_uses_table_layout_and_light_background(self):
        html = render_mail("test_mail", {"sender_address": "a@example.com"}).html
        self.assertNotIn("display:flex", html)
        self.assertNotIn("display:grid", html)
        self.assertIn('name="color-scheme" content="light"', html)
        self.assertIn("background-color:#f5fafb", html)

    def test_context_is_escaped(self):
        rendered = render_mail("appointment_reminder", {"appointments": [{"title": "<script>x</script>"}]})
        self.assertNotIn("<script>", rendered.html)

    def test_unknown_template_does_not_raise(self):
        MailConfig.objects.create(**CONFIGURED)
        result = send_template_mail("max@example.com", "gibt-es-nicht")
        self.assertFalse(result)


@override_settings(GRAPH_MAIL={})
class MailConfigAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="geheim-123"
        )
        self.client.force_login(self.user)
        self.testmail_url = reverse("admin:services_mailconfig_testmail")

    def test_secret_is_not_rendered_in_change_form(self):
        MailConfig.objects.create(**CONFIGURED)
        response = self.client.get(reverse("admin:services_mailconfig_change", args=[1]))
        self.assertNotContains(response, "sehr-geheimes-client-secret")

    def test_empty_secret_keeps_stored_value(self):
        MailConfig.objects.create(**CONFIGURED)
        response = self.client.post(
            reverse("admin:services_mailconfig_change", args=[1]),
            {
                "is_active": "on",
                "tenant_id": "tenant-123",
                "client_id": "client-abc",
                "client_secret": "",
                "sender_address": "aquarium@example.com",
                "sender_name": "Neuer Name",
                "reply_to": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        config = MailConfig.load()
        self.assertEqual(config.sender_name, "Neuer Name")
        self.assertEqual(config.client_secret, "sehr-geheimes-client-secret")

    def test_test_mail_button_hidden_without_configuration(self):
        MailConfig.objects.create(**{**CONFIGURED, "client_secret": "", "tenant_id": ""})
        response = self.client.get(reverse("admin:services_mailconfig_change", args=[1]))
        self.assertNotContains(response, self.testmail_url)

    def test_test_mail_button_visible_when_configured(self):
        MailConfig.objects.create(**CONFIGURED)
        response = self.client.get(reverse("admin:services_mailconfig_change", args=[1]))
        self.assertContains(response, self.testmail_url)

    def test_test_mail_view_redirects_without_configuration(self):
        response = self.client.get(self.testmail_url, follow=True)
        self.assertContains(response, "nicht konfiguriert")

    def test_test_mail_form_is_prefilled_with_own_address(self):
        MailConfig.objects.create(**CONFIGURED)
        response = self.client.get(self.testmail_url)
        self.assertContains(response, "admin@example.com")

    def test_test_mail_reports_success(self):
        MailConfig.objects.create(**CONFIGURED)
        with patch("services.admin.GraphMailService") as service_class:
            service_class.return_value.send.return_value = MailResult(True)
            response = self.client.post(
                self.testmail_url, {"recipient": "max@example.com"}, follow=True
            )
        self.assertContains(response, "Testmail an max@example.com versendet.")

    def test_test_mail_reports_failure(self):
        MailConfig.objects.create(**CONFIGURED)
        with patch("services.admin.GraphMailService") as service_class:
            service_class.return_value.send.return_value = MailResult(False, "403 ErrorAccessDenied")
            response = self.client.post(
                self.testmail_url, {"recipient": "max@example.com"}, follow=True
            )
        self.assertContains(response, "403 ErrorAccessDenied")

    def test_test_mail_requires_staff(self):
        self.client.logout()
        response = self.client.get(self.testmail_url)
        self.assertEqual(response.status_code, 302)

    def test_second_config_cannot_be_added(self):
        MailConfig.objects.create(**CONFIGURED)
        response = self.client.get(reverse("admin:services_mailconfig_add"))
        self.assertEqual(response.status_code, 403)
