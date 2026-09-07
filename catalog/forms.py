"""Formulare der Katalogpflege.

Der Katalog ist userübergreifend: was hier gespeichert wird, sehen alle. Die
Formulare prüfen deshalb etwas strenger als die am Becken — ein verdrehter
Temperaturbereich in einem Steckbrief steht sonst allen im Weg.
"""

from django import forms

from core.forms import BootstrapMixin, MultipleImageField

from .models import AnimalSpecies, PlantSpecies, normalize_variant, unique_slug

#: Steckbrieffelder, die sich Pflanzen und Tiere teilen.
SHARED_FIELDS = [
    "scientific_name",
    "variant",
    "is_cultivated_form",
    "common_name",
    "summary",
    "description",
    "water_type",
    "difficulty",
    "temperature_min",
    "temperature_max",
    "ph_min",
    "ph_max",
    "gh_min",
    "gh_max",
]

#: Bereichspaare, die nicht verdreht sein dürfen.
RANGES = [
    ("temperature_min", "temperature_max", "Temperatur"),
    ("ph_min", "ph_max", "pH-Wert"),
    ("gh_min", "gh_max", "Gesamthärte"),
]


class SpeciesForm(BootstrapMixin, forms.ModelForm):
    """Gemeinsamer Rumpf der beiden Steckbrief-Formulare.

    Der Slug wird aus wissenschaftlichem Namen und Sorte abgeleitet: er ist die
    Adresse des Steckbriefs und keine Angabe, über die jemand nachdenken soll.
    Ohne die Sorte im Slug kollidierten Stamm- und Zuchtform derselben Art.
    Er bleibt beim Bearbeiten erhalten, sonst brächen bestehende Links.
    """

    class Meta:
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def clean_variant(self):
        return normalize_variant(self.cleaned_data.get("variant", ""))

    def clean(self):
        cleaned = super().clean()
        for low, high, label in RANGES:
            minimum, maximum = cleaned.get(low), cleaned.get(high)
            if minimum is not None and maximum is not None and minimum > maximum:
                self.add_error(high, f"{label}: die Obergrenze liegt unter der Untergrenze.")
        return cleaned

    def save(self, commit=True):
        species = super().save(commit=False)
        if not species.slug:
            species.slug = self._unique_slug(species)
        if commit:
            species.save()
        return species

    def _unique_slug(self, species):
        try:
            return unique_slug(
                type(species),
                species.scientific_name,
                species.variant,
                exclude_pk=species.pk,
            )
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class PlantSpeciesForm(SpeciesForm):
    class Meta(SpeciesForm.Meta):
        model = PlantSpecies
        fields = SHARED_FIELDS + [
            "placement",
            "growth_rate",
            "light_demand",
            "co2_required",
            "max_height_cm",
        ]


class AnimalSpeciesForm(SpeciesForm):
    class Meta(SpeciesForm.Meta):
        model = AnimalSpecies
        fields = SHARED_FIELDS + [
            "category",
            "temperament",
            "adult_size_cm",
            "min_group_size",
            "min_tank_volume_l",
        ]


class SpeciesImageUploadForm(BootstrapMixin, forms.Form):
    """Bilder zu einer Art — mehrere in einem Durchgang."""

    images = MultipleImageField(label="Bilder", help_text="Mehrfachauswahl möglich.")
    caption = forms.CharField(
        label="Bildunterschrift",
        max_length=200,
        required=False,
        help_text="Gilt für alle ausgewählten Bilder.",
    )

    def save(self, species, image_model):
        """Legt die Bilder an; das erste wird Primärbild, wenn es noch keins gibt."""
        has_primary = species.images.filter(is_primary=True).exists()
        created = []
        for image in self.cleaned_data["images"]:
            created.append(
                image_model.objects.create(
                    species=species,
                    image=image,
                    caption=self.cleaned_data.get("caption", ""),
                    is_primary=not has_primary and not created,
                )
            )
        return created
