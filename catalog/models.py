"""Userübergreifender Katalog für Pflanzen- und Tierarten."""

from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse

from core.enums import Difficulty, WaterType
from core.images import ImageVariantsMixin


def plant_image_path(instance, filename):
    return f"catalog/plants/{instance.species_id}/{filename}"


def plant_thumb_path(instance, filename):
    return f"catalog/plants/{instance.species_id}/thumbs/{filename}"


def plant_preview_path(instance, filename):
    return f"catalog/plants/{instance.species_id}/preview/{filename}"


def animal_image_path(instance, filename):
    return f"catalog/animals/{instance.species_id}/{filename}"


def animal_thumb_path(instance, filename):
    return f"catalog/animals/{instance.species_id}/thumbs/{filename}"


def animal_preview_path(instance, filename):
    return f"catalog/animals/{instance.species_id}/preview/{filename}"


class CatalogPermission(models.Model):
    """Trägermodell der katalogweiten Pflegeberechtigung.

    Am Becken entscheidet Eigentümerschaft, hier nicht: der Katalog ist
    userübergreifend, ein Steckbrief gehört niemandem. Deshalb — und nur
    hier — ein echtes Django-Recht.

    Das Modell hat keine Tabelle (``managed = False``) und dient allein dazu,
    dass es das Recht genau einmal gibt. An beide Artmodelle gehängt, gäbe es
    ``can_edit_catalog`` zweimal und niemand wüsste, welches gemeint ist.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [("can_edit_catalog", "Darf den Katalog pflegen")]
        verbose_name = "Katalogberechtigung"
        verbose_name_plural = "Katalogberechtigungen"


class SpeciesQuerySet(models.QuerySet):
    def search(self, term):
        """Freitextsuche über wissenschaftlichen und deutschen Namen."""
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            models.Q(scientific_name__icontains=term)
            | models.Q(common_name__icontains=term)
            | models.Q(summary__icontains=term)
        )

    def with_images(self):
        return self.prefetch_related("images")


class Species(models.Model):
    """Gemeinsamer Steckbrief-Rumpf von Pflanzen- und Tierarten."""

    scientific_name = models.CharField("wissenschaftlicher Name", max_length=150, unique=True)
    common_name = models.CharField("deutscher Name", max_length=150, blank=True)
    slug = models.SlugField("Slug", max_length=160, unique=True)
    summary = models.CharField("Kurzbeschreibung", max_length=250, blank=True)
    description = models.TextField("Beschreibung", blank=True)

    water_type = models.CharField(
        "Wassertyp", max_length=10, choices=WaterType.choices, default=WaterType.FRESHWATER
    )
    difficulty = models.CharField(
        "Anspruch", max_length=10, choices=Difficulty.choices, default=Difficulty.EASY
    )

    temperature_min = models.DecimalField(
        "Temperatur min (°C)", max_digits=4, decimal_places=1, null=True, blank=True
    )
    temperature_max = models.DecimalField(
        "Temperatur max (°C)", max_digits=4, decimal_places=1, null=True, blank=True
    )
    ph_min = models.DecimalField("pH min", max_digits=3, decimal_places=1, null=True, blank=True)
    ph_max = models.DecimalField("pH max", max_digits=3, decimal_places=1, null=True, blank=True)
    gh_min = models.PositiveSmallIntegerField("GH min (°dH)", null=True, blank=True)
    gh_max = models.PositiveSmallIntegerField("GH max (°dH)", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SpeciesQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = [Lower("scientific_name")]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.common_name or self.scientific_name

    @property
    def primary_image(self):
        """Primärbild, sonst das erste Bild der Galerie, sonst ``None``.

        Arbeitet auf einer bereits geladenen Bildliste, damit Rasteransichten
        mit ``prefetch_related`` ohne zusätzliche Abfrage pro Karte auskommen.
        """
        images = list(self.images.all())
        if not images:
            return None
        for image in images:
            if image.is_primary:
                return image
        return images[0]

    @staticmethod
    def _range_text(low, high, unit=""):
        if low is None and high is None:
            return ""
        if low is not None and high is not None:
            return f"{low:g}–{high:g}{unit}"
        if low is not None:
            return f"ab {low:g}{unit}"
        return f"bis {high:g}{unit}"

    @property
    def temperature_range(self):
        return self._range_text(self.temperature_min, self.temperature_max, " °C")

    @property
    def ph_range(self):
        return self._range_text(self.ph_min, self.ph_max)

    @property
    def gh_range(self):
        return self._range_text(self.gh_min, self.gh_max, " °dH")


class PlantSpecies(Species):
    class Placement(models.TextChoices):
        FOREGROUND = "foreground", "Vordergrund"
        MIDGROUND = "midground", "Mittelgrund"
        BACKGROUND = "background", "Hintergrund"
        FLOATING = "floating", "Schwimmpflanze"
        EPIPHYTE = "epiphyte", "Aufsitzerpflanze"

    class GrowthRate(models.TextChoices):
        SLOW = "slow", "Langsam"
        MEDIUM = "medium", "Mittel"
        FAST = "fast", "Schnell"

    class LightDemand(models.TextChoices):
        LOW = "low", "Wenig"
        MEDIUM = "medium", "Mittel"
        HIGH = "high", "Viel"

    placement = models.CharField(
        "Standort", max_length=12, choices=Placement.choices, default=Placement.MIDGROUND
    )
    growth_rate = models.CharField(
        "Wuchsgeschwindigkeit", max_length=6, choices=GrowthRate.choices, default=GrowthRate.MEDIUM
    )
    light_demand = models.CharField(
        "Lichtbedarf", max_length=6, choices=LightDemand.choices, default=LightDemand.MEDIUM
    )
    co2_required = models.BooleanField("CO₂ erforderlich", default=False)
    max_height_cm = models.PositiveSmallIntegerField("Wuchshöhe (cm)", null=True, blank=True)

    class Meta(Species.Meta):
        abstract = False
        verbose_name = "Pflanzenart"
        verbose_name_plural = "Pflanzenarten"

    def get_absolute_url(self):
        return reverse("catalog:plant-detail", args=[self.slug])


class AnimalSpecies(Species):
    class Category(models.TextChoices):
        FISH = "fish", "Fisch"
        SHRIMP = "shrimp", "Garnele"
        CRAYFISH = "crayfish", "Krebs"
        SNAIL = "snail", "Schnecke"
        MUSSEL = "mussel", "Muschel"
        OTHER = "other", "Sonstiges"

    class Temperament(models.TextChoices):
        PEACEFUL = "peaceful", "Friedlich"
        ROBUST = "robust", "Robust"
        TERRITORIAL = "territorial", "Revierbildend"
        PREDATORY = "predatory", "Räuberisch"

    category = models.CharField(
        "Kategorie", max_length=10, choices=Category.choices, default=Category.FISH
    )
    temperament = models.CharField(
        "Verhalten", max_length=12, choices=Temperament.choices, default=Temperament.PEACEFUL
    )
    adult_size_cm = models.DecimalField(
        "Endgröße (cm)", max_digits=4, decimal_places=1, null=True, blank=True
    )
    min_group_size = models.PositiveSmallIntegerField(
        "Mindestgruppengröße",
        default=1,
        help_text="Unterschreitet der Besatz diesen Wert, meldet das Dashboard eine Warnung.",
    )
    min_tank_volume_l = models.PositiveIntegerField("Mindestvolumen (l)", null=True, blank=True)

    class Meta(Species.Meta):
        abstract = False
        verbose_name = "Tierart"
        verbose_name_plural = "Tierarten"

    def get_absolute_url(self):
        return reverse("catalog:animal-detail", args=[self.slug])


class SpeciesImage(ImageVariantsMixin, models.Model):
    caption = models.CharField("Bildunterschrift", max_length=200, blank=True)
    # Die Maße gelten für alle Varianten (Seitenverhältnis bleibt erhalten);
    # die Varianten selbst stehen in den konkreten Modellen, weil sie je Art
    # in einem anderen Verzeichnis liegen.
    width = models.PositiveIntegerField("Breite", null=True, blank=True, editable=False)
    height = models.PositiveIntegerField("Höhe", null=True, blank=True, editable=False)
    is_primary = models.BooleanField("Primärbild", default=False)
    sort_order = models.PositiveSmallIntegerField("Reihenfolge", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-is_primary", "sort_order", "pk"]

    def __str__(self):
        return self.caption or f"Bild {self.pk}"


class PlantImage(SpeciesImage):
    species = models.ForeignKey(PlantSpecies, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField("Bild", upload_to=plant_image_path)
    thumbnail = models.ImageField(
        "Kachel", upload_to=plant_thumb_path, blank=True, editable=False
    )
    preview = models.ImageField(
        "Vorschau", upload_to=plant_preview_path, blank=True, editable=False
    )

    class Meta(SpeciesImage.Meta):
        abstract = False
        verbose_name = "Pflanzenbild"
        verbose_name_plural = "Pflanzenbilder"


class AnimalImage(SpeciesImage):
    species = models.ForeignKey(AnimalSpecies, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField("Bild", upload_to=animal_image_path)
    thumbnail = models.ImageField(
        "Kachel", upload_to=animal_thumb_path, blank=True, editable=False
    )
    preview = models.ImageField(
        "Vorschau", upload_to=animal_preview_path, blank=True, editable=False
    )

    class Meta(SpeciesImage.Meta):
        abstract = False
        verbose_name = "Tierbild"
        verbose_name_plural = "Tierbilder"
