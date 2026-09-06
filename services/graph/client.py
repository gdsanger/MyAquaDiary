"""Ausgehender Mailversand über die Microsoft Graph API.

Authentifiziert wird per Client-Credentials-Flow (App-Registrierung mit der
Application-Permission ``Mail.Send``) — es gibt keinen angemeldeten Benutzer,
der Versand läuft aus Cron-Jobs heraus.

Gesendet wird als MIME-Nachricht (``multipart/alternative``), damit HTML- und
Plaintext-Variante gemeinsam beim Empfänger ankommen; der JSON-Body von
``sendMail`` transportiert immer nur eine der beiden Varianten.

Kein Ingest, keine Webhook-Subscriptions — für eingehende Mail gibt es in
diesem Projekt keinen Anwendungsfall.
"""

import base64
import logging
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

import requests

from ..models import MailConfig, MailLog
from .exceptions import GraphMailError, MailNotConfigured

logger = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

REQUEST_TIMEOUT = 15
#: Sicherheitsabstand, mit dem ein Token vor Ablauf erneuert wird.
TOKEN_EXPIRY_MARGIN = 60
#: Fehlerantworten werden gekürzt protokolliert.
MAX_ERROR_LENGTH = 500
#: Ab dieser Länge wird ein Secret aus Meldungen herausgefiltert. Kürzere Werte
#: sind keine echten Graph-Secrets (die sind ~40 Zeichen) und würden als
#: Teilstring harmlose Wörter zerstückeln.
MIN_REDACTION_LENGTH = 8

_token_lock = threading.Lock()
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


def reset_token_cache() -> None:
    """Verwirft zwischengespeicherte Access-Tokens (z. B. nach Config-Änderung)."""
    with _token_lock:
        _token_cache.clear()


@dataclass(frozen=True)
class MailResult:
    """Ergebnis eines Sendeversuchs. Truthy genau dann, wenn versendet wurde."""

    ok: bool
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


def normalize_recipients(to) -> list[str]:
    """Nimmt einen String oder ein Iterable und liefert eine bereinigte Liste."""
    if to is None:
        return []
    if isinstance(to, str):
        candidates = to.split(",")
    else:
        candidates = list(to)
    return [address for address in (str(c).strip() for c in candidates) if address]


def sanitize_header(value: str) -> str:
    """Entfernt Zeilenumbrüche — schützt vor Header-Injection über Betreffzeilen."""
    return " ".join(str(value or "").split())


class GraphMailService:
    """Versendet Mails über Microsoft Graph.

    ``send()`` wirft keine Ausnahmen: ein fehlgeschlagener Versand wird
    protokolliert und als ``MailResult(ok=False)`` zurückgegeben, damit ein
    Cron-Command daran nicht abbricht.
    """

    def __init__(self, config: MailConfig | None = None, *, timeout: int = REQUEST_TIMEOUT, session=None):
        self.config = config if config is not None else MailConfig.load()
        self.timeout = timeout
        # requests selbst erfüllt das Session-Protokoll (post) und ist damit
        # ein brauchbarer Default; in Tests wird ein Fake übergeben.
        self.session = session or requests

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def send(self, to, subject, html, text="", *, cc=None, template="") -> MailResult:
        """Versendet eine Mail mit HTML- und Plaintext-Teil.

        :param to: Empfänger als String (auch kommagetrennt) oder Iterable.
        :returns: :class:`MailResult` — niemals eine Ausnahme.
        """
        recipients = normalize_recipients(to)
        cc_recipients = normalize_recipients(cc)
        subject = sanitize_header(subject)

        if not recipients:
            message = "Kein Empfänger angegeben"
            logger.warning("Mailversand übersprungen: %s", message)
            return MailResult(False, message)

        if not self.is_configured:
            message = "Mailversand ist nicht konfiguriert"
            logger.warning("Mailversand an %s übersprungen: %s", ", ".join(recipients), message)
            self._log(recipients, subject, template, MailLog.Status.SKIPPED, message)
            return MailResult(False, message)

        try:
            self._deliver(recipients, subject, html, text, cc_recipients)
        except (GraphMailError, requests.RequestException) as exc:
            message = self._redact(str(exc))
            logger.error("Mailversand an %s fehlgeschlagen: %s", ", ".join(recipients), message)
            self._log(recipients, subject, template, MailLog.Status.FAILED, message)
            return MailResult(False, message)
        except Exception:  # pragma: no cover - Notnagel, darf keinen Command stoppen
            logger.exception("Unerwarteter Fehler beim Mailversand an %s", ", ".join(recipients))
            self._log(recipients, subject, template, MailLog.Status.FAILED, "Unerwarteter Fehler")
            return MailResult(False, "Unerwarteter Fehler")

        logger.info("Mail an %s versendet: %s", ", ".join(recipients), subject)
        self._log(recipients, subject, template, MailLog.Status.SENT, "")
        return MailResult(True)

    def _deliver(self, recipients, subject, html, text, cc_recipients):
        token = self._access_token()
        mime = self.build_mime_message(recipients, subject, html, text, cc_recipients)
        url = f"{GRAPH_ROOT}/users/{quote(self.config.sender_address)}/sendMail"
        response = self.session.post(
            url,
            data=base64.b64encode(mime),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            timeout=self.timeout,
        )
        if response.status_code != 202:
            raise GraphMailError(f"Graph sendMail antwortete mit {response.status_code}: {_describe(response)}")

    def build_mime_message(self, recipients, subject, html, text="", cc_recipients=None) -> bytes:
        """Baut die MIME-Nachricht mit Plaintext- und HTML-Alternative."""
        message = EmailMessage()
        message["Subject"] = sanitize_header(subject)
        message["From"] = formataddr(
            (sanitize_header(self.config.sender_name), self.config.sender_address)
        )
        message["To"] = ", ".join(recipients)
        if cc_recipients:
            message["Cc"] = ", ".join(cc_recipients)
        if self.config.reply_to:
            message["Reply-To"] = self.config.reply_to
        # Plaintext zuerst, HTML als bevorzugte Alternative dahinter.
        message.set_content(text or _html_to_text_fallback(html))
        message.add_alternative(html, subtype="html")
        return message.as_bytes()

    def _access_token(self) -> str:
        if not self.is_configured:
            raise MailNotConfigured("Mailversand ist nicht konfiguriert")

        cache_key = (self.config.tenant_id, self.config.client_id)
        now = time.monotonic()
        with _token_lock:
            cached = _token_cache.get(cache_key)
            if cached and cached[1] > now:
                return cached[0]

        response = self.session.post(
            f"{AUTHORITY}/{quote(self.config.tenant_id)}/oauth2/v2.0/token",
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": GRAPH_SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise GraphMailError(
                f"Token-Anforderung fehlgeschlagen ({response.status_code}): {_describe(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphMailError("Token-Antwort war kein gültiges JSON") from exc

        token = payload.get("access_token")
        if not token:
            raise GraphMailError("Token-Antwort enthielt kein access_token")

        try:
            expires_in = int(payload.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600

        with _token_lock:
            _token_cache[cache_key] = (token, now + max(expires_in - TOKEN_EXPIRY_MARGIN, 0))
        return token

    def _redact(self, message: str) -> str:
        """Stellt sicher, dass das Client-Secret nie in Logs oder UI landet."""
        secret = self.config.client_secret
        if secret and len(secret) >= MIN_REDACTION_LENGTH:
            message = message.replace(secret, "***")
        return message

    def _log(self, recipients, subject, template, status, error):
        try:
            MailLog.objects.create(
                recipients=", ".join(recipients),
                subject=subject[:500],
                template=template[:100],
                status=status,
                error=self._redact(error)[:MAX_ERROR_LENGTH],
            )
        except Exception:  # pragma: no cover - Protokoll darf nie blockieren
            logger.exception("Mail-Protokoll konnte nicht geschrieben werden")


def _describe(response) -> str:
    """Kurzfassung einer Fehlerantwort — ohne Zugangsdaten, gekürzt."""
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        return (getattr(response, "text", "") or "")[:MAX_ERROR_LENGTH]

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return f"{error.get('code', '')} {error.get('message', '')}".strip()[:MAX_ERROR_LENGTH]
        description = payload.get("error_description") or error or ""
        return str(description)[:MAX_ERROR_LENGTH]
    return str(payload)[:MAX_ERROR_LENGTH]


def _html_to_text_fallback(html: str) -> str:
    """Notdürftige Textvariante, falls ein Aufrufer keine mitliefert."""
    from django.utils.html import strip_tags

    return strip_tags(html or "").strip()
