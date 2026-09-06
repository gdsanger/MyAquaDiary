"""Tests der KI-Seiten.

Der Aufruf an Claude wird auf Ebene der Anwendungsfälle ersetzt — geprüft wird
hier die Seite: was sie zeigt, was sie anlegt, und dass sie ohne API-Key gar
nicht erst existiert.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from services import ai
from services.models import AIConfig, AISuggestion

CONFIGURED = {"api_key": "sk-ant-api03-testschluessel", "model_name": "claude-opus-5"}


def identification(**kwargs):
    defaults = {
        "ok": True,
        "candidates": [
            ai.Candidate(
                scientific_name="Poecilia reticulata",
                common_name="Guppy",
                confidence=0.82,
                reasoning="Fächerschwanz mit farbiger Zeichnung.",
            )
        ],
        "image_notes": "Die Schwanzflosse ist angeschnitten.",
    }
    return ai.Identification(**{**defaults, **kwargs})


class AIViewTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="greta", email="greta@example.com", password="geheim"
        )
        self.client.force_login(self.user)
        AIConfig.objects.create(**CONFIGURED)

    def draft(self, **kwargs):
        return AISuggestion.objects.create(
            user=self.user,
            kind=AISuggestion.Kind.ANIMAL,
            scientific_name="Poecilia reticulata",
            common_name="Guppy",
            **kwargs,
        )


class HiddenWithoutKeyTests(TestCase):
    """Ohne API-Key sind alle KI-Funktionen ausgeblendet, nichts bricht."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="hans", email="hans@example.com", password="geheim"
        )
        self.client.force_login(self.user)

    def test_every_ai_page_is_gone(self):
        for name, args in [
            ("services:ai_identify", []),
            ("services:ai_suggestion_list", []),
            ("services:ai_suggestion_detail", [1]),
        ]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404)

    def test_navigation_does_not_offer_the_ai(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertNotContains(response, reverse("services:ai_identify"))

    def test_the_rest_of_the_application_carries_on(self):
        self.assertEqual(self.client.get(reverse("services:device_list")).status_code, 200)

    def test_a_switched_off_assistant_is_hidden_too(self):
        AIConfig.objects.create(**CONFIGURED, is_enabled=False)
        self.assertEqual(self.client.get(reverse("services:ai_identify")).status_code, 404)


class IdentifyPageTests(AIViewTestCase):
    def test_navigation_offers_the_ai_once_a_key_is_there(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, reverse("services:ai_identify"))

    def test_shows_the_upload_form(self):
        response = self.client.get(reverse("services:ai_identify"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bestimmen lassen")

    def test_shows_candidates_and_marks_them_as_ai(self):
        with patch("services.views.ai.identify", return_value=identification()) as identify:
            response = self.client.post(
                reverse("services:ai_identify"),
                {"kind": AISuggestion.Kind.ANIMAL, "photo": _photo(), "notes": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Poecilia reticulata")
        self.assertContains(response, "82 % sicher")
        self.assertContains(response, "KI-Vorschlag")
        self.assertContains(response, "angeschnitten")
        # Nichts wird gespeichert, solange niemand übernimmt.
        self.assertFalse(AISuggestion.objects.exists())
        self.assertTrue(identify.called)

    def test_reports_a_failure_without_crashing(self):
        failed = ai.Identification(ok=False, error="Anthropic war nicht erreichbar.")
        with patch("services.views.ai.identify", return_value=failed):
            response = self.client.post(
                reverse("services:ai_identify"),
                {"kind": AISuggestion.Kind.PLANT, "photo": _photo(), "notes": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anthropic war nicht erreichbar.")

    def test_shows_the_budget(self):
        response = self.client.get(reverse("services:ai_identify"))
        self.assertContains(response, "Token-Verbrauch")


class SuggestionFlowTests(AIViewTestCase):
    def test_taking_over_a_candidate_creates_an_unverified_draft(self):
        response = self.client.post(
            reverse("services:ai_suggestion_create"),
            {
                "kind": AISuggestion.Kind.ANIMAL,
                "scientific_name": "Poecilia reticulata",
                "common_name": "Guppy",
                "confidence": "0.82",
                "reasoning": "Fächerschwanz.",
            },
        )

        suggestion = AISuggestion.objects.get()
        self.assertRedirects(
            response, reverse("services:ai_suggestion_detail", args=[suggestion.pk])
        )
        self.assertFalse(suggestion.verified)
        self.assertEqual(suggestion.user, self.user)

    def test_a_nameless_candidate_is_refused(self):
        response = self.client.post(
            reverse("services:ai_suggestion_create"),
            {"kind": AISuggestion.Kind.ANIMAL, "scientific_name": "", "common_name": ""},
        )
        self.assertRedirects(response, reverse("services:ai_identify"))
        self.assertFalse(AISuggestion.objects.exists())

    def test_detail_shows_the_draft_and_both_decisions(self):
        suggestion = self.draft(payload={"min_tank_liters": 54})
        response = self.client.get(
            reverse("services:ai_suggestion_detail", args=[suggestion.pk])
        )

        self.assertContains(response, "Mindestvolumen (l)")
        self.assertContains(response, "Geprüft und bestätigen")
        self.assertContains(response, "Verwerfen")
        self.assertContains(response, "KI-Vorschlag")

    def test_profile_draft_is_requested_on_demand_only(self):
        suggestion = self.draft()
        with patch("services.views.ai.draft_profile") as draft_profile:
            draft_profile.return_value = ai.Answer(ok=True, data={"min_tank_liters": 54})
            response = self.client.post(
                reverse("services:ai_suggestion_profile", args=[suggestion.pk])
            )

        self.assertRedirects(
            response, reverse("services:ai_suggestion_detail", args=[suggestion.pk])
        )
        self.assertTrue(draft_profile.called)

    def test_a_profile_is_never_drafted_by_simply_visiting_the_page(self):
        suggestion = self.draft()
        with patch("services.views.ai.draft_profile") as draft_profile:
            self.client.get(reverse("services:ai_suggestion_detail", args=[suggestion.pk]))
        self.assertFalse(draft_profile.called)

    def test_confirming_marks_the_suggestion_verified(self):
        suggestion = self.draft(payload={"min_tank_liters": 54})
        response = self.client.post(
            reverse("services:ai_suggestion_decide", args=[suggestion.pk, "bestaetigen"])
        )

        suggestion.refresh_from_db()
        self.assertRedirects(
            response, reverse("services:ai_suggestion_detail", args=[suggestion.pk])
        )
        self.assertTrue(suggestion.verified)

    def test_rejecting_keeps_it_out_of_the_catalog(self):
        suggestion = self.draft()
        self.client.post(
            reverse("services:ai_suggestion_decide", args=[suggestion.pk, "verwerfen"])
        )

        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, AISuggestion.Status.REJECTED)
        self.assertFalse(suggestion.verified)

    def test_a_decision_cannot_be_taken_twice(self):
        suggestion = self.draft(status=AISuggestion.Status.VERIFIED)
        response = self.client.post(
            reverse("services:ai_suggestion_decide", args=[suggestion.pk, "bestaetigen"])
        )
        self.assertEqual(response.status_code, 404)

    def test_decisions_need_a_post(self):
        suggestion = self.draft()
        response = self.client.get(
            reverse("services:ai_suggestion_decide", args=[suggestion.pk, "bestaetigen"])
        )
        self.assertEqual(response.status_code, 405)

    def test_foreign_suggestions_do_not_exist(self):
        other = get_user_model().objects.create_user(
            username="ines", email="ines@example.com", password="geheim"
        )
        suggestion = AISuggestion.objects.create(user=other, kind=AISuggestion.Kind.PLANT)
        response = self.client.get(
            reverse("services:ai_suggestion_detail", args=[suggestion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_list_separates_open_drafts_from_decided_ones(self):
        self.draft()
        self.draft(status=AISuggestion.Status.REJECTED)
        response = self.client.get(reverse("services:ai_suggestion_list"))

        self.assertEqual(len(response.context["drafts"]), 1)
        self.assertEqual(len(response.context["decided"]), 1)


class LoginRequiredTests(TestCase):
    def test_anonymous_visitors_are_sent_to_the_login(self):
        AIConfig.objects.create(**CONFIGURED)
        response = self.client.get(reverse("services:ai_identify"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


def _photo():
    """Ein winziges JPEG als Upload."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (20, 80, 120)).save(buffer, format="JPEG")
    return SimpleUploadedFile("fisch.jpg", buffer.getvalue(), content_type="image/jpeg")
