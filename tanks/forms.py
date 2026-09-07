"""Formulare für das Erfassen und Pflegen am Becken.

Zum Eigentum gehört hier nichts: welchem Becken ein Datensatz zugeordnet wird,
entscheidet die Ansicht über :class:`tanks.views.TankScopedMixin`. Die
Formulare kennen das Becken nur, soweit sie es für Prüfungen brauchen (etwa
für die Eindeutigkeit eines Zielbereichs).
"""

from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.text import slugify

from catalog.models import AnimalSpecies, PlantSpecies
from core.forms import BootstrapMixin, DateField, DateTimeField, MultipleImageField
from core.images import taken_at

from .models import (
    CareTask,
    Event,
    Measurement,
    Parameter,
    Planting,
    Stocking,
    Tank,
    TankParameterTarget,
    TankPhoto,
)

#: Eingaben, die „nicht nachweisbar" bedeuten. Kleinschreibung und ohne
#: Leerzeichen verglichen, damit „N. N." genauso durchgeht wie „n.n.".
NOT_DETECTED_INPUTS = {"n.n.", "n.n", "nn", "nichtnachweisbar", "n.d.", "nd"}

#: Zeitpunkte dürfen so weit in der Zukunft liegen — Spielraum für eine
#: falsch gestellte Uhr, aber kein Freibrief für Tippfehler im Jahr.
FUTURE_TOLERANCE = timedelta(hours=12)


def _check_not_in_future(value, label):
    """Wirft für Zeitpunkte, die erkennbar nicht stattgefunden haben können."""
    if value is not None and value > timezone.now() + FUTURE_TOLERANCE:
        raise forms.ValidationError(f"{label} liegt in der Zukunft.")
    return value


def _shots_of(images):
    """``[(Bild, Aufnahmezeitpunkt oder None)]`` für eine Auswahl.

    Der Zeitpunkt kommt aus dem EXIF-Block und ist die Ortszeit der Kamera;
    umgerechnet wird er deshalb nicht. Eine falsch gestellte Kamerauhr soll
    aber kein Foto in der Zukunft erzeugen — was nach ``FUTURE_TOLERANCE``
    liegt, gilt als unbrauchbar.
    """
    horizon = timezone.localtime() + FUTURE_TOLERANCE
    shots = []
    for image in images:
        stamp = taken_at(image)
        if stamp is not None:
            stamp = timezone.make_aware(stamp, timezone.get_current_timezone())
            if stamp > horizon:
                stamp = None
        shots.append((image, stamp))
    return shots


class MeasurementValueField(forms.DecimalField):
    """Messwert — nimmt „n.n." und ein deutsches Dezimalkomma entgegen.

    „nicht nachweisbar" ist bei Tröpfchentests die übliche Angabe für alles
    unterhalb der Nachweisgrenze. Gespeichert wird dafür 0: nur als Zahl lässt
    sich der Wert im Verlauf zeichnen und gegen einen Zielbereich prüfen — und
    0 ist die Aussage, die der Test tatsächlich macht.

    Das Widget ist bewusst ein Textfeld: in ein ``type="number"`` ließe sich
    „n.n." gar nicht erst eintippen.
    """

    widget = forms.TextInput

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 8)
        kwargs.setdefault("decimal_places", 3)
        super().__init__(**kwargs)
        self.widget.attrs.setdefault("inputmode", "decimal")
        self.widget.attrs.setdefault("placeholder", "z. B. 7,2 oder n.n.")

    def to_python(self, value):
        if isinstance(value, str):
            compact = value.strip().lower().replace(" ", "")
            if compact in NOT_DETECTED_INPUTS:
                return Decimal("0")
            value = value.strip().replace(",", ".")
        return super().to_python(value)


class TankForm(BootstrapMixin, forms.ModelForm):
    """Stammdaten eines Beckens.

    Der Slug wird aus dem Namen abgeleitet statt abgefragt: er ist Teil der
    Adresse und für den Benutzer nichts, worüber er entscheiden möchte.
    Eindeutig muss er nur je Besitzer sein (siehe ``unique_tank_slug_per_owner``).
    Beim Umbenennen bleibt er stehen — ein Lesezeichen auf das eigene Becken
    soll eine Namenskorrektur überleben.
    """

    setup_date = DateField(label="Einrichtung", initial=timezone.localdate)

    class Meta:
        model = Tank
        fields = [
            "name",
            "water_type",
            "volume_liters",
            "length_cm",
            "width_cm",
            "height_cm",
            "location",
            "setup_date",
            "accent",
            "cover_image",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner if owner is not None else getattr(self.instance, "owner", None)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not slugify(name):
            raise forms.ValidationError(
                "Aus diesem Namen lässt sich keine Adresse bilden. Bitte mindestens "
                "einen Buchstaben oder eine Ziffer verwenden."
            )
        return name

    def save(self, commit=True):
        tank = super().save(commit=False)
        if self.owner is not None:
            tank.owner = self.owner
        if not tank.slug:
            tank.slug = self._unique_slug(tank)
        if commit:
            tank.save()
        return tank

    def _unique_slug(self, tank):
        """Freier Slug für dieses Becken, mit Zähler bei Namensgleichheit."""
        base = slugify(tank.name)[:130] or "becken"
        taken = (
            Tank.objects.filter(owner=tank.owner)
            .exclude(pk=tank.pk)
            .values_list("slug", flat=True)
        )
        taken = set(taken)
        if base not in taken:
            return base
        for suffix in range(2, 1000):
            candidate = f"{base}-{suffix}"
            if candidate not in taken:
                return candidate
        return f"{base}-{timezone.now():%Y%m%d%H%M%S}"


class TankDissolveForm(BootstrapMixin, forms.ModelForm):
    """Auflösung eines Beckens — der Ersatz für „löschen mit Historie"."""

    dissolved_on = DateField(label="Aufgelöst am", initial=timezone.localdate)

    class Meta:
        model = Tank
        fields = ["dissolved_on"]

    def clean_dissolved_on(self):
        value = self.cleaned_data["dissolved_on"]
        if value < self.instance.setup_date:
            raise forms.ValidationError(
                "Das Becken kann nicht vor seiner Einrichtung aufgelöst worden sein."
            )
        if value > timezone.localdate():
            raise forms.ValidationError("Das Auflösungsdatum liegt in der Zukunft.")
        return value


class MeasurementSeriesForm(BootstrapMixin, forms.Form):
    """Eine ganze Messreihe zu einem Zeitpunkt.

    Wer testet, testet selten einen einzelnen Wert: ein Tröpfchentest-Durchgang
    liefert pH, KH, NO₂ und NO₃ zur selben Uhrzeit. Das Formular bietet deshalb
    alle Parameter an; gespeichert wird, was ausgefüllt ist.
    """

    measured_at = DateTimeField(label="Gemessen am", initial=timezone.now)
    note = forms.CharField(label="Notiz", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parameters = list(Parameter.objects.all())
        for parameter in self.parameters:
            label = f"{parameter.name} ({parameter.unit})" if parameter.unit else parameter.name
            field = MeasurementValueField(label=label, required=False)
            # Die Wertfelder entstehen erst hier und damit nach dem
            # BootstrapMixin — ihre Klasse muss deshalb von Hand gesetzt werden.
            field.widget.attrs.setdefault("class", "form-control")
            self.fields[self.field_name(parameter)] = field
        # Zeitpunkt nach oben, Notiz nach unten, die Werte dazwischen.
        self.order_fields(["measured_at", *[self.field_name(p) for p in self.parameters], "note"])

    @staticmethod
    def field_name(parameter):
        return f"parameter_{parameter.pk}"

    def clean_measured_at(self):
        return _check_not_in_future(self.cleaned_data["measured_at"], "Der Messzeitpunkt")

    def clean(self):
        cleaned = super().clean()
        if not any(
            cleaned.get(self.field_name(parameter)) is not None for parameter in self.parameters
        ):
            raise forms.ValidationError("Bitte mindestens einen Wert eintragen.")
        return cleaned

    def save(self, tank, user=None):
        """Legt je ausgefülltem Parameter einen Messwert an."""
        created = []
        for parameter in self.parameters:
            value = self.cleaned_data.get(self.field_name(parameter))
            if value is None:
                continue
            created.append(
                Measurement(
                    tank=tank,
                    parameter=parameter,
                    value=value,
                    measured_at=self.cleaned_data["measured_at"],
                    note=self.cleaned_data.get("note", ""),
                    created_by=user,
                )
            )
        return Measurement.objects.bulk_create(created)


class MeasurementForm(BootstrapMixin, forms.ModelForm):
    """Ein einzelner Messwert — für Korrekturen an der Messreihe."""

    value = MeasurementValueField(label="Wert")
    measured_at = DateTimeField(label="Gemessen am", initial=timezone.now)

    class Meta:
        model = Measurement
        fields = ["parameter", "value", "measured_at", "note"]

    def clean_measured_at(self):
        return _check_not_in_future(self.cleaned_data["measured_at"], "Der Messzeitpunkt")


class EventForm(BootstrapMixin, forms.ModelForm):
    """Ereignis — auf Wunsch samt Bildern.

    Die Bilder hängen am Ereignis und nicht bloß am Becken: wer eine
    Beobachtung festhält, macht ein Foto und schreibt zwei Sätze dazu. Das ist
    ein Vorgang und nicht drei (Ereignis anlegen, Galerie öffnen, zuordnen).
    Ein Behandlungsverlauf oder ein Vorfall gewinnt genauso daran.

    Der Zeitpunkt ist deshalb optional: liegt er in den Bildern, muss ihn
    niemand abtippen.
    """

    occurred_at = DateTimeField(
        label="Zeitpunkt",
        required=False,
        help_text="Ohne Angabe: der Aufnahmezeitpunkt der Bilder, sonst jetzt.",
    )
    images = MultipleImageField(
        label="Bilder",
        required=False,
        help_text="Mehrfachauswahl möglich. Das Aufnahmedatum kommt aus dem Bild.",
    )

    class Meta:
        model = Event
        fields = ["occurred_at", "category", "title", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #: ``[(Bild, Aufnahmezeitpunkt)]`` — einmal aus dem EXIF gelesen und
        #: zweimal gebraucht: für den Zeitpunkt des Ereignisses und für
        #: ``taken_on`` je Foto.
        self.shots = []
        # Auf dem Telefon zählt die Reihenfolge: was passiert ist, dann die
        # Bilder. Der Zeitpunkt steht zuletzt — er ist optional und leitet sich
        # meist aus den Bildern ab.
        self.order_fields(["title", "category", "description", "images", "occurred_at"])

    def clean_occurred_at(self):
        return _check_not_in_future(self.cleaned_data.get("occurred_at"), "Der Zeitpunkt")

    def clean_images(self):
        images = self.cleaned_data["images"]
        self.shots = _shots_of(images)
        return images

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("occurred_at"):
            stamps = [stamp for _, stamp in self.shots if stamp is not None]
            cleaned["occurred_at"] = min(stamps) if stamps else timezone.now()
        return cleaned

    def save_photos(self, event):
        """Legt die hochgeladenen Bilder als Fotos am Ereignis an.

        Getrennt von ``save()``, weil das Becken erst die Ansicht setzt
        (:class:`tanks.views.TankScopedMixin`) — vorher gibt es nichts, woran
        ein Foto hängen könnte.
        """
        fallback = timezone.localtime(event.occurred_at).date()
        return [
            TankPhoto.objects.create(
                tank=event.tank,
                event=event,
                image=image,
                taken_on=timezone.localtime(stamp).date() if stamp else fallback,
            )
            for image, stamp in self.shots
        ]


class StockingForm(BootstrapMixin, forms.ModelForm):
    added_on = DateField(label="Eingesetzt am", initial=timezone.localdate)

    class Meta:
        model = Stocking
        fields = ["species", "quantity", "added_on", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["species"].queryset = AnimalSpecies.objects.all()
        self.fields["species"].label = "Tierart"

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity < 1:
            raise forms.ValidationError("Ohne Tiere gibt es keinen Besatz.")
        return quantity


class StockingRemovalForm(BootstrapMixin, forms.ModelForm):
    """Abgang eines Besatzes.

    Besatz wird nicht gelöscht: dass in einem Becken einmal Neons schwammen,
    gehört zu seiner Geschichte — auch wenn heute keine mehr darin sind.
    """

    removed_on = DateField(label="Entnommen am", initial=timezone.localdate)

    class Meta:
        model = Stocking
        fields = ["removed_on", "note"]

    def clean_removed_on(self):
        value = self.cleaned_data["removed_on"]
        if value < self.instance.added_on:
            raise forms.ValidationError(
                "Der Abgang kann nicht vor dem Einsetzen gebucht worden sein."
            )
        if value > timezone.localdate():
            raise forms.ValidationError("Das Datum liegt in der Zukunft.")
        return value


class PlantingForm(BootstrapMixin, forms.ModelForm):
    planted_on = DateField(label="Gepflanzt am", initial=timezone.localdate)
    removed_on = DateField(label="Entfernt am", required=False)

    class Meta:
        model = Planting
        fields = ["species", "quantity", "planted_on", "removed_on", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["species"].queryset = PlantSpecies.objects.all()
        self.fields["species"].label = "Pflanzenart"

    def clean(self):
        cleaned = super().clean()
        planted, removed = cleaned.get("planted_on"), cleaned.get("removed_on")
        if planted and removed and removed < planted:
            self.add_error(
                "removed_on", "Die Pflanze kann nicht vor dem Einsetzen entfernt worden sein."
            )
        return cleaned


class CareTaskForm(BootstrapMixin, forms.ModelForm):
    due_on = DateField(label="Fällig am", initial=timezone.localdate)

    class Meta:
        model = CareTask
        fields = ["title", "category", "interval_days", "due_on", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class TankParameterTargetForm(BootstrapMixin, forms.ModelForm):
    """Beckeneigener Zielbereich für einen Parameter."""

    class Meta:
        model = TankParameterTarget
        fields = ["parameter", "minimum", "maximum"]

    def __init__(self, *args, tank=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tank = tank if tank is not None else getattr(self.instance, "tank", None)
        self.fields["minimum"].label = "Zielbereich ab"
        self.fields["maximum"].label = "Zielbereich bis"

    def clean(self):
        cleaned = super().clean()
        minimum, maximum = cleaned.get("minimum"), cleaned.get("maximum")
        if minimum is None and maximum is None:
            raise forms.ValidationError("Ohne Grenze ist es kein Zielbereich.")
        if minimum is not None and maximum is not None and minimum > maximum:
            self.add_error("maximum", "Die Obergrenze liegt unter der Untergrenze.")

        parameter = cleaned.get("parameter")
        if parameter is not None and self.tank is not None:
            existing = TankParameterTarget.objects.filter(
                tank=self.tank, parameter=parameter
            ).exclude(pk=self.instance.pk)
            if existing.exists():
                self.add_error(
                    "parameter", "Für diesen Parameter gibt es hier bereits einen Zielbereich."
                )
        return cleaned


class PhotoUploadForm(BootstrapMixin, forms.Form):
    """Mehrere Fotos in einem Durchgang.

    Fotos entstehen serienweise; sie einzeln hochzuladen wäre der Bedienung
    nicht angemessen. Bildunterschrift und Aufnahmedatum gelten deshalb für
    die ganze Auswahl und lassen sich danach je Bild ändern.

    Das Aufnahmedatum ist dabei nur die Vorgabe für Bilder ohne EXIF: was das
    Bild selbst mitbringt, ist genauer als eine Angabe für die ganze Auswahl.
    """

    images = MultipleImageField(
        label="Bilder", help_text="Mehrfachauswahl möglich."
    )
    taken_on = DateField(
        label="Aufgenommen am",
        required=False,
        help_text="Ohne Angabe: der Aufnahmezeitpunkt aus dem Bild, sonst heute.",
    )
    caption = forms.CharField(
        label="Bildunterschrift",
        max_length=200,
        required=False,
        help_text="Gilt für alle ausgewählten Bilder.",
    )

    def clean_taken_on(self):
        value = self.cleaned_data.get("taken_on")
        if value is not None and value > timezone.localdate():
            raise forms.ValidationError("Das Aufnahmedatum liegt in der Zukunft.")
        return value

    def save(self, tank):
        fallback = self.cleaned_data.get("taken_on") or timezone.localdate()
        return [
            TankPhoto.objects.create(
                tank=tank,
                image=image,
                caption=self.cleaned_data.get("caption", ""),
                taken_on=timezone.localtime(stamp).date() if stamp else fallback,
            )
            for image, stamp in _shots_of(self.cleaned_data["images"])
        ]


class EventChoiceField(forms.ModelChoiceField):
    """Ereignisauswahl mit Datum davor — zwei „Trübung“ sind sonst gleich."""

    def label_from_instance(self, obj):
        return f"{timezone.localtime(obj.occurred_at):%d.%m.%Y} · {obj.title}"


class PhotoForm(BootstrapMixin, forms.ModelForm):
    """Bildunterschrift, Aufnahmedatum und Zuordnung eines Fotos.

    Über das Ereignisfeld findet ein bereits hochgeladenes Foto nachträglich zu
    der Beobachtung, zu der es gehört — ohne es erneut hochladen zu müssen.
    """

    taken_on = DateField(label="Aufgenommen am")
    event = EventChoiceField(
        label="Ereignis",
        queryset=Event.objects.none(),
        required=False,
        empty_label="— keinem Ereignis zugeordnet —",
        help_text="Das Foto erscheint dann auch am Ereignis.",
    )

    class Meta:
        model = TankPhoto
        fields = ["caption", "taken_on", "event"]

    def __init__(self, *args, tank=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tank = tank if tank is not None else getattr(self.instance, "tank", None)
        # Zur Auswahl stehen nur Ereignisse desselben Beckens: ein Foto aus
        # Becken A gehört zu keinem Ereignis aus Becken B — und die Ansicht
        # prüft die Zugehörigkeit des Beckens, nicht die jedes Ereignisses.
        if self.tank is not None:
            self.fields["event"].queryset = Event.objects.filter(tank=self.tank)
