from django import forms
from django.forms import inlineformset_factory

from .models import Measurement, MeasurementValue, Photo, Tank, TankParameterTarget


class TankForm(forms.ModelForm):
    class Meta:
        model = Tank
        exclude = ["owner", "slug", "cover_photo"]
        widgets = {
            "started_on": forms.DateInput(attrs={"type": "date"}),
            "shut_down_on": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "substrate": forms.Textarea(attrs={"rows": 2}),
            "hardscape": forms.Textarea(attrs={"rows": 2}),
            "filtration": forms.Textarea(attrs={"rows": 2}),
            "lighting": forms.Textarea(attrs={"rows": 2}),
            "co2": forms.Textarea(attrs={"rows": 2}),
            "fertilization": forms.Textarea(attrs={"rows": 2}),
        }


TankPhotoFormSet = inlineformset_factory(
    Tank,
    Photo,
    fields=["image", "caption", "taken_on", "position"],
    extra=1,
    can_delete=True,
)


class TankParameterTargetForm(forms.ModelForm):
    class Meta:
        model = TankParameterTarget
        fields = ["target", "minimum", "maximum"]


class MeasurementForm(forms.ModelForm):
    class Meta:
        model = Measurement
        fields = ["measured_at", "note", "source"]
        widgets = {
            "measured_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "note": forms.Textarea(attrs={"rows": 2}),
        }


class MeasurementValueForm(forms.ModelForm):
    class Meta:
        model = MeasurementValue
        fields = ["parameter", "value", "below_detection"]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("DELETE"):
            return cleaned_data
        parameter = cleaned_data.get("parameter")
        if parameter is None:
            return cleaned_data
        value = cleaned_data.get("value")
        below_detection = cleaned_data.get("below_detection")
        if value is not None and below_detection:
            self.add_error(None, "Entweder Messwert oder „n.n.“ angeben, nicht beides.")
        elif value is None and not below_detection:
            self.add_error(None, "Messwert oder „n.n.“ ist erforderlich.")
        elif below_detection and not parameter.supports_below_detection:
            self.add_error("below_detection", "Dieser Parameter unterstützt „n.n.“ nicht.")
        return cleaned_data


MeasurementPhotoFormSet = inlineformset_factory(
    Measurement,
    Photo,
    fields=["image", "caption", "taken_on", "position"],
    extra=1,
    can_delete=True,
)
