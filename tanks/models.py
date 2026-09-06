from django.conf import settings
from django.db import models
from django.utils.text import slugify


class WaterType(models.TextChoices):
    SUESSWASSER = "suess", "Süßwasser"
    MEERWASSER = "meer", "Meerwasser"
    BRACKWASSER = "brack", "Brackwasser"


class TankQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(owner=user)

    def active(self):
        return self.filter(shut_down_on__isnull=True)

    def dissolved(self):
        return self.filter(shut_down_on__isnull=False)


class Tank(models.Model):
    """A user's aquarium. Ownership is exclusive — sharing between users is
    intentionally out of scope for version 1."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tanks",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120, blank=True)
    model_name = models.CharField("Modellbezeichnung", max_length=120, blank=True)

    length_cm = models.PositiveSmallIntegerField("Länge (cm)", null=True, blank=True)
    height_cm = models.PositiveSmallIntegerField("Höhe (cm)", null=True, blank=True)
    depth_cm = models.PositiveSmallIntegerField("Tiefe (cm)", null=True, blank=True)
    volume_gross_l = models.DecimalField(
        "Bruttovolumen (l)", max_digits=7, decimal_places=1, null=True, blank=True
    )
    volume_net_l = models.DecimalField(
        "Nettovolumen (l)", max_digits=7, decimal_places=1, null=True, blank=True
    )

    water_type = models.CharField(max_length=10, choices=WaterType.choices)
    biotope = models.CharField(max_length=120, blank=True)

    started_on = models.DateField("Inbetriebnahme")
    shut_down_on = models.DateField("Auflösung", null=True, blank=True)
    description = models.TextField(blank=True)

    # Technik als Freitext-Blöcke, keine eigenen Modelle in v1 — siehe Modul-Docstring.
    substrate = models.TextField("Bodengrund", blank=True)
    hardscape = models.TextField("Hardscape", blank=True)
    filtration = models.TextField("Filterung", blank=True)
    lighting = models.TextField("Beleuchtung", blank=True)
    co2 = models.TextField("CO2", blank=True)
    fertilization = models.TextField("Düngung", blank=True)

    cover_photo = models.ForeignKey(
        "Photo",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    objects = TankQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "slug"], name="unique_tank_slug_per_owner"
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def _build_unique_slug(self):
        base_slug = slugify(self.name)
        slug = base_slug
        suffix = 2
        while (
            Tank.objects.filter(owner=self.owner, slug=slug)
            .exclude(pk=self.pk)
            .exists()
        ):
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        return slug

    @property
    def is_dissolved(self):
        return self.shut_down_on is not None


class Photo(models.Model):
    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="tanks/%Y/%m/")
    caption = models.CharField(max_length=200, blank=True)
    taken_on = models.DateField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.tank} – {self.caption or self.image.name}"


class Parameter(models.Model):
    """Globaler Katalog der Messgrößen — keine Spalten am Messmodell. Neue
    Messgrößen (z. B. Kalium, Kupfer) sind ein Katalogeintrag, keine Migration."""

    key = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    unit = models.CharField(max_length=20, blank=True)
    decimals = models.PositiveSmallIntegerField(default=1)
    position = models.PositiveSmallIntegerField(default=100)
    supports_below_detection = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


class TankParameterTarget(models.Model):
    """Zielbereich eines Parameters für ein konkretes Becken. Bewusst am Becken
    verortet statt am Parameter, da z. B. ein Diskus- und ein Malawibecken
    denselben Parameter mit völlig unterschiedlichem Zielbereich haben."""

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="targets")
    parameter = models.ForeignKey(
        Parameter, on_delete=models.PROTECT, related_name="tank_targets"
    )
    target = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    minimum = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    maximum = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["parameter__position", "parameter__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tank", "parameter"], name="unique_tank_parameter_target"
            ),
        ]

    def __str__(self):
        return f"{self.tank} – {self.parameter}"
