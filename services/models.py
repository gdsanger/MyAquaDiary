"""Persistente Konfiguration und Protokoll der externen Services."""

import json
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.fields import EncryptedTextField
from services.eheim import (
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    ERROR_LABELS,
    KIND_CLASSICVARIO,
    KIND_EHEIM_OTHER,
    PUMP_MODE_LABELS,
    is_supported_firmware,
    normalize_mac,
)

logger = logging.getLogger(__name__)

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


class Device(models.Model):
    """Ein angebundenes Gerät im Netz des Benutzers.

    Bis das Becken-Modell im Epic liegt, hängt ein Gerät am Benutzer; die
    Zuordnung zum Becken kommt später als zusätzliches Feld dazu und ändert an
    dieser Klasse sonst nichts.

    Die Zugangsdaten liegen als JSON (``user``, ``password``) verschlüsselt in
    der Datenbank und werden weder angezeigt noch protokolliert.
    """

    class Kind(models.TextChoices):
        EHEIM_CLASSICVARIO = KIND_CLASSICVARIO, "Eheim classicVARIO+e"
        EHEIM_OTHER = KIND_EHEIM_OTHER, "Eheim (sonstiges)"
        SHELLY_PLUG = "shelly_plug", "Shelly Plug"

    #: Arten, die über die Eheim-REST-API angesprochen werden.
    EHEIM_KINDS = frozenset({Kind.EHEIM_CLASSICVARIO, Kind.EHEIM_OTHER})

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Besitzer",
        on_delete=models.CASCADE,
        related_name="devices",
    )
    kind = models.CharField("Art", max_length=30, choices=Kind.choices)
    name = models.CharField("Name", max_length=120)
    mac_address = models.CharField(
        "MAC-Adresse",
        max_length=17,
        blank=True,
        help_text="Pflicht bei Eheim-Geräten — jeder Request adressiert das Gerät darüber.",
    )
    host = models.CharField(
        "Adresse",
        max_length=200,
        blank=True,
        help_text="IP oder Hostname im lokalen Netz.",
    )
    credentials = EncryptedTextField("Zugangsdaten", blank=True, default="")
    firmware = models.CharField("Gerätesoftware", max_length=40, blank=True)
    is_active = models.BooleanField(
        "aktiv",
        default=True,
        help_text="Inaktive Geräte werden weder abgefragt noch gesteuert.",
    )
    last_seen = models.DateTimeField("zuletzt erreicht", null=True, blank=True)
    created_at = models.DateTimeField("angelegt", auto_now_add=True)

    class Meta:
        verbose_name = "Gerät"
        verbose_name_plural = "Geräte"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "mac_address"],
                condition=~models.Q(mac_address=""),
                name="unique_device_mac_per_owner",
            )
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.mac_address = normalize_mac(self.mac_address) if self.mac_address else ""
        super().save(*args, **kwargs)

    def clean(self):
        self.mac_address = normalize_mac(self.mac_address) if self.mac_address else ""
        if self.is_eheim:
            if not self.mac_address:
                raise ValidationError({"mac_address": "Eheim-Geräte brauchen eine MAC-Adresse."})
            if len(self.mac_address) != 17:
                raise ValidationError({"mac_address": "Keine gültige MAC-Adresse."})
            if not self.host:
                raise ValidationError({"host": "Ohne Adresse lässt sich das Gerät nicht erreichen."})

    # -- Zugangsdaten --------------------------------------------------------

    def set_credentials(self, user: str, password: str) -> None:
        """Zugangsdaten setzen; gespeichert wird verschlüsselt."""
        self.credentials = json.dumps({"user": user or DEFAULT_USERNAME, "password": password or ""})

    @property
    def credentials_dict(self) -> dict:
        if not self.credentials:
            return {}
        try:
            data = json.loads(self.credentials)
        except (TypeError, ValueError):
            # Nach einem Schlüsselwechsel liefert das Feld einen leeren String
            # bzw. Unsinn — das Gerät gilt dann als nicht konfiguriert.
            logger.warning("Zugangsdaten von Gerät %s sind nicht lesbar", self.pk)
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def api_user(self) -> str:
        return self.credentials_dict.get("user") or DEFAULT_USERNAME

    @property
    def api_password(self) -> str:
        return self.credentials_dict.get("password") or ""

    @property
    def uses_default_password(self) -> bool:
        """True, solange das Werkspasswort hinterlegt ist."""
        return self.api_password == DEFAULT_PASSWORD

    # -- Zustand -------------------------------------------------------------

    @property
    def is_eheim(self) -> bool:
        return self.kind in self.EHEIM_KINDS

    @property
    def firmware_supported(self):
        """True/False anhand des bekannten Softwarestands, ``None`` wenn unbekannt."""
        if not self.firmware:
            return None
        return is_supported_firmware(self.firmware)

    @property
    def latest_reading(self):
        return self.readings.first()


class DeviceReading(models.Model):
    """Eine Statusabfrage — ausgewertete Felder plus unveränderte Rohantwort.

    Die Rohantwort wird immer mitgespeichert: erweitert Eheim die Antwort oder
    belegt ein Feld anders, lässt sich die Historie nachträglich auswerten,
    ohne neu messen zu müssen.
    """

    device = models.ForeignKey(
        Device, verbose_name="Gerät", on_delete=models.CASCADE, related_name="readings"
    )
    read_at = models.DateTimeField("Zeitpunkt")
    payload = models.JSONField("Rohantwort", default=dict)

    rpm_percent = models.PositiveSmallIntegerField("Drehzahl (%)", null=True, blank=True)
    pump_mode = models.PositiveSmallIntegerField("Pumpenmodus", null=True, blank=True)
    error_code = models.PositiveSmallIntegerField("Fehlercode", null=True, blank=True)
    service_due_in = models.PositiveIntegerField("Wartung in (h)", null=True, blank=True)
    is_on = models.BooleanField("eingeschaltet", null=True, blank=True)

    class Meta:
        verbose_name = "Gerätemesswert"
        verbose_name_plural = "Gerätemesswerte"
        ordering = ["-read_at"]
        indexes = [models.Index(fields=["device", "-read_at"])]

    def __str__(self):
        return f"{self.device} – {self.read_at:%d.%m.%Y %H:%M}"

    @property
    def has_error(self) -> bool:
        return bool(self.error_code)

    @property
    def error_text(self) -> str:
        if self.error_code is None:
            return ""
        return ERROR_LABELS.get(self.error_code, f"unbekannter Fehler ({self.error_code})")

    @property
    def mode_label(self) -> str:
        if self.pump_mode is None:
            return ""
        return PUMP_MODE_LABELS.get(self.pump_mode, f"unbekannter Modus ({self.pump_mode})")

    @property
    def service_due_in_days(self):
        if self.service_due_in is None:
            return None
        return round(self.service_due_in / 24)


class DeviceEvent(models.Model):
    """Protokoll jeder schreibenden Aktion an einem Gerät.

    Die Felder sind absichtlich wie ``tanks.Event`` geschnitten
    (``occurred_at``, ``title``, ``description``): sobald das Becken-Modell im
    Epic liegt, wird derselbe Satz zusätzlich als Ereignis der Kategorie
    *Technik* am Becken abgelegt — geschrieben wird das an genau einer Stelle,
    in :func:`services.devices.record_event`.
    """

    device = models.ForeignKey(
        Device, verbose_name="Gerät", on_delete=models.CASCADE, related_name="events"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Benutzer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="device_events",
    )
    occurred_at = models.DateTimeField("Zeitpunkt", auto_now_add=True)
    action = models.CharField("Aktion", max_length=40)
    title = models.CharField("Titel", max_length=200)
    description = models.TextField("Beschreibung", blank=True)
    succeeded = models.BooleanField("erfolgreich", default=True)

    class Meta:
        verbose_name = "Geräte-Ereignis"
        verbose_name_plural = "Geräte-Ereignisse"
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["device", "-occurred_at"])]

    def __str__(self):
        return f"{self.device} – {self.title}"
