"""Formulare der Services-Administration."""

from django import forms

from .models import MailConfig


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
