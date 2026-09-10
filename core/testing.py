"""Hilfsfunktionen, die sich mehrere Testmodule teilen."""

from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from catalog.models import AnimalSpecies, PlantSpecies
from tanks.models import CareTask, Measurement, Parameter, Stocking, Tank

PASSWORD = "test-passwort-1234"


def create_user(username="aquarianer", **kwargs):
    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", password=PASSWORD, **kwargs
    )


def create_tank(owner, name="Gesellschaftsbecken", **kwargs):
    defaults = {
        "slug": kwargs.pop("slug", name.lower().replace(" ", "-")),
        "volume_liters": Decimal("240.0"),
        "setup_date": timezone.localdate() - timedelta(days=400),
        "accent": 3,
    }
    defaults.update(kwargs)
    return Tank.objects.create(owner=owner, name=name, **defaults)


def tank_named(owner, name="Becken 1"):
    """Becken eines Benutzers, bei Bedarf angelegt.

    Für Tests, in denen das Becken nur Beiwerk ist: ein Gerät braucht eines,
    aber welches, ist der Sache egal.
    """
    existing = Tank.objects.filter(owner=owner, name=name).first()
    return existing or create_tank(owner, name=name)


def create_measurement(tank, parameter_key="ph", value="7.0", days_ago=0, below_detection=False):
    parameter = Parameter.objects.get(key=parameter_key)
    return Measurement.objects.create(
        tank=tank,
        parameter=parameter,
        # „n.n.“ trägt keinen Zahlenwert: entweder eine Zahl oder below_detection.
        value=None if below_detection else Decimal(value),
        below_detection=below_detection,
        measured_at=timezone.now() - timedelta(days=days_ago),
    )


def create_task(tank, title="Wasserwechsel", days_until_due=0, interval_days=7):
    return CareTask.objects.create(
        tank=tank,
        title=title,
        category=CareTask.Category.WATER_CHANGE,
        interval_days=interval_days,
        due_on=timezone.localdate() + timedelta(days=days_until_due),
    )


def create_animal(scientific_name="Paracheirodon innesi", **kwargs):
    defaults = {
        "slug": kwargs.pop("slug", scientific_name.lower().replace(" ", "-")),
        "common_name": kwargs.pop("common_name", "Neonsalmler"),
        "min_group_size": 10,
    }
    defaults.update(kwargs)
    return AnimalSpecies.objects.create(scientific_name=scientific_name, **defaults)


def create_plant(scientific_name="Cryptocoryne wendtii", **kwargs):
    defaults = {
        "slug": kwargs.pop("slug", scientific_name.lower().replace(" ", "-")),
        "common_name": kwargs.pop("common_name", "Wendts Wasserkelch"),
    }
    defaults.update(kwargs)
    return PlantSpecies.objects.create(scientific_name=scientific_name, **defaults)


def image_upload(name="foto.png"):
    """Winziges, echtes PNG als Upload.

    Ein ``ImageField`` lässt Pillow prüfen, ob die Datei wirklich ein Bild ist;
    ein paar zufällige Bytes reichen dafür nicht.
    """
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


#: EXIF-Tags, die in Tests gesetzt werden: Aufnahmezeitpunkt, Orientierung
#: und der Verweis auf den GPS-Block.
_EXIF_TAKEN_AT = 36867
_EXIF_ORIENTATION = 274
_EXIF_GPS = 0x8825

#: Ein Aufnahmeort, wie ihn ein Telefon schreibt (Landshut, gerundet).
_GPS_BLOCK = {1: "N", 2: (48.0, 32.0, 0.0), 3: "E", 4: (12.0, 9.0, 0.0)}


def photo_upload(name="foto.jpg", taken_at=None, size=(2, 2), orientation=None, located=False):
    """JPEG mit EXIF-Block als Upload.

    ``taken_at`` ist ein naives ``datetime`` — genau das, was eine Kamera
    schreibt: die Ortszeit ihrer eigenen Uhr, ohne Zeitzone. ``size`` macht das
    Bild groß genug, um verkleinert zu werden; ``orientation`` und ``located``
    hängen Orientierung und Aufnahmeort an, wie ein Telefon es tut.
    """
    exif = Image.Exif()
    if taken_at is not None:
        exif[_EXIF_TAKEN_AT] = taken_at.strftime("%Y:%m:%d %H:%M:%S")
    if orientation is not None:
        exif[_EXIF_ORIENTATION] = orientation
    if located:
        exif[_EXIF_GPS] = dict(_GPS_BLOCK)
    buffer = BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="JPEG", exif=exif)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


#: Elemente ohne Inhalt und ohne Endtag.
_VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


class _TagBalance(HTMLParser):
    """Zählt Start- und Endtags mit und merkt sich, was nicht zusammenpasst."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.problems = []

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID_ELEMENTS:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in _VOID_ELEMENTS:
            return
        if not self.stack:
            self.problems.append(f"</{tag}> in Zeile {self.getpos()[0]} ohne Anfang")
        elif self.stack[-1][0] != tag:
            open_tag, line = self.stack[-1]
            self.problems.append(
                f"</{tag}> in Zeile {self.getpos()[0]} schließt <{open_tag}> aus Zeile {line}"
            )
        else:
            self.stack.pop()

    def close(self):
        super().close()
        self.problems += [f"<{tag}> aus Zeile {line} bleibt offen" for tag, line in self.stack]
        return self.problems


def unbalanced_tags(markup):
    """Nicht geschlossene oder falsch geschachtelte Elemente in ``markup``.

    Leere Liste heißt: sauber verschachtelt. Kein vollständiger Validator —
    aber genau der Fehler, der eine Karte in die nächste rutschen lässt.
    Setzt voraus, dass auch die optionalen Endtags (``</li>``, ``</p>``)
    geschrieben sind; in diesem Projekt ist das so.
    """
    parser = _TagBalance()
    parser.feed(markup)
    return parser.close()


def stock(tank, species, quantity=12, days_ago=30):
    return Stocking.objects.create(
        tank=tank,
        species=species,
        quantity=quantity,
        added_on=timezone.localdate() - timedelta(days=days_ago),
    )
