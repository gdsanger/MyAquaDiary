import calendar
import io
from datetime import datetime, timedelta
from decimal import Decimal

from PIL import Image

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from catalog.models import CatalogAnimal, CatalogPlant

# EXIF-IFD-Pointer und Tags, siehe Pillow-Doku zu Exif.get_ifd(). Kein
# piexif nötig, Pillow kann Sub-IFDs seit 6.0 selbst lesen/schreiben.
_EXIF_SUBIFD_TAG = 0x8769
_EXIF_GPSINFO_TAG = 0x8825
_EXIF_DATETIME_ORIGINAL_TAG = 36867
_THUMBNAIL_SIZE = (480, 480)


def read_exif_taken_at(image_file):
    """Liest den EXIF-Aufnahmezeitpunkt (DateTimeOriginal) eines hochgeladenen
    Fotos aus, als Vorbelegung für `Photo.taken_at` beim Sammel-Upload. Rein
    lesend und best-effort: ein Bild ohne oder mit kaputten EXIF-Daten liefert
    einfach `None`, statt den Upload scheitern zu lassen."""

    try:
        image_file.seek(0)
        with Image.open(image_file) as image:
            raw_datetime = image.getexif().get_ifd(_EXIF_SUBIFD_TAG).get(
                _EXIF_DATETIME_ORIGINAL_TAG
            )
    except Exception:
        return None
    finally:
        image_file.seek(0)
    if not raw_datetime:
        return None
    try:
        naive = datetime.strptime(raw_datetime, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


def _strip_gps_and_build_thumbnail(image_file):
    """Entfernt EXIF-GPS-Daten aus dem Originalbild und erzeugt daraus ein
    Thumbnail — beides einmalig beim Upload (siehe Photo.save), nicht bei
    jeder Anzeige einer 200-Bilder-Beckengalerie. Best-effort: schlägt das
    Verarbeiten fehl (z. B. unbekanntes Format), wird `(None, None)`
    zurückgegeben und das Originalbild unverändert gespeichert."""

    try:
        image_file.seek(0)
        with Image.open(image_file) as image:
            image.load()
            exif = image.getexif()
            if _EXIF_GPSINFO_TAG in exif:
                del exif[_EXIF_GPSINFO_TAG]
            image_format = image.format or "JPEG"
            save_kwargs = {"format": image_format}
            if exif:
                save_kwargs["exif"] = exif.tobytes()

            if image_format == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            cleaned_bytes = io.BytesIO()
            image.save(cleaned_bytes, **save_kwargs)

            thumbnail = image.copy()
            thumbnail.thumbnail(_THUMBNAIL_SIZE)
            thumbnail_bytes = io.BytesIO()
            thumbnail.save(thumbnail_bytes, format=image_format)
    except Exception:
        return None, None
    return ContentFile(cleaned_bytes.getvalue()), ContentFile(thumbnail_bytes.getvalue())


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
    """Gemeinsames Foto-Modell für Becken, Messung und Ereignis — eine Tabelle
    statt drei, damit sich die Beckengalerie mit einer einzigen Abfrage bauen
    lässt. `tank` bleibt Pflichtfeld, wird bei einem Messungs- oder
    Ereignisfoto aber automatisch aus der Zuordnung übernommen, so landet ein
    Belegfoto zugleich in der Becken-Galerie, ohne dass die Anlage doppelt
    gepflegt werden muss."""

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="tanks/%Y/%m/")
    thumbnail = models.ImageField(upload_to="tanks/%Y/%m/thumbs/", blank=True)
    taken_at = models.DateTimeField(default=timezone.now, blank=True)
    caption = models.CharField(max_length=200, blank=True)

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

    is_full_tank_shot = models.BooleanField("Übersichtsfoto", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-taken_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=~(
                    models.Q(measurement__isnull=False) & models.Q(event__isnull=False)
                ),
                name="photo_not_both_measurement_and_event",
            ),
        ]

    def __str__(self):
        return f"{self.tank} – {self.caption or self.image.name}"

    def save(self, *args, **kwargs):
        if self.measurement_id and not self.tank_id:
            self.tank_id = self.measurement.tank_id
        if self.event_id and not self.tank_id:
            self.tank_id = self.event.tank_id

        if self.image and not self.image._committed:
            original_name = self.image.name
            cleaned_image, thumbnail_image = _strip_gps_and_build_thumbnail(self.image)
            if cleaned_image is not None:
                self.image.save(original_name, cleaned_image, save=False)
                self.thumbnail.save(original_name, thumbnail_image, save=False)

        super().save(*args, **kwargs)


@receiver(post_delete, sender=Photo)
def _delete_photo_files_from_storage(sender, instance, **kwargs):
    """Räumt Bild- und Thumbnail-Datei im Storage mit auf. Läuft auch bei
    kaskadierendem Löschen über Tank/Measurement/Event, da Djangos Collector
    für jede betroffene Zeile dieses Signal feuert, nicht nur bei
    `Photo.delete()` direkt."""

    for field_file in (instance.image, instance.thumbnail):
        if field_file:
            field_file.storage.delete(field_file.name)


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


def _add_months(date, months):
    """Kalendermonate addieren statt fixer Tagesanzahl, sonst wandert ein
    monatlicher Termin über die Zeit durch den Monat (28–31 Tage Drift)."""

    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, calendar.monthrange(year, month)[1])
    return date.replace(year=year, month=month, day=day)


class MaintenanceSchedule(models.Model):
    """Fälligkeits-Timer für wiederkehrende Pflege (Filterreinigung,
    Wasserwechsel-Rhythmus etc.). Genau ein nächster Termin je Serie — keine
    `Occurrence`-Historie versäumter Einzeltermine, siehe Modul-Docstring
    dieses Tickets."""

    class Interval(models.TextChoices):
        DAILY = "daily", "Täglich"
        WEEKLY = "weekly", "Wöchentlich"
        BIWEEKLY = "biweekly", "Alle 2 Wochen"
        MONTHLY = "monthly", "Monatlich"
        QUARTERLY = "quarterly", "Vierteljährlich"
        YEARLY = "yearly", "Jährlich"
        CUSTOM = "custom", "Individuell (Tage)"

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="schedules")
    title = models.CharField(max_length=200)
    event_category = models.CharField(
        "Ereigniskategorie",
        max_length=20,
        choices=Event.Category.choices,
        default=Event.Category.MAINTENANCE,
    )
    description = models.TextField(blank=True)

    interval = models.CharField(max_length=10, choices=Interval.choices, default=Interval.MONTHLY)
    interval_days = models.PositiveSmallIntegerField(
        "Intervall (Tage)", null=True, blank=True
    )
    next_due_on = models.DateField("Nächster Termin", default=timezone.localdate)
    last_done_on = models.DateField("Zuletzt erledigt", null=True, blank=True)
    last_reminded_on = models.DateField(null=True, blank=True)

    lead_days = models.PositiveSmallIntegerField("Vorlauf (Tage)", default=2)
    notify_email = models.BooleanField("Per E-Mail erinnern", default=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_due_on"]

    def __str__(self):
        return f"{self.tank} – {self.title}"

    def clean(self):
        super().clean()
        if self.interval == self.Interval.CUSTOM and not self.interval_days:
            raise ValidationError(
                {"interval_days": "Bei individuellem Intervall ist die Tagesanzahl erforderlich."}
            )

    @property
    def is_due(self):
        return self.is_active and self.next_due_on <= timezone.localdate()

    @property
    def is_upcoming(self):
        if not self.is_active or self.is_due:
            return False
        return self.next_due_on <= timezone.localdate() + timedelta(days=self.lead_days)

    def compute_next_due_on(self, from_date):
        if self.interval == self.Interval.DAILY:
            return from_date + timedelta(days=1)
        if self.interval == self.Interval.WEEKLY:
            return from_date + timedelta(days=7)
        if self.interval == self.Interval.BIWEEKLY:
            return from_date + timedelta(days=14)
        if self.interval == self.Interval.MONTHLY:
            return _add_months(from_date, 1)
        if self.interval == self.Interval.QUARTERLY:
            return _add_months(from_date, 3)
        if self.interval == self.Interval.YEARLY:
            return _add_months(from_date, 12)
        return from_date + timedelta(days=self.interval_days)

    def mark_done(self, done_on):
        """Rechnet den nächsten Termin vom Erledigungsdatum aus fort statt
        vom alten Solltermin — sonst türmen sich nach einer versäumten
        Woche mehrere Fälligkeiten übereinander."""

        self.last_done_on = done_on
        self.next_due_on = self.compute_next_due_on(done_on)
        self.save(update_fields=["last_done_on", "next_due_on"])


class StockStatus(models.TextChoices):
    """Gemeinsame Stati für Besatz (TankAnimal) und Bepflanzung (TankPlant)."""

    PLANNED = "planned", "Geplant"
    PRESENT = "present", "Vorhanden"
    TEMPORARY = "temporary", "Temporär"
    GONE = "gone", "Abgegeben / eingegangen"


# (Parameter-Key, Toleranz-Attribut min, Toleranz-Attribut max) — dieselben
# Kürzel wie in seed_parameters.py. CatalogPlant kennt kein gh_min/gh_max,
# getattr liefert dafür None und der Check wird übersprungen.
_TOLERANCE_CHECKS = [
    ("temp", "temp_min_c", "temp_max_c"),
    ("ph", "ph_min", "ph_max"),
    ("kh", "kh_min", "kh_max"),
    ("gh", "gh_min", "gh_max"),
]


def _tolerance_warnings(tank, catalog_entry):
    """Vergleicht die zuletzt erfasste Messung des Beckens mit der Toleranz
    der Art/Pflanze. Ohne Messung oder ohne hinterlegte Toleranz gibt es
    nichts zu warnen — beides ist der Normalfall bei neu angelegten Becken."""

    measurement = tank.measurements.first()
    if measurement is None:
        return []
    warnings = []
    for parameter_key, min_attr, max_attr in _TOLERANCE_CHECKS:
        tolerance_min = getattr(catalog_entry, min_attr, None)
        tolerance_max = getattr(catalog_entry, max_attr, None)
        if tolerance_min is None and tolerance_max is None:
            continue
        measured = measurement.value_for(parameter_key)
        if measured is None or measured.value is None:
            continue
        if (tolerance_min is not None and measured.value < tolerance_min) or (
            tolerance_max is not None and measured.value > tolerance_max
        ):
            warnings.append(
                {
                    "parameter": measured.parameter,
                    "value": measured.value,
                    "minimum": tolerance_min,
                    "maximum": tolerance_max,
                }
            )
    return warnings


class TankAnimal(models.Model):
    """Was tatsächlich im Becken schwimmt/lebt — der Katalogeintrag liefert
    den Steckbrief, hier steht Anzahl und Status. `quantity` ist der
    fortgeschriebene Bestand, siehe `TankAnimalMovement` für die Historie
    dahinter."""

    Status = StockStatus

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="animals")
    animal = models.ForeignKey(
        CatalogAnimal, on_delete=models.PROTECT, related_name="tank_stock"
    )
    label = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=StockStatus.choices, default=StockStatus.PRESENT)

    quantity = models.PositiveIntegerField(default=1)
    quantity_male = models.PositiveSmallIntegerField(null=True, blank=True)
    quantity_female = models.PositiveSmallIntegerField(null=True, blank=True)

    added_on = models.DateField(null=True, blank=True)
    origin = models.CharField(max_length=160, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["animal__scientific_name", "animal__variety"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gte=0), name="tankanimal_quantity_gte_0"
            ),
        ]

    def __str__(self):
        return f"{self.tank} – {self.label or self.animal}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and self.quantity:
            # Die Ersterfassung ist selbst ein Zugang — so bleibt `quantity`
            # durchgehend die Summe gebuchter Bewegungen, ohne dass die erste
            # Bewegung von Hand nachgetragen werden muss.
            TankAnimalMovement(
                tank_animal=self,
                direction=TankAnimalMovement.Direction.IN,
                reason=TankAnimalMovement.Reason.PURCHASE,
                quantity=self.quantity,
                occurred_on=self.added_on or timezone.localdate(),
                note="Ersterfassung",
            ).save(apply_to_stock=False)

    @property
    def is_below_min_group_size(self):
        min_group_size = self.animal.min_group_size
        if not min_group_size or self.status != StockStatus.PRESENT:
            return False
        return self.quantity < min_group_size

    @property
    def parameter_warnings(self):
        return _tolerance_warnings(self.tank, self.animal)

    @property
    def stock_matches_movements(self):
        """Konsistenz-Check: Summe der Bewegungen muss dem fortgeschriebenen
        Bestand entsprechen. Weicht das ab, ist irgendwo eine Bewegung ohne
        Fortschreibung erfasst worden (z. B. durch einen Datenimport)."""

        net = 0
        for movement in self.movements.all():
            net += movement.quantity if movement.direction == TankAnimalMovement.Direction.IN else -movement.quantity
        return net == self.quantity


class TankAnimalMovement(models.Model):
    """Zu- und Abgänge — die Historie hinter TankAnimal.quantity. Bewegungen
    sind ein Buchungsjournal: einmal gebucht, wird nicht mehr verändert,
    nur storniert (gelöscht, was die Buchung zurücknimmt)."""

    class Direction(models.TextChoices):
        IN = "in", "Zugang"
        OUT = "out", "Abgang"

    class Reason(models.TextChoices):
        PURCHASE = "purchase", "Zukauf"
        BREEDING = "breeding", "Eigene Nachzucht"
        TRANSFER_IN = "transfer_in", "Umsetzung aus anderem Becken"
        TRANSFER_OUT = "transfer_out", "Umsetzung in anderes Becken"
        SOLD = "sold", "Verkauft / abgegeben"
        DIED = "died", "Eingegangen"
        PREDATION = "predation", "Gefressen"
        JUMPED = "jumped", "Gesprungen"
        UNKNOWN = "unknown", "Unbekannt / verschwunden"

    tank_animal = models.ForeignKey(TankAnimal, on_delete=models.CASCADE, related_name="movements")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    occurred_on = models.DateField()
    # Nur die Verknüpfung, siehe Modul-Docstring: die Gegenbuchung im
    # Zielbecken erfolgt in v1 manuell, nicht automatisch.
    target_tank = models.ForeignKey(
        Tank, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.TextField(blank=True)
    event = models.ForeignKey(
        Event, null=True, blank=True, on_delete=models.SET_NULL, related_name="animal_movements"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_on", "-id"]

    def __str__(self):
        return f"{self.tank_animal} – {self.get_direction_display()} ({self.quantity})"

    def clean(self):
        super().clean()
        if self.direction == self.Direction.OUT and self.reason == self.Reason.TRANSFER_OUT and not self.target_tank_id:
            raise ValidationError(
                {"target_tank": "Bei Umsetzung in ein anderes Becken ist das Zielbecken erforderlich."}
            )

    def save(self, *args, apply_to_stock=True, **kwargs):
        is_new = self._state.adding
        if is_new and apply_to_stock:
            with transaction.atomic():
                self._apply_to_stock()
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def _apply_to_stock(self):
        tank_animal = TankAnimal.objects.get(pk=self.tank_animal_id)
        delta = self.quantity if self.direction == self.Direction.IN else -self.quantity
        new_quantity = tank_animal.quantity + delta
        if new_quantity < 0:
            raise ValidationError({"quantity": "Der Bestand kann nicht unter null fallen."})
        tank_animal.quantity = new_quantity
        tank_animal.save(update_fields=["quantity"])

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            tank_animal = TankAnimal.objects.get(pk=self.tank_animal_id)
            delta = -self.quantity if self.direction == self.Direction.IN else self.quantity
            new_quantity = tank_animal.quantity + delta
            if new_quantity < 0:
                raise ValidationError(
                    "Diese Bewegung kann nicht storniert werden, der Bestand würde unter null fallen."
                )
            tank_animal.quantity = new_quantity
            tank_animal.save(update_fields=["quantity"])
            super().delete(*args, **kwargs)


class TankPlant(models.Model):
    """Was tatsächlich im Becken wächst. Anders als bei Tieren gibt es keine
    Bewegungshistorie — eine Stängelpflanze wird gestutzt und vermehrt sich,
    das exakt zu zählen hat keinen Nutzen (siehe Modul-Docstring)."""

    Status = StockStatus

    tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="plants")
    plant = models.ForeignKey(
        CatalogPlant, on_delete=models.PROTECT, related_name="tank_stock"
    )
    status = models.CharField(max_length=10, choices=StockStatus.choices, default=StockStatus.PRESENT)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    placement = models.CharField(max_length=120, blank=True)
    attached_to = models.CharField(max_length=120, blank=True)
    added_on = models.DateField(null=True, blank=True)
    removed_on = models.DateField(null=True, blank=True)
    identification_certain = models.BooleanField(default=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["plant__scientific_name", "plant__cultivar"]

    def __str__(self):
        return f"{self.tank} – {self.plant}"

    @property
    def parameter_warnings(self):
        return _tolerance_warnings(self.tank, self.plant)
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
