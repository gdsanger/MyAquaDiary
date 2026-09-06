from django import forms
from django.forms import inlineformset_factory

from .models import Photo, Tank, TankParameterTarget


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
