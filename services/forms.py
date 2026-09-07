"""Formulare der Services-Administration und der Geräteverwaltung."""

from datetime import datetime, time

from django import forms
from django.conf import settings
from django.utils import timezone

from core.forms import BootstrapMixin, DateField
from tanks.models import Tank

from .eheim import DEFAULT_PASSWORD, DEFAULT_USERNAME
from .models import (
    AIConfig,
    AISuggestion,
    Device,
    DeviceDocument,
    DeviceLink,
    DeviceSpec,
    MailConfig,
    MCPToken,
    default_token_expiry,
)
from .shelly import GEN2_USERNAME


class MailConfigForm(forms.ModelForm):
    """Pflege der Graph-Zugangsdaten.

    Das Client-Secret wird nie in das Formular zurückgeschrieben. Bleibt das
    Feld leer, behält der gespeicherte Wert seine Gültigkeit.
    """

    client_secret = forms.CharField(
        label="Client-Secret",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Wird verschlüsselt gespeichert und nie angezeigt. Leer lassen, "
        "um das gespeicherte Secret beizubehalten.",
    )

    class Meta:
        model = MailConfig
        fields = [
            "is_active",
            "tenant_id",
            "client_id",
            "client_secret",
            "sender_address",
            "sender_name",
            "reply_to",
        ]

    def clean_client_secret(self):
        value = self.cleaned_data.get("client_secret", "")
        if not value and self.instance.pk:
            return self.instance.client_secret
        return value


class TestMailForm(forms.Form):
    """Empfänger für die Testmail aus dem Admin."""

    recipient = forms.EmailField(label="Empfänger", widget=forms.EmailInput(attrs={"size": 40}))


class AIConfigForm(forms.ModelForm):
    """Pflege des Claude-Zugangs.

    Der API-Key wird nie in das Formular zurückgeschrieben. Bleibt das Feld
    leer, behält der gespeicherte Key seine Gültigkeit.
    """

    api_key = forms.CharField(
        label="API-Key",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Wird verschlüsselt gespeichert und nie angezeigt. Leer lassen, "
        "um den gespeicherten Key beizubehalten.",
    )

    class Meta:
        model = AIConfig
        fields = [
            "is_enabled",
            "api_key",
            "model_name",
            "monthly_token_budget",
            "per_user_daily_limit",
        ]

    def clean_api_key(self):
        value = self.cleaned_data.get("api_key", "")
        if not value and self.instance.pk:
            return self.instance.api_key
        return value


DEFAULT_PASSWORD_HINT = (
    "Werksseitig lautet der Zugang api / admin. Ein unverändertes Standardpasswort im "
    "LAN ist kein guter Zustand — nach dem Anlegen bitte über „Zugangsdaten ändern“ "
    "ein eigenes Passwort setzen."
)


class DeviceDiscoveryForm(BootstrapMixin, forms.Form):
    """Zugang zu einem Eheim-Gerät, über das das Mesh durchsucht wird."""

    host = forms.CharField(
        label="Adresse",
        max_length=200,
        help_text="IP oder Hostname eines Eheim-Geräts im LAN. Es antwortet für das ganze Mesh.",
    )
    username = forms.CharField(label="Benutzer", max_length=100, initial=DEFAULT_USERNAME)
    password = forms.CharField(
        label="Passwort",
        max_length=200,
        initial=DEFAULT_PASSWORD,
        widget=forms.PasswordInput(render_value=True),
        help_text=DEFAULT_PASSWORD_HINT,
    )


#: Felder, die jedes Gerät beschreiben — mit Anbindung wie ohne.
DOCUMENTATION_FIELDS = [
    "manufacturer",
    "model_name",
    "installed_on",
    "maintenance_interval_days",
    "last_maintenance_on",
]

#: Kaufmännische Stammdaten. Stehen an jedem Gerät, weil die Frage „was hat das
#: gekostet und wie lange ist Garantie drauf" unabhängig davon ist, ob hinter
#: dem Gerät eine API steckt.
COMMERCIAL_FIELDS = [
    "serial_number",
    "supplier",
    "purchased_on",
    "purchase_price",
    "warranty_until",
]

#: Typisierte technische Daten — nur das, womit die Anwendung rechnet.
TECHNICAL_FIELDS = ["power_watts", "flow_rate_lph", "daily_runtime_hours"]


class TankScopedDeviceForm(BootstrapMixin, forms.ModelForm):
    """Basis aller Geräteformulare: das Becken ist Pflicht und ist ein eigenes.

    Die Auswahl steht auf ``Tank.objects.for_user()`` — ein fremdes Becken
    taucht nicht auf und wird beim Absenden abgewiesen, auch bei einer von Hand
    geschickten Kennung. Ohne Benutzer (Django-Admin) bleibt die volle Auswahl
    stehen; dort ist der Besitzer ein Feld des Formulars.
    """

    installed_on = DateField(label="In Betrieb seit", required=False)
    last_maintenance_on = DateField(label="Letzte Wartung", required=False)
    purchased_on = DateField(label="Kaufdatum", required=False)
    warranty_until = DateField(label="Garantie bis", required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        owner = user or (self.instance.owner if self.instance.owner_id else None)
        if owner is not None and "tank" in self.fields:
            self.fields["tank"].queryset = Tank.objects.for_user(owner)


class DeviceForm(TankScopedDeviceForm):
    """Anlegen eines Geräts — meist vorbelegt aus der Mesh-Suche.

    Die Zugangsdaten werden als JSON verschlüsselt im Feld ``credentials``
    abgelegt; das Passwort wird nie in das Formular zurückgeschrieben.
    """

    username = forms.CharField(label="Benutzer", max_length=100, initial=DEFAULT_USERNAME)
    password = forms.CharField(
        label="Passwort",
        max_length=200,
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leer lassen, um ein gespeichertes Passwort beizubehalten.",
    )

    class Meta:
        model = Device
        fields = ["name", "kind", "tank", "mac_address", "host", "is_active",
                  *DOCUMENTATION_FIELDS, *COMMERCIAL_FIELDS, *TECHNICAL_FIELDS]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["username"].initial = self.instance.api_user

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        if not password and self.instance.pk:
            return self.instance.api_password
        return password

    def save(self, commit=True):
        device = super().save(commit=False)
        device.set_credentials(self.cleaned_data["username"], self.cleaned_data.get("password", ""))
        if commit:
            device.save()
        return device


class DevicePasswordForm(BootstrapMixin, forms.Form):
    """Neues Gerätepasswort (``POST /changeauth``)."""

    password = forms.CharField(
        label="Neues Passwort",
        min_length=4,
        max_length=200,
        widget=forms.PasswordInput(render_value=False),
    )
    password_repeat = forms.CharField(
        label="Wiederholung",
        max_length=200,
        widget=forms.PasswordInput(render_value=False),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned["password"] != cleaned.get("password_repeat"):
            raise forms.ValidationError("Die beiden Passwörter stimmen nicht überein.")
        if cleaned.get("password") == DEFAULT_PASSWORD:
            raise forms.ValidationError("Das Werkspasswort ist als neues Passwort nicht zulässig.")
        return cleaned


class SpeedField(forms.IntegerField):
    """Drehzahl in Prozent — die API kennt nur 0–100."""

    def __init__(self, label, **kwargs):
        kwargs.setdefault("min_value", 0)
        kwargs.setdefault("max_value", 100)
        super().__init__(label=label, **kwargs)


class ManualModeForm(BootstrapMixin, forms.Form):
    """Manueller Modus (Pumpenmodus 16)."""

    speed_percent = SpeedField("Drehzahl (%)", initial=70)


class BioModeForm(BootstrapMixin, forms.Form):
    """Bio-Modus (Pumpenmodus 4) mit Tag- und Nachtphase.

    Die Uhrzeiten werden hier als Uhrzeiten eingegeben; die Umrechnung in
    Minuten seit Mitternacht macht die Service-Schicht.
    """

    day_speed = SpeedField("Drehzahl Tag (%)", initial=80)
    night_speed = SpeedField("Drehzahl Nacht (%)", initial=40)
    day_start = forms.TimeField(label="Tag ab", initial="11:00", widget=forms.TimeInput(
        attrs={"type": "time"}, format="%H:%M"))
    night_start = forms.TimeField(label="Nacht ab", initial="23:00", widget=forms.TimeInput(
        attrs={"type": "time"}, format="%H:%M"))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("day_start") and cleaned.get("day_start") == cleaned.get("night_start"):
            raise forms.ValidationError("Tag- und Nachtphase dürfen nicht zur selben Zeit beginnen.")
        return cleaned


class PulseModeForm(BootstrapMixin, forms.Form):
    """Pulse-Modus (Pumpenmodus 8)."""

    high_speed = SpeedField("Drehzahl hoch (%)", initial=80)
    high_seconds = forms.IntegerField(label="Dauer hoch (s)", min_value=1, max_value=3600, initial=30)
    low_speed = SpeedField("Drehzahl niedrig (%)", initial=40)
    low_seconds = forms.IntegerField(label="Dauer niedrig (s)", min_value=1, max_value=3600, initial=30)


class ManualDeviceForm(TankScopedDeviceForm):
    """Ein Gerät ohne Anbindung — Filter, Heizer, CO₂-Anlage, Beleuchtung.

    Nicht jedes Gerät hängt am Netz, und nicht jedes soll es müssen: eine
    Heizung ohne WLAN gehört genauso in die Geräteliste des Beckens. Erfasst
    wird sie als reiner Dokumentationseintrag, mit Wartungsintervall und einem
    Status, den hier ein Mensch setzt — bei angebundenen Geräten schreibt ihn
    die Anwendung selbst fort.
    """

    class Meta:
        model = Device
        fields = ["name", "kind", "tank", *DOCUMENTATION_FIELDS, *COMMERCIAL_FIELDS,
                  *TECHNICAL_FIELDS, "status", "status_message", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [
            (value, label)
            for value, label in Device.Kind.choices
            if value in Device.DOCUMENTED_KINDS
        ]
        self.fields["kind"].initial = Device.Kind.OTHER


class ShellyDeviceForm(TankScopedDeviceForm):
    """Anlegen einer Shelly-Steckdose über ihre Adresse.

    Es gibt keine Mesh-Suche wie bei Eheim: eine Shelly-Steckdose ist im LAN
    für sich allein erreichbar. Angegeben wird deshalb die Adresse; Generation,
    Modell und Softwarestand liest die Anwendung selbst über ``/shelly``.

    Zugangsdaten sind optional — im Auslieferungszustand ist die lokale API
    offen; erst ein am Gerät gesetzter Login macht sie nötig.
    """

    username = forms.CharField(
        label="Benutzer",
        max_length=100,
        required=False,
        initial=GEN2_USERNAME,
        help_text="Nur nötig, wenn am Gerät ein Login gesetzt ist. Gen2 kennt nur „admin“.",
    )
    password = forms.CharField(
        label="Passwort",
        max_length=200,
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leer lassen, wenn am Gerät kein Login gesetzt ist.",
    )

    class Meta:
        model = Device
        fields = ["name", "host", "tank", "is_active", *DOCUMENTATION_FIELDS,
                  *COMMERCIAL_FIELDS, *TECHNICAL_FIELDS]
        help_texts = {"host": "IP oder Hostname der Steckdose im lokalen Netz."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.kind = Device.Kind.SHELLY_PLUG
        # Am Modell ist die Adresse optional (Eheim-Geräte im Mesh brauchen
        # keine eigene); für eine Steckdose ist sie der ganze Zugang.
        self.fields["host"].required = True
        if self.instance.pk:
            self.fields["username"].initial = self.instance.api_user

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        if not password and self.instance.pk:
            return self.instance.api_password
        return password

    def save(self, commit=True):
        device = super().save(commit=False)
        device.kind = Device.Kind.SHELLY_PLUG
        device.set_credentials(
            self.cleaned_data.get("username") or GEN2_USERNAME,
            self.cleaned_data.get("password", ""),
        )
        if commit:
            device.save()
        return device


class DeviceCoverForm(BootstrapMixin, forms.Form):
    """Titelbild eines Geräts — hochladen oder ersetzen.

    Kein ``ModelForm``: gespeichert wird über
    :meth:`core.images.CoverImageMixin.set_cover`, damit das bisherige Bild
    dabei aus dem Speicher verschwindet. Ein Formular über dem ganzen Gerät
    würde zudem bei jedem Bildwechsel die Zugangsdaten neu verschlüsseln.

    ``accept`` bietet auf dem Telefon Kamera und Galerie an; dass die Datei
    wirklich ein Bild ist, prüft Pillow über das ``ImageField``.
    """

    cover_image = forms.ImageField(
        label="Titelbild",
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}),
        help_text="Ein Bild des Geräts, an dem es in der Liste zu erkennen ist.",
    )


class DeviceSpecForm(BootstrapMixin, forms.ModelForm):
    """Eine freie technische Angabe."""

    class Meta:
        model = DeviceSpec
        fields = ["label", "value", "unit", "position"]


class DeviceLinkForm(BootstrapMixin, forms.ModelForm):
    """Ein Verweis am Gerät."""

    class Meta:
        model = DeviceLink
        fields = ["title", "url", "position"]


class DeviceDocumentForm(BootstrapMixin, forms.ModelForm):
    """Ein Dokument am Gerät.

    Geprüft werden Endung und Größe: die Endung über den Validator am Modell,
    die Größe hier. Beides ist keine Sicherheitsmaßnahme für sich — die liegt
    darin, dass die Datei nicht öffentlich ausgeliefert wird —, sondern hält
    die Ablage sauber und fängt den versehentlich gewählten Film ab, bevor er
    im Speicher landet.
    """

    class Meta:
        model = DeviceDocument
        fields = ["title", "kind", "file"]

    def clean_file(self):
        upload = self.cleaned_data["file"]
        limit = settings.DEVICE_DOCUMENT_MAX_BYTES
        if upload.size > limit:
            raise forms.ValidationError(
                f"Die Datei ist größer als {limit // (1024 * 1024)} MB."
            )
        return upload


#: Aktion -> (Formularklasse, Anzeigetext). Aktionen ohne Parameter (ein/aus)
#: haben kein Formular.
EHEIM_CONTROLS = {
    "on": (None, "Filter einschalten"),
    "off": (None, "Filter ausschalten"),
    "manual": (ManualModeForm, "Manueller Modus"),
    "bio": (BioModeForm, "Bio-Modus"),
    "pulse": (PulseModeForm, "Pulse-Modus"),
}

#: Eine Steckdose kann genau zwei Dinge — und mehr soll sie hier auch nicht
#: können. Zeitpläne und Automatik kann der Shelly selbst besser.
SHELLY_CONTROLS = {
    "on": (None, "Steckdose einschalten"),
    "off": (None, "Steckdose ausschalten"),
}


def controls_for(device) -> dict:
    """Schaltbare Aktionen einer Geräteart — leer heißt: keine Steuerung.

    Ein Gerät ohne Anbindung landet hier ebenso im leeren Ergebnis wie ein
    angebundenes ohne Schaltbefehle: es gibt nichts, wohin ein Befehl ginge.
    """
    if device.is_shelly:
        return SHELLY_CONTROLS
    if device.kind == Device.Kind.EHEIM_CLASSICVARIO:
        return EHEIM_CONTROLS
    return {}


class IdentifyForm(BootstrapMixin, forms.Form):
    """Foto für eine Bestimmung.

    Das Bild wird nicht gespeichert: es geht verkleinert an Claude und ist
    danach wieder weg. Was bleibt, ist der Vorschlag — und den bestätigt der
    Benutzer selbst.
    """

    kind = forms.ChoiceField(label="Was ist zu sehen?", choices=AISuggestion.Kind.choices)
    photo = forms.ImageField(
        label="Foto",
        help_text="Wird vor dem Versand verkleinert. Je schärfer und näher, desto besser.",
    )
    notes = forms.CharField(
        label="Beobachtung (optional)",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Größe, Verhalten, Fundort — alles, was das Bild nicht zeigt.",
    )


class CandidateForm(forms.Form):
    """Der übernommene Kandidat einer Bestimmung.

    Die Bestimmung selbst wird nicht zwischengespeichert; der ausgewählte
    Kandidat kommt aus den versteckten Feldern der Ergebnisliste zurück.
    """

    kind = forms.ChoiceField(choices=AISuggestion.Kind.choices)
    scientific_name = forms.CharField(max_length=160, required=False)
    common_name = forms.CharField(max_length=160, required=False)
    confidence = forms.FloatField(min_value=0, max_value=1, required=False)
    reasoning = forms.CharField(max_length=2000, required=False)

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("scientific_name") or cleaned.get("common_name")):
            raise forms.ValidationError("Ohne Namen lässt sich kein Vorschlag ablegen.")
        return cleaned


class MCPTokenForm(BootstrapMixin, forms.Form):
    """Ein neuer Zugang zum MCP-Endpunkt.

    Kein ModelForm: der Token entsteht nicht aus Formularfeldern, sondern in
    :meth:`services.models.MCPToken.issue` — dort, wo auch der Klartext
    entsteht, den es genau einmal zu sehen gibt.

    Schreibrecht ist bewusst nicht vorbelegt. Ein Zugang, der nur auswerten
    soll, braucht keines, und ein Haken, den man setzen muss, wird bewusster
    gesetzt als einer, den man wegnehmen müsste.

    Das Ablaufdatum dagegen **ist** vorbelegt und lässt sich nicht leeren: Der
    Token steht in der Adresse des Clients und ist damit schwerer geheim zu
    halten als einer in einem Header. Ein Zugang, an dessen Ende niemand denken
    muss, ist die einfachste Gegenmaßnahme.
    """

    name = forms.CharField(
        label="Name",
        max_length=120,
        help_text="Wofür der Zugang gedacht ist, z. B. „Claude Desktop, Arbeitsrechner“.",
    )
    allow_write = forms.BooleanField(
        label="Darf schreiben",
        required=False,
        help_text="Ohne Haken kann der Zugang Messreihen, Ereignisse und Besatz "
        "nur lesen — nichts anlegen.",
    )
    expires_at = forms.DateField(
        label="Gültig bis",
        # ``format`` ist hier nicht kosmetisch: ein ``<input type="date">``
        # versteht ausschließlich ISO, die deutsche Lokalisierung würde den
        # vorbelegten Wert sonst verschlucken.
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Danach ist der Zugang von selbst wertlos. Ein neuer ist "
        "schnell angelegt — lieber kurz als unbegrenzt.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expires_at"].initial = default_token_expiry()

    def clean_expires_at(self):
        """Das Ablaufdatum gilt bis zum Ende des gewählten Tages."""
        value = self.cleaned_data.get("expires_at")
        if value is None:
            return None
        if value < timezone.localdate():
            raise forms.ValidationError("Das Ablaufdatum liegt in der Vergangenheit.")
        return timezone.make_aware(
            datetime.combine(value, time.max), timezone.get_current_timezone()
        )

    def issue(self, user):
        """Legt den Token an und gibt ihn mit seinem Klartext zurück."""
        return MCPToken.issue(
            user,
            self.cleaned_data["name"],
            allow_write=self.cleaned_data["allow_write"],
            expires_at=self.cleaned_data["expires_at"],
        )
