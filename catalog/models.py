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
