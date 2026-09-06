"""Microsoft-Graph-Anbindung — aktuell ausschließlich ausgehende Mail.

Öffentliche Schnittstelle::

    from services.graph import GraphMailService, send_template_mail, mail_enabled

    GraphMailService().send("max@example.com", "Betreff", "<p>Hallo</p>", "Hallo")
    send_template_mail(user.email, "appointment_reminder", {"appointments": ...})
"""

from .client import GraphMailService, MailResult, reset_token_cache
from .exceptions import GraphMailError, MailNotConfigured
from .mail import RenderedMail, mail_enabled, render_mail, send_mail, send_template_mail

__all__ = [
    "GraphMailError",
    "GraphMailService",
    "MailNotConfigured",
    "MailResult",
    "RenderedMail",
    "mail_enabled",
    "render_mail",
    "reset_token_cache",
    "send_mail",
    "send_template_mail",
]
