from django.conf import settings
from django.db import models
from django.utils.text import slugify


class AnimalGroup(models.TextChoices):
    FISCH = "fisch", "Fisch"
    GARNELE = "garnele", "Garnele"
    KREBS = "krebs", "Krebs"
    SCHNECKE = "schnecke", "Schnecke"
    MUSCHEL = "muschel", "Muschel"


class SocialBehavior(models.TextChoices):
    EINZELN = "einzeln", "Einzeln"
    PAAR = "paar", "Paar"
    HAREM = "harem", "Harem"
    GRUPPE = "gruppe", "Gruppe"
    SCHWARM = "schwarm", "Schwarm"


class Zone(models.TextChoices):
    BODEN = "boden", "Boden"
    MITTE = "mitte", "Mitte"
    OBERFLAECHE = "oberflaeche", "Oberfläche"


class Difficulty(models.TextChoices):
    EASY = "easy", "einfach"
    MEDIUM = "medium", "mittel"
    DEMANDING = "demanding", "anspruchsvoll"


class Diet(models.TextChoices):
    ALLESFRESSER = "allesfresser", "Allesfresser"
    FLEISCHFRESSER = "fleisch", "Fleischfresser"
    PFLANZENFRESSER = "pflanzen", "Pflanzenfresser"
    AUFWUCHSFRESSER = "aufwuchs", "Aufwuchsfresser"


class BreedingType(models.TextChoices):
    FREILAICHER = "freilaicher", "Freilaicher"
    SUBSTRATLAICHER = "substratlaicher", "Substratlaicher"
    HOEHLENBRUETER = "hoehlenbrueter", "Höhlenbrüter"
    MAULBRUETER = "maulbrueter", "Maulbrüter"
    LEBENDGEBAEREND = "lebendgebaerend", "Lebendgebärend"


class Sex(models.TextChoices):
    MAENNLICH = "maennlich", "männlich"
    WEIBLICH = "weiblich", "weiblich"
    UNBESTIMMT = "unbestimmt", "unbestimmt"


class GrowthForm(models.TextChoices):
    STEM = "stem", "Stängel"
    ROSETTE = "rosette", "Rosette"
    EPIPHYTE = "epiphyte", "Aufsitzer"
    GROUND_COVER = "ground_cover", "Bodendecker"
    FLOATING = "floating", "Schwimmpflanze"
    MOSS = "moss", "Moos"


class Placement(models.TextChoices):
    FOREGROUND = "foreground", "Vordergrund"
    MIDGROUND = "midground", "Mittelgrund"
    BACKGROUND = "background", "Hintergrund"


class GrowthRate(models.TextChoices):
    SLOW = "slow", "langsam"
    MEDIUM = "medium", "mittel"
    FAST = "fast", "schnell"


class Demand(models.TextChoices):
    LOW = "low", "niedrig"
    MEDIUM = "medium", "mittel"
    HIGH = "high", "hoch"


class CatalogAnimal(models.Model):
    """Userübergreifende Artendatenbank für Fische, Garnelen, Krebse,
    Schnecken und Muscheln. `scientific_name` + `variety` bilden die
    fachliche Identität — Wildform und Zuchtform derselben Art
    unterscheiden sich oft erheblich in Robustheit und Brutverhalten."""

    scientific_name = models.CharField(max_length=160)
    variety = models.CharField(max_length=80, blank=True)
    common_name = models.CharField(max_length=160, blank=True)
    slug = models.SlugField(unique=True, blank=True)
    group = models.CharField(max_length=20, choices=AnimalGroup.choices)
    family = models.CharField(max_length=100, blank=True)
    origin = models.CharField(max_length=160, blank=True)

    size_max_cm = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    min_tank_liters = models.PositiveIntegerField(blank=True, null=True)
    min_tank_length_cm = models.PositiveSmallIntegerField(blank=True, null=True)
    min_group_size = models.PositiveSmallIntegerField(blank=True, null=True)
    social_behavior = models.CharField(max_length=20, choices=SocialBehavior.choices, blank=True)
    zone = models.CharField(max_length=20, choices=Zone.choices, blank=True)
    difficulty = models.CharField(max_length=20, choices=Difficulty.choices)
    lifespan_years = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)

    temp_min_c = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    temp_max_c = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    ph_min = models.DecimalField(max_digits=3, decimal_places=1, blank=True, null=True)
    ph_max = models.DecimalField(max_digits=3, decimal_places=1, blank=True, null=True)
    kh_min = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    kh_max = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    gh_min = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    gh_max = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)

    diet = models.CharField(max_length=20, choices=Diet.choices, blank=True)
    breeding_type = models.CharField(max_length=20, choices=BreedingType.choices, blank=True)
    breeding_notes = models.TextField(blank=True)

    description = models.TextField(blank=True)
    care_notes = models.TextField(blank=True)
    compatibility_notes = models.TextField(blank=True)
    warning = models.TextField(blank=True)

    is_line_bred_variant = models.BooleanField(default=False)
    source_url = models.URLField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="catalog_animals",
    )
    verified = models.BooleanField(default=False)

    class Meta:
        ordering = ["scientific_name", "variety"]
        constraints = [
            models.UniqueConstraint(
                fields=["scientific_name", "variety"],
                name="unique_catalog_animal_scientific_name_variety",
            ),
        ]

    def __str__(self):
        if self.variety:
            return f"{self.scientific_name} '{self.variety}'"
        return self.scientific_name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def _build_unique_slug(self):
        base_slug = slugify(str(self))
        slug = base_slug
        suffix = 2
        while CatalogAnimal.objects.exclude(pk=self.pk).filter(slug=slug).exists():
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        return slug

    @property
    def primary_image(self):
        for image in self.images.all():
            if image.is_primary:
                return image
        return None


class CatalogAnimalImage(models.Model):
    animal = models.ForeignKey(CatalogAnimal, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="catalog/animals/%Y/%m/")
    caption = models.CharField(max_length=200, blank=True)
    sex = models.CharField(max_length=20, choices=Sex.choices, blank=True)
    is_primary = models.BooleanField(default=False)
    credit = models.CharField(max_length=200, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["animal"],
                condition=models.Q(is_primary=True),
                name="unique_primary_image_per_animal",
            ),
        ]

    def __str__(self):
        return f"{self.animal} – {self.caption or self.image.name}"

    def save(self, *args, **kwargs):
        if not self.pk and not CatalogAnimalImage.objects.filter(animal=self.animal).exists():
            self.is_primary = True
        if self.is_primary:
            CatalogAnimalImage.objects.filter(animal=self.animal, is_primary=True).exclude(
                pk=self.pk
            ).update(is_primary=False)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        animal = self.animal
        was_primary = self.is_primary
        super().delete(*args, **kwargs)
        if was_primary:
            next_image = animal.images.first()
            if next_image is not None:
                next_image.is_primary = True
                next_image.save(update_fields=["is_primary"])


class CatalogPlant(models.Model):
    """Userübergreifende Artendatenbank für Pflanzen, einmal gepflegt und von
    allen Becken referenziert. `scientific_name` + `cultivar` bilden die
    fachliche Identität — Sorten unterscheiden sich oft deutlich in den
    Ansprüchen."""

    scientific_name = models.CharField(max_length=160)
    cultivar = models.CharField(max_length=80, blank=True)
    common_name = models.CharField(max_length=160, blank=True)
    slug = models.SlugField(unique=True, blank=True)
    family = models.CharField(max_length=100, blank=True)
    origin = models.CharField(max_length=160, blank=True)

    growth_form = models.CharField(max_length=20, choices=GrowthForm.choices)
    placement = models.CharField(max_length=20, choices=Placement.choices)
    difficulty = models.CharField(max_length=20, choices=Difficulty.choices)
    growth_rate = models.CharField(max_length=20, choices=GrowthRate.choices)
    light_demand = models.CharField(max_length=20, choices=Demand.choices)
    co2_demand = models.CharField(max_length=20, choices=Demand.choices)

    height_min_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    height_max_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    temp_min_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    temp_max_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    ph_min = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    ph_max = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    kh_min = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    kh_max = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    propagation = models.TextField(blank=True)
    description = models.TextField(blank=True)
    care_notes = models.TextField(blank=True)
    warning = models.TextField(blank=True)

    source_url = models.URLField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="catalog_plants",
    )
    verified = models.BooleanField(default=False)

    class Meta:
        ordering = ["scientific_name", "cultivar"]
        constraints = [
            models.UniqueConstraint(
                fields=["scientific_name", "cultivar"],
                name="unique_catalog_plant_scientific_name_cultivar",
            ),
        ]

    def __str__(self):
        if self.cultivar:
            return f"{self.scientific_name} '{self.cultivar}'"
        return self.scientific_name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def _build_unique_slug(self):
        base_slug = slugify(str(self))
        slug = base_slug
        suffix = 2
        while CatalogPlant.objects.exclude(pk=self.pk).filter(slug=slug).exists():
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        return slug

    @property
    def primary_image(self):
        for image in self.images.all():
            if image.is_primary:
                return image
        return None


class CatalogPlantImage(models.Model):
    plant = models.ForeignKey(CatalogPlant, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="catalog/plants/%Y/%m/")
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    credit = models.CharField(max_length=200, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["plant"],
                condition=models.Q(is_primary=True),
                name="unique_primary_image_per_plant",
            ),
        ]

    def __str__(self):
        return f"{self.plant} – {self.caption or self.image.name}"

    def save(self, *args, **kwargs):
        if not self.pk and not CatalogPlantImage.objects.filter(plant=self.plant).exists():
            self.is_primary = True
        if self.is_primary:
            CatalogPlantImage.objects.filter(plant=self.plant, is_primary=True).exclude(
                pk=self.pk
            ).update(is_primary=False)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        plant = self.plant
        was_primary = self.is_primary
        super().delete(*args, **kwargs)
        if was_primary:
            next_image = plant.images.first()
            if next_image is not None:
                next_image.is_primary = True
                next_image.save(update_fields=["is_primary"])
"""Userübergreifender Katalog für Pflanzen- und Tierarten."""

from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse

from core.enums import Difficulty, WaterType


def plant_image_path(instance, filename):
    return f"catalog/plants/{instance.species_id}/{filename}"


def animal_image_path(instance, filename):
    return f"catalog/animals/{instance.species_id}/{filename}"


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


class SpeciesImage(models.Model):
    caption = models.CharField("Bildunterschrift", max_length=200, blank=True)
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

    class Meta(SpeciesImage.Meta):
        abstract = False
        verbose_name = "Pflanzenbild"
        verbose_name_plural = "Pflanzenbilder"


class AnimalImage(SpeciesImage):
    species = models.ForeignKey(AnimalSpecies, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField("Bild", upload_to=animal_image_path)

    class Meta(SpeciesImage.Meta):
        abstract = False
        verbose_name = "Tierbild"
        verbose_name_plural = "Tierbilder"
