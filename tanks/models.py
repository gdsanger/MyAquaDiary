"""Becken und alles, was daran hängt: Messwerte, Ereignisse, Besatz,
Bepflanzung, Termine, Geräte und Fotos."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Count, Max, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from core.enums import Status, WaterType

#: Anzahl Farbkennungen für Becken (siehe ``.mad-tank-accent-*`` im Stylesheet).
TANK_ACCENT_COUNT = 8

#: Abweichung bis zu diesem Anteil der Zielbereichsbreite gilt noch als
#: „Abweichung" (orange), darüber als „kritisch" (rot).
WARN_TOLERANCE = Decimal("0.2")

#: Termine innerhalb dieses Zeitraums gelten als „anstehend".
UPCOMING_DAYS = 14


def tank_cover_path(instance, filename):
    return f"tanks/{instance.pk or 'neu'}/cover/{filename}"


def tank_photo_path(instance, filename):
    return f"tanks/{instance.tank_id}/photos/{filename}"


class TankQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(owner=user)

    def active(self):
        return self.filter(dissolved_on__isnull=True)

    def dissolved(self):
        return self.filter(dissolved_on__isnull=False)

    def with_overview(self, today=None):
        """Kennzahlen für das Kartenraster in einer Abfrage.

        Die Werte kommen als Subqueries statt als mehrfaches ``annotate`` über
        JOINs — sonst multiplizieren sich die Zeilen und alle Summen sind falsch.
        """
        today = today or timezone.localdate()
        animals = (
            Stocking.objects.filter(tank=OuterRef("pk"), removed_on__isnull=True)
            .order_by()
            .values("tank")
            .annotate(total=Sum("quantity"))
            .values("total")
        )
        plants = (
            Planting.objects.filter(tank=OuterRef("pk"), removed_on__isnull=True)
            .order_by()
            .values("tank")
            .annotate(total=Sum("quantity"))
            .values("total")
        )
        open_tasks = (
            CareTask.objects.filter(tank=OuterRef("pk"), is_active=True, due_on__lte=today)
            .order_by()
            .values("tank")
            .annotate(total=Count("pk"))
            .values("total")
        )
        last_measurement = (
            Measurement.objects.filter(tank=OuterRef("pk"))
            .order_by()
            .values("tank")
            .annotate(latest=Max("measured_at"))
            .values("latest")
        )
        return self.annotate(
            animal_count=Coalesce(Subquery(animals, output_field=models.IntegerField()), 0),
            plant_count=Coalesce(Subquery(plants, output_field=models.IntegerField()), 0),
            open_task_count=Coalesce(Subquery(open_tasks, output_field=models.IntegerField()), 0),
            last_measured_at=Subquery(last_measurement, output_field=models.DateTimeField()),
        )


class Tank(models.Model):
    """Ein Aquarium eines Benutzers."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="tanks", on_delete=models.CASCADE, verbose_name="Besitzer"
    )
    name = models.CharField("Name", max_length=120)
    slug = models.SlugField("Slug", max_length=140)
    water_type = models.CharField(
        "Wassertyp", max_length=10, choices=WaterType.choices, default=WaterType.FRESHWATER
    )
    volume_liters = models.DecimalField("Volumen (l)", max_digits=7, decimal_places=1)
    length_cm = models.PositiveSmallIntegerField("Länge (cm)", null=True, blank=True)
    width_cm = models.PositiveSmallIntegerField("Breite (cm)", null=True, blank=True)
    height_cm = models.PositiveSmallIntegerField("Höhe (cm)", null=True, blank=True)
    location = models.CharField("Standort", max_length=120, blank=True)
    setup_date = models.DateField("Einrichtung")
    dissolved_on = models.DateField("Aufgelöst am", null=True, blank=True)
    accent = models.PositiveSmallIntegerField(
        "Farbkennung",
        default=1,
        choices=[(i, f"Farbe {i}") for i in range(1, TANK_ACCENT_COUNT + 1)],
        help_text="Bestimmt die Farbmarkierung des Beckens in Listen und auf dem Dashboard.",
    )
    cover_image = models.ImageField("Titelbild", upload_to=tank_cover_path, blank=True)
    notes = models.TextField("Notizen", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TankQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Becken"
        verbose_name_plural = "Becken"
        constraints = [
            models.UniqueConstraint(fields=["owner", "slug"], name="unique_tank_slug_per_owner"),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("tanks:detail", args=[self.slug])

    @property
    def is_dissolved(self):
        return self.dissolved_on is not None

    @property
    def accent_class(self):
        """CSS-Klasse der Farbkennung — die Farbe selbst steht im Stylesheet."""
        return f"mad-tank-accent-{self.accent}"

    @property
    def dimensions(self):
        parts = [self.length_cm, self.width_cm, self.height_cm]
        if not all(parts):
            return ""
        return " × ".join(f"{p}" for p in parts) + " cm"

    @property
    def age(self):
        """Alter als Zeitspanne bis heute bzw. bis zur Auflösung."""
        end = self.dissolved_on or timezone.localdate()
        return end - self.setup_date

    @property
    def age_display(self):
        days = self.age.days
        if days < 0:
            return "geplant"
        if days < 31:
            return f"{days} Tage"
        months = days // 30
        if months < 24:
            return f"{months} Monate"
        return f"{months // 12} Jahre"


class Parameter(models.Model):
    """Wasserparameter (pH, NO₂, …) samt Standard-Zielbereich."""

    key = models.SlugField("Schlüssel", max_length=30, unique=True)
    name = models.CharField("Name", max_length=60)
    unit = models.CharField("Einheit", max_length=20, blank=True)
    decimals = models.PositiveSmallIntegerField("Nachkommastellen", default=1)
    default_min = models.DecimalField("Zielbereich min", max_digits=8, decimal_places=3, null=True, blank=True)
    default_max = models.DecimalField("Zielbereich max", max_digits=8, decimal_places=3, null=True, blank=True)
    is_key_parameter = models.BooleanField(
        "Leitparameter", default=False, help_text="Wird im Verlaufsdiagramm auf dem Dashboard gezeigt."
    )
    sort_order = models.PositiveSmallIntegerField("Reihenfolge", default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Parameter"
        verbose_name_plural = "Parameter"

    def __str__(self):
        return self.name

    def format_value(self, value):
        if value is None:
            return "—"
        return f"{value:.{self.decimals}f}".replace(".", ",")

    def format_range(self, minimum, maximum):
        """Zielbereich als Text, z. B. „6,5–7,5 pH" oder „bis 0,2 mg/l"."""
        unit = f" {self.unit}" if self.unit else ""
        if minimum is None and maximum is None:
            return "—"
        if minimum is not None and maximum is not None:
            return f"{self.format_value(minimum)}–{self.format_value(maximum)}{unit}"
        if minimum is not None:
            return f"ab {self.format_value(minimum)}{unit}"
        return f"bis {self.format_value(maximum)}{unit}"


class TankParameterTarget(models.Model):
    """Beckenspezifischer Zielbereich, überschreibt den Parameter-Standard."""

    tank = models.ForeignKey(Tank, related_name="parameter_targets", on_delete=models.CASCADE)
    parameter = models.ForeignKey(Parameter, related_name="targets", on_delete=models.CASCADE)
    minimum = models.DecimalField("min", max_digits=8, decimal_places=3, null=True, blank=True)
    maximum = models.DecimalField("max", max_digits=8, decimal_places=3, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tank", "parameter"], name="unique_target_per_tank_parameter"),
        ]
        verbose_name = "Zielbereich"
        verbose_name_plural = "Zielbereiche"

    def __str__(self):
        return f"{self.tank} · {self.parameter}"


def classify_value(value, minimum, maximum):
    """Einheitliche Statuslogik für Messwerte.

    Innerhalb des Zielbereichs → ``OK``. Außerhalb, aber höchstens um
    ``WARN_TOLERANCE`` der Bereichsbreite daneben → ``WARN``, sonst ``CRITICAL``.
    Ohne hinterlegten Zielbereich lässt sich nichts aussagen → ``UNKNOWN``.
    """
    if value is None or (minimum is None and maximum is None):
        return Status.UNKNOWN

    below = minimum is not None and value < minimum
    above = maximum is not None and value > maximum
    if not below and not above:
        return Status.OK

    if minimum is not None and maximum is not None:
        tolerance = (maximum - minimum) * WARN_TOLERANCE
    else:
        reference = abs(minimum if minimum is not None else maximum)
        tolerance = reference * WARN_TOLERANCE

    deviation = (minimum - value) if below else (value - maximum)
    return Status.WARN if deviation <= tolerance else Status.CRITICAL


class MeasurementQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(tank__owner=user)


class Measurement(models.Model):
    tank = models.ForeignKey(Tank, related_name="measurements", on_delete=models.CASCADE)
    parameter = models.ForeignKey(Parameter, related_name="measurements", on_delete=models.PROTECT)
    value = models.DecimalField("Wert", max_digits=8, decimal_places=3)
    measured_at = models.DateTimeField("Gemessen am")
    note = models.CharField("Notiz", max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = MeasurementQuerySet.as_manager()

    class Meta:
        ordering = ["-measured_at"]
        verbose_name = "Messwert"
        verbose_name_plural = "Messwerte"
        indexes = [models.Index(fields=["tank", "parameter", "-measured_at"])]

    def __str__(self):
        return f"{self.parameter}: {self.display_value}"

    @property
    def display_value(self):
        return f"{self.parameter.format_value(self.value)} {self.parameter.unit}".strip()

    def target_range(self, targets=None):
        """Zielbereich des Beckens, sonst der Parameter-Standard.

        ``targets`` erlaubt es, eine vorgeladene Zuordnung
        ``{parameter_id: TankParameterTarget}`` mitzugeben und so eine Abfrage
        je Messwert zu sparen.
        """
        target = None
        if targets is not None:
            target = targets.get(self.parameter_id)
        else:
            target = TankParameterTarget.objects.filter(
                tank_id=self.tank_id, parameter_id=self.parameter_id
            ).first()
        if target is not None:
            return target.minimum, target.maximum
        return self.parameter.default_min, self.parameter.default_max

    def status(self, targets=None):
        minimum, maximum = self.target_range(targets)
        return classify_value(self.value, minimum, maximum)

    @property
    def status_value(self):
        """Template-freundlicher Zugriff (Templates rufen keine Argumente auf)."""
        return self.status()


class Event(models.Model):
    class Category(models.TextChoices):
        WATER_CHANGE = "water_change", "Wasserwechsel"
        MAINTENANCE = "maintenance", "Wartung"
        TREATMENT = "treatment", "Behandlung"
        STOCKING = "stocking", "Besatzänderung"
        INCIDENT = "incident", "Vorfall"
        OTHER = "other", "Sonstiges"

    tank = models.ForeignKey(Tank, related_name="events", on_delete=models.CASCADE)
    category = models.CharField("Kategorie", max_length=15, choices=Category.choices, default=Category.OTHER)
    title = models.CharField("Titel", max_length=160)
    description = models.TextField("Beschreibung", blank=True)
    occurred_at = models.DateTimeField("Zeitpunkt")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name = "Ereignis"
        verbose_name_plural = "Ereignisse"

    def __str__(self):
        return self.title


class Stocking(models.Model):
    """Besatz: eine Tierart in einem Becken."""

    tank = models.ForeignKey(Tank, related_name="stockings", on_delete=models.CASCADE)
    species = models.ForeignKey("catalog.AnimalSpecies", related_name="stockings", on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField("Anzahl", default=1)
    added_on = models.DateField("Eingesetzt am")
    removed_on = models.DateField("Entnommen am", null=True, blank=True)
    note = models.CharField("Notiz", max_length=200, blank=True)

    class Meta:
        ordering = ["species__scientific_name"]
        verbose_name = "Besatz"
        verbose_name_plural = "Besatz"

    def __str__(self):
        return f"{self.quantity}× {self.species}"

    @property
    def is_active(self):
        return self.removed_on is None

    @property
    def group_status(self):
        """Gruppengröße unterschritten? Gilt nur für aktiven Besatz."""
        if not self.is_active or self.species.min_group_size <= 1:
            return Status.OK
        if self.quantity >= self.species.min_group_size:
            return Status.OK
        return Status.WARN


class Planting(models.Model):
    """Bepflanzung: eine Pflanzenart in einem Becken."""

    tank = models.ForeignKey(Tank, related_name="plantings", on_delete=models.CASCADE)
    species = models.ForeignKey("catalog.PlantSpecies", related_name="plantings", on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField("Anzahl", default=1)
    planted_on = models.DateField("Gepflanzt am")
    removed_on = models.DateField("Entfernt am", null=True, blank=True)
    note = models.CharField("Notiz", max_length=200, blank=True)

    class Meta:
        ordering = ["species__scientific_name"]
        verbose_name = "Bepflanzung"
        verbose_name_plural = "Bepflanzung"

    def __str__(self):
        return f"{self.quantity}× {self.species}"

    @property
    def is_active(self):
        return self.removed_on is None


class CareTaskQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(tank__owner=user)

    def open(self, today=None, horizon_days=UPCOMING_DAYS):
        """Fällige, überfällige und in Kürze anstehende Termine."""
        today = today or timezone.localdate()
        return self.filter(
            is_active=True,
            tank__dissolved_on__isnull=True,
            due_on__lte=today + timedelta(days=horizon_days),
        )


class CareTask(models.Model):
    """Wiederkehrender oder einmaliger Termin an einem Becken."""

    class Category(models.TextChoices):
        WATER_CHANGE = "water_change", "Wasserwechsel"
        FILTER = "filter", "Filterwartung"
        FERTILIZER = "fertilizer", "Düngung"
        TEST = "test", "Wassertest"
        EQUIPMENT = "equipment", "Technik"
        OTHER = "other", "Sonstiges"

    tank = models.ForeignKey(Tank, related_name="tasks", on_delete=models.CASCADE)
    title = models.CharField("Titel", max_length=160)
    category = models.CharField("Kategorie", max_length=15, choices=Category.choices, default=Category.OTHER)
    interval_days = models.PositiveSmallIntegerField(
        "Intervall (Tage)", null=True, blank=True, help_text="Leer lassen für einmalige Termine."
    )
    due_on = models.DateField("Fällig am")
    last_completed_on = models.DateField("Zuletzt erledigt", null=True, blank=True)
    is_active = models.BooleanField("Aktiv", default=True)
    notes = models.TextField("Notizen", blank=True)

    objects = CareTaskQuerySet.as_manager()

    class Meta:
        ordering = ["due_on", "title"]
        verbose_name = "Termin"
        verbose_name_plural = "Termine"
        indexes = [models.Index(fields=["due_on", "is_active"])]

    def __str__(self):
        return self.title

    def days_until_due(self, today=None):
        today = today or timezone.localdate()
        return (self.due_on - today).days

    def status(self, today=None):
        days = self.days_until_due(today)
        if days < 0:
            return Status.CRITICAL
        if days == 0:
            return Status.WARN
        return Status.UNKNOWN

    @property
    def status_value(self):
        return self.status()

    @property
    def due_label(self):
        days = self.days_until_due()
        if days < -1:
            return f"{abs(days)} Tage überfällig"
        if days == -1:
            return "1 Tag überfällig"
        if days == 0:
            return "heute fällig"
        if days == 1:
            return "morgen fällig"
        return f"in {days} Tagen"

    def complete(self, on=None, user=None, note=""):
        """Termin quittieren und — bei Intervall — neu einplanen.

        Der nächste Termin wird ab dem Erledigungsdatum gerechnet, nicht ab dem
        alten Fälligkeitsdatum: sonst häufen sich bei einem lange liegen
        gebliebenen Termin sofort mehrere neue Fälligkeiten an.
        """
        on = on or timezone.localdate()
        completion = TaskCompletion.objects.create(
            task=self, completed_on=on, completed_by=user, note=note
        )
        self.last_completed_on = on
        if self.interval_days:
            self.due_on = on + timedelta(days=self.interval_days)
        else:
            self.is_active = False
        self.save(update_fields=["last_completed_on", "due_on", "is_active"])
        return completion


class TaskCompletion(models.Model):
    task = models.ForeignKey(CareTask, related_name="completions", on_delete=models.CASCADE)
    completed_on = models.DateField("Erledigt am")
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField("Notiz", max_length=200, blank=True)

    class Meta:
        ordering = ["-completed_on", "-pk"]
        verbose_name = "Quittierung"
        verbose_name_plural = "Quittierungen"

    def __str__(self):
        return f"{self.task} · {self.completed_on}"


class Device(models.Model):
    """Technik am Becken: Filter, Heizer, Beleuchtung, Sensoren, Steckdosen."""

    class Kind(models.TextChoices):
        FILTER = "filter", "Filter"
        HEATER = "heater", "Heizer"
        LIGHT = "light", "Beleuchtung"
        CO2 = "co2", "CO₂-Anlage"
        PUMP = "pump", "Pumpe"
        DOSER = "doser", "Dosierpumpe"
        SENSOR = "sensor", "Sensor"
        SOCKET = "socket", "Steckdose"
        OTHER = "other", "Sonstiges"

    tank = models.ForeignKey(Tank, related_name="devices", on_delete=models.CASCADE)
    name = models.CharField("Name", max_length=120)
    kind = models.CharField("Art", max_length=10, choices=Kind.choices, default=Kind.OTHER)
    manufacturer = models.CharField("Hersteller", max_length=80, blank=True)
    model_name = models.CharField("Modell", max_length=80, blank=True)
    installed_on = models.DateField("In Betrieb seit", null=True, blank=True)
    status = models.CharField(
        "Status", max_length=10, choices=Status.choices, default=Status.OK
    )
    status_message = models.CharField("Statusmeldung", max_length=200, blank=True)
    last_seen_at = models.DateTimeField("Zuletzt erreicht", null=True, blank=True)
    maintenance_interval_days = models.PositiveSmallIntegerField(
        "Wartungsintervall (Tage)", null=True, blank=True
    )
    last_maintenance_on = models.DateField("Letzte Wartung", null=True, blank=True)

    class Meta:
        ordering = ["kind", "name"]
        verbose_name = "Gerät"
        verbose_name_plural = "Geräte"

    def __str__(self):
        return self.name

    @property
    def maintenance_due_on(self):
        if not self.maintenance_interval_days:
            return None
        reference = self.last_maintenance_on or self.installed_on
        if reference is None:
            return None
        return reference + timedelta(days=self.maintenance_interval_days)

    def maintenance_status(self, today=None):
        due = self.maintenance_due_on
        if due is None:
            return Status.UNKNOWN
        today = today or timezone.localdate()
        if due < today:
            return Status.CRITICAL
        if due <= today + timedelta(days=UPCOMING_DAYS):
            return Status.WARN
        return Status.OK

    @property
    def maintenance_status_value(self):
        return self.maintenance_status()


class TankPhoto(models.Model):
    tank = models.ForeignKey(Tank, related_name="photos", on_delete=models.CASCADE)
    image = models.ImageField("Bild", upload_to=tank_photo_path)
    caption = models.CharField("Bildunterschrift", max_length=200, blank=True)
    taken_on = models.DateField("Aufgenommen am")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-taken_on", "-pk"]
        verbose_name = "Foto"
        verbose_name_plural = "Fotos"

    def __str__(self):
        return self.caption or f"Foto {self.taken_on}"


def species_in_own_tanks(user, *, animal=None, plant=None):
    """Becken des Benutzers, in denen eine Art aktuell vorkommt.

    Wird auf den Katalog-Detailseiten gebraucht („in welchen eigenen Becken
    kommt die Art vor").
    """
    if animal is not None:
        condition = Q(stockings__species=animal, stockings__removed_on__isnull=True)
    elif plant is not None:
        condition = Q(plantings__species=plant, plantings__removed_on__isnull=True)
    else:
        return Tank.objects.none()
    return Tank.objects.for_user(user).filter(condition).distinct()
