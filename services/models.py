"""Persistente Konfiguration und Protokoll der externen Services."""

import hashlib
import json
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.enums import Status
from core.fields import EncryptedTextField
from services.eheim import (
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    KIND_CLASSICVARIO,
    KIND_EHEIM_OTHER,
    error_text,
    is_supported_firmware,
    mode_label,
    normalize_mac,
)
from services.shelly import (
    KIND_SHELLY_PLUG,
    generation_label,
    watt_hours_to_kilowatt_hours,
)

logger = logging.getLogger(__name__)

SINGLETON_PK = 1

#: Eine Wartung, die innerhalb dieser Frist fällig wird, gilt als „anstehend".
#: Dieselbe Spanne wie bei den Terminen (``tanks.models.UPCOMING_DAYS``); sie
#: steht hier noch einmal, damit die Gerätemodelle ohne Import aus ``tanks``
#: auskommen — der Fremdschlüssel zeigt in diese Richtung, die Abhängigkeit
#: soll es nicht auch noch tun.
MAINTENANCE_HORIZON_DAYS = 14


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
    """Ein Gerät an einem Becken — angebunden oder nur dokumentiert.

    Es gibt genau ein Gerätemodell. Ob hinter einem Gerät eine API steckt, ist
    ein Merkmal der Art (:attr:`CONNECTED_KINDS`) und keine Voraussetzung
    dafür, es überhaupt zu erfassen: ein CO₂-Nachtabschalter ohne Netzanschluss
    gehört genauso in die Geräteliste des Beckens wie ein Eheim-Filter, der
    seinen Fehlercode selbst meldet.

    Das Becken ist Pflicht. Freitext war es einmal, mit den bekannten Folgen:
    ein Tippfehler erzeugte in der Verbrauchsauswertung eine zweite Gruppe, und
    ein Gerätefehler fand den Weg zum Becken gar nicht erst.

    Die Zugangsdaten liegen als JSON (``user``, ``password``) verschlüsselt in
    der Datenbank und werden weder angezeigt noch protokolliert.
    """

    class Kind(models.TextChoices):
        # Anbindbar — hinter diesen Arten steckt eine API.
        EHEIM_CLASSICVARIO = KIND_CLASSICVARIO, "Eheim classicVARIO+e"
        EHEIM_OTHER = KIND_EHEIM_OTHER, "Eheim (sonstiges)"
        SHELLY_PLUG = KIND_SHELLY_PLUG, "Shelly Plug"
        # Nur dokumentiert — erfasst, aber nicht abfragbar und nicht schaltbar.
        FILTER = "filter", "Filter"
        HEATER = "heater", "Heizer"
        LIGHT = "light", "Beleuchtung"
        CO2 = "co2", "CO₂-Anlage"
        PUMP = "pump", "Pumpe"
        DOSER = "doser", "Dosierpumpe"
        SENSOR = "sensor", "Sensor"
        SOCKET = "socket", "Steckdose"
        OTHER = "other", "Sonstiges"

    #: Arten, die über die Eheim-REST-API angesprochen werden.
    EHEIM_KINDS = frozenset({Kind.EHEIM_CLASSICVARIO, Kind.EHEIM_OTHER})
    #: Arten, die über die lokale Shelly-API angesprochen werden.
    SHELLY_KINDS = frozenset({Kind.SHELLY_PLUG})
    #: Alles, was eine Anbindung hat: abfragbar, teils schaltbar.
    CONNECTED_KINDS = EHEIM_KINDS | SHELLY_KINDS
    #: Alles, was ``poll_devices`` periodisch abfragt.
    POLLED_KINDS = CONNECTED_KINDS
    #: Arten ohne Anbindung — reine Dokumentation, Status von Hand.
    DOCUMENTED_KINDS = frozenset(Kind) - CONNECTED_KINDS

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Besitzer",
        on_delete=models.CASCADE,
        related_name="devices",
    )
    tank = models.ForeignKey(
        "tanks.Tank",
        verbose_name="Becken",
        on_delete=models.CASCADE,
        related_name="devices",
        help_text="Becken, an dem das Gerät hängt — Grundlage der Verbrauchsauswertung "
        "und der Warnungen.",
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
    generation = models.PositiveSmallIntegerField(
        "Generation",
        null=True,
        blank=True,
        help_text="Nur bei Shelly: 1 oder 2+. Wird beim ersten Kontakt über /shelly erkannt.",
    )
    manufacturer = models.CharField("Hersteller", max_length=80, blank=True)
    model_name = models.CharField("Modell", max_length=80, blank=True)
    installed_on = models.DateField("In Betrieb seit", null=True, blank=True)
    maintenance_interval_days = models.PositiveSmallIntegerField(
        "Wartungsintervall (Tage)",
        null=True,
        blank=True,
        help_text="Leer lassen, wenn das Gerät keine wiederkehrende Wartung braucht.",
    )
    last_maintenance_on = models.DateField("Letzte Wartung", null=True, blank=True)
    status = models.CharField(
        "Status",
        max_length=10,
        choices=Status.choices,
        default=Status.OK,
        help_text="Bei angebundenen Geräten aus dem letzten Messwert fortgeschrieben, "
        "sonst eine Handeingabe.",
    )
    status_message = models.CharField("Statusmeldung", max_length=200, blank=True)
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
        if self.tank_id and self.owner_id and self.tank.owner_id != self.owner_id:
            raise ValidationError({"tank": "Das Becken gehört einem anderen Benutzer."})
        if self.is_shelly and not self.host:
            raise ValidationError({"host": "Ohne Adresse lässt sich das Gerät nicht erreichen."})
        if not self.is_eheim:
            return
        # Beide Felder gemeinsam melden, sonst schickt das Formular den
        # Benutzer zweimal hintereinander los.
        errors = {}
        if not self.mac_address:
            errors["mac_address"] = "Eheim-Geräte brauchen eine MAC-Adresse."
        elif len(self.mac_address) != 17:
            errors["mac_address"] = "Keine gültige MAC-Adresse."
        if not self.host:
            errors["host"] = "Ohne Adresse lässt sich das Gerät nicht erreichen."
        if errors:
            raise ValidationError(errors)

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
        """True, solange das Eheim-Werkspasswort hinterlegt ist.

        Nur für Eheim eine Aussage: eine Shelly-Steckdose kommt ohne gesetztes
        Passwort aus dem Karton, ein leeres Feld ist dort kein Warnzeichen.
        """
        return self.is_eheim and self.api_password == DEFAULT_PASSWORD

    # -- Zustand -------------------------------------------------------------

    @property
    def is_eheim(self) -> bool:
        return self.kind in self.EHEIM_KINDS

    @property
    def is_shelly(self) -> bool:
        return self.kind in self.SHELLY_KINDS

    @property
    def is_connected(self) -> bool:
        """True, wenn das Gerät abgefragt werden kann.

        Entscheidet darüber, ob Status, Verlauf und Steuerung überhaupt
        erscheinen — und ob :attr:`status` selbst geschrieben wird oder von
        Hand gepflegt bleibt.
        """
        return self.kind in self.CONNECTED_KINDS

    @property
    def generation_label(self) -> str:
        """Shelly-Generation im Klartext (``Gen1``/``Gen2+``)."""
        return generation_label(self.generation)

    @property
    def firmware_supported(self):
        """True/False anhand des bekannten Softwarestands, ``None`` wenn unbekannt."""
        if not self.firmware:
            return None
        return is_supported_firmware(self.firmware)

    @property
    def latest_reading(self):
        return self.readings.first()

    def update_status_from(self, reading) -> list[str]:
        """Schreibt den Status eines angebundenen Geräts aus einem Messwert fort.

        Ein Fehlercode (Rotor blockiert, Luft im Filter) ist ein kritischer
        Zustand und gehört als solcher an das Becken — sonst steht der Wert nur
        im Messwert und niemand sieht ihn. Bei Geräten ohne Anbindung bleibt
        das Feld unberührt: dort ist es eine Handeingabe.

        Zurück kommen die geänderten Feldnamen, damit der Aufrufer sie an sein
        ``update_fields`` hängen kann.
        """
        if not self.is_connected:
            return []
        if reading.has_error:
            status, message = Status.CRITICAL, reading.error_text
        else:
            status, message = Status.OK, ""
        if (self.status, self.status_message) == (status, message):
            return []
        self.status, self.status_message = status, message
        return ["status", "status_message"]

    # -- Wartung -------------------------------------------------------------

    @property
    def maintenance_due_on(self):
        """Nächster Wartungstermin, ``None`` ohne hinterlegtes Intervall."""
        if not self.maintenance_interval_days:
            return None
        reference = self.last_maintenance_on or self.installed_on
        if reference is None:
            return None
        return reference + timedelta(days=self.maintenance_interval_days)

    def maintenance_status(self, today=None):
        due = self.maintenance_due_on
        if due is None:
            return Status.UNKNOWN
        today = today or timezone.localdate()
        if due < today:
            return Status.CRITICAL
        if due <= today + timedelta(days=MAINTENANCE_HORIZON_DAYS):
            return Status.WARN
        return Status.OK

    @property
    def maintenance_status_value(self):
        """Template-freundlicher Zugriff (Templates rufen keine Argumente auf)."""
        return self.maintenance_status()


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

    # Steckdosen (Shelly). ``energy_total_wh`` ist der Zählerstand des Geräts,
    # kein Verbrauch je Zeitraum — der entsteht als Differenz zweier Messwerte
    # in services.energy.
    power_w = models.DecimalField(
        "Leistung (W)", max_digits=9, decimal_places=2, null=True, blank=True
    )
    energy_total_wh = models.DecimalField(
        "Zählerstand (Wh)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    temperature_c = models.DecimalField(
        "Gerätetemperatur (°C)", max_digits=5, decimal_places=1, null=True, blank=True
    )

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
        return error_text(self.error_code)

    @property
    def mode_label(self) -> str:
        return mode_label(self.pump_mode)

    @property
    def service_due_in_days(self):
        if self.service_due_in is None:
            return None
        return round(self.service_due_in / 24)

    @property
    def energy_total_kwh(self):
        """Zählerstand in Kilowattstunden — die Einheit auf der Stromrechnung."""
        return watt_hours_to_kilowatt_hours(self.energy_total_wh)


class DeviceEvent(models.Model):
    """Protokoll jeder schreibenden Aktion an einem Gerät.

    Die Felder sind wie ``tanks.Event`` geschnitten (``occurred_at``,
    ``title``, ``description``): derselbe Satz wird zusätzlich als Ereignis der
    Kategorie *Technik* am Becken abgelegt, damit eine Schaltaktion in der
    Beckenhistorie auftaucht und nicht nur im Geräteprotokoll. Geschrieben wird
    beides an genau einer Stelle, in :func:`services.devices.record_event`.
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


# --------------------------------------------------------------------------
# KI-Assistenz (Anthropic Claude)
# --------------------------------------------------------------------------

#: Startmodell der KI-Assistenz. Im Admin frei änderbar — deshalb ein
#: CharField und keine Auswahlliste: ein neues Modell soll keine Migration
#: kosten. Structured Outputs (erzwungenes JSON-Schema) beherrschen nicht alle
#: Modelle; siehe ``services.ai.pricing.supports_structured_output``.
DEFAULT_AI_MODEL = "claude-opus-5"


class AIConfig(models.Model):
    """Zugang und Grenzen der KI-Assistenz.

    Singleton wie :class:`MailConfig` (``pk=1``). Der API-Key liegt
    verschlüsselt in der Datenbank und wird weder angezeigt noch protokolliert
    — auch nicht in Fehlermeldungen (siehe ``services.ai.client``).

    Ohne Key ist die Anwendung vollständig benutzbar, sämtliche KI-Funktionen
    sind dann schlicht ausgeblendet.
    """

    api_key = EncryptedTextField("API-Key", blank=True, default="")
    model_name = models.CharField(
        "Modell",
        max_length=100,
        default=DEFAULT_AI_MODEL,
        help_text="Modell-ID von Anthropic, z. B. claude-opus-5, claude-sonnet-5 "
        "oder claude-haiku-4-5.",
    )
    is_enabled = models.BooleanField(
        "KI-Funktionen aktiv",
        default=True,
        help_text="Schaltet die Assistenz ab, ohne den Key zu löschen.",
    )
    monthly_token_budget = models.BigIntegerField(
        "Token-Budget je Monat",
        default=2_000_000,
        help_text="Über alle Benutzer, je Kalendermonat. 0 = ohne Begrenzung.",
    )
    per_user_daily_limit = models.IntegerField(
        "Token-Limit je Benutzer und Tag",
        default=50_000,
        help_text="0 = ohne Begrenzung.",
    )
    updated_at = models.DateTimeField("zuletzt geändert", auto_now=True)

    class Meta:
        verbose_name = "KI-Konfiguration"
        verbose_name_plural = "KI-Konfiguration"

    def __str__(self):
        return "KI-Konfiguration"

    def save(self, *args, **kwargs):
        self.pk = SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def defaults_from_env(cls) -> dict:
        """Startwerte aus ``settings.ANTHROPIC``."""
        configured = getattr(settings, "ANTHROPIC", {}) or {}
        return {
            "api_key": configured.get("API_KEY", ""),
            "model_name": configured.get("MODEL", DEFAULT_AI_MODEL),
        }

    @classmethod
    def load(cls) -> "AIConfig":
        """Gespeicherte Konfiguration oder — falls keine existiert — eine
        ungespeicherte Instanz mit den Environment-Werten."""
        existing = cls.objects.filter(pk=SINGLETON_PK).first()
        if existing is not None:
            return existing
        return cls(pk=SINGLETON_PK, **cls.defaults_from_env())

    @property
    def is_configured(self) -> bool:
        """True, wenn Anfragen an Claude gestellt werden dürfen."""
        return bool(self.is_enabled and self.api_key and self.model_name)


class AIUsageLog(models.Model):
    """Ein Eintrag je Aufruf — auch je abgelehntem.

    Grundlage der Budgetprüfung und der Kostenanzeige im Admin. Gespeichert
    werden ausschließlich Metadaten: keine Prompts, keine Antworten, kein Key.
    """

    class Action(models.TextChoices):
        IDENTIFY_ANIMAL = "identify_animal", "Tier bestimmen"
        IDENTIFY_PLANT = "identify_plant", "Pflanze bestimmen"
        PROFILE_ANIMAL = "profile_animal", "Steckbrief Tier"
        PROFILE_PLANT = "profile_plant", "Steckbrief Pflanze"
        MEASUREMENTS = "measurements", "Messwerte deuten"
        STOCKING = "stocking", "Besatz prüfen"
        TANK_REPORT = "tank_report", "Beckenbericht"
        TEST = "test", "Verbindungstest"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Benutzer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_usage",
    )
    action = models.CharField("Aktion", max_length=50, choices=Action.choices)
    # Das Modell steht am Protokolleintrag, nicht nur in der Konfiguration:
    # der Preis hängt am Modell, und die Konfiguration ändert sich.
    model_name = models.CharField("Modell", max_length=100, blank=True)
    prompt_tokens = models.IntegerField("Token Eingabe", default=0)
    completion_tokens = models.IntegerField("Token Ausgabe", default=0)
    total_cost_usd = models.DecimalField(
        "Kosten (USD)", max_digits=8, decimal_places=4, default=0
    )
    duration_ms = models.IntegerField("Dauer (ms)", default=0)
    success = models.BooleanField("erfolgreich", default=True)
    error_message = models.TextField("Fehler", blank=True)
    created_at = models.DateTimeField("Zeitpunkt", auto_now_add=True)

    class Meta:
        verbose_name = "KI-Verbrauch"
        verbose_name_plural = "KI-Verbrauch"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} – {self.total_tokens} Token"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AISuggestion(models.Model):
    """Ein KI-Vorschlag — Entwurf, kein Ergebnis.

    Eine Bestimmung oder ein Steckbrief entsteht hier mit ``verified=False``
    und wird erst durch eine ausdrückliche Bestätigung in den Katalog
    übernommen. Ein unbestätigt übernommener Steckbrief verbreitet Fehler über
    alle Benutzer — deshalb der Zwischenschritt.

    ``payload`` trägt die Felder so, wie der Katalog sie erwartet
    (``scientific_name``, ``common_name``, ``difficulty`` …). Solange die
    Katalog-Modelle im Epic noch fehlen, bleibt der bestätigte Entwurf hier
    liegen und wird beim Bestätigen übernommen, sobald der Katalog da ist
    (siehe :mod:`services.ai.catalog`).
    """

    class Kind(models.TextChoices):
        ANIMAL = "animal", "Tier"
        PLANT = "plant", "Pflanze"

    class Status(models.TextChoices):
        DRAFT = "draft", "Entwurf"
        VERIFIED = "verified", "bestätigt"
        REJECTED = "rejected", "verworfen"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Benutzer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_suggestions",
    )
    kind = models.CharField("Art", max_length=10, choices=Kind.choices)
    scientific_name = models.CharField("Wissenschaftlicher Name", max_length=160, blank=True)
    common_name = models.CharField("Deutscher Name", max_length=160, blank=True)
    confidence = models.DecimalField(
        "Konfidenz",
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Selbsteinschätzung des Modells zwischen 0 und 1 — keine Messgröße.",
    )
    reasoning = models.TextField("Begründung", blank=True)
    payload = models.JSONField("Entwurf", default=dict, blank=True)
    status = models.CharField(
        "Status", max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    catalog_ref = models.CharField(
        "Katalogeintrag",
        max_length=100,
        blank=True,
        help_text="Gesetzt, sobald der bestätigte Entwurf im Katalog gelandet ist.",
    )
    decided_at = models.DateTimeField("entschieden am", null=True, blank=True)
    created_at = models.DateTimeField("erstellt", auto_now_add=True)

    class Meta:
        verbose_name = "KI-Vorschlag"
        verbose_name_plural = "KI-Vorschläge"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status", "-created_at"])]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        """Anzeigename — der wissenschaftliche Name führt, wie im Katalog."""
        if self.scientific_name and self.common_name:
            return f"{self.scientific_name} ({self.common_name})"
        return self.scientific_name or self.common_name or "Unbenannter Vorschlag"

    @property
    def verified(self) -> bool:
        """Ein Vorschlag gilt erst als bestätigt, wenn ein Mensch ihn geprüft hat."""
        return self.status == self.Status.VERIFIED

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def confidence_percent(self):
        """Konfidenz in Prozent für die Anzeige, ``None`` wenn unbekannt."""
        if self.confidence is None:
            return None
        return round(float(self.confidence) * 100)

    @property
    def has_profile(self) -> bool:
        """True, sobald ein Steckbrief-Entwurf am Vorschlag hängt."""
        return bool(self.payload)


# --------------------------------------------------------------------------
# MCP-Server
# --------------------------------------------------------------------------

#: Erkennungszeichen am Anfang jedes Tokens. Macht einen versehentlich in einen
#: Chat kopierten Token als Zugangsdatum erkennbar — und für eine spätere
#: Suche nach geleakten Tokens greifbar.
MCP_TOKEN_PREFIX = "mad_"
#: Zufallsanteil in Bytes. 32 Byte = 256 Bit; ein Rateversuch ist damit
#: aussichtslos, weshalb der Token als schneller SHA-256 gespeichert werden darf
#: (siehe :meth:`MCPToken.hash_key`).
MCP_TOKEN_BYTES = 32
#: So viele Zeichen des Zufallsanteils bleiben im Klartext stehen, damit der
#: Benutzer in der Liste erkennt, welcher Token in welchem Client steckt.
MCP_TOKEN_HINT_CHARS = 6


class MCPTokenQuerySet(models.QuerySet):
    def usable(self):
        """Tokens, mit denen man sich gerade anmelden kann."""
        now = timezone.now()
        return self.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class MCPToken(models.Model):
    """Persönlicher Zugang eines Benutzers zum MCP-Endpunkt.

    Der Klartext existiert genau einmal — beim Anlegen, auf der Seite, die ihn
    ausgibt. Gespeichert wird nur der SHA-256-Hash: geht die Datenbank verloren,
    verliert niemand seine Becken an einen fremden KI-Client. Ein Salt (bcrypt,
    Argon2) brächte hier nichts: der Token ist kein Passwort, sondern 256 Bit
    Zufall, und ein Wörterbuchangriff darauf existiert nicht. Der schnelle Hash
    erlaubt dafür den Zugriff über einen Index statt über einen Tabellenscan.

    Der Token trägt den Benutzer — und **nur** den Benutzer. Jedes Tool arbeitet
    ausschließlich gegen dessen Becken; einen Token, der auf fremde Daten zeigt,
    gibt es nicht (siehe :mod:`services.mcp.data`).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Benutzer",
        on_delete=models.CASCADE,
        related_name="mcp_tokens",
    )
    name = models.CharField(
        "Name",
        max_length=120,
        help_text="Wofür der Token gedacht ist, z. B. „Claude Desktop, Arbeitsrechner“.",
    )
    token_hash = models.CharField("Hash", max_length=64, unique=True, editable=False)
    hint = models.CharField(
        "Erkennung",
        max_length=20,
        blank=True,
        editable=False,
        help_text="Die ersten Zeichen des Tokens — nur zur Wiedererkennung in der Liste.",
    )
    allow_write = models.BooleanField(
        "darf schreiben",
        default=False,
        help_text="Ohne Haken kann der Token ausschließlich lesen. Ein Token für "
        "reine Auswertungen braucht kein Schreibrecht.",
    )
    created_at = models.DateTimeField("angelegt", auto_now_add=True)
    last_used_at = models.DateTimeField("zuletzt benutzt", null=True, blank=True)
    expires_at = models.DateTimeField(
        "gültig bis",
        null=True,
        blank=True,
        help_text="Leer = unbegrenzt gültig.",
    )
    revoked_at = models.DateTimeField("widerrufen am", null=True, blank=True)

    objects = MCPTokenQuerySet.as_manager()

    class Meta:
        verbose_name = "MCP-Token"
        verbose_name_plural = "MCP-Tokens"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.name} ({self.user})"

    # -- Anlegen und Prüfen ---------------------------------------------------

    @staticmethod
    def hash_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, user, name: str, **fields) -> tuple["MCPToken", str]:
        """Legt einen Token an und gibt ihn zusammen mit dem Klartext zurück.

        Der Klartext wird nirgends gespeichert und nirgends protokolliert — der
        Aufrufer zeigt ihn einmal an, danach ist er weg.
        """
        key = MCP_TOKEN_PREFIX + secrets.token_urlsafe(MCP_TOKEN_BYTES)
        token = cls.objects.create(
            user=user,
            name=name,
            token_hash=cls.hash_key(key),
            hint=key[: len(MCP_TOKEN_PREFIX) + MCP_TOKEN_HINT_CHARS],
            **fields,
        )
        return token, key

    @classmethod
    def resolve(cls, key: str) -> "MCPToken | None":
        """Der zu einem Klartext gehörende, benutzbare Token — sonst ``None``.

        Widerrufene und abgelaufene Tokens sind hier bereits aussortiert; der
        Aufrufer bekommt keine Auskunft darüber, welcher der beiden Fälle
        vorlag oder ob es den Token je gab.
        """
        if not key:
            return None
        return (
            cls.objects.usable()
            .select_related("user")
            .filter(token_hash=cls.hash_key(key))
            .first()
        )

    def touch(self) -> None:
        """Hält fest, dass der Token gerade benutzt wurde."""
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    def revoke(self) -> None:
        """Entzieht den Token. Zurücknehmen lässt sich das nicht — ein
        widerrufener Token ist verbrannt, der Client bekommt einen neuen."""
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])

    # -- Zustand --------------------------------------------------------------

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_usable(self) -> bool:
        return not (self.is_revoked or self.is_expired)

    @property
    def status_label(self) -> str:
        if self.is_revoked:
            return "widerrufen"
        if self.is_expired:
            return "abgelaufen"
        return "aktiv"

    @property
    def access_label(self) -> str:
        return "lesen und schreiben" if self.allow_write else "nur lesen"


class MCPAccessLog(models.Model):
    """Protokoll der schreibenden MCP-Aufrufe — auch der abgewiesenen.

    Wer später eine unplausible Messreihe findet, soll erkennen können, woher
    sie kam: welcher Token, welches Werkzeug, welche Parameter, welcher
    Datensatz. Lesende Aufrufe stehen bewusst nicht hier — sie verändern nichts,
    und ein Protokoll jeder Abfrage wäre eine Bewegungsdatenbank über den
    eigenen Benutzer, kein Sicherheitsgewinn.

    ``arguments`` enthält die Parameter des Aufrufs so, wie der Client sie
    geschickt hat. Über MCP laufen keine Zugangsdaten, deshalb ist das
    unproblematisch.
    """

    token = models.ForeignKey(
        MCPToken,
        verbose_name="Token",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_log",
    )
    # Der Benutzer steht zusätzlich am Eintrag: ein gelöschter Token soll die
    # Herkunft eines Datensatzes nicht mit ins Grab nehmen.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Benutzer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mcp_access_log",
    )
    token_name = models.CharField("Token-Name", max_length=120, blank=True)
    tool = models.CharField("Werkzeug", max_length=60)
    arguments = models.JSONField("Parameter", default=dict, blank=True)
    object_ref = models.CharField(
        "Datensatz",
        max_length=100,
        blank=True,
        help_text="Angelegter Datensatz, z. B. tanks.Measurement:12.",
    )
    succeeded = models.BooleanField("erfolgreich", default=True)
    error_message = models.TextField("Fehler", blank=True)
    created_at = models.DateTimeField("Zeitpunkt", auto_now_add=True)

    class Meta:
        verbose_name = "MCP-Protokoll"
        verbose_name_plural = "MCP-Protokoll"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.tool} – {self.token_name or 'unbekannter Token'}"
