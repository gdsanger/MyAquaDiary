"""Gekapselter Zugang zu Anthropic Claude.

Alles, was mit der API spricht, geht durch :class:`AIService` — das ist die
einzige Stelle, an der der API-Key gelesen wird, und die einzige, die
Verbrauch protokolliert. Aufrufer bekommen ein :class:`AIResult` und nie eine
Ausnahme; eine gescheiterte Anfrage darf keine Seite in Stücke reißen.

Ablauf je Aufruf:

1. Konfiguration und Budget prüfen — beides vor dem ersten Byte an die API
2. Anfrage stellen (adaptives Denken, ``effort`` je Aktion)
3. Antwort einsammeln, bei erwartetem JSON parsen
4. Token, Kosten und Dauer in :class:`~services.models.AIUsageLog` schreiben

Der API-Key erscheint in keinem Log und in keiner Meldung: er wird aus
Fehlertexten herausgefiltert, bevor sie irgendwohin gehen.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

from ..models import AIConfig, AIUsageLog
from . import budget as budget_service
from .exceptions import AIError, AINotConfigured, AIResponseError
from .images import PreparedImage
from .pricing import cost_usd, supports_structured_output

logger = logging.getLogger(__name__)

#: Voreinstellung je Aufruf. Großzügig genug, dass adaptives Denken plus
#: Antwort hineinpassen — ein abgeschnittener Steckbrief kostet dieselben
#: Token wie ein vollständiger und ist nichts wert.
DEFAULT_MAX_TOKENS = 8000
DEFAULT_EFFORT = "medium"
DEFAULT_TIMEOUT = 120
#: Fehlertexte werden gekürzt protokolliert.
MAX_ERROR_LENGTH = 500
#: Ab dieser Länge wird der Key aus Meldungen gefiltert. Kürzere Werte sind
#: keine echten Keys und würden als Teilstring harmlose Wörter zerstückeln.
MIN_REDACTION_LENGTH = 8

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class AIResult:
    """Ergebnis eines Aufrufs. Truthy genau dann, wenn eine Antwort vorliegt."""

    ok: bool
    text: str = ""
    data: dict = field(default_factory=dict)
    error: str = ""
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    duration_ms: int = 0
    #: Der geschriebene Protokolleintrag — damit ein gespeichertes Ergebnis
    #: sagen kann, was es gekostet hat. ``None``, wenn das Protokoll nicht
    #: geschrieben werden konnte; das darf einen Aufruf nie scheitern lassen.
    usage_log: object | None = None

    def __bool__(self) -> bool:
        return self.ok

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AIService:
    """Fragt Claude und protokolliert dabei jeden Aufruf.

    :param config: Konfiguration; ohne Angabe die gespeicherte Singleton.
    :param client: SDK-Client; ohne Angabe wird er beim ersten Aufruf gebaut.
        In Tests wird hier ein Doppel übergeben, damit kein Netz nötig ist.
    """

    def __init__(self, config: AIConfig | None = None, *, client=None, timeout: int | None = None):
        self.config = config if config is not None else AIConfig.load()
        self.timeout = timeout if timeout is not None else getattr(
            settings, "AI_TIMEOUT", DEFAULT_TIMEOUT
        )
        self._client = client

    # -- Zustand -------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    @property
    def model_name(self) -> str:
        return self.config.model_name

    # -- Aufruf --------------------------------------------------------------

    def ask(
        self,
        action: str,
        *,
        prompt: str,
        system: str,
        images=(),
        schema: dict | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = DEFAULT_EFFORT,
        user=None,
    ) -> AIResult:
        """Stellt eine Anfrage und liefert das Ergebnis — ohne Ausnahmen.

        :param action: Wert aus :class:`~services.models.AIUsageLog.Action`.
        :param schema: JSON-Schema; gesetzt, wenn strukturiertes JSON erwartet
            wird. Beherrscht das Modell keine Structured Outputs, wird das
            Schema nur im Prompt beschrieben und die Antwort nachsichtig
            geparst.
        """
        started = time.monotonic()

        try:
            self._ensure_ready(user)
            message = self._send(prompt, system, images, schema, max_tokens, effort)
            text = _text_of(message)
            data = _parse_json(text) if schema else {}
        except AIError as exc:
            return self._fail(action, user, self._redact(str(exc)), started)
        except Exception as exc:  # pragma: no cover - Notnagel, kein Aufrufer darf sterben
            logger.exception("Unerwarteter Fehler beim KI-Aufruf %s", action)
            return self._fail(
                action, user, self._redact(f"Unerwarteter Fehler: {type(exc).__name__}"), started
            )

        prompt_tokens, completion_tokens, cost = _account(message, self.model_name)
        duration_ms = _elapsed_ms(started)
        usage_log = self._log(
            action,
            user,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            duration_ms=duration_ms,
            success=True,
        )
        logger.info(
            "KI-Aufruf %s: %s Token (%s ein, %s aus), %s ms",
            action,
            prompt_tokens + completion_tokens,
            prompt_tokens,
            completion_tokens,
            duration_ms,
        )
        return AIResult(
            ok=True,
            text=text,
            data=data,
            model_name=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
            usage_log=usage_log,
        )

    # -- Innenleben ----------------------------------------------------------

    def _ensure_ready(self, user) -> None:
        if not self.is_configured:
            raise AINotConfigured(
                "Die KI-Assistenz ist nicht eingerichtet. Ein Administrator "
                "hinterlegt den API-Key unter Services → KI-Konfiguration."
            )
        budget_service.check(user, self.config)

    def _send(self, prompt, system, images, schema, max_tokens, effort):
        content = [image.as_content_block() for image in images if isinstance(image, PreparedImage)]
        content.append({"type": "text", "text": prompt})

        output_config = {"effort": effort}
        if schema and supports_structured_output(self.model_name):
            output_config["format"] = {"type": "json_schema", "schema": schema}

        try:
            message = self.client().messages.create(
                model=self.model_name,
                max_tokens=max_tokens,
                system=system,
                output_config=output_config,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            raise AIError(_describe(exc)) from exc

        if getattr(message, "stop_reason", "") == "refusal":
            raise AIResponseError(
                "Claude hat die Antwort abgelehnt. Bitte formuliere die Anfrage anders "
                "oder versuche es mit einem anderen Foto."
            )
        if getattr(message, "stop_reason", "") == "max_tokens":
            raise AIResponseError(
                "Die Antwort war länger als erlaubt und wurde abgeschnitten. "
                "Bitte versuche es erneut."
            )
        return message

    def client(self):
        """Der SDK-Client — erst beim ersten Aufruf gebaut."""
        if self._client is None:
            # Lazy, damit ein Import von services.models ohne installiertes
            # SDK nicht scheitert und Tests ohne Netz auskommen.
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=self.config.api_key, timeout=self.timeout
            )
        return self._client

    def _fail(self, action, user, message, started) -> AIResult:
        duration_ms = _elapsed_ms(started)
        logger.warning("KI-Aufruf %s fehlgeschlagen: %s", action, message)
        self._log(action, user, duration_ms=duration_ms, success=False, error=message)
        return AIResult(ok=False, error=message, model_name=self.model_name, duration_ms=duration_ms)

    def _log(
        self,
        action,
        user,
        *,
        prompt_tokens=0,
        completion_tokens=0,
        cost=Decimal(0),
        duration_ms=0,
        success=True,
        error="",
    ):
        """Schreibt den Protokolleintrag — auch für abgelehnte Aufrufe.

        Ein am Budget gescheiterter Versuch steht damit im Admin, und ein
        Protokollfehler stoppt nie den Aufrufer: dann gibt es eben keinen
        Eintrag, auf den sich ein Ergebnis berufen kann.
        """
        try:
            return AIUsageLog.objects.create(
                user=user if getattr(user, "pk", None) else None,
                action=action[:50],
                model_name=self.model_name[:100],
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_cost_usd=cost,
                duration_ms=duration_ms,
                success=success,
                error_message=self._redact(error)[:MAX_ERROR_LENGTH],
            )
        except Exception:  # pragma: no cover - Protokoll darf nie blockieren
            logger.exception("KI-Protokoll konnte nicht geschrieben werden")
            return None

    def _redact(self, message: str) -> str:
        """Stellt sicher, dass der API-Key nie in Logs oder UI landet."""
        key = self.config.api_key
        if key and len(key) >= MIN_REDACTION_LENGTH:
            message = message.replace(key, "***")
        return message


# --------------------------------------------------------------------------
# Antwort auswerten
# --------------------------------------------------------------------------


def _text_of(message) -> str:
    """Alle Text-Blöcke einer Antwort, zusammengesetzt.

    Denk-Blöcke haben keinen ``text`` und fallen damit von selbst heraus.
    """
    parts = [
        block.text
        for block in getattr(message, "content", []) or []
        if getattr(block, "type", "") == "text" and getattr(block, "text", "")
    ]
    text = "\n".join(parts).strip()
    if not text:
        raise AIResponseError("Claude hat nichts zurückgegeben.")
    return text


def _parse_json(text: str) -> dict:
    """Liest das JSON-Objekt aus einer Antwort.

    Mit Structured Outputs ist der Text bereits reines JSON. Ohne — bei einem
    Modell, das das nicht kann — kommt es gern in einem Codeblock oder mit
    einem Satz davor; beides wird hier noch abgefangen.
    """
    for candidate in _json_candidates(text):
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AIResponseError("Die Antwort war nicht im erwarteten Format.")


def _json_candidates(text: str):
    yield text
    fenced = _JSON_FENCE.search(text)
    if fenced:
        yield fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        yield text[start : end + 1]


def _account(message, model_name):
    """Token und Kosten einer Antwort."""
    usage = getattr(message, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cost = cost_usd(model_name, prompt_tokens, completion_tokens, cache_write, cache_read)
    # Cache-Token zählen für das Budget als Eingabe mit; sonst wäre ein
    # gecachter Prompt im Budget unsichtbar.
    return prompt_tokens + cache_write + cache_read, completion_tokens, cost


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _describe(exc: Exception) -> str:
    """Kurzfassung eines SDK-Fehlers, für Benutzer lesbar."""
    name = type(exc).__name__
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "Der hinterlegte API-Key wurde von Anthropic abgelehnt."
    if name == "RateLimitError":
        return "Anthropic hat wegen zu vieler Anfragen abgeregelt. Bitte kurz warten."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "Anthropic war nicht erreichbar. Bitte später erneut versuchen."
    if name == "NotFoundError":
        return "Das eingestellte Modell gibt es nicht. Bitte die KI-Konfiguration prüfen."
    if name == "BadRequestError":
        return f"Anthropic hat die Anfrage abgelehnt: {str(exc)[:MAX_ERROR_LENGTH]}"
    return f"{name}: {str(exc)[:MAX_ERROR_LENGTH]}"
