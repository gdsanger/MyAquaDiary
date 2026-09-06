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
