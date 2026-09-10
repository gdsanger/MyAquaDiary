"""Becken und alles, was daran hängt: Messwerte, Ereignisse, Besatz,
Bepflanzung, Einrichtung, Termine und Fotos.

Die Geräte hängen ebenfalls am Becken, stehen aber in :mod:`services.models`:
es gibt genau ein Gerätemodell, und das trägt neben Hersteller und Wartung auch
die Anbindung (Eheim, Shelly). Über ``tank.devices`` ist es von hier aus
erreichbar."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Max, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from core.enums import Status, WaterType
from core.images import (
    CoverImageMixin,
    ImageVariantsMixin,
    cover_path,
    cover_preview_path,
    cover_thumb_path,
)
from core.values import ValueFormatMixin

from . import derived

#: Anzahl Farbkennungen für Becken (siehe ``.mad-tank-accent-*`` im Stylesheet).
TANK_ACCENT_COUNT = 8

#: Abweichung bis zu diesem Anteil der Zielbereichsbreite gilt noch als
#: „Abweichung" (orange), darüber als „kritisch" (rot).
WARN_TOLERANCE = Decimal("0.2")

#: Termine innerhalb dieses Zeitraums gelten als „anstehend".
UPCOMING_DAYS = 14

#: Standzeit eines Nährstoffdepots: vier Monate. Die Spanne reicht von drei bis
#: sechs Monaten und hängt an Produkt und Zehrung der Pflanzen — die Zahl ist
#: deshalb nur die Vorbelegung, überschrieben wird sie im Formular.
NUTRIENT_DEPOT_DAYS = 120

#: Erlenzapfen, Laub und Seemandelbaumblätter sind nach etwa sechs Wochen
#: erschöpft; danach geben sie keine Huminstoffe mehr ab.
BOTANICALS_DAYS = 42


# Der Ablageort des Titelbilds kommt jetzt aus ``core.images`` und gilt für
# jedes Modell mit Titelbild. Die drei alten Namen bleiben als Verweis stehen:
# Migration 0005 nennt sie, und eine gelaufene Migration schreibt man nicht um.
# Der Pfad bleibt derselbe — ``Tank.COVER_DIR`` ist ``tanks``.
tank_cover_path = cover_path
tank_cover_thumb_path = cover_thumb_path
tank_cover_preview_path = cover_preview_path


def format_cm(value):
    """Zentimeterangabe deutsch und ohne überflüssige Null: „6", „6,5"."""
    if value is None:
        return ""
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def tank_photo_path(instance, filename):
    return f"tanks/{instance.tank_id}/photos/{filename}"


def tank_thumb_path(instance, filename):
    return f"tanks/{instance.tank_id}/thumbs/{filename}"


def tank_preview_path(instance, filename):
    return f"tanks/{instance.tank_id}/preview/{filename}"


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


class Tank(CoverImageMixin, models.Model):
    """Ein Aquarium eines Benutzers."""

    #: Titelbild und Varianten stehen im Mixin; hier steht nur, wo sie liegen.
    COVER_DIR = "tanks"

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
    def has_history(self):
        """Hängt am Becken etwas Erfasstes?

        Entscheidet darüber, ob es gelöscht oder aufgelöst wird: Löschen ist
        für den Fehlgriff beim Anlegen da, alles andere hat eine Geschichte,
        die niemand versehentlich wegwerfen können soll.
        """
        related = [
            self.measurements,
            self.events,
            self.stockings,
            self.plantings,
            self.tasks,
            self.devices,
            self.photos,
            self.substrate_layers,
            self.hardscape,
            # Umzüge in beide Richtungen: sie hängen mit ``PROTECT`` am Becken.
            # Fehlten sie hier, versuchte die Löschansicht ein Becken zu
            # löschen, das die Datenbank nicht hergibt.
            self.transfers_out,
            self.transfers_in,
        ]
        return any(manager.exists() for manager in related)

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

    @property
    def substrate_depth_cm(self):
        """Gesamthöhe des Bodengrunds als Summe der Schichtmächtigkeiten.

        Gerechnet wird über ``.all()`` und damit über ein ``prefetch_related``,
        wo die Aufrufstelle eines gesetzt hat. Schichten ohne Angabe zählen
        nicht mit — eine fehlende Mächtigkeit ist keine Null.
        """
        depths = [layer.depth_cm for layer in self.substrate_layers.all() if layer.depth_cm]
        return sum(depths) if depths else None

    @property
    def setup_summary(self):
        """Einrichtung in einer Zeile: „6 cm Bodengrund · 3 Wurzeln · 5 Steine".

        Für die Übersicht gedacht, wo kein Platz für den Schichtstapel ist.
        Gezählt wird nur, was im Becken liegt; entferntes Hardscape gehört in
        die Geschichte, nicht in die Zusammenfassung.
        """
        parts = []
        depth = self.substrate_depth_cm
        if depth:
            parts.append(f"{format_cm(depth)} cm Bodengrund")

        counts = {}
        for item in self.hardscape.all():
            if item.is_active:
                counts[item.kind] = counts.get(item.kind, 0) + (item.quantity or 1)
        for kind, singular, plural in HardscapeItem.SUMMARY_LABELS:
            count = counts.get(kind)
            if count:
                parts.append(f"{count} {singular if count == 1 else plural}")
        return " · ".join(parts)


class Parameter(ValueFormatMixin, models.Model):
    """Wasserparameter (pH, NO₂, …) samt Standard-Zielbereich.

    Nur **gemessene** Größen stehen hier. Was gerechnet wird, hat bewusst
    keinen Eintrag — sonst böte das Erfassungsformular es zum Eintippen an
    (siehe :mod:`tanks.derived`).
    """

    key = models.SlugField("Schlüssel", max_length=30, unique=True)
    name = models.CharField("Name", max_length=60)
    unit = models.CharField("Einheit", max_length=20, blank=True)
    decimals = models.PositiveSmallIntegerField("Nachkommastellen", default=1)
    default_min = models.DecimalField("Zielbereich min", max_digits=8, decimal_places=3, null=True, blank=True)
    default_max = models.DecimalField("Zielbereich max", max_digits=8, decimal_places=3, null=True, blank=True)
    # Die Nachweisgrenze eines Tröpfchentests: alles darunter meldet der Test
    # als „nicht nachweisbar“. Sie dokumentiert zugleich, *welcher* Grenze ein
    # n.n. entspricht — wer den Testkoffer wechselt, sieht den Unterschied im
    # Verlauf. Leer bei Größen ohne Nachweisgrenze (pH, Temperatur, KH).
    detection_limit = models.DecimalField(
        "Nachweisgrenze",
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Leer lassen, wenn der Parameter keine hat (pH, Temperatur, KH).",
    )
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

    @property
    def has_detection_limit(self):
        """Kennt dieser Parameter eine Nachweisgrenze — darf es hier ein n.n. geben?"""
        return self.detection_limit is not None


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

    @property
    def range_label(self):
        """Zielbereich als Text — Templates rufen keine Methoden mit Argumenten."""
        return self.parameter.format_range(self.minimum, self.maximum)


class TankDerivedTarget(models.Model):
    """Beckenspezifischer Zielbereich einer abgeleiteten Größe (CO₂).

    Warum nicht :class:`TankParameterTarget`: dessen Zielbereich hängt an
    einem ``Parameter``, und genau den bekommt eine gerechnete Größe nicht
    (#1240). Angesprochen wird sie deshalb über ihren Schlüssel; welche es
    gibt, steht in :mod:`tanks.derived`.

    Die Vorgabe (CO₂: 15–25 mg/l) steht nicht in dieser Tabelle, sondern am
    Parameter im Code. Eine Zeile hier gibt es nur, wo jemand sie überschrieben
    hat — wie bei den gemessenen Größen auch.
    """

    tank = models.ForeignKey(Tank, related_name="derived_targets", on_delete=models.CASCADE)
    key = models.SlugField("Größe", max_length=30, choices=derived.CHOICES)
    minimum = models.DecimalField("min", max_digits=8, decimal_places=3, null=True, blank=True)
    maximum = models.DecimalField("max", max_digits=8, decimal_places=3, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tank", "key"], name="unique_derived_target_per_tank"),
        ]
        verbose_name = "Zielbereich (berechnet)"
        verbose_name_plural = "Zielbereiche (berechnet)"

    def __str__(self):
        return f"{self.tank} · {self.parameter}"

    @property
    def parameter(self):
        """Die abgeleitete Größe — ein Objekt aus dem Code, kein Datensatz."""
        return derived.DERIVED_PARAMETERS[self.key]

    @property
    def range_label(self):
        return self.parameter.format_range(self.minimum, self.maximum)


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


def classify_below_detection(minimum, maximum, detection_limit=None):
    """Statuslogik für einen nicht nachweisbaren Wert („n.n.“).

    „n.n.“ heißt: der wahre Wert liegt irgendwo zwischen null und der
    Nachweisgrenze — eine Zahl gibt es nicht, und deshalb greift
    :func:`classify_value` nicht.

    Für die Schadstoffe, um die es geht (Nitrit, Ammonium, Nitrat), kennt der
    Zielbereich nur eine Obergrenze; die ist unter der Nachweisgrenze
    zwangsläufig eingehalten, und n.n. ist **in Ordnung**. Wo ein Becken eine
    Untergrenze braucht (Phosphat als Pflanzennährstoff) und die Nachweisgrenze
    darunter liegt, ist der Wert dagegen erkennbar zu niedrig — bewertet wird
    dann die Nachweisgrenze als der höchstmögliche Wert.
    """
    if minimum is None and maximum is None:
        return Status.UNKNOWN
    if minimum is not None and (detection_limit is None or detection_limit < minimum):
        return classify_value(
            detection_limit if detection_limit is not None else Decimal(0), minimum, maximum
        )
    return Status.OK


class MeasurementQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(tank__owner=user)


class Measurement(models.Model):
    tank = models.ForeignKey(Tank, related_name="measurements", on_delete=models.CASCADE)
    parameter = models.ForeignKey(Parameter, related_name="measurements", on_delete=models.PROTECT)
    # ``null``, weil ein Wert unterhalb der Nachweisgrenze keine Zahl ist: „n.n.“
    # steht in ``below_detection``, nicht als 0 im Wert. 0 und n.n. sähen im
    # Verlauf sonst gleich aus, und ein Mittelwert würde falsch.
    value = models.DecimalField("Wert", max_digits=8, decimal_places=3, null=True, blank=True)
    below_detection = models.BooleanField(
        "nicht nachweisbar",
        default=False,
        help_text="Der Test hat unterhalb seiner Nachweisgrenze nichts angezeigt.",
    )
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
        constraints = [
            # Entweder eine Zahl oder n.n. — nie beides, nie keines. Die Regel
            # steht zusätzlich in ``clean()`` (mit lesbaren Meldungen); hier
            # sichert sie auch Schreibwege ab, die an der Validierung vorbeigehen.
            models.CheckConstraint(
                check=Q(value__isnull=False, below_detection=False)
                | Q(value__isnull=True, below_detection=True),
                name="measurement_value_xor_below_detection",
            ),
        ]

    def __str__(self):
        return f"{self.parameter}: {self.display_value}"

    def clean(self):
        """Genau eines von Wert und n.n. — und n.n. nur mit Nachweisgrenze."""
        super().clean()
        has_value = self.value is not None
        if has_value and self.below_detection:
            raise ValidationError(
                "Ein Messwert ist entweder eine Zahl oder „nicht nachweisbar“ — nicht beides."
            )
        if not has_value and not self.below_detection:
            raise ValidationError(
                {"value": "Bitte einen Wert eintragen oder „nicht nachweisbar“ wählen."}
            )
        if self.below_detection and self.parameter_id and not self.parameter.has_detection_limit:
            raise ValidationError(
                {
                    "below_detection": (
                        f"{self.parameter.name} hat keine Nachweisgrenze; "
                        "„nicht nachweisbar“ ist hier nicht vorgesehen."
                    )
                }
            )

    @property
    def display_value(self):
        # „n.n.“ statt einer Zahl: nicht 0 (das wäre eine gemessene Abwesenheit)
        # und nicht leer (das sähe aus wie „nie gemessen“).
        if self.below_detection:
            return "n.n."
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
        if self.below_detection:
            return classify_below_detection(minimum, maximum, self.parameter.detection_limit)
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
        # Alles, was an der Technik passiert — auch das, was die Anwendung
        # selbst schaltet (siehe services.devices.record_event).
        EQUIPMENT = "equipment", "Technik"
        INCIDENT = "incident", "Vorfall"
        # Was am Becken auffällt, ohne dass jemand eingegriffen hat: neue
        # Blätter, Balzverhalten, eine Trübung. Ein eigenes Modell wäre
        # dasselbe Ereignis mit denselben Feldern unter anderem Namen.
        OBSERVATION = "observation", "Beobachtung"
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


class ProvenanceMixin:
    """Gemeinsame Anzeige der Bezugsquelle an Besatz und Bepflanzung.

    Die Auswahllisten unterscheiden sich — Wildfang und Nachzucht beim Tier,
    InVitro und Vorkultur bei der Pflanze —, die Darstellung nicht. Gemeinsame
    Felder gibt es deshalb keine, eine gemeinsame Ausgabe schon.
    """

    @property
    def provenance_label(self):
        """Bezugsquelle samt Händler oder Züchter, sofern erfasst."""
        if not self.provenance:
            return ""
        label = self.get_provenance_display()
        return f"{label} · {self.provenance_detail}" if self.provenance_detail else label


class Stocking(ProvenanceMixin, models.Model):
    """Besatz: eine Tierart in einem Becken."""

    class Provenance(models.TextChoices):
        """Woher die Tiere dieses Postens stammen.

        Gehört an den Besatz und nicht in den Katalog: dieselbe Art kann aus
        ganz verschiedenen Quellen kommen, und bei *Mikrogeophagus ramirezi*
        entscheidet genau das über Lebenserwartung und Brutverhalten —
        asiatische Massennachzuchten sind oft hormonbehandelt und zeigen keine
        Brutpflege mehr, europäische Privatnachzuchten sind unproblematisch.
        """

        WILD = "wild", "Wildfang"
        BRED_LOCAL = "bred_local", "Eigene Nachzucht"
        BRED_DE = "bred_de", "Deutsche / europäische Nachzucht"
        BRED_ASIA = "bred_asia", "Asiatische Nachzucht"
        RETAIL = "retail", "Handel, Herkunft unbekannt"
        UNKNOWN = "unknown", "Unbekannt"

    tank = models.ForeignKey(Tank, related_name="stockings", on_delete=models.CASCADE)
    species = models.ForeignKey("catalog.AnimalSpecies", related_name="stockings", on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField("Anzahl", default=1)
    # Optional, weil bei einem Schwarm niemand die Geschlechter zählt. Bei Paar-
    # und Haremshaltung ist die Verteilung dagegen die eigentliche Aussage.
    quantity_male = models.PositiveSmallIntegerField("davon Männchen", null=True, blank=True)
    quantity_female = models.PositiveSmallIntegerField("davon Weibchen", null=True, blank=True)
    added_on = models.DateField("Eingesetzt am")
    removed_on = models.DateField("Entnommen am", null=True, blank=True)
    provenance = models.CharField(
        "Bezugsquelle", max_length=10, choices=Provenance.choices, blank=True
    )
    provenance_detail = models.CharField(
        "Züchter / Händler", max_length=200, blank=True, help_text="Wer die Tiere abgegeben hat."
    )
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

    @property
    def sex_label(self):
        """Die erfasste Geschlechterverteilung, z. B. „1 ♂ · 3 ♀“.

        Leer, solange nichts erfasst ist — und ein einzelner erfasster Wert
        steht für sich: „2 ♂“ heißt nicht, dass es keine Weibchen gibt.
        """
        parts = []
        if self.quantity_male is not None:
            parts.append(f"{self.quantity_male} ♂")
        if self.quantity_female is not None:
            parts.append(f"{self.quantity_female} ♀")
        return " · ".join(parts)

    @property
    def social_hints(self):
        """Hinweise zur Sozialstruktur — Hinweise, keine Sperre.

        Geprüft wird nur, was erfasst ist: ohne Geschlechterverteilung gibt es
        zu Paar und Harem keinen Hinweis. Das ist Absicht — bei einem Schwarm
        zählt die Geschlechter niemand, und ein Hinweis auf eine fehlende
        Angabe stünde dann an jedem zweiten Posten.
        """
        if not self.is_active:
            return []
        structure = self.species.social_structure
        social = self.species.Social
        male, female = self.quantity_male, self.quantity_female
        hints = []
        if structure == social.SOLITARY and self.quantity > 1:
            hints.append(
                f"Die Art wird einzeln gehalten, im Becken stehen {self.quantity} Tiere."
            )
        if structure == social.PAIR and male is not None and female is not None:
            if not (male and female):
                hints.append("Ein Paar braucht ein Männchen und ein Weibchen.")
        if structure == social.HAREM and male is not None and male > 1:
            hints.append(f"Ein Harem verträgt nur ein Männchen, erfasst sind {male}.")
        return hints


class Planting(ProvenanceMixin, models.Model):
    """Bepflanzung: eine Pflanzenart in einem Becken."""

    class Provenance(models.TextChoices):
        """Wie die Pflanzen vorgezogen wurden.

        Eigene Auswahlliste und nicht die des Besatzes: bei Pflanzen machen
        InVitro, submers und emers vorgezogen die erheblichen Unterschiede beim
        Anwachsen — „Wildfang“ und „Nachzucht“ sagen hier nichts.
        """

        IN_VITRO = "in_vitro", "InVitro"
        SUBMERSED = "submersed", "Submers vorgezogen"
        EMERSED = "emersed", "Emers vorgezogen"
        OWN_CUTTING = "own_cutting", "Eigener Ableger"
        RETAIL = "retail", "Handel, Herkunft unbekannt"
        UNKNOWN = "unknown", "Unbekannt"

    tank = models.ForeignKey(Tank, related_name="plantings", on_delete=models.CASCADE)
    species = models.ForeignKey("catalog.PlantSpecies", related_name="plantings", on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField("Anzahl", default=1)
    planted_on = models.DateField("Gepflanzt am")
    removed_on = models.DateField("Entfernt am", null=True, blank=True)
    provenance = models.CharField(
        "Bezugsquelle", max_length=11, choices=Provenance.choices, blank=True
    )
    provenance_detail = models.CharField(
        "Gärtnerei / Händler", max_length=200, blank=True, help_text="Woher die Pflanzen kamen."
    )
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


class Transfer(models.Model):
    """Ein Umzug von Tieren oder Pflanzen zwischen zwei eigenen Becken.

    Ohne diesen Datensatz sind ein Umzug und ein Verlust nicht zu
    unterscheiden: im Quellbecken sinkt die Stückzahl, im Zielbecken taucht
    eine auf, und dass beides derselbe Vorgang war, weiß nur noch der Halter.

    Es ist bewusst **kein** vollständiges Bewegungskonto (#1218): Zukauf,
    Nachwuchs und Abgang bleiben eine geänderte Stückzahl am Besatz. Erfasst
    wird der eine Fall, in dem zwei Becken zugleich betroffen sind und die
    Verbindung sonst verloren ginge.

    ``source_tank`` und ``target_tank`` sind ``PROTECT``: ein Becken, das
    einmal Tiere abgegeben hat, ist Teil der Geschichte des anderen. Gelöscht
    werden kann es damit nicht mehr — aufgelöst schon (:attr:`Tank.has_history`
    zählt Umzüge deshalb mit).
    """

    class Kind(models.TextChoices):
        ANIMAL = "animal", "Tiere"
        PLANT = "plant", "Pflanzen"

    kind = models.CharField("Art", max_length=6, choices=Kind.choices)
    source_tank = models.ForeignKey(
        Tank, related_name="transfers_out", on_delete=models.PROTECT, verbose_name="Quellbecken"
    )
    target_tank = models.ForeignKey(
        Tank, related_name="transfers_in", on_delete=models.PROTECT, verbose_name="Zielbecken"
    )
    # Zwei Fremdschlüssel statt einer generischen Beziehung: es gibt genau zwei
    # Kataloge, und beide sollen ``PROTECT`` und ``select_related`` behalten.
    animal = models.ForeignKey(
        "catalog.AnimalSpecies",
        related_name="transfers",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name="Tierart",
    )
    plant = models.ForeignKey(
        "catalog.PlantSpecies",
        related_name="transfers",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name="Pflanzenart",
    )
    quantity = models.PositiveIntegerField("Anzahl")
    moved_on = models.DateField("Umgesetzt am")
    note = models.TextField("Notiz", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-moved_on", "-pk"]
        verbose_name = "Umzug"
        verbose_name_plural = "Umzüge"
        indexes = [models.Index(fields=["target_tank", "kind", "moved_on"])]
        constraints = [
            models.CheckConstraint(
                check=~Q(source_tank=models.F("target_tank")),
                name="transfer_between_two_tanks",
                violation_error_message="Quell- und Zielbecken sind dasselbe Becken.",
            ),
            # Genau ein Katalogeintrag, und zwar der zur Art passende. Ohne die
            # Bedingung wäre ein Umzug ohne Art speicherbar — und der ließe sich
            # später nicht mehr zuordnen.
            models.CheckConstraint(
                # Die Werte als Zeichenkette: ``Kind`` steht im Klassenkörper
                # von ``Transfer`` und ist aus ``Meta`` heraus nicht zu sehen.
                check=Q(kind="animal", animal__isnull=False, plant__isnull=True)
                | Q(kind="plant", plant__isnull=False, animal__isnull=True),
                name="transfer_species_matches_kind",
            ),
        ]

    def __str__(self):
        return f"{self.quantity}× {self.species} → {self.target_tank}"

    @property
    def species(self):
        """Die umgesetzte Art — je nach ``kind`` die Tier- oder die Pflanzenart."""
        return self.animal if self.kind == self.Kind.ANIMAL else self.plant

    @property
    def origin_label(self):
        """Herkunft in einer Zeile: „aus 80er Cube, 15.10.2026“."""
        return f"aus {self.source_tank.name}, {self.moved_on:%d.%m.%Y}"


class SubstrateLayer(models.Model):
    """Eine Schicht des Bodengrunds, gezählt von unten nach oben.

    Bodengrund ist kein Satz im Notizfeld, sondern eine Reihenfolge mit
    Mächtigkeiten: „2 cm Nährstoffdepot, darüber 4 cm Sand". Erst als Schichten
    erfasst ergeben sich Gesamthöhe und die Standzeit eines Depots — und die
    Standzeit ist der Grund für die Erfassung: ein aufgebrauchtes Depot kippt
    ein Becken gern über N- oder K-Mangel in die Algen.

    Ein Gerät ist das nicht: ein Filter hat Betriebszustand, Verbrauch und
    Wartungsintervall, eine Schicht hat Mächtigkeit und ein Verfallsdatum.
    """

    class Kind(models.TextChoices):
        NUTRIENT = "nutrient", "Nährstoffdepot"
        SOIL = "soil", "Aquasoil"
        GRAVEL = "gravel", "Kies"
        SAND = "sand", "Sand"
        LAVA = "lava", "Lavagranulat"
        FILTER_MAT = "filter_mat", "Filtermatte / Trennschicht"
        OTHER = "other", "Sonstiges"

    #: Schichtarten mit begrenzter Standzeit. Kies zehrt nicht auf, ein Depot
    #: schon — nur dafür wird ein Verfallsdatum vorgeschlagen.
    DEPOT_KINDS = {Kind.NUTRIENT}

    tank = models.ForeignKey(Tank, related_name="substrate_layers", on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField(
        "Position", default=0, help_text="0 ist die unterste Schicht."
    )
    kind = models.CharField("Art", max_length=12, choices=Kind.choices, default=Kind.GRAVEL)
    product = models.CharField(
        "Produkt", max_length=160, blank=True, help_text="z. B. „Dennerle Sansibar“."
    )
    grain_size = models.CharField("Körnung", max_length=60, blank=True, help_text="z. B. „0,5–1 mm“.")
    depth_cm = models.DecimalField("Mächtigkeit (cm)", max_digits=4, decimal_places=1, null=True, blank=True)
    added_on = models.DateField("Eingebracht am", null=True, blank=True)
    depleted_on = models.DateField(
        "Erschöpft am",
        null=True,
        blank=True,
        help_text="Bei Depots das rechnerische Ende der Standzeit.",
    )
    note = models.TextField("Notiz", blank=True)

    class Meta:
        # Aufsteigend, also von unten nach oben — die Darstellung dreht das um.
        ordering = ["position", "pk"]
        verbose_name = "Bodengrundschicht"
        verbose_name_plural = "Bodengrund"

    def __str__(self):
        depth = f"{self.depth_display} " if self.depth_cm else ""
        product = f" ({self.product})" if self.product else ""
        return f"{depth}{self.get_kind_display()}{product}"

    @property
    def depth_display(self):
        return f"{format_cm(self.depth_cm)} cm" if self.depth_cm else ""

    @property
    def is_depot(self):
        return self.kind in self.DEPOT_KINDS

    def move(self, step):
        """Verschiebt die Schicht im Stapel; ``step`` ist +1 nach oben.

        Nummeriert dabei den ganzen Stapel lückenlos durch: Positionen aus dem
        Admin oder aus einer gelöschten Schicht müssen keine Folge bilden, und
        eine Reihenfolge mit Lücken lässt sich nicht zuverlässig tauschen.
        Am Rand des Stapels passiert nichts.
        """
        layers = list(self.tank.substrate_layers.all())
        index = next((i for i, layer in enumerate(layers) if layer.pk == self.pk), None)
        if index is None:
            return False
        target = index + step
        if not 0 <= target < len(layers):
            return False
        layers[index], layers[target] = layers[target], layers[index]
        for position, layer in enumerate(layers):
            layer.position = position
        SubstrateLayer.objects.bulk_update(layers, ["position"])
        return True

    def default_depleted_on(self):
        """Vorgeschlagenes Ende der Standzeit — nur für Depots."""
        if not self.is_depot or self.added_on is None:
            return None
        return self.added_on + timedelta(days=NUTRIENT_DEPOT_DAYS)

    def depletion_status(self, today=None):
        """Ist die Standzeit abgelaufen? Ohne Datum gibt es dazu keine Aussage."""
        if self.depleted_on is None:
            return Status.UNKNOWN
        today = today or timezone.localdate()
        return Status.WARN if self.depleted_on <= today else Status.OK

    @property
    def status_value(self):
        return self.depletion_status()

    @property
    def depletion_label(self):
        if self.depleted_on is None:
            return ""
        if self.depletion_status() == Status.WARN:
            return f"erschöpft seit {self.depleted_on:%d.%m.%Y}"
        return f"reicht bis {self.depleted_on:%d.%m.%Y}"

    @property
    def suggests_reminder(self):
        """Lässt sich aus dieser Schicht ein Termin ableiten?"""
        return bool(self.is_depot and (self.depleted_on or self.default_depleted_on()))

    def reminder_defaults(self):
        """Vorbelegung des angebotenen Termins — angelegt wird er nicht hier."""
        name = self.product or self.get_kind_display()
        return {
            "title": f"Nährstoffdepot erschöpft: {name}"[:160],
            "category": CareTask.Category.FERTILIZER,
            "due_on": self.depleted_on or self.default_depleted_on(),
            "notes": (
                "Das Depot ist rechnerisch aufgebraucht. Düngung über die "
                "Wassersäule prüfen, bevor Mangelerscheinungen auftreten."
            ),
        }


class HardscapeItem(models.Model):
    """Wurzel, Stein, Erlenzapfen, Rückwand — was sonst noch im Becken liegt.

    Erfasst wird es wegen der Wasserwerte: Moorkienwurzel und Erlenzapfen geben
    Huminstoffe ab und drücken den pH, kalkhaltiges Gestein hebt KH und
    Leitwert. Bei einer unerklärten Veränderung ist die Einrichtung der erste
    Verdächtige — nachvollziehen lässt sich das nur, wenn erfasst ist, was wann
    hineinkam und was wieder heraus ist.
    """

    class Kind(models.TextChoices):
        WOOD = "wood", "Wurzel / Holz"
        STONE = "stone", "Stein"
        BOTANICALS = "botanicals", "Erlenzapfen, Laub, Seemandelbaumblätter"
        BACKGROUND = "background", "Rückwand"
        CAVE = "cave", "Höhle / Versteck"
        OTHER = "other", "Sonstiges"

    #: Kurzbezeichnungen (Einzahl, Mehrzahl) für ``Tank.setup_summary``, in der
    #: Reihenfolge, in der sie dort erscheinen. Die Klartexte der Auswahlliste
    #: sind dafür zu lang — „5 Erlenzapfen, Laub, Seemandelbaumblätter" liest
    #: sich in einer Kartenzeile nicht.
    SUMMARY_LABELS = [
        (Kind.WOOD, "Wurzel", "Wurzeln"),
        (Kind.STONE, "Stein", "Steine"),
        (Kind.BOTANICALS, "Zapfen & Laub", "Zapfen & Laub"),
        (Kind.CAVE, "Höhle", "Höhlen"),
        (Kind.BACKGROUND, "Rückwand", "Rückwände"),
        (Kind.OTHER, "Dekoration", "Dekorationen"),
    ]

    #: Arten, die sich aufbrauchen und deshalb einen Termin nahelegen.
    PERISHABLE_KINDS = {Kind.BOTANICALS}

    tank = models.ForeignKey(Tank, related_name="hardscape", on_delete=models.CASCADE)
    kind = models.CharField("Art", max_length=12, choices=Kind.choices, default=Kind.WOOD)
    name = models.CharField("Bezeichnung", max_length=160, help_text="z. B. „Moorkienwurzel“.")
    quantity = models.PositiveSmallIntegerField("Anzahl", null=True, blank=True)
    added_on = models.DateField("Eingebracht am", null=True, blank=True)
    removed_on = models.DateField("Entfernt am", null=True, blank=True)
    # Ob ein Stein auslaugt, hängt vom Gestein ab und nicht von der Kategorie:
    # deshalb ein eigenes Merkmal statt einer Ableitung aus ``kind``.
    affects_water = models.BooleanField("Wirkt auf die Wasserwerte", default=False)
    water_effect = models.CharField(
        "Wirkung", max_length=200, blank=True, help_text="z. B. „Huminstoffe, senkt pH“."
    )
    note = models.TextField("Notiz", blank=True)

    class Meta:
        # Was im Becken liegt, steht oben; Entferntes sortiert sich mit dem
        # jüngsten Abgang dahinter.
        ordering = [models.F("removed_on").asc(nulls_first=True), "kind", "name"]
        verbose_name = "Hardscape"
        verbose_name_plural = "Hardscape"

    def __str__(self):
        return f"{self.quantity}× {self.name}" if self.quantity else self.name

    @property
    def is_active(self):
        return self.removed_on is None

    @property
    def expected_depletion(self):
        """Rechnerisches Ende botanischen Hardscapes — abgeleitet, kein Feld.

        Erlenzapfen und Laub geben nach vier bis sechs Wochen nichts mehr ab.
        Das ist eine Erfahrungsgröße und keine Eigenschaft des Stücks; sie
        gehört deshalb nicht in die Tabelle.
        """
        if self.kind not in self.PERISHABLE_KINDS or self.added_on is None:
            return None
        return self.added_on + timedelta(days=BOTANICALS_DAYS)

    @property
    def effect_label(self):
        """Wirkung auf die Wasserwerte in Worten, für Liste und Prompt."""
        if not self.affects_water:
            return ""
        return self.water_effect or "wirkt auf die Wasserwerte"

    @property
    def suggests_reminder(self):
        return bool(self.is_active and self.expected_depletion)

    def reminder_defaults(self):
        return {
            "title": f"{self.name} erneuern"[:160],
            "category": CareTask.Category.OTHER,
            "due_on": self.expected_depletion,
            "notes": (
                "Erlenzapfen und Laub sind nach vier bis sechs Wochen erschöpft "
                "und geben keine Huminstoffe mehr ab."
            ),
        }


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


class TankPhoto(ImageVariantsMixin, models.Model):
    tank = models.ForeignKey(Tank, related_name="photos", on_delete=models.CASCADE)
    # ``SET_NULL``, nicht ``CASCADE``: wer ein Ereignis löscht, will nicht die
    # Fotos mitlöschen. Sie bleiben in der Galerie und verlieren nur ihre
    # Zuordnung.
    event = models.ForeignKey(
        Event,
        related_name="photos",
        verbose_name="Ereignis",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    image = models.ImageField("Bild", upload_to=tank_photo_path)
    # Kachel- und Vorschaugröße entstehen beim Speichern; das Original bleibt
    # daneben stehen. Nicht editierbar: kein Formular soll sie anbieten.
    thumbnail = models.ImageField(
        "Kachel", upload_to=tank_thumb_path, blank=True, editable=False
    )
    preview = models.ImageField(
        "Vorschau", upload_to=tank_preview_path, blank=True, editable=False
    )
    width = models.PositiveIntegerField("Breite", null=True, blank=True, editable=False)
    height = models.PositiveIntegerField("Höhe", null=True, blank=True, editable=False)
    caption = models.CharField("Bildunterschrift", max_length=200, blank=True)
    taken_on = models.DateField("Aufgenommen am")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-taken_on", "-pk"]
        verbose_name = "Foto"
        verbose_name_plural = "Fotos"

    def __str__(self):
        return self.caption or f"Foto {self.taken_on}"

    @property
    def alt_text(self):
        """Bildbeschreibung für die Ausgabe: Bildunterschrift, sonst das Datum.

        Ein Foto ohne Unterschrift ist deshalb nicht beschreibungslos — das
        Aufnahmedatum sagt einem Screenreader immerhin, worum es geht.
        """
        return self.caption or f"Foto vom {self.taken_on:%d.%m.%Y}"

    def clean(self):
        """Foto und Ereignis gehören zum selben Becken.

        Die Oberfläche stellt nur passende Ereignisse zur Auswahl; im Admin
        gibt es diese Einschränkung nicht, und ein Becken lässt sich am Foto
        nachträglich umhängen. Die Regel steht deshalb am Modell.
        """
        if self.event_id and self.event.tank_id != self.tank_id:
            raise ValidationError({"event": "Das Ereignis gehört zu einem anderen Becken."})


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
