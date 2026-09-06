"""Ersatzmodelle für die Tagebuch-Daten in den MCP-Tests.

Becken und Katalog entstehen in einem anderen Schritt des Epics; auf diesem
Stand gibt es sie noch nicht (siehe :mod:`services.mcp.data`). Getestet werden
sollen die Werkzeuge aber gegen echte Modelle mit echten Abfragen — ein
handgeschriebenes QuerySet-Attrappe würde genau das nicht prüfen, worauf es
hier ankommt: dass die Eingrenzung auf den Token-Inhaber in der Datenbank
stattfindet und nicht im Kopf des Autors.

Deshalb stehen hier verkleinerte Nachbauten der Modelle aus dem Datenmodell-
Schritt: dieselben Feldnamen, dieselben Zusammenhänge, dieselbe Fortschreibung
von Beständen und Terminen — nur ohne Fotos, Slugs und Oberflächen-Beiwerk.
Ihre Tabellen legt :class:`DataModelTestCase` je Testklasse an und wieder ab;
``services.mcp.data.MODELS`` zeigt währenddessen auf sie.

Nach dem Zusammenführen mit dem Datenmodell-Schritt bleibt dieses Modul
brauchbar (die echten Modelle heißen anders und liegen in anderen Apps) — die
Tests, die die echten Modelle benutzen wollen, ziehen dann einfach um.
"""

import calendar
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.test import TestCase
from django.utils import timezone

from services.mcp import data

#: Zuordnung für ``services.mcp.data.MODELS`` — dieselben Kurznamen, andere
#: Modelle. Mehr braucht es nicht, damit die Werkzeuge hier laufen.
STUB_MODELS = {
    "tank": ("services", "StubTank"),
    "parameter": ("services", "StubParameter"),
    "measurement": ("services", "StubMeasurement"),
    "measurement_value": ("services", "StubMeasurementValue"),
    "event": ("services", "StubEvent"),
    "schedule": ("services", "StubMaintenanceSchedule"),
    "tank_animal": ("services", "StubTankAnimal"),
    "tank_animal_movement": ("services", "StubTankAnimalMovement"),
    "tank_plant": ("services", "StubTankPlant"),
    "catalog_animal": ("services", "StubCatalogAnimal"),
    "catalog_plant": ("services", "StubCatalogPlant"),
}


class StubCatalogAnimal(models.Model):
    class Meta:
        app_label = "services"

    scientific_name = models.CharField(max_length=160)
    variety = models.CharField(max_length=80, blank=True)
    common_name = models.CharField(max_length=160, blank=True)
    group = models.CharField(
        max_length=20,
        choices=[("fisch", "Fisch"), ("garnele", "Garnele")],
        blank=True,
    )
    family = models.CharField(max_length=100, blank=True)
    origin = models.CharField(max_length=160, blank=True)
    difficulty = models.CharField(
        max_length=20, choices=[("easy", "einfach"), ("medium", "mittel")], default="easy"
    )
    min_group_size = models.PositiveSmallIntegerField(null=True, blank=True)
    size_max_cm = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    temp_min_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    temp_max_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    description = models.TextField(blank=True)
    verified = models.BooleanField(default=False)

    def __str__(self):
        if self.variety:
            return f"{self.scientific_name} '{self.variety}'"
        return self.scientific_name


class StubCatalogPlant(models.Model):
    class Meta:
        app_label = "services"

    scientific_name = models.CharField(max_length=160)
    cultivar = models.CharField(max_length=80, blank=True)
    common_name = models.CharField(max_length=160, blank=True)
    difficulty = models.CharField(
        max_length=20, choices=[("easy", "einfach"), ("medium", "mittel")], default="easy"
    )
    growth_form = models.CharField(
        max_length=20, choices=[("stem", "Stängel"), ("moss", "Moos")], blank=True
    )
    description = models.TextField(blank=True)
    verified = models.BooleanField(default=False)

    def __str__(self):
        if self.cultivar:
            return f"{self.scientific_name} '{self.cultivar}'"
        return self.scientific_name


class StubTankQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(owner=user)


class StubTank(models.Model):
    class Meta:
        app_label = "services"
        ordering = ["name"]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stub_tanks"
    )
    name = models.CharField(max_length=120)
    model_name = models.CharField(max_length=120, blank=True)
    length_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    height_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    depth_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    volume_gross_l = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    volume_net_l = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    water_type = models.CharField(
        max_length=10,
        choices=[("suess", "Süßwasser"), ("meer", "Meerwasser")],
        default="suess",
    )
    biotope = models.CharField(max_length=120, blank=True)
    started_on = models.DateField(default=timezone.localdate)
    shut_down_on = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    substrate = models.TextField(blank=True)
    hardscape = models.TextField(blank=True)
    filtration = models.TextField(blank=True)
    lighting = models.TextField(blank=True)
    co2 = models.TextField(blank=True)
    fertilization = models.TextField(blank=True)

    objects = StubTankQuerySet.as_manager()

    def __str__(self):
        return self.name

    @property
    def is_dissolved(self):
        return self.shut_down_on is not None


class StubParameter(models.Model):
    class Meta:
        app_label = "services"
        ordering = ["position", "name"]

    key = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    unit = models.CharField(max_length=20, blank=True)
    position = models.PositiveSmallIntegerField(default=100)
    supports_below_detection = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class StubTankParameterTarget(models.Model):
    class Meta:
        app_label = "services"

    tank = models.ForeignKey(StubTank, on_delete=models.CASCADE, related_name="targets")
    parameter = models.ForeignKey(StubParameter, on_delete=models.PROTECT, related_name="+")
    target = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    minimum = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    maximum = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)


class StubMeasurement(models.Model):
    class Meta:
        app_label = "services"
        ordering = ["-measured_at"]

    tank = models.ForeignKey(StubTank, on_delete=models.CASCADE, related_name="measurements")
    measured_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)
    source = models.CharField(
        max_length=10,
        choices=[("manual", "Manuell"), ("device", "Gerät"), ("import", "Import")],
        default="manual",
    )

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
        kh = self.value_for("kh")
        ph = self.value_for("ph")
        if kh is None or ph is None:
            return None
        return Decimal(str(round(3 * float(kh.value) * (10 ** (7 - float(ph.value))), 1)))


class StubMeasurementValue(models.Model):
    class Meta:
        app_label = "services"
        ordering = ["parameter__position", "parameter__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["measurement", "parameter"], name="unique_stub_measurement_parameter"
            ),
        ]

    measurement = models.ForeignKey(
        StubMeasurement, on_delete=models.CASCADE, related_name="values"
    )
    parameter = models.ForeignKey(StubParameter, on_delete=models.PROTECT, related_name="+")
    value = models.DecimalField(max_digits=9, decimal_places=3, null=True, blank=True)
    below_detection = models.BooleanField(default=False)

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
        bounds = self.target
        if bounds is None:
            return None
        if bounds.minimum is not None and self.value < bounds.minimum:
            return "low"
        if bounds.maximum is not None and self.value > bounds.maximum:
            return "high"
        return "ok"


class StubMaintenanceSchedule(models.Model):
    class Meta:
        app_label = "services"
        ordering = ["next_due_on"]

    class Interval(models.TextChoices):
        WEEKLY = "weekly", "Wöchentlich"
        MONTHLY = "monthly", "Monatlich"

    tank = models.ForeignKey(StubTank, on_delete=models.CASCADE, related_name="schedules")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    event_category = models.CharField(max_length=20, default="maintenance")
    interval = models.CharField(max_length=10, choices=Interval.choices, default=Interval.MONTHLY)
    next_due_on = models.DateField(default=timezone.localdate)
    last_done_on = models.DateField(null=True, blank=True)
    lead_days = models.PositiveSmallIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    @property
    def is_due(self):
        return self.is_active and self.next_due_on <= timezone.localdate()

    @property
    def is_upcoming(self):
        if not self.is_active or self.is_due:
            return False
        return self.next_due_on <= timezone.localdate() + timedelta(days=self.lead_days)

    def compute_next_due_on(self, from_date):
        if self.interval == self.Interval.WEEKLY:
            return from_date + timedelta(days=7)
        month_index = from_date.month
        year = from_date.year + month_index // 12
        month = month_index % 12 + 1
        return from_date.replace(
            year=year, month=month, day=min(from_date.day, calendar.monthrange(year, month)[1])
        )

    def mark_done(self, done_on):
        self.last_done_on = done_on
        self.next_due_on = self.compute_next_due_on(done_on)
        self.save(update_fields=["last_done_on", "next_due_on"])


class StubEvent(models.Model):
    class Meta:
        app_label = "services"
        ordering = ["-occurred_at"]

    class Category(models.TextChoices):
        WATER_CHANGE = "water_change", "Wasserwechsel"
        MAINTENANCE = "maintenance", "Pflege"
        OBSERVATION = "observation", "Beobachtung"
        OTHER = "other", "Sonstiges"

    tank = models.ForeignKey(StubTank, on_delete=models.CASCADE, related_name="events")
    occurred_at = models.DateTimeField(default=timezone.now)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    water_changed_l = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    schedule = models.ForeignKey(
        StubMaintenanceSchedule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )

    @property
    def water_change_percent(self):
        if self.water_changed_l is None or not self.tank.volume_net_l:
            return None
        return (self.water_changed_l / self.tank.volume_net_l * 100).quantize(Decimal("0.1"))


class StubStockStatus(models.TextChoices):
    PLANNED = "planned", "Geplant"
    PRESENT = "present", "Vorhanden"
    GONE = "gone", "Abgegeben / eingegangen"


class StubTankAnimal(models.Model):
    class Meta:
        app_label = "services"

    Status = StubStockStatus

    tank = models.ForeignKey(StubTank, on_delete=models.CASCADE, related_name="animals")
    animal = models.ForeignKey(StubCatalogAnimal, on_delete=models.PROTECT, related_name="+")
    label = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=10, choices=StubStockStatus.choices, default=StubStockStatus.PRESENT
    )
    quantity = models.PositiveIntegerField(default=1)
    quantity_male = models.PositiveSmallIntegerField(null=True, blank=True)
    quantity_female = models.PositiveSmallIntegerField(null=True, blank=True)
    added_on = models.DateField(null=True, blank=True)
    origin = models.CharField(max_length=160, blank=True)
    note = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and self.quantity:
            StubTankAnimalMovement(
                tank_animal=self,
                direction=StubTankAnimalMovement.Direction.IN,
                reason=StubTankAnimalMovement.Reason.PURCHASE,
                quantity=self.quantity,
                occurred_on=self.added_on or timezone.localdate(),
                note="Ersterfassung",
            ).save(apply_to_stock=False)

    @property
    def is_below_min_group_size(self):
        minimum = self.animal.min_group_size
        if not minimum or self.status != StubStockStatus.PRESENT:
            return False
        return self.quantity < minimum


class StubTankAnimalMovement(models.Model):
    class Meta:
        app_label = "services"
        ordering = ["-occurred_on", "-id"]

    class Direction(models.TextChoices):
        IN = "in", "Zugang"
        OUT = "out", "Abgang"

    class Reason(models.TextChoices):
        PURCHASE = "purchase", "Zukauf"
        BREEDING = "breeding", "Eigene Nachzucht"
        TRANSFER_OUT = "transfer_out", "Umsetzung in anderes Becken"
        DIED = "died", "Eingegangen"

    tank_animal = models.ForeignKey(
        StubTankAnimal, on_delete=models.CASCADE, related_name="movements"
    )
    direction = models.CharField(max_length=3, choices=Direction.choices)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    quantity = models.PositiveIntegerField()
    occurred_on = models.DateField()
    target_tank = models.ForeignKey(
        StubTank, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.TextField(blank=True)

    def clean(self):
        super().clean()
        if (
            self.direction == self.Direction.OUT
            and self.reason == self.Reason.TRANSFER_OUT
            and not self.target_tank_id
        ):
            raise ValidationError(
                {"target_tank": "Bei Umsetzung in ein anderes Becken ist das Zielbecken erforderlich."}
            )

    def save(self, *args, apply_to_stock=True, **kwargs):
        if self._state.adding and apply_to_stock:
            with transaction.atomic():
                self._apply_to_stock()
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def _apply_to_stock(self):
        stock = StubTankAnimal.objects.get(pk=self.tank_animal_id)
        delta = self.quantity if self.direction == self.Direction.IN else -self.quantity
        if stock.quantity + delta < 0:
            raise ValidationError({"quantity": "Der Bestand kann nicht unter null fallen."})
        stock.quantity += delta
        stock.save(update_fields=["quantity"])


class StubTankPlant(models.Model):
    class Meta:
        app_label = "services"

    Status = StubStockStatus

    tank = models.ForeignKey(StubTank, on_delete=models.CASCADE, related_name="plants")
    plant = models.ForeignKey(StubCatalogPlant, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(
        max_length=10, choices=StubStockStatus.choices, default=StubStockStatus.PRESENT
    )
    quantity = models.PositiveIntegerField(null=True, blank=True)
    placement = models.CharField(max_length=120, blank=True)
    attached_to = models.CharField(max_length=120, blank=True)
    added_on = models.DateField(null=True, blank=True)
    removed_on = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)


#: Anlegereihenfolge — ein Fremdschlüssel braucht seine Zieltabelle.
TABLE_ORDER = [
    StubCatalogAnimal,
    StubCatalogPlant,
    StubTank,
    StubParameter,
    StubTankParameterTarget,
    StubMeasurement,
    StubMeasurementValue,
    StubMaintenanceSchedule,
    StubEvent,
    StubTankAnimal,
    StubTankAnimalMovement,
    StubTankPlant,
]


class DataModelTestCase(TestCase):
    """Testfall mit vorhandenem Tagebuch-Datenmodell.

    Die Tabellen der Ersatzmodelle entstehen je Testklasse über den
    ``schema_editor`` — also unabhängig davon, ob die App Migrationen hat.
    Solange die Klasse läuft, zeigt ``services.mcp.data.MODELS`` auf sie.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            for model in TABLE_ORDER:
                editor.create_model(model)
        cls._original_models = dict(data.MODELS)
        data.MODELS.update(STUB_MODELS)

    @classmethod
    def tearDownClass(cls):
        data.MODELS.clear()
        data.MODELS.update(cls._original_models)
        with connection.schema_editor() as editor:
            for model in reversed(TABLE_ORDER):
                editor.delete_model(model)
        super().tearDownClass()
