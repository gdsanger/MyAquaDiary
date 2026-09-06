"""Rendern und Versenden der Mailtemplates.

Zu jedem Template gehören drei Dateien unter ``templates/mail/``:

* ``<name>.subject.txt`` — die Betreffzeile
* ``<name>.html`` — die HTML-Variante (Table-Layout, heller Hintergrund)
* ``<name>.txt`` — die Plaintext-Variante

Beide Body-Varianten gehen immer gemeinsam raus.
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError
from django.template.loader import render_to_string

from ..models import MailConfig
from .client import GraphMailService, MailResult, sanitize_header

logger = logging.getLogger(__name__)

TEMPLATE_DIR = "mail"


@dataclass(frozen=True)
class RenderedMail:
    subject: str
    html: str
    text: str


def base_context(extra: dict | None = None) -> dict:
    """Kontext, den jedes Mailtemplate erhält."""
    context = {
        "site_name": "MyAquaDiary",
        "site_url": getattr(settings, "SITE_URL", ""),
    }
    context.update(extra or {})
    return context


def render_mail(template: str, context: dict | None = None) -> RenderedMail:
    """Rendert Betreff, HTML- und Plaintext-Variante eines Mailtemplates."""
    full_context = base_context(context)
    return RenderedMail(
        subject=sanitize_header(render_to_string(f"{TEMPLATE_DIR}/{template}.subject.txt", full_context)),
        html=render_to_string(f"{TEMPLATE_DIR}/{template}.html", full_context),
        text=render_to_string(f"{TEMPLATE_DIR}/{template}.txt", full_context).strip() + "\n",
    )


def mail_enabled() -> bool:
    """True, wenn der Mailversand nutzbar ist.

    Fängt auch Datenbankfehler ab (z. B. noch nicht migrierte Tabelle), damit
    Templates und Views die Mailfunktionen gefahrlos ausblenden können.
    """
    try:
        return MailConfig.load().is_configured
    except DatabaseError:
        logger.warning("Mail-Konfiguration konnte nicht gelesen werden — Mailversand inaktiv")
        return False


def send_mail(to, subject, html, text="", **kwargs) -> MailResult:
    """Bequemer Einzeiler für den Versand ohne Template."""
    return GraphMailService().send(to, subject, html, text, **kwargs)


def send_template_mail(to, template: str, context: dict | None = None, **kwargs) -> MailResult:
    """Rendert ein Mailtemplate und versendet es.

    Schlägt schon das Rendern fehl, wird das protokolliert und ein negatives
    Ergebnis zurückgegeben — auch hier bricht kein Aufrufer ab.
    """
    try:
        rendered = render_mail(template, context)
    except Exception:
        logger.exception("Mailtemplate %s konnte nicht gerendert werden", template)
        return MailResult(False, f"Template {template} konnte nicht gerendert werden")
    return GraphMailService().send(
        to, rendered.subject, rendered.html, rendered.text, template=template, **kwargs
    )
