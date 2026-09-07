"""Tests der KI-Auswertung einer Messreihe.

Kein Test spricht mit Anthropic: :func:`fake_service` liefert vorbereitete
Antworten in derselben Form wie das SDK. Die Auswertung läuft in den Tests im
Anfrage-Thread (``AI_ANALYSIS_BACKGROUND=False``) — ein Hintergrund-Thread
liefe außerhalb der Testtransaktion und fände die Messreihe nicht.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.testing import (
    PASSWORD,
    create_animal,
    create_measurement,
    create_plant,
    create_tank,
    create_user,
)
from services.ai.client import AIService
from services.models import AIConfig, AIUsageLog, MeasurementAnalysis
from tanks import analysis
from tanks.models import Event, Measurement, Parameter, Planting, Stocking, Tank

CONFIGURED = {"api_key": "sk-ant-api03-testschluessel", "model_name": "claude-opus-5"}
SYNC = {"AI_ANALYSIS_BACKGROUND": False}


def answer(text="Der pH ist seit gestern leicht gefallen."):
    """Eine Antwort, wie das SDK sie liefert."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=120, output_tokens=80),
        stop_reason="end_turn",
    )


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0) if self.responses else answer()
        if isinstance(response, Exception):
            raise response
        return response


class FakeAnthropic:
    def __init__(self, *responses):
        self.messages = FakeMessages(responses)

    @property
    def calls(self):
        return self.messages.calls


def fake_service(*responses, **config):
    """Ein :class:`AIService`, der nie ins Netz geht."""
    return AIService(AIConfig(**{**CONFIGURED, **config}), client=FakeAnthropic(*responses))


def patched(*responses, **config):
    """Ersetzt den Dienst, den die Auswertung sich selbst baut."""
    service = fake_service(*responses, **config)
    return patch("services.ai.usecases.AIService", return_value=service), service


def measure(tank, when, values, note=""):
    """Eine ganze Messreihe zu einem Zeitpunkt."""
    created = []
    for key, value in values.items():
        created.append(
            Measurement.objects.create(
                tank=tank,
                parameter=Parameter.objects.get(key=key),
                value=Decimal(value),
                measured_at=when,
                note=note,
            )
        )
    return created


class Co2Tests(TestCase):
    """Die CO₂-Näherung muss zu den gedruckten Tabellen passen."""

    def test_matches_the_common_table(self):
        self.assertAlmostEqual(analysis.co2(Decimal("4"), Decimal("7.0")), 12.0, places=1)
        self.assertAlmostEqual(analysis.co2(Decimal("4"), Decimal("6.8")), 19.0, places=0)

    def test_without_kh_or_ph_there_is_nothing_to_compute(self):
        self.assertIsNone(analysis.co2(None, Decimal("7.0")))
        self.assertIsNone(analysis.co2(Decimal("4"), None))
        self.assertIsNone(analysis.co2(Decimal("0"), Decimal("7.0")))


class SeriesTests(TestCase):
    """Eine Messreihe ist kein Modell, sondern ein Zeitpunkt."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        self.when = timezone.now() - timedelta(hours=1)
        self.rows = measure(self.tank, self.when, {"ph": "7.2", "kh": "5"})

    def test_the_oldest_entry_represents_the_series(self):
        anchor = analysis.anchor_of(self.rows[1])
        self.assertEqual(anchor.pk, self.rows[0].pk)
        self.assertEqual(analysis.latest_anchor(self.tank).pk, self.rows[0].pk)

    def test_the_series_holds_everything_measured_at_that_moment(self):
        keys = {row.parameter.key for row in analysis.series_of(self.rows[0])}
        self.assertEqual(keys, {"ph", "kh"})

    def test_an_older_series_is_not_the_latest(self):
        newer = measure(self.tank, timezone.now(), {"ph": "7.4"})
        self.assertEqual(analysis.latest_anchor(self.tank).pk, newer[0].pk)


class ContextTests(TestCase):
    """Was in den Prompt geht — und was der Auswertung sonst fehlen würde."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user, notes="Sandboden, viel Wurzelholz")
        self.when = timezone.now()
        measure(self.tank, self.when - timedelta(days=2), {"ph": "7.4", "kh": "6"})
        measure(self.tank, self.when - timedelta(days=30), {"ph": "6.9"})
        self.rows = measure(self.tank, self.when, {"ph": "7.2", "kh": "5"}, note="Nachmittags")
        Event.objects.create(
            tank=self.tank,
            category=Event.Category.MAINTENANCE,
            title="Osmosewasser nachgefüllt",
            occurred_at=self.when - timedelta(days=1),
        )
        Stocking.objects.create(
            tank=self.tank,
            species=create_animal(temperature_min=Decimal("23"), temperature_max=Decimal("27")),
            quantity=12,
            added_on=timezone.localdate() - timedelta(days=100),
        )
        Planting.objects.create(
            tank=self.tank,
            species=create_plant(co2_required=True),
            quantity=5,
            planted_on=timezone.localdate() - timedelta(days=100),
        )
        self.context = analysis.build_context(analysis.anchor_of(self.rows[0]))

    def test_the_current_series_carries_target_and_status(self):
        text = self.context.current.as_block()
        self.assertIn("pH-Wert: 7,2", text)
        self.assertIn("Ziel 6,5–7,5", text)
        self.assertIn("In Ordnung", text)
        self.assertIn("Nachmittags", text)

    def test_co2_is_computed_from_kh_and_ph(self):
        # 3 · 5 °dH · 10^(7 − 7,2) ≈ 9 mg/l
        self.assertEqual(self.context.current.co2, "9")

    def test_history_reaches_ten_days_back_and_no_further(self):
        dates = [series.measured_at for series in self.context.history]
        self.assertEqual(len(dates), 1)
        self.assertIn(
            timezone.localtime(self.when - timedelta(days=2)).strftime("%d.%m.%Y"), dates[0]
        )

    def test_events_come_along(self):
        self.assertIn("Osmosewasser nachgefüllt", self.context.events[0].title)

    def test_stock_and_plants_come_with_their_demands(self):
        text = self.context.as_text()
        self.assertIn("12× Neonsalmler (Tier)", text)
        self.assertIn("23–27 °C", text)
        self.assertIn("Wendts Wasserkelch (Pflanze)", text)
        self.assertIn("CO₂ nötig", text)

    def test_tank_facts_carry_volume_age_and_targets(self):
        text = self.context.tank.as_text()
        self.assertIn("240 l", text)
        self.assertIn("Alter", text)
        self.assertIn("Sandboden", text)

    def test_the_fingerprint_reacts_to_a_new_event(self):
        before = self.context.fingerprint()
        Event.objects.create(
            tank=self.tank,
            category=Event.Category.WATER_CHANGE,
            title="Wasserwechsel 30 %",
            occurred_at=self.when - timedelta(hours=2),
        )
        after = analysis.build_context(analysis.anchor_of(self.rows[0])).fingerprint()
        self.assertNotEqual(before, after)


@override_settings(**SYNC)
class RunTests(TestCase):
    """Auslösen, festhalten, wiederverwenden."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        self.rows = measure(self.tank, timezone.now(), {"ph": "7.2", "kh": "5"})
        self.anchor = analysis.anchor_of(self.rows[0])
        AIConfig.objects.create(pk=1, **CONFIGURED)

    def test_the_result_is_stored_with_model_tokens_and_costs(self):
        mock, service = patched(answer("Alles im Rahmen, weitermessen."))
        with mock:
            analysis.start(self.anchor, user=self.user)

        stored = MeasurementAnalysis.objects.get()
        self.assertEqual(stored.status, MeasurementAnalysis.Status.READY)
        self.assertIn("weitermessen", stored.text)
        self.assertEqual(stored.model_name, "claude-opus-5")
        self.assertEqual(stored.usage_log.action, AIUsageLog.Action.MEASUREMENTS)
        self.assertEqual(stored.usage_log.total_tokens, 200)
        self.assertEqual(len(service.client().calls), 1)

    def test_unchanged_data_is_not_evaluated_twice(self):
        mock, service = patched(answer(), answer("zweiter Aufruf"))
        with mock:
            analysis.start(self.anchor, user=self.user)
            state = analysis.start(self.anchor, user=self.user)

        self.assertEqual(MeasurementAnalysis.objects.count(), 1)
        self.assertEqual(len(service.client().calls), 1)
        self.assertIn("nichts geändert", state.notice)

    def test_changed_data_can_be_evaluated_again(self):
        mock, service = patched(answer(), answer("nach dem Wasserwechsel"))
        with mock:
            analysis.start(self.anchor, user=self.user)
            # Nachgetragenes Ereignis: es ging der Messung voraus und ändert
            # damit, wie sie zu lesen ist.
            Event.objects.create(
                tank=self.tank,
                category=Event.Category.WATER_CHANGE,
                title="Wasserwechsel 30 %",
                occurred_at=self.anchor.measured_at - timedelta(hours=3),
            )
            self.assertTrue(analysis.state(self.tank).stale)
            analysis.start(self.anchor, user=self.user)

        self.assertEqual(MeasurementAnalysis.objects.count(), 2)
        self.assertEqual(len(service.client().calls), 2)
        self.assertIn("Wasserwechsel", analysis.state(self.tank).analysis.text)

    def test_a_failed_call_is_visible_and_repeatable(self):
        mock, _service = patched(
            RuntimeError("Anthropic antwortet nicht"), answer("Beim zweiten Mal ging es.")
        )
        with mock:
            state = analysis.start(self.anchor, user=self.user)
            stored = MeasurementAnalysis.objects.get()
            self.assertEqual(stored.status, MeasurementAnalysis.Status.FAILED)
            self.assertTrue(stored.error_message)
            self.assertTrue(state.can_start)

            # Trotz unveränderter Datenlage: ein Fehlschlag ist kein Ergebnis.
            analysis.start(self.anchor, user=self.user)

        self.assertIn("zweiten Mal", analysis.state(self.tank).analysis.text)

    def test_nothing_happens_while_the_tank_has_it_switched_off(self):
        self.tank.ai_analysis_mode = Tank.AIAnalysis.OFF
        self.tank.save(update_fields=["ai_analysis_mode"])
        mock, service = patched(answer())
        with mock:
            state = analysis.start(self.anchor, user=self.user)

        self.assertEqual(MeasurementAnalysis.objects.count(), 0)
        self.assertEqual(service.client().calls, [])
        self.assertFalse(state.available)

    def test_the_budget_limit_stops_the_evaluation(self):
        """Erschöpftes Budget: kein Aufruf, aber ein sichtbarer Grund."""
        AIUsageLog.objects.create(
            action=AIUsageLog.Action.MEASUREMENTS, prompt_tokens=150, completion_tokens=0
        )
        mock, service = patched(answer(), monthly_token_budget=100)
        with mock:
            analysis.start(self.anchor, user=self.user)

        stored = MeasurementAnalysis.objects.get()
        self.assertEqual(stored.status, MeasurementAnalysis.Status.FAILED)
        self.assertIn("Budget", stored.error_message)
        self.assertEqual(service.client().calls, [])

    def test_without_an_api_key_there_is_nothing_to_see(self):
        AIConfig.objects.all().delete()
        with self.settings(ANTHROPIC={"API_KEY": "", "MODEL": "claude-opus-5"}):
            state = analysis.start(self.anchor, user=self.user)
            self.assertFalse(state.available)
        self.assertEqual(MeasurementAnalysis.objects.count(), 0)


@override_settings(**SYNC)
class ViewTests(TestCase):
    """Der Weg durch die Oberfläche."""

    def setUp(self):
        self.user = create_user()
        self.tank = create_tank(self.user)
        AIConfig.objects.create(pk=1, **CONFIGURED)
        self.client.login(username=self.user.username, password=PASSWORD)
        self.url = reverse("tanks:measurement-analysis", args=[self.tank.slug])

    def htmx(self, method, url, data=None):
        return getattr(self.client, method)(url, data or {}, headers={"hx-request": "true"})

    def test_saving_a_series_does_not_wait_for_the_evaluation(self):
        """Ohne „automatisch" wird beim Speichern nichts aufgerufen."""
        mock, service = patched(answer())
        with mock:
            response = self.htmx(
                "post",
                reverse("tanks:measurement-create", args=[self.tank.slug]),
                {"measured_at": "01.09.2026 20:00", f"parameter_{Parameter.objects.get(key='ph').pk}": "7,2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Measurement.objects.count(), 1)
        self.assertEqual(service.client().calls, [])
        self.assertEqual(MeasurementAnalysis.objects.count(), 0)

    def test_automatic_mode_evaluates_a_freshly_saved_series(self):
        self.tank.ai_analysis_mode = Tank.AIAnalysis.AUTO
        self.tank.save(update_fields=["ai_analysis_mode"])
        mock, _service = patched(answer("Der Nitritwert ist unauffällig."))
        with mock:
            self.htmx(
                "post",
                reverse("tanks:measurement-create", args=[self.tank.slug]),
                {"measured_at": "01.09.2026 20:00", f"parameter_{Parameter.objects.get(key='ph').pk}": "7,2"},
            )

        stored = MeasurementAnalysis.objects.get()
        self.assertEqual(stored.status, MeasurementAnalysis.Status.READY)

    def test_the_button_triggers_and_the_answer_is_marked_as_such(self):
        create_measurement(self.tank, "ph", "7.2")
        mock, _service = patched(answer("Der pH liegt im Zielbereich."))
        with mock:
            response = self.htmx("post", self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zielbereich")
        self.assertContains(response, "KI-Auswertung")
        self.assertContains(response, "Anthropic Claude")

    def test_the_measurement_tab_shows_the_stored_result_without_javascript(self):
        create_measurement(self.tank, "ph", "7.2")
        mock, _service = patched(answer("Alles ruhig."))
        with mock:
            analysis.start(analysis.latest_anchor(self.tank), user=self.user)

        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=messwerte")
        self.assertContains(response, "Alles ruhig.")

    def test_without_an_api_key_the_block_and_the_address_are_gone(self):
        AIConfig.objects.all().delete()
        create_measurement(self.tank, "ph", "7.2")
        with self.settings(ANTHROPIC={"API_KEY": "", "MODEL": "claude-opus-5"}):
            response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=messwerte")
            self.assertNotContains(response, "KI-Auswertung der Messreihe")
            self.assertEqual(self.htmx("post", self.url).status_code, 404)

    def test_a_tank_with_the_evaluation_switched_off_shows_nothing(self):
        self.tank.ai_analysis_mode = Tank.AIAnalysis.OFF
        self.tank.save(update_fields=["ai_analysis_mode"])
        create_measurement(self.tank, "ph", "7.2")
        response = self.client.get(f"{self.tank.get_absolute_url()}?reiter=messwerte")
        self.assertNotContains(response, "KI-Auswertung der Messreihe")

    def test_a_foreign_tank_stays_invisible(self):
        stranger = create_user(username="fremder")
        foreign = create_tank(stranger, name="Fremdbecken", slug="fremdbecken")
        url = reverse("tanks:measurement-analysis", args=[foreign.slug])
        self.assertEqual(self.htmx("get", url).status_code, 404)
        self.assertEqual(self.htmx("post", url).status_code, 404)

    def test_without_htmx_the_answer_is_the_tank_page(self):
        create_measurement(self.tank, "ph", "7.2")
        mock, _service = patched(answer())
        with mock:
            response = self.client.post(self.url)
        self.assertRedirects(response, f"{self.tank.get_absolute_url()}?reiter=messwerte")
