"""Persistente Konfiguration und Protokoll der externen Services."""

from django.conf import settings
from django.db import models

from core.fields import EncryptedTextField

SINGLETON_PK = 1


class MailConfig(models.Model):
    """Zugangsdaten für den ausgehenden Mailversand über Microsoft Graph.

    Singleton: es gibt genau einen Datensatz (``pk=1``). Existiert noch keiner,
    liefert :meth:`load` eine ungespeicherte Instanz mit den Werten aus
    ``settings.GRAPH_MAIL`` — eine reine Environment-Konfiguration ist damit
    ebenso möglich wie die Pflege über den Admin.

    Das Client-Secret liegt verschlüsselt in der Datenbank und wird weder in
    ``__str__`` noch in Log- oder Fehlermeldungen ausgegeben.
    """

    tenant_id = models.CharField("Tenant-ID", max_length=255, blank=True)
    client_id = models.CharField("Client-ID", max_length=255, blank=True)
    client_secret = EncryptedTextField("Client-Secret", blank=True, default="")
    sender_address = models.EmailField(
        "Absenderadresse",
        blank=True,
        help_text="Postfach, aus dem gesendet wird. Die App-Registrierung braucht "
        "darauf die Application-Permission Mail.Send.",
    )
    sender_name = models.CharField("Absendername", max_length=255, blank=True)
    reply_to = models.EmailField("Antwortadresse", blank=True)
    is_active = models.BooleanField(
        "Mailversand aktiv",
        default=True,
        help_text="Deaktiviert den Versand, ohne die Zugangsdaten zu löschen.",
    )
    updated_at = models.DateTimeField("zuletzt geändert", auto_now=True)

    class Meta:
        verbose_name = "Mail-Konfiguration"
        verbose_name_plural = "Mail-Konfiguration"

    def __str__(self):
        return "Mail-Konfiguration"

    def save(self, *args, **kwargs):
        self.pk = SINGLETON_PK
        super().save(*args, **kwargs)
        self._invalidate_token_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        self._invalidate_token_cache()
        return result

    @staticmethod
    def _invalidate_token_cache():
        # Lazy, um einen Import-Zyklus models -> graph -> models zu vermeiden.
        from services.graph.client import reset_token_cache

        reset_token_cache()

    @classmethod
    def defaults_from_env(cls) -> dict:
        """Startwerte aus ``settings.GRAPH_MAIL``."""
        configured = getattr(settings, "GRAPH_MAIL", {}) or {}
        return {
            "tenant_id": configured.get("TENANT_ID", ""),
            "client_id": configured.get("CLIENT_ID", ""),
            "client_secret": configured.get("CLIENT_SECRET", ""),
            "sender_address": configured.get("SENDER_ADDRESS", ""),
            "sender_name": configured.get("SENDER_NAME", ""),
            "reply_to": configured.get("REPLY_TO", ""),
        }

    @classmethod
    def load(cls) -> "MailConfig":
        """Gespeicherte Konfiguration oder — falls keine existiert — eine
        ungespeicherte Instanz mit den Environment-Werten."""
        existing = cls.objects.filter(pk=SINGLETON_PK).first()
        if existing is not None:
            return existing
        return cls(pk=SINGLETON_PK, **cls.defaults_from_env())

    @property
    def is_configured(self) -> bool:
        """True, wenn alle Pflichtangaben für einen Versand vorliegen."""
        return bool(
            self.is_active
            and self.tenant_id
            and self.client_id
            and self.client_secret
            and self.sender_address
        )


class MailLog(models.Model):
    """Protokoll je Sendeversuch — bewusst ohne Mailinhalt, nur Metadaten."""

    class Status(models.TextChoices):
        SENT = "sent", "versendet"
        FAILED = "failed", "fehlgeschlagen"
        SKIPPED = "skipped", "übersprungen"

    created_at = models.DateTimeField("Zeitpunkt", auto_now_add=True)
    recipients = models.TextField("Empfänger")
    subject = models.CharField("Betreff", max_length=500, blank=True)
    template = models.CharField("Template", max_length=100, blank=True)
    status = models.CharField("Status", max_length=10, choices=Status.choices)
    error = models.TextField("Fehler", blank=True)

    class Meta:
        verbose_name = "Mail-Protokoll"
        verbose_name_plural = "Mail-Protokoll"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self):
        return f"{self.get_status_display()}: {self.subject or '(ohne Betreff)'}"
