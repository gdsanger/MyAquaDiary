"""Userübergreifender Katalog für Pflanzen- und Tierarten."""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils.text import slugify

from core.enums import Difficulty, WaterType
from core.images import ImageVariantsMixin


def normalize_variant(value):
    """Eine Sortenbezeichnung ohne die Anführungszeichen der Anzeige.

    Die setzt :attr:`Species.display_name`, nicht der Erfasser — und auch nicht
    die KI. Sonst stünde 'Flamingo' einmal mit und einmal ohne Hochkomma in der
    Datenbank, und die Eindeutigkeit über Name und Sorte hinge daran, wie
    jemand getippt hat.
    """
    return (value or "").strip().strip("'\"‚‘’„“").strip()


def unique_slug(model, scientific_name, variant="", *, exclude_pk=None):
    """Eine freie Adresse für einen Steckbrief — aus Name und Sorte.

    Der Slug ist die Adresse des Steckbriefs und keine Angabe, über die jemand
    nachdenken soll. Ohne die Sorte kollidierten Stamm- und Zuchtform derselben
    Art. Hier und nicht im Formular, weil auch die Übernahme eines
    KI-Entwurfs (:mod:`services.ai.catalog`) einen Slug braucht — zwei
    Ableitungen ergäben zwei Adressschemata für dieselbe Art.

    :raises ValueError: wenn zu diesem Namen keine freie Adresse mehr übrig ist.
    """
    base = slugify(f"{scientific_name} {variant}".strip())[:150] or "art"
    taken = set(model.objects.exclude(pk=exclude_pk).values_list("slug", flat=True))
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise ValueError("Für diesen Namen ist keine freie Adresse mehr zu finden.")


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
        """Freitextsuche über wissenschaftlichen und deutschen Namen.

        Die Sortenbezeichnung gehört dazu: wer „Electric Blue“ sucht, sucht
        nicht nach *Mikrogeophagus ramirezi*, sondern nach genau dieser Form.
        """
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            models.Q(scientific_name__icontains=term)
            | models.Q(common_name__icontains=term)
            | models.Q(variant__icontains=term)
            | models.Q(summary__icontains=term)
        )

    def wild_forms(self):
        """Nur Stammformen — Zuchtformen bleiben außen vor."""
        return self.filter(is_cultivated_form=False)

    def with_images(self):
        return self.prefetch_related("images")


class Species(models.Model):
    """Gemeinsamer Steckbrief-Rumpf von Pflanzen- und Tierarten.

    Der wissenschaftliche Name allein ist nicht die Identität eines
    Steckbriefs: Wild- und Zuchtform derselben Art unterscheiden sich in
    Robustheit, Lebenserwartung und Verhalten so deutlich, dass ein
    gemeinsamer Steckbrief für beide falsch wäre. Die Identität ist deshalb
    ``scientific_name`` **plus** ``variant``.
    """

    class Region(models.TextChoices):
        """Natürliche Verbreitung — das Gebiet, aus dem die Art stammt.

        Nicht zu verwechseln mit der Bezugsquelle: aus welcher Zucht ein
        einzelnes Tier kommt, steht am Besatzeintrag (``tanks.Stocking``) und
        nicht hier — dieselbe Art kann aus ganz verschiedenen Quellen stammen.
        """

        SOUTH_AMERICA = "south_america", "Südamerika"
        CENTRAL_AMERICA = "central_america", "Mittelamerika"
        NORTH_AMERICA = "north_america", "Nordamerika"
        AFRICA = "africa", "Afrika"
        ASIA = "asia", "Asien"
        AUSTRALIA = "australia", "Australien / Ozeanien"
        EUROPE = "europe", "Europa"
        # Eine Aussage, keine Lücke: 'Electric Blue' hat kein Wildvorkommen.
        CULTIVAR = "cultivar", "Zuchtform ohne Wildvorkommen"
        UNKNOWN = "unknown", "Unbekannt"

    scientific_name = models.CharField("wissenschaftlicher Name", max_length=150)
    variant = models.CharField(
        "Sorte / Zuchtform",
        max_length=80,
        blank=True,
        help_text="Sortenbezeichnung wie 'Flamingo', 'Red Ruby', 'Electric Blue'. "
        "Leer lassen bei der Stammform.",
    )
    is_cultivated_form = models.BooleanField(
        "Zuchtform",
        default=False,
        help_text="Durch Selektion entstanden, nicht in der Natur vorkommend.",
    )
    common_name = models.CharField("deutscher Name", max_length=150, blank=True)
    slug = models.SlugField("Slug", max_length=160, unique=True)
    summary = models.CharField("Kurzbeschreibung", max_length=250, blank=True)
    description = models.TextField("Beschreibung", blank=True)

    # Auswahlfeld **und** Freitext: das Auswahlfeld macht Filtern und Auswerten
    # möglich („nur Südamerika“ beim Zusammenstellen eines Biotopbeckens), der
    # Freitext trägt die eigentliche Information. Nur Freitext wäre nicht
    # filterbar, nur Auswahl zu grob. Leer heißt „nicht erfasst“ — ``UNKNOWN``
    # dagegen „nachgesehen, niemand weiß es“.
    origin_region = models.CharField(
        "Verbreitungsgebiet", max_length=15, choices=Region.choices, blank=True
    )
    origin_detail = models.CharField(
        "Herkunft im Detail",
        max_length=200,
        blank=True,
        help_text="z. B. „Orinoco-Einzug, Venezuela und Kolumbien“.",
    )

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
        # Die Stammform (``variant=""``) steht vor ihren Zuchtformen.
        ordering = [Lower("scientific_name"), Lower("variant")]
        constraints = [
            # Statt ``unique`` am Namen: erst Name und Sorte zusammen sind
            # eindeutig. Case-insensitiv, damit 'Electric Blue' und
            # 'electric blue' nicht zweimal nebeneinander stehen. Der Name
            # trägt App und Modell, weil beide Kataloge dieselbe Bedingung
            # erben und Constraint-Namen projektweit eindeutig sein müssen.
            models.UniqueConstraint(
                Lower("scientific_name"),
                Lower("variant"),
                name="%(app_label)s_%(class)s_unique_variant",
                violation_error_message=(
                    "Diese Art gibt es mit dieser Sorte bereits im Katalog."
                ),
            )
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        """Anzeigename samt Sorte: *Zwergbuntbarsch* 'Electric Blue'."""
        base = self.common_name or self.scientific_name
        return f"{base} '{self.variant}'" if self.variant else base

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

    @property
    def origin_display(self):
        """Verbreitungsgebiet und Freitext in einer Zeile.

        Beides steht nebeneinander, weil beides gemeint ist: das Gebiet für den
        Überblick, der Freitext für den Einzug, in dem die Art wirklich lebt.
        """
        region = self.get_origin_region_display() if self.origin_region else ""
        return " · ".join(part for part in (region, self.origin_detail) if part)


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

    class Zone(models.TextChoices):
        """Wo sich die Art im Becken aufhält.

        Grundlage jeder sinnvollen Besatzzusammenstellung: drei Bodenbewohner
        nebeneinander ergeben ein leeres Becken mit Gedränge am Boden.
        """

        BOTTOM = "bottom", "Boden"
        LOWER = "lower", "Unterer Bereich"
        MIDDLE = "middle", "Mittlerer Bereich"
        UPPER = "upper", "Oberer Bereich"
        SURFACE = "surface", "Oberfläche"
        ALL = "all", "Alle Bereiche"

    class Diet(models.TextChoices):
        """Ernährung in drei Stufen.

        Bewusst nur drei Werte: Feinheiten wie Aufwuchs- oder Insektenfresser
        gehören in die Beschreibung. Eine feiner gegliederte Auswahl wäre
        schwerer auszufüllen und im Alltag nicht nützlicher.
        """

        CARNIVORE = "carnivore", "Fleischlich"
        HERBIVORE = "herbivore", "Pflanzlich"
        OMNIVORE = "omnivore", "Beides"

    class Social(models.TextChoices):
        """Sozialstruktur — was ``min_group_size`` nicht sagen kann.

        Bei einem Paar steht dort 2, was auch „mindestens zwei Tiere“ heißen
        könnte; dass es ein Männchen und ein Weibchen sein müssen, geht dabei
        verloren. Genau diese Aussage steht hier.
        """

        SOLITARY = "solitary", "Einzeln"
        PAIR = "pair", "Paar (♂ + ♀)"
        HAREM = "harem", "Harem (1 ♂ + mehrere ♀)"
        GROUP = "group", "Gruppe"
        SHOAL = "shoal", "Schwarm"

    category = models.CharField(
        "Kategorie", max_length=10, choices=Category.choices, default=Category.FISH
    )
    temperament = models.CharField(
        "Verhalten", max_length=12, choices=Temperament.choices, default=Temperament.PEACEFUL
    )
    zone = models.CharField(
        "Aufenthaltsbereich", max_length=7, choices=Zone.choices, blank=True
    )
    diet = models.CharField("Ernährung", max_length=9, choices=Diet.choices, blank=True)
    social_structure = models.CharField(
        "Sozialstruktur", max_length=8, choices=Social.choices, blank=True
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


class SpeciesLink(models.Model):
    """Ein Verweis von einem Steckbrief auf eine fremde Wissensquelle.

    Ein Steckbrief im eigenen Katalog wird nie so vollständig sein wie eine
    Fachdatenbank. Statt alles nachzupflegen, verweist er auf die Quelle —
    DRTA-Archiv bei den Tieren, Flowgrow bei den Pflanzen, dazu Hersteller,
    Artikel und Forenbeiträge.

    **Ein** Modell für beide Kataloge mit zwei optionalen Fremdschlüsseln, von
    denen genau einer gesetzt ist (Bedingung in ``Meta.constraints``). Zwei
    getrennte Modelle wären dieselbe Logik doppelt; eine generische Beziehung
    verlöre ``CASCADE`` und die Vorabladung.

    Die Adresse wird nicht abgerufen: ein automatischer Datenabruf aus den
    Quellen ist nicht vorgesehen (#1250). Es gibt keine Schnittstelle, die
    Steckbriefe sind redaktionelle Inhalte Dritter, und ein Scraper bräche bei
    jeder Layoutänderung — auffallen würde das erst beim Nutzer. Beim Pflegen
    hilft stattdessen eine Suchadresse (siehe :mod:`catalog.sources`).
    """

    class Kind(models.TextChoices):
        DATABASE = "database", "Artdatenbank"
        SUPPLIER = "supplier", "Hersteller / Händler"
        ARTICLE = "article", "Artikel"
        FORUM = "forum", "Forenbeitrag"
        VIDEO = "video", "Video"
        OTHER = "other", "Sonstiges"

    plant = models.ForeignKey(
        PlantSpecies,
        verbose_name="Pflanzenart",
        null=True,
        blank=True,
        related_name="links",
        on_delete=models.CASCADE,
    )
    animal = models.ForeignKey(
        AnimalSpecies,
        verbose_name="Tierart",
        null=True,
        blank=True,
        related_name="links",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(
        "Art der Quelle", max_length=8, choices=Kind.choices, default=Kind.DATABASE
    )
    title = models.CharField("Titel", max_length=200)
    url = models.URLField("Adresse", max_length=500)
    position = models.PositiveSmallIntegerField(
        "Reihenfolge", default=0, help_text="Kleinere Zahlen stehen oben."
    )

    class Meta:
        verbose_name = "Quellenlink"
        verbose_name_plural = "Quellenlinks"
        ordering = ["position", "title"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(plant__isnull=False, animal__isnull=True)
                    | models.Q(plant__isnull=True, animal__isnull=False)
                ),
                name="catalog_specieslink_one_species",
                violation_error_message="Ein Link gehört zu genau einer Art.",
            )
        ]

    def __str__(self):
        return self.title

    def clean(self):
        """Dieselbe Bedingung wie in der Datenbank, nur mit lesbarer Meldung.

        Die Oberfläche setzt die Art aus der Adresse und zeigt die beiden
        Felder nicht; im Admin wären sie ohne diese Prüfung ein
        ``IntegrityError`` statt einer Fehlermeldung am Formular.
        """
        super().clean()
        if bool(self.plant_id) == bool(self.animal_id):
            raise ValidationError(
                "Ein Link gehört zu genau einer Art — entweder zu einer Pflanze "
                "oder zu einem Tier."
            )

    @property
    def species(self):
        """Die Art, an der der Link hängt — Pflanze oder Tier."""
        return self.plant if self.plant_id else self.animal

    @property
    def species_kind(self):
        """``plant`` oder ``animal`` — der Schlüssel, unter dem die Adressen stehen."""
        return "plant" if self.plant_id else "animal"


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
