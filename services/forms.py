"""Formulare der Services-Administration und der Geräteverwaltung."""

from django import forms

from .eheim import DEFAULT_PASSWORD, DEFAULT_USERNAME
from .models import Device, MailConfig
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


class BootstrapMixin:
    """Setzt die Bootstrap-Klassen auf allen Widgets.

    Die Anwendung nutzt Bootstrap 5.3 ohne crispy-forms; die Klassen einmal
    hier zu setzen ist weniger Wiederholung als in jedem Template.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")


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


class DeviceForm(BootstrapMixin, forms.ModelForm):
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
        fields = ["name", "kind", "tank_label", "mac_address", "host", "is_active"]

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


class ShellyDeviceForm(BootstrapMixin, forms.ModelForm):
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
        fields = ["name", "host", "tank_label", "is_active"]
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
    """Schaltbare Aktionen einer Geräteart — leer heißt: keine Steuerung."""
    if device.is_shelly:
        return SHELLY_CONTROLS
    if device.kind == Device.Kind.EHEIM_CLASSICVARIO:
        return EHEIM_CONTROLS
    return {}
