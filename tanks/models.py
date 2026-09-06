from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
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
    """Gemeinsames Foto-Modell für Becken- und Messungsbelege. `tank` bleibt
    Pflichtfeld, wird bei einem Messungsfoto aber automatisch aus der
    Messung übernommen — so landet ein Belegfoto zugleich in der
    Becken-Galerie, ohne dass die Anlage doppelt gepflegt werden muss."""

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="photos")
    measurement = models.ForeignKey(
        "Measurement",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="photos",
    )
    event = models.ForeignKey(
        "Event",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="photos",
    )
    image = models.ImageField(upload_to="tanks/%Y/%m/")
    caption = models.CharField(max_length=200, blank=True)
    taken_on = models.DateField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.tank} – {self.caption or self.image.name}"

    def save(self, *args, **kwargs):
        if self.measurement_id and not self.tank_id:
            self.tank_id = self.measurement.tank_id
        if self.event_id and not self.tank_id:
            self.tank_id = self.event.tank_id
        super().save(*args, **kwargs)


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


class Source(models.TextChoices):
    MANUAL = "manual", "Manuell"
    DEVICE = "device", "Gerät"
    IMPORT = "import", "Import"


class Measurement(models.Model):
    """Eine Messreihe zu einem Zeitpunkt (z. B. pH, KH und Temperatur aus
    einem Tröpfchentest), nicht ein einzelner Messwert. Die Einzelwerte
    hängen als `MeasurementValue` daran."""

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="measurements")
    measured_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-measured_at"]
        indexes = [models.Index(fields=["tank", "-measured_at"])]

    def __str__(self):
        return f"{self.tank} – {timezone.localtime(self.measured_at):%d.%m.%Y %H:%M}"

    def value_for(self, parameter_key):
        return next(
            (
                value
                for value in self.values.all()
                if value.parameter.key == parameter_key and value.value is not None
            ),
            None,
        )

    @property
    def co2_mg_l(self):
        """CO2 [mg/l] = 3 * KH * 10^(7 - pH) — bewusst berechnet statt
        gespeichert, sonst driftet der Wert bei nachträglicher Korrektur
        von KH oder pH auseinander."""

        kh = self.value_for("kh")
        ph = self.value_for("ph")
        if kh is None or ph is None:
            return None
        co2 = 3 * float(kh.value) * (10 ** (7 - float(ph.value)))
        return Decimal(str(round(co2, 1)))


class MeasurementValue(models.Model):
    measurement = models.ForeignKey(Measurement, on_delete=models.CASCADE, related_name="values")
    parameter = models.ForeignKey(
        Parameter, on_delete=models.PROTECT, related_name="measurement_values"
    )
    value = models.DecimalField(max_digits=9, decimal_places=3, null=True, blank=True)
    below_detection = models.BooleanField("n.n.", default=False)

    class Meta:
        ordering = ["parameter__position", "parameter__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["measurement", "parameter"], name="unique_measurement_parameter"
            ),
        ]

    def __str__(self):
        return f"{self.measurement} – {self.parameter}"

    def clean(self):
        super().clean()
        if self.value is not None and self.below_detection:
            raise ValidationError("Entweder Messwert oder „n.n.“ angeben, nicht beides.")
        if self.value is None and not self.below_detection:
            raise ValidationError("Messwert oder „n.n.“ ist erforderlich.")
        if self.below_detection and self.parameter_id and not self.parameter.supports_below_detection:
            raise ValidationError("Dieser Parameter unterstützt „n.n.“ nicht.")

    @property
    def target(self):
        return self.measurement.tank.targets.filter(parameter_id=self.parameter_id).first()

    @property
    def status(self):
        if self.value is None:
            return None
        target = self.target
        if target is None:
            return None
        if target.minimum is not None and self.value < target.minimum:
            return "low"
        if target.maximum is not None and self.value > target.maximum:
            return "high"
        return "ok"


class Event(models.Model):
    """Alles, was am Becken passiert und keine Messung ist: Wasserwechsel,
    Technikänderung, Düngung, Beobachtung, Problem, Krankheit, Nachwuchs."""

    class Category(models.TextChoices):
        SETUP = "setup", "Einrichtung"
        WATER_CHANGE = "water_change", "Wasserwechsel"
        STOCK = "stock", "Besatz"
        PLANTS = "plants", "Bepflanzung"
        TECH = "tech", "Technik"
        FERTILIZER = "fertilizer", "Düngung"
        MAINTENANCE = "maintenance", "Pflege"
        OBSERVATION = "observation", "Beobachtung"
        BREEDING = "breeding", "Nachwuchs"
        DISEASE = "disease", "Krankheit"
        PROBLEM = "problem", "Problem"
        OTHER = "other", "Sonstiges"

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="events")
    occurred_at = models.DateTimeField(default=timezone.now)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # nur bei Wasserwechsel relevant; Prozent wird gegen tank.volume_net_l
    # berechnet, nicht gespeichert (siehe water_change_percent).
    water_changed_l = models.DecimalField(
        "Wasserwechsel (l)", max_digits=7, decimal_places=1, null=True, blank=True
    )

    # gesetzt, wenn das Ereignis aus einem fälligen Termin erledigt wurde
    schedule = models.ForeignKey(
        "MaintenanceSchedule",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["tank", "-occurred_at"])]

    def __str__(self):
        return f"{self.tank} – {self.title}"

    @property
    def water_change_percent(self):
        if self.water_changed_l is None or not self.tank.volume_net_l:
            return None
        percent = self.water_changed_l / self.tank.volume_net_l * 100
        return percent.quantize(Decimal("0.1"))


class MaintenanceSchedule(models.Model):
    """Fälligkeits-Timer für wiederkehrende Pflege (Filterreinigung,
    Wasserwechsel-Rhythmus etc.). Die volle Terminverwaltung mit
    Wiederholungsregeln und Dashboard-Übersicht ist ein eigenes Ticket —
    hier nur das Minimum, damit ein Ereignis einen fälligen Termin
    quittieren kann."""

    tank = models.ForeignKey(
        Tank, on_delete=models.CASCADE, related_name="maintenance_schedules"
    )
    title = models.CharField(max_length=200)
    category = models.CharField(
        max_length=20, choices=Event.Category.choices, default=Event.Category.MAINTENANCE
    )
    interval_days = models.PositiveSmallIntegerField("Intervall (Tage)")
    next_due_at = models.DateTimeField(default=timezone.now)
    last_done_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_due_at"]

    def __str__(self):
        return f"{self.tank} – {self.title}"

    @property
    def is_due(self):
        return self.active and self.next_due_at <= timezone.now()

    def mark_done(self, occurred_at):
        self.last_done_at = occurred_at
        self.next_due_at = occurred_at + timedelta(days=self.interval_days)
        self.save(update_fields=["last_done_at", "next_due_at"])
