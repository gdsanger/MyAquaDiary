"""Die Anwendungsfälle der KI-Assistenz.

Jede Funktion hier ist ein Aufruf wert und keiner mehr: sie baut den Prompt,
lässt :class:`~services.ai.client.AIService` fragen und gibt ein Ergebnis
zurück, das eine Seite direkt anzeigen kann. Keine Funktion schreibt etwas an
ein Gerät, legt einen Termin an oder verändert den Katalog — dafür braucht es
immer eine ausdrückliche Bestätigung durch den Benutzer
(:func:`confirm_suggestion`).

Die drei Auswertungen (Messwerte, Besatz, Bericht) nehmen einfache
Datenstrukturen statt Modellinstanzen entgegen: Becken, Messreihen und Besatz
liegen in einem anderen Schritt des Epics. Die Beckenseiten füllen
:class:`~services.ai.prompts.TankFacts` und Co., sobald es sie gibt.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from ..models import AISuggestion, AIUsageLog
from . import catalog as catalog_service
from . import prompts, schemas
from .client import AIResult, AIService
from .exceptions import AIError
from .images import prepare_image
from .prompts import ReportPeriod, StockItem, TankFacts

#: Vorschlagsart -> (Bezeichnung im Prompt, Aktion, Steckbrief-Schema).
KINDS = {
    AISuggestion.Kind.ANIMAL: (
        "ein Tier (Fisch, Garnele, Krebs, Schnecke oder Muschel)",
        AIUsageLog.Action.IDENTIFY_ANIMAL,
        AIUsageLog.Action.PROFILE_ANIMAL,
        schemas.ANIMAL_PROFILE_SCHEMA,
    ),
    AISuggestion.Kind.PLANT: (
        "eine Wasserpflanze",
        AIUsageLog.Action.IDENTIFY_PLANT,
        AIUsageLog.Action.PROFILE_PLANT,
        schemas.PLANT_PROFILE_SCHEMA,
    ),
}

#: Höchstens so viele Kandidaten werden übernommen — mehr hilft niemandem bei
#: der Auswahl.
MAX_CANDIDATES = 5
#: Bestimmen ist Mustererkennung und braucht wenig Nachdenken; Steckbrief und
#: Auswertungen dagegen schon.
EFFORT_IDENTIFY = "low"
EFFORT_REASONING = "medium"


@dataclass(frozen=True)
class Candidate:
    """Ein Bestimmungsvorschlag samt Katalogtreffern."""

    scientific_name: str
    common_name: str = ""
    confidence: float | None = None
    reasoning: str = ""
    distinguishing_features: str = ""
    matches: list = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.scientific_name and self.common_name:
            return f"{self.scientific_name} ({self.common_name})"
        return self.scientific_name or self.common_name or "Unbestimmt"

    @property
    def confidence_percent(self):
        return None if self.confidence is None else round(self.confidence * 100)

    @property
    def known_in_catalog(self) -> bool:
        return bool(self.matches)


@dataclass(frozen=True)
class Identification:
    """Ergebnis einer Bestimmung aus einem Foto."""

    ok: bool
    candidates: list = field(default_factory=list)
    image_notes: str = ""
    error: str = ""
    result: AIResult | None = None

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class Answer:
    """Eine Antwort in Textform (Markdown) oder als Befundliste."""

    ok: bool
    text: str = ""
    data: dict = field(default_factory=dict)
    error: str = ""
    result: AIResult | None = None

    def __bool__(self) -> bool:
        return self.ok


# --------------------------------------------------------------------------
# Bestimmen
# --------------------------------------------------------------------------


def identify(image_file, kind: str, *, user=None, notes: str = "", service=None) -> Identification:
    """Bestimmt die Art auf einem Foto und gleicht sie mit dem Katalog ab.

    :param image_file: Hochgeladenes Bild; wird vor dem Versand verkleinert.
    :param kind: ``animal`` oder ``plant``.
    :param notes: Freiwillige Beobachtung des Halters, geht in den Prompt ein.
    """
    kind_label, action, _profile_action, _schema = KINDS[kind]

    try:
        image = prepare_image(image_file)
    except AIError as exc:
        return Identification(ok=False, error=str(exc))

    service = service or AIService()
    result = service.ask(
        action,
        system=prompts.IDENTIFY_SYSTEM,
        prompt=prompts.identify_prompt(kind_label, notes),
        images=[image],
        schema=schemas.IDENTIFICATION_SCHEMA,
        effort=EFFORT_IDENTIFY,
        user=user,
    )
    if not result:
        return Identification(ok=False, error=result.error, result=result)

    candidates = [
        _candidate(entry, kind)
        for entry in (result.data.get("candidates") or [])[:MAX_CANDIDATES]
        if isinstance(entry, dict) and (entry.get("scientific_name") or entry.get("common_name"))
    ]
    if not candidates:
        return Identification(
            ok=False,
            error="Auf dem Foto war keine Art zu erkennen.",
            image_notes=str(result.data.get("image_notes") or ""),
            result=result,
        )
    return Identification(
        ok=True,
        candidates=candidates,
        image_notes=str(result.data.get("image_notes") or ""),
        result=result,
    )


def _candidate(entry: dict, kind: str) -> Candidate:
    scientific_name = str(entry.get("scientific_name") or "").strip()
    common_name = str(entry.get("common_name") or "").strip()
    return Candidate(
        scientific_name=scientific_name,
        common_name=common_name,
        confidence=_confidence(entry.get("confidence")),
        reasoning=str(entry.get("reasoning") or "").strip(),
        distinguishing_features=str(entry.get("distinguishing_features") or "").strip(),
        matches=catalog_service.find_matches(kind, scientific_name, common_name),
    )


def _confidence(value):
    """Konfidenz auf 0–1 begrenzen; Unsinn wird zu ``None``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1:  # manche Modelle antworten in Prozent
        number = number / 100
    return min(max(number, 0.0), 1.0)


# --------------------------------------------------------------------------
# Vorschläge festhalten und bestätigen
# --------------------------------------------------------------------------


def save_suggestion(user, kind: str, candidate: Candidate, payload: dict | None = None) -> AISuggestion:
    """Legt einen Kandidaten als Entwurf ab — ``verified`` bleibt False.

    Erst :func:`confirm_suggestion` macht daraus einen Katalogeintrag.
    """
    return AISuggestion.objects.create(
        user=user if getattr(user, "pk", None) else None,
        kind=kind,
        scientific_name=candidate.scientific_name[:160],
        common_name=candidate.common_name[:160],
        confidence=_as_decimal(candidate.confidence),
        reasoning=candidate.reasoning,
        payload=payload or {},
    )


def draft_profile(suggestion: AISuggestion, *, user=None, service=None) -> Answer:
    """Entwirft den Steckbrief zu einem Vorschlag und hängt ihn an.

    Der Entwurf landet in ``suggestion.payload``; der Vorschlag bleibt dabei
    ein Entwurf. Sichtbar im Katalog wird er erst nach der Bestätigung.
    """
    kind_label, _identify_action, action, schema = KINDS[suggestion.kind]
    service = service or AIService()
    result = service.ask(
        action,
        system=prompts.PROFILE_SYSTEM,
        prompt=prompts.profile_prompt(
            kind_label, suggestion.scientific_name, suggestion.common_name
        ),
        schema=schema,
        effort=EFFORT_REASONING,
        user=user or suggestion.user,
    )
    if not result:
        return Answer(ok=False, error=result.error, result=result)

    suggestion.payload = _clean_payload(result.data)
    suggestion.save(update_fields=["payload"])
    return Answer(ok=True, data=suggestion.payload, result=result)


def confirm_suggestion(suggestion: AISuggestion, user=None) -> AISuggestion:
    """Bestätigt einen Entwurf und übernimmt ihn in den Katalog.

    Das ist der einzige Weg, auf dem ein KI-Vorschlag im Katalog landet.
    """
    suggestion.status = AISuggestion.Status.VERIFIED
    suggestion.decided_at = timezone.now()
    suggestion.catalog_ref = catalog_service.publish(suggestion)
    suggestion.save(update_fields=["status", "decided_at", "catalog_ref"])
    return suggestion


def reject_suggestion(suggestion: AISuggestion) -> AISuggestion:
    """Verwirft einen Entwurf. Er bleibt als Protokoll stehen."""
    suggestion.status = AISuggestion.Status.REJECTED
    suggestion.decided_at = timezone.now()
    suggestion.save(update_fields=["status", "decided_at"])
    return suggestion


def _clean_payload(data: dict) -> dict:
    """Leere Felder fliegen raus — ein leeres Feld ist keine Angabe."""
    return {key: value for key, value in (data or {}).items() if value not in (None, "", [])}


def _as_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(round(float(value), 3)))
    except (TypeError, ValueError, InvalidOperation):  # pragma: no cover - defensiv
        return None


# --------------------------------------------------------------------------
# Auswertungen
# --------------------------------------------------------------------------


def read_measurements(tank: TankFacts, measurements, *, user=None, service=None) -> Answer:
    """Ordnet einen Messwertverlauf ein — Hinweise, keine Diagnose."""
    if not measurements:
        return Answer(ok=False, error="Für diesen Zeitraum liegen keine Messwerte vor.")
    service = service or AIService()
    result = service.ask(
        AIUsageLog.Action.MEASUREMENTS,
        system=prompts.MEASUREMENT_SYSTEM,
        prompt=prompts.measurement_prompt(tank, measurements),
        effort=EFFORT_REASONING,
        user=user,
    )
    return _answer(result)


def analyse_series(context: prompts.MeasurementContext, *, user=None, service=None) -> Answer:
    """Ordnet eine Messreihe im Zusammenhang ein.

    Der Unterschied zu :func:`read_measurements` ist der Kontext: hier gehen
    Verlauf, Ereignisse, Stammdaten und Besatz mit in den Prompt. Was
    zurückkommt, sind Hinweise — keine Diagnose, und nichts davon schaltet
    Technik oder legt Termine an.
    """
    if not context.current.readings:
        return Answer(ok=False, error="Die Messreihe enthält keine Werte.")
    service = service or AIService()
    result = service.ask(
        AIUsageLog.Action.MEASUREMENTS,
        system=prompts.SERIES_SYSTEM,
        prompt=prompts.series_prompt(context),
        effort=EFFORT_REASONING,
        user=user,
    )
    return _answer(result)


def check_stocking(tank: TankFacts, stock, *, user=None, service=None) -> Answer:
    """Prüft eine Besatzplanung auf Beckengröße, Gruppen und Verträglichkeit."""
    items = [item for item in stock or () if isinstance(item, StockItem)]
    if not items:
        return Answer(ok=False, error="Es ist kein Besatz angegeben, den man prüfen könnte.")
    service = service or AIService()
    result = service.ask(
        AIUsageLog.Action.STOCKING,
        system=prompts.STOCKING_SYSTEM,
        prompt=prompts.stocking_prompt(tank, items),
        schema=schemas.STOCKING_SCHEMA,
        effort=EFFORT_REASONING,
        user=user,
    )
    return _answer(result)


def tank_report(tank: TankFacts, period: ReportPeriod, *, user=None, service=None) -> Answer:
    """Fasst einen Zeitraum des Beckentagebuchs als Markdown zusammen."""
    service = service or AIService()
    result = service.ask(
        AIUsageLog.Action.TANK_REPORT,
        system=prompts.REPORT_SYSTEM,
        prompt=prompts.report_prompt(tank, period),
        effort=EFFORT_REASONING,
        user=user,
    )
    return _answer(result)


def _answer(result: AIResult) -> Answer:
    if not result:
        return Answer(ok=False, error=result.error, result=result)
    return Answer(ok=True, text=result.text, data=result.data, result=result)
