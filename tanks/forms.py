from django import forms
from django.forms import inlineformset_factory

from .models import (
    Event,
    MaintenanceSchedule,
    Measurement,
    MeasurementValue,
    Photo,
    Tank,
    TankAnimal,
    TankAnimalMovement,
    TankParameterTarget,
    TankPlant,
)


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


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ["occurred_at", "category", "title", "description", "water_changed_l"]
        widgets = {
            "occurred_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


EventPhotoFormSet = inlineformset_factory(
    Event,
    Photo,
    fields=["image", "caption", "taken_on", "position"],
    extra=1,
    can_delete=True,
)


class TankAnimalForm(forms.ModelForm):
    class Meta:
        model = TankAnimal
        fields = [
            "animal",
            "label",
            "status",
            "quantity",
            "quantity_male",
            "quantity_female",
            "added_on",
            "origin",
            "note",
        ]
        widgets = {
            "added_on": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }


class TankAnimalUpdateForm(TankAnimalForm):
    """Ohne `quantity` — der Bestand wird ausschließlich über gebuchte
    Bewegungen fortgeschrieben, nicht durch direktes Überschreiben."""

    class Meta(TankAnimalForm.Meta):
        fields = [field for field in TankAnimalForm.Meta.fields if field != "quantity"]


class TankAnimalMovementForm(forms.ModelForm):
    class Meta:
        model = TankAnimalMovement
        fields = ["direction", "reason", "quantity", "occurred_on", "target_tank", "note"]
        widgets = {
            "occurred_on": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, owner=None, exclude_tank=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Tank.objects.for_user(owner) if owner else Tank.objects.none()
        if exclude_tank is not None:
            queryset = queryset.exclude(pk=exclude_tank.pk)
        self.fields["target_tank"].queryset = queryset


class TankPlantForm(forms.ModelForm):
    class Meta:
        model = TankPlant
        fields = [
            "plant",
            "status",
            "quantity",
            "placement",
            "attached_to",
            "added_on",
            "removed_on",
            "identification_certain",
            "note",
        ]
        widgets = {
            "added_on": forms.DateInput(attrs={"type": "date"}),
            "removed_on": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }


class MaintenanceScheduleForm(forms.ModelForm):
    class Meta:
        model = MaintenanceSchedule
        fields = [
            "title",
            "event_category",
            "description",
            "interval",
            "interval_days",
            "next_due_on",
            "lead_days",
            "notify_email",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "next_due_on": forms.DateInput(attrs={"type": "date"}),
        }
