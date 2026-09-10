"""Tests der KI-Assistenz.

Kein Test spricht mit Anthropic: :class:`FakeAnthropic` liefert vorbereitete
Antworten in derselben Form wie das SDK (Objekte mit ``content``, ``usage``,
``stop_reason``). Damit lassen sich auch die unangenehmen Fälle prüfen —
abgeschnittene Antworten, kaputtes JSON, erschöpftes Budget.
"""

import io
import json
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from catalog.models import AnimalSpecies, PlantSpecies
from core.enums import Difficulty
from services import ai
from services.ai import budget, catalog, images, pricing, prompts, schemas
from services.ai.client import AIService
from services.ai.exceptions import AIError
from services.models import AIConfig, AISuggestion, AIUsageLog

API_KEY = "sk-ant-api03-sehr-geheimer-testschluessel"
CONFIGURED = {"api_key": API_KEY, "model_name": "claude-opus-5"}


# --------------------------------------------------------------------------
# Testdoppel
# --------------------------------------------------------------------------


def message(text="", *, input_tokens=100, output_tokens=50, stop_reason="end_turn", **usage):
    """Eine Antwort, wie das SDK sie liefert."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)] if text else [],
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens, **usage
        ),
        stop_reason=stop_reason,
    )


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0) if self.responses else message("ok")
        if isinstance(response, Exception):
            raise response
        return response


class FakeAnthropic:
    """Minimaler Ersatz für ``anthropic.Anthropic``."""

    def __init__(self, *responses):
        self.messages = FakeMessages(responses)

    @property
    def calls(self):
        return self.messages.calls


def service_with(*responses, config=None, user=None) -> AIService:
    return AIService(config or AIConfig(**CONFIGURED), client=FakeAnthropic(*responses))


def photo(size=(2000, 1500), color=(30, 90, 140)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return SimpleUploadedFile("becken.jpg", buffer.getvalue(), content_type="image/jpeg")


def identification_payload():
    return json.dumps(
        {
            "candidates": [
                {
                    "scientific_name": "Poecilia reticulata",
                    "common_name": "Guppy",
                    "confidence": 0.82,
                    "reasoning": "Fächerschwanz mit farbiger Zeichnung.",
                    "distinguishing_features": "Männchen deutlich bunter.",
                },
                {
                    "scientific_name": "Poecilia wingei",
                    "common_name": "Endler-Guppy",
                    "confidence": 0.4,
                    "reasoning": "Ähnliche Zeichnung, kleinerer Körper.",
                    "distinguishing_features": "Bleibt deutlich kleiner.",
                },
            ],
            "image_notes": "Die Schwanzflosse ist angeschnitten.",
        }
    )


# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------


class AIConfigTests(TestCase):
    def test_is_a_singleton(self):
        config = AIConfig.objects.create(**CONFIGURED)
        self.assertEqual(config.pk, 1)
        config.model_name = "claude-sonnet-5"
        config.save()
        self.assertEqual(AIConfig.objects.count(), 1)

    def test_api_key_is_encrypted_at_rest(self):
        AIConfig.objects.create(**CONFIGURED)
        with connection.cursor() as cursor:
            cursor.execute("SELECT api_key FROM services_aiconfig WHERE id = 1")
            stored = cursor.fetchone()[0]
        self.assertNotIn(API_KEY, stored)
        self.assertEqual(AIConfig.load().api_key, API_KEY)

    def test_str_does_not_leak_the_key(self):
        config = AIConfig.objects.create(**CONFIGURED)
        self.assertNotIn(API_KEY, str(config))

    @override_settings(ANTHROPIC={"API_KEY": "sk-env", "MODEL": "claude-sonnet-5"})
    def test_falls_back_to_environment(self):
        config = AIConfig.load()
        self.assertEqual(config.api_key, "sk-env")
        self.assertEqual(config.model_name, "claude-sonnet-5")
        self.assertTrue(config.is_configured)

    def test_is_not_configured_without_key_or_when_switched_off(self):
        self.assertFalse(AIConfig(model_name="claude-opus-5").is_configured)
        self.assertFalse(AIConfig(**CONFIGURED, is_enabled=False).is_configured)

    @override_settings(ANTHROPIC={})
    def test_ai_enabled_is_false_without_configuration(self):
        self.assertFalse(ai.ai_enabled())


# --------------------------------------------------------------------------
# Preise
# --------------------------------------------------------------------------


class PricingTests(TestCase):
    def test_computes_cost_from_tokens(self):
        # 1000 Eingabe = 0,005 USD, 500 Ausgabe = 0,0125 USD
        self.assertEqual(pricing.cost_usd("claude-opus-5", 1000, 500), Decimal("0.0175"))

    def test_cache_tokens_are_weighted_differently(self):
        with_cache = pricing.cost_usd("claude-opus-5", 0, 0, cache_write=1000, cache_read=1000)
        self.assertEqual(with_cache, Decimal("0.0068"))

    def test_unknown_model_costs_nothing_instead_of_guessing(self):
        self.assertEqual(pricing.cost_usd("claude-von-morgen", 1000, 1000), Decimal("0"))

    def test_knows_which_models_can_be_forced_into_json(self):
        self.assertTrue(pricing.supports_structured_output("claude-opus-5"))
        self.assertFalse(pricing.supports_structured_output("claude-sonnet-4-6"))


# --------------------------------------------------------------------------
# Bilder
# --------------------------------------------------------------------------


class ImageTests(TestCase):
    def test_scales_down_to_the_configured_edge(self):
        prepared = images.prepare_image(photo((2000, 1000)), edge=512)
        self.assertEqual((prepared.width, prepared.height), (512, 256))
        self.assertEqual(prepared.media_type, "image/jpeg")

    def test_small_images_are_not_blown_up(self):
        prepared = images.prepare_image(photo((300, 200)), edge=1024)
        self.assertEqual((prepared.width, prepared.height), (300, 200))

    def test_scaling_saves_a_lot_of_bytes(self):
        original = photo((2400, 1800))
        prepared = images.prepare_image(original, edge=1024)
        self.assertLess(prepared.size_bytes, original.size)

    def test_content_block_carries_base64_jpeg(self):
        block = images.prepare_image(photo((100, 100))).as_content_block()
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["media_type"], "image/jpeg")
        self.assertTrue(block["source"]["data"])

    def test_rejects_something_that_is_not_an_image(self):
        upload = SimpleUploadedFile("notizen.txt", b"kein bild", content_type="text/plain")
        with self.assertRaises(AIError):
            images.prepare_image(upload)

    def test_rejects_oversized_uploads_before_opening_them(self):
        upload = SimpleUploadedFile("riesig.jpg", b"x" * 10, content_type="image/jpeg")
        upload.size = images.MAX_UPLOAD_BYTES + 1
        with self.assertRaises(AIError):
            images.prepare_image(upload)


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


class BudgetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="anke", email="anke@example.com", password="geheim"
        )
        self.config = AIConfig(**CONFIGURED, monthly_token_budget=1000, per_user_daily_limit=100)

    def _usage(self, prompt=0, completion=0, user=None):
        return AIUsageLog.objects.create(
            user=user, action=AIUsageLog.Action.TEST,
            prompt_tokens=prompt, completion_tokens=completion,
        )

    def test_counts_prompt_and_completion_tokens(self):
        self._usage(prompt=40, completion=10, user=self.user)
        self.assertEqual(budget.used_this_month(), 50)
        self.assertEqual(budget.used_today(self.user), 50)

    def test_other_users_do_not_count_against_the_daily_limit(self):
        other = get_user_model().objects.create_user(
            username="bert", email="bert@example.com", password="geheim"
        )
        self._usage(prompt=90, user=other)
        self.assertEqual(budget.used_today(self.user), 0)
        self.assertEqual(budget.used_this_month(), 90)

    def test_last_month_does_not_count(self):
        entry = self._usage(prompt=900, user=self.user)
        last_month = timezone.localtime().replace(day=1) - timezone.timedelta(days=1)
        AIUsageLog.objects.filter(pk=entry.pk).update(created_at=last_month)
        self.assertEqual(budget.used_this_month(), 0)

    def test_daily_limit_blocks_with_a_readable_message(self):
        self._usage(prompt=100, user=self.user)
        with self.assertRaises(ai.AIBudgetExceeded) as caught:
            budget.check(self.user, self.config)
        self.assertIn("Tageslimit", str(caught.exception))

    def test_monthly_budget_blocks_everyone(self):
        self._usage(prompt=1000)
        with self.assertRaises(ai.AIBudgetExceeded) as caught:
            budget.check(self.user, self.config)
        self.assertIn("Monat", str(caught.exception))

    def test_zero_means_no_limit(self):
        self._usage(prompt=10_000, user=self.user)
        unlimited = AIConfig(**CONFIGURED, monthly_token_budget=0, per_user_daily_limit=0)
        self.assertFalse(budget.status(self.user, unlimited).exceeded)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class AIServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="carla", email="carla@example.com", password="geheim"
        )

    def test_asks_and_logs_tokens_and_cost(self):
        service = service_with(message("Alles gut.", input_tokens=1000, output_tokens=500))
        result = service.ask(
            AIUsageLog.Action.TEST, system="System", prompt="Frage", user=self.user
        )

        self.assertTrue(result)
        self.assertEqual(result.text, "Alles gut.")
        entry = AIUsageLog.objects.get()
        self.assertEqual(entry.prompt_tokens, 1000)
        self.assertEqual(entry.completion_tokens, 500)
        self.assertEqual(entry.total_cost_usd, Decimal("0.0175"))
        self.assertEqual(entry.model_name, "claude-opus-5")
        self.assertEqual(entry.user, self.user)
        self.assertTrue(entry.success)

    def test_sends_effort_and_schema_when_the_model_can_take_it(self):
        service = service_with(message('{"verdict": "unbedenklich"}'))
        service.ask(
            AIUsageLog.Action.STOCKING,
            system="System",
            prompt="Frage",
            schema=schemas.STOCKING_SCHEMA,
            effort="medium",
        )
        sent = service.client().calls[0]
        self.assertEqual(sent["output_config"]["effort"], "medium")
        self.assertEqual(sent["output_config"]["format"]["type"], "json_schema")

    def test_omits_the_schema_on_a_model_that_cannot_do_it(self):
        config = AIConfig(api_key=API_KEY, model_name="claude-sonnet-4-6")
        service = service_with(message('{"verdict": "unbedenklich"}'), config=config)
        service.ask(
            AIUsageLog.Action.STOCKING, system="S", prompt="F", schema=schemas.STOCKING_SCHEMA
        )
        self.assertNotIn("format", service.client().calls[0]["output_config"])

    def test_reads_json_out_of_a_code_fence(self):
        service = service_with(message('Hier:\n```json\n{"verdict": "bedenklich"}\n```'))
        result = service.ask(
            AIUsageLog.Action.STOCKING, system="S", prompt="F", schema=schemas.STOCKING_SCHEMA
        )
        self.assertEqual(result.data["verdict"], "bedenklich")

    def test_image_goes_in_front_of_the_question(self):
        service = service_with(message("ok"))
        prepared = images.prepare_image(photo((80, 80)))
        service.ask(AIUsageLog.Action.TEST, system="S", prompt="Was ist das?", images=[prepared])
        content = service.client().calls[0]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[1]["type"], "text")

    def test_cache_tokens_count_towards_the_budget(self):
        service = service_with(
            message("ok", input_tokens=10, output_tokens=5,
                    cache_creation_input_tokens=100, cache_read_input_tokens=200)
        )
        service.ask(AIUsageLog.Action.TEST, system="S", prompt="F")
        self.assertEqual(AIUsageLog.objects.get().prompt_tokens, 310)

    def test_unconfigured_service_explains_itself_and_calls_nothing(self):
        service = AIService(AIConfig(), client=FakeAnthropic(message("darf nicht passieren")))
        result = service.ask(AIUsageLog.Action.TEST, system="S", prompt="F")

        self.assertFalse(result)
        self.assertIn("nicht eingerichtet", result.error)
        self.assertEqual(service.client().calls, [])

    def test_exhausted_budget_blocks_before_the_call(self):
        AIUsageLog.objects.create(
            user=self.user, action=AIUsageLog.Action.TEST, prompt_tokens=500
        )
        config = AIConfig(**CONFIGURED, per_user_daily_limit=100)
        service = service_with(message("darf nicht passieren"), config=config)
        result = service.ask(AIUsageLog.Action.TEST, system="S", prompt="F", user=self.user)

        self.assertFalse(result)
        self.assertIn("Tageslimit", result.error)
        self.assertEqual(service.client().calls, [])
        # Auch der abgelehnte Versuch steht im Protokoll.
        self.assertEqual(AIUsageLog.objects.filter(success=False).count(), 1)

    def test_api_error_is_reported_without_the_key(self):
        class AuthenticationError(Exception):
            pass

        service = service_with(AuthenticationError(f"key {API_KEY} rejected"))
        result = service.ask(AIUsageLog.Action.TEST, system="S", prompt="F")

        self.assertFalse(result)
        self.assertNotIn(API_KEY, result.error)
        self.assertNotIn(API_KEY, AIUsageLog.objects.get().error_message)

    def test_key_is_stripped_from_arbitrary_error_text(self):
        service = service_with(RuntimeError(f"boom {API_KEY}"))
        result = service.ask(AIUsageLog.Action.TEST, system="S", prompt="F")
        self.assertNotIn(API_KEY, result.error)

    def test_truncated_answer_is_a_failure_not_half_a_result(self):
        service = service_with(message("Der Steckbrief beg", stop_reason="max_tokens"))
        result = service.ask(AIUsageLog.Action.TEST, system="S", prompt="F")
        self.assertFalse(result)
        self.assertIn("abgeschnitten", result.error)

    def test_refusal_is_reported_as_such(self):
        service = service_with(message("", stop_reason="refusal"))
        result = service.ask(AIUsageLog.Action.TEST, system="S", prompt="F")
        self.assertFalse(result)
        self.assertIn("abgelehnt", result.error)

    def test_unparsable_json_does_not_reach_the_caller(self):
        service = service_with(message("Das ist kein JSON."))
        result = service.ask(
            AIUsageLog.Action.TEST, system="S", prompt="F", schema=schemas.STOCKING_SCHEMA
        )
        self.assertFalse(result)
        self.assertIn("Format", result.error)


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------


class PromptTests(TestCase):
    def test_guardrails_are_part_of_every_system_prompt(self):
        for system in (prompts.IDENTIFY_SYSTEM, prompts.PROFILE_SYSTEM,
                       prompts.MEASUREMENT_SYSTEM, prompts.STOCKING_SYSTEM,
                       prompts.REPORT_SYSTEM):
            self.assertIn("Verantwortung", system)

    def test_measurement_prompt_forbids_a_diagnosis(self):
        self.assertIn("keine Diagnose", prompts.MEASUREMENT_SYSTEM)

    def test_tank_facts_skip_what_is_unknown(self):
        text = prompts.TankFacts(name="Becken 1").as_text()
        self.assertIn("Becken 1", text)
        self.assertNotIn("Volumen", text)

    def test_measurements_without_values_are_named_as_such(self):
        self.assertIn("keine Messwerte", prompts.measurements_table([]))

    def test_measurement_rows_drop_empty_values(self):
        table = prompts.measurements_table([{"measured_at": "01.09.", "ph": 7.2, "no2": None}])
        self.assertIn("ph 7.2", table)
        self.assertNotIn("no2", table)

    def test_stocking_prompt_separates_planned_from_present(self):
        text = prompts.stocking_prompt(
            prompts.TankFacts(name="Becken", volume_liters=54),
            [
                prompts.StockItem("Guppy", count=6),
                prompts.StockItem("Antennenwels", count=1, planned=True),
            ],
        )
        self.assertIn("Vorhandener Besatz", text)
        self.assertIn("Geplant dazu", text)
        self.assertIn("6× Guppy", text)


# --------------------------------------------------------------------------
# Anwendungsfälle
# --------------------------------------------------------------------------


class IdentifyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="dora", email="dora@example.com", password="geheim"
        )

    def test_returns_candidates_sorted_as_delivered(self):
        service = service_with(message(identification_payload()))
        found = ai.identify(photo(), AISuggestion.Kind.ANIMAL, user=self.user, service=service)

        self.assertTrue(found)
        self.assertEqual(len(found.candidates), 2)
        first = found.candidates[0]
        self.assertEqual(first.scientific_name, "Poecilia reticulata")
        self.assertEqual(first.confidence_percent, 82)
        self.assertIn("angeschnitten", found.image_notes)

    def test_percentages_are_normalised_and_clamped(self):
        payload = json.dumps(
            {"candidates": [{"scientific_name": "Danio rerio", "confidence": 95}],
             "image_notes": ""}
        )
        found = ai.identify(
            photo(), AISuggestion.Kind.ANIMAL, service=service_with(message(payload))
        )
        self.assertEqual(found.candidates[0].confidence_percent, 95)

    def test_nameless_answer_counts_as_not_identified(self):
        payload = json.dumps({"candidates": [{"scientific_name": "", "common_name": ""}],
                              "image_notes": "Zu unscharf."})
        found = ai.identify(
            photo(), AISuggestion.Kind.PLANT, service=service_with(message(payload))
        )
        self.assertFalse(found)
        self.assertIn("Zu unscharf", found.image_notes)

    def test_a_broken_upload_never_reaches_the_api(self):
        service = service_with(message(identification_payload()))
        upload = SimpleUploadedFile("x.jpg", b"kein bild", content_type="image/jpeg")
        found = ai.identify(upload, AISuggestion.Kind.ANIMAL, service=service)

        self.assertFalse(found)
        self.assertEqual(service.client().calls, [])

    def test_notes_of_the_keeper_go_into_the_prompt(self):
        service = service_with(message(identification_payload()))
        ai.identify(
            photo(), AISuggestion.Kind.ANIMAL, service=service, notes="Etwa 3 cm, Schwarm"
        )
        self.assertIn("Etwa 3 cm, Schwarm", service.client().calls[0]["messages"][0]["content"][1]["text"])

    def test_identification_uses_low_effort(self):
        service = service_with(message(identification_payload()))
        ai.identify(photo(), AISuggestion.Kind.ANIMAL, service=service)
        self.assertEqual(service.client().calls[0]["output_config"]["effort"], "low")

    def test_a_species_that_is_already_in_the_catalog_is_recognised(self):
        """Der Fall, um den es geht: eine erfasste Art gilt nicht als unbekannt."""
        guppy = AnimalSpecies.objects.create(
            scientific_name="Poecilia reticulata", common_name="Guppy", slug="poecilia-reticulata"
        )
        service = service_with(message(identification_payload()))
        found = ai.identify(photo(), AISuggestion.Kind.ANIMAL, user=self.user, service=service)

        first = found.candidates[0]
        self.assertTrue(first.known_in_catalog)
        self.assertEqual([match.pk for match in first.matches], [guppy.pk])
        self.assertEqual(first.matches[0].status_label, "genauer Treffer")


class SuggestionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="emil", email="emil@example.com", password="geheim"
        )
        self.candidate = ai.Candidate(
            scientific_name="Poecilia reticulata", common_name="Guppy", confidence=0.82
        )

    def test_a_new_suggestion_is_a_draft(self):
        suggestion = ai.save_suggestion(self.user, AISuggestion.Kind.ANIMAL, self.candidate)
        self.assertFalse(suggestion.verified)
        self.assertTrue(suggestion.is_draft)
        self.assertEqual(suggestion.status, AISuggestion.Status.DRAFT)
        self.assertEqual(suggestion.payload, {})

    def test_profile_draft_is_attached_but_stays_unverified(self):
        suggestion = ai.save_suggestion(self.user, AISuggestion.Kind.ANIMAL, self.candidate)
        payload = json.dumps(
            {"scientific_name": "Poecilia reticulata", "min_tank_liters": 54,
             "min_group_size": 6, "family": "", "warning": None}
        )
        answer = ai.draft_profile(suggestion, service=service_with(message(payload)))

        self.assertTrue(answer)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.payload["min_tank_liters"], 54)
        # Leere Felder sind keine Angabe und fliegen raus.
        self.assertNotIn("family", suggestion.payload)
        self.assertNotIn("warning", suggestion.payload)
        self.assertFalse(suggestion.verified)

    def test_failed_profile_leaves_the_draft_untouched(self):
        suggestion = ai.save_suggestion(self.user, AISuggestion.Kind.ANIMAL, self.candidate)
        answer = ai.draft_profile(suggestion, service=service_with(RuntimeError("weg")))

        self.assertFalse(answer)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.payload, {})

    def test_confirming_marks_it_verified(self):
        suggestion = ai.save_suggestion(self.user, AISuggestion.Kind.ANIMAL, self.candidate)
        ai.confirm_suggestion(suggestion, user=self.user)

        suggestion.refresh_from_db()
        self.assertTrue(suggestion.verified)
        self.assertIsNotNone(suggestion.decided_at)

    def test_rejecting_keeps_it_as_a_record(self):
        suggestion = ai.save_suggestion(self.user, AISuggestion.Kind.ANIMAL, self.candidate)
        ai.reject_suggestion(suggestion)

        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, AISuggestion.Status.REJECTED)
        self.assertFalse(suggestion.verified)
        self.assertTrue(AISuggestion.objects.filter(pk=suggestion.pk).exists())


class CatalogMatchTests(TestCase):
    """Abgleich mit dem Katalog — beide Richtungen, gegen die echten Modelle.

    Die Tests arbeiten bewusst ohne Testdoppel: der Abgleich hat lange gegen
    Modellnamen gesucht, die es nicht gibt, und genau das hat ein Doppel des
    Katalogs nicht auffallen lassen.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="frida", email="frida@example.com", password="geheim"
        )
        cls.guppy = AnimalSpecies.objects.create(
            scientific_name="Poecilia reticulata",
            common_name="Guppy",
            slug="poecilia-reticulata",
        )

    def suggestion(self, kind, payload, **kwargs):
        candidate = ai.Candidate(
            scientific_name=kwargs.pop("scientific_name", payload.get("scientific_name", "")),
            common_name=kwargs.pop("common_name", ""),
        )
        return ai.save_suggestion(self.user, kind, candidate, payload)

    # -- Nachschlagen ------------------------------------------------------

    def test_the_catalog_models_are_the_ones_that_exist(self):
        self.assertIs(catalog.model_for("animal"), AnimalSpecies)
        self.assertIs(catalog.model_for("plant"), PlantSpecies)

    def test_matches_the_scientific_name_exactly(self):
        matches = catalog.find_matches("animal", "poecilia RETICULATA")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].pk, self.guppy.pk)
        self.assertEqual(matches[0].label, "Guppy")
        self.assertEqual(matches[0].url, self.guppy.get_absolute_url())
        self.assertTrue(matches[0].exact)
        self.assertEqual(matches[0].status_label, "genauer Treffer")

    def test_a_similar_name_is_offered_as_an_inexact_match(self):
        matches = catalog.find_matches("animal", "Poecilia")

        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0].exact)
        self.assertEqual(matches[0].status_label, "ähnlicher Name")

    def test_the_german_name_is_the_fallback(self):
        matches = catalog.find_matches("animal", "Lebistes reticulatus", "Guppy")

        self.assertEqual([match.pk for match in matches], [self.guppy.pk])
        self.assertFalse(matches[0].exact)

    def test_a_cultivated_form_is_a_match_of_its_own(self):
        blue = AnimalSpecies.objects.create(
            scientific_name="Poecilia reticulata",
            common_name="Guppy",
            variant="Blue Grass",
            is_cultivated_form=True,
            slug="poecilia-reticulata-blue-grass",
        )
        matches = catalog.find_matches("animal", "Poecilia reticulata")

        self.assertEqual({match.pk for match in matches}, {self.guppy.pk, blue.pk})
        self.assertIn("Guppy 'Blue Grass'", [match.label for match in matches])

    def test_an_unknown_species_has_no_matches(self):
        self.assertEqual(catalog.find_matches("plant", "Anubias barteri"), [])

    def test_a_nameless_candidate_asks_nothing(self):
        self.assertEqual(catalog.find_matches("animal", "", ""), [])

    # -- Übernehmen --------------------------------------------------------

    def test_the_variant_field_is_the_one_both_catalog_models_have(self):
        """Die Übernahme sucht über Name *und* Sorte — der Feldname muss stimmen.

        Solange ``publish`` gegen einen Feldnamen sucht, den es im Katalog
        nicht gibt, fiele der Abgleich lautlos auf die falsche Bedingung
        zurück und legte Dubletten an.
        """
        for model in (AnimalSpecies, PlantSpecies):
            with self.subTest(model=model.__name__):
                self.assertTrue(model._meta.get_field(catalog.VARIANT_FIELD))

    def test_the_profile_schemas_ask_for_the_variant(self):
        for schema in (schemas.ANIMAL_PROFILE_SCHEMA, schemas.PLANT_PROFILE_SCHEMA):
            self.assertIn("variant", schema["properties"])
            self.assertIn("variant", schema["required"])
            self.assertIn("is_cultivated_form", schema["properties"])

    def test_publishing_an_animal_creates_the_entry(self):
        suggestion = self.suggestion(
            AISuggestion.Kind.ANIMAL,
            {
                "scientific_name": "Mikrogeophagus ramirezi",
                "common_name": "Schmetterlingsbuntbarsch",
                "variant": "Electric Blue",
                "is_cultivated_form": True,
                "group": "fisch",
                "difficulty": "hard",
                "temp_min_c": 24.0,
                "temp_max_c": 28.0,
                "ph_min": 6.0,
                "ph_max": 7.5,
                "gh_min": 2,
                "gh_max": 12,
                "size_max_cm": 5.0,
                "min_tank_liters": 80,
                "min_group_size": 2,
                "description": "Ruhiger Buntbarsch für weiches Wasser.",
                # Felder ohne Entsprechung im Katalog — sie bleiben am Entwurf.
                "family": "Cichlidae",
                "uncertainties": "Zur Herkunft der Zuchtform ist wenig belegt.",
            },
        )

        reference = catalog.publish(suggestion)

        entry = AnimalSpecies.objects.get(scientific_name="Mikrogeophagus ramirezi")
        self.assertEqual(reference, f"catalog.AnimalSpecies:{entry.pk}")
        self.assertEqual(entry.slug, "mikrogeophagus-ramirezi-electric-blue")
        self.assertEqual(entry.variant, "Electric Blue")
        self.assertTrue(entry.is_cultivated_form)
        self.assertEqual(entry.category, AnimalSpecies.Category.FISH)
        self.assertEqual(entry.difficulty, "hard")
        self.assertEqual(entry.temperature_min, Decimal("24.0"))
        self.assertEqual(entry.temperature_max, Decimal("28.0"))
        self.assertEqual(entry.ph_min, Decimal("6.0"))
        self.assertEqual(entry.gh_max, 12)
        self.assertEqual(entry.adult_size_cm, Decimal("5.0"))
        self.assertEqual(entry.min_tank_volume_l, 80)
        self.assertEqual(entry.min_group_size, 2)
        self.assertIn("weiches Wasser", entry.description)

    def test_publishing_a_plant_translates_its_own_fields(self):
        suggestion = self.suggestion(
            AISuggestion.Kind.PLANT,
            {
                "scientific_name": "Anubias barteri",
                "common_name": "Speerblatt",
                "variant": "Nana",
                "is_cultivated_form": False,
                "placement": "epiphyte",
                "growth_rate": "slow",
                "light_demand": "low",
                "co2_demand": "low",
                "height_max_cm": 15,
                "temp_min_c": 22.0,
                "growth_form": "rosette",
                "propagation": "Teilung des Rhizoms.",
            },
        )

        catalog.publish(suggestion)

        entry = PlantSpecies.objects.get(scientific_name="Anubias barteri")
        self.assertEqual(entry.placement, PlantSpecies.Placement.EPIPHYTE)
        self.assertEqual(entry.growth_rate, PlantSpecies.GrowthRate.SLOW)
        self.assertEqual(entry.light_demand, PlantSpecies.LightDemand.LOW)
        self.assertFalse(entry.co2_required)
        self.assertEqual(entry.max_height_cm, 15)
        self.assertEqual(entry.temperature_min, Decimal("22.0"))
        self.assertFalse(entry.is_cultivated_form)

    def test_the_variety_is_written_as_the_form_would_write_it(self):
        """Ohne dieselbe Schreibweise stünde 'Nana' zweimal im Katalog."""
        suggestion = self.suggestion(
            AISuggestion.Kind.PLANT,
            {"scientific_name": "Anubias barteri", "variant": "'Nana'"},
        )
        catalog.publish(suggestion)

        entry = PlantSpecies.objects.get(scientific_name="Anubias barteri")
        self.assertEqual(entry.variant, "Nana")
        self.assertEqual(entry.display_name, "Anubias barteri 'Nana'")

    def test_the_husbandry_traits_of_a_draft_land_in_the_catalog(self):
        """Der Entwurf fragt Zone, Ernährung und Sozialverhalten schon lange ab.

        Bis #1250 hatte der Katalog dafür kein Feld und die Angaben blieben am
        Vorschlag liegen — jetzt wandern sie mit, in den Werten der
        Katalog-Auswahllisten.
        """
        suggestion = self.suggestion(
            AISuggestion.Kind.ANIMAL,
            {
                "scientific_name": "Otocinclus affinis",
                "origin": "Südostbrasilien, Küstenflüsse",
                "social_behavior": "gruppe",
                "zone": "boden",
                "diet": "aufwuchs",
            },
        )
        catalog.publish(suggestion)

        entry = AnimalSpecies.objects.get(scientific_name="Otocinclus affinis")
        self.assertEqual(entry.origin_detail, "Südostbrasilien, Küstenflüsse")
        self.assertEqual(entry.social_structure, AnimalSpecies.Social.GROUP)
        self.assertEqual(entry.zone, AnimalSpecies.Zone.BOTTOM)
        # „Aufwuchs" ist eine pflanzliche Ernährung; die Feinheit gehört in die
        # Beschreibung und nicht in die Auswahlliste.
        self.assertEqual(entry.diet, AnimalSpecies.Diet.HERBIVORE)
        # Das Verbreitungsgebiet bleibt dem Menschen: aus einem Freitext ein
        # filterbares Gebiet zu raten hieße, eine Angabe zu erfinden.
        self.assertEqual(entry.origin_region, "")

    def test_a_plant_draft_keeps_its_origin_as_well(self):
        suggestion = self.suggestion(
            AISuggestion.Kind.PLANT,
            {"scientific_name": "Echinodorus bleheri", "origin": "Südamerika"},
        )
        catalog.publish(suggestion)

        entry = PlantSpecies.objects.get(scientific_name="Echinodorus bleheri")
        self.assertEqual(entry.origin_detail, "Südamerika")

    def test_a_high_co2_demand_becomes_a_requirement(self):
        suggestion = self.suggestion(
            AISuggestion.Kind.PLANT,
            {"scientific_name": "Rotala wallichii", "co2_demand": "high"},
        )
        catalog.publish(suggestion)

        self.assertTrue(PlantSpecies.objects.get(scientific_name="Rotala wallichii").co2_required)

    def test_a_value_the_catalog_does_not_know_is_left_out(self):
        """Lieber ein Steckbrief ohne die Angabe als einer, der nicht speichert."""
        suggestion = self.suggestion(
            AISuggestion.Kind.ANIMAL,
            {
                "scientific_name": "Danio rerio",
                "difficulty": "kinderleicht",
                "group": "fabelwesen",
                "size_max_cm": 12345.6,
            },
        )

        with self.assertLogs("services.ai.catalog", level="WARNING") as logged:
            catalog.publish(suggestion)

        entry = AnimalSpecies.objects.get(scientific_name="Danio rerio")
        self.assertEqual(entry.difficulty, Difficulty.EASY)
        self.assertEqual(entry.category, AnimalSpecies.Category.FISH)
        self.assertIsNone(entry.adult_size_cm)
        self.assertEqual(len(logged.output), 3)

    def test_an_older_draft_still_finds_its_way(self):
        """``demanding`` stand vor #1242 in der Auswahlliste des Prompts."""
        suggestion = self.suggestion(
            AISuggestion.Kind.ANIMAL,
            {"scientific_name": "Betta splendens", "difficulty": "demanding"},
        )
        catalog.publish(suggestion)

        self.assertEqual(
            AnimalSpecies.objects.get(scientific_name="Betta splendens").difficulty, "hard"
        )

    def test_publishing_twice_points_at_the_same_entry(self):
        payload = {"scientific_name": "Poecilia reticulata", "common_name": "Guppy"}
        first = catalog.publish(self.suggestion(AISuggestion.Kind.ANIMAL, payload))
        second = catalog.publish(self.suggestion(AISuggestion.Kind.ANIMAL, payload))

        self.assertEqual(first, f"catalog.AnimalSpecies:{self.guppy.pk}")
        self.assertEqual(second, first)
        self.assertEqual(AnimalSpecies.objects.filter(scientific_name__iexact=payload["scientific_name"]).count(), 1)

    def test_a_variety_does_not_overwrite_the_wild_form(self):
        reference = catalog.publish(
            self.suggestion(
                AISuggestion.Kind.ANIMAL,
                {
                    "scientific_name": "Poecilia reticulata",
                    "common_name": "Guppy",
                    "variant": "Blue Grass",
                    "is_cultivated_form": True,
                },
            )
        )

        self.assertNotEqual(reference, f"catalog.AnimalSpecies:{self.guppy.pk}")
        self.assertEqual(AnimalSpecies.objects.count(), 2)
        self.assertEqual(
            AnimalSpecies.objects.get(variant="Blue Grass").slug,
            "poecilia-reticulata-blue-grass",
        )

    def test_publishing_without_a_scientific_name_does_nothing(self):
        suggestion = self.suggestion(AISuggestion.Kind.PLANT, {"common_name": "Irgendwas"})

        with self.assertLogs("services.ai.catalog", level="WARNING"):
            self.assertEqual(catalog.publish(suggestion), "")
        self.assertFalse(PlantSpecies.objects.exists())

    def test_the_name_of_the_draft_carries_the_entry(self):
        """Ohne Steckbrief-Entwurf reicht, was die Bestimmung ergeben hat."""
        suggestion = ai.save_suggestion(
            self.user,
            AISuggestion.Kind.PLANT,
            ai.Candidate(scientific_name="Cryptocoryne wendtii", common_name="Wasserkelch"),
        )

        catalog.publish(suggestion)

        entry = PlantSpecies.objects.get(scientific_name="Cryptocoryne wendtii")
        self.assertEqual(entry.common_name, "Wasserkelch")
        self.assertEqual(entry.slug, "cryptocoryne-wendtii")

    def test_confirming_a_suggestion_puts_it_in_the_catalog(self):
        suggestion = self.suggestion(
            AISuggestion.Kind.PLANT,
            {"scientific_name": "Vallisneria spiralis", "common_name": "Wasserschraube"},
        )

        ai.confirm_suggestion(suggestion, user=self.user)

        suggestion.refresh_from_db()
        entry = PlantSpecies.objects.get(scientific_name="Vallisneria spiralis")
        self.assertEqual(suggestion.catalog_ref, f"catalog.PlantSpecies:{entry.pk}")


class EvaluationTests(TestCase):
    """Messwerte deuten, Besatz prüfen, Beckenbericht."""

    def setUp(self):
        self.tank = prompts.TankFacts(name="Gesellschaftsbecken", volume_liters=180)

    def test_measurements_are_interpreted_as_markdown(self):
        service = service_with(message("Der Nitritwert steigt seit einer Woche."))
        answer = ai.read_measurements(
            self.tank,
            [{"measured_at": "01.09.2026", "no2": 0.1}, {"measured_at": "05.09.2026", "no2": 0.4}],
            service=service,
        )
        self.assertTrue(answer)
        self.assertIn("Nitritwert", answer.text)
        self.assertIn("0.4", service.client().calls[0]["messages"][0]["content"][0]["text"])

    def test_no_measurements_means_no_call(self):
        service = service_with(message("darf nicht passieren"))
        answer = ai.read_measurements(self.tank, [], service=service)
        self.assertFalse(answer)
        self.assertEqual(service.client().calls, [])

    def test_stocking_check_returns_findings(self):
        payload = json.dumps(
            {
                "verdict": "mit_einschraenkungen",
                "summary": "Die Gruppengröße passt nicht.",
                "findings": [
                    {"topic": "gruppengroesse", "severity": "warnung",
                     "subject": "Neonsalmler", "message": "Mindestens zehn Tiere."}
                ],
                "open_questions": "",
            }
        )
        answer = ai.check_stocking(
            self.tank,
            [prompts.StockItem("Neonsalmler", count=4)],
            service=service_with(message(payload)),
        )
        self.assertTrue(answer)
        self.assertEqual(answer.data["verdict"], "mit_einschraenkungen")
        self.assertEqual(answer.data["findings"][0]["topic"], "gruppengroesse")

    def test_empty_stocking_means_no_call(self):
        service = service_with(message("darf nicht passieren"))
        answer = ai.check_stocking(self.tank, [], service=service)
        self.assertFalse(answer)
        self.assertEqual(service.client().calls, [])

    def test_report_carries_measurements_and_events(self):
        service = service_with(message("## Überblick\nRuhiger Monat."))
        period = prompts.ReportPeriod(
            start="01.08.2026",
            end="31.08.2026",
            measurements=[{"measured_at": "05.08.2026", "ph": 7.0}],
            events=[{"occurred_at": "10.08.2026", "title": "Wasserwechsel", "description": "30 %"}],
        )
        answer = ai.tank_report(self.tank, period, service=service)

        self.assertTrue(answer)
        sent = service.client().calls[0]["messages"][0]["content"][0]["text"]
        self.assertIn("Wasserwechsel", sent)
        self.assertIn("ph 7.0", sent)
