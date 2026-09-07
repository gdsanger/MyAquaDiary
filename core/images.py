"""Was aus einem hochgeladenen Bild wird: Angaben daraus und Varianten davon.

Zwei Dinge passieren hier. Erstens wird gelesen, was im Bild steht — der
Aufnahmezeitpunkt aus dem EXIF-Block, den praktisch jede Kamera schreibt: wer
ein Foto vom Vortag nachträgt, soll das Datum nicht von Hand suchen müssen.

Zweitens entstehen beim Speichern kleinere Ausgaben. Ein Handyfoto bringt
mehrere Megabyte bei 4000 px Kantenlänge mit; eine Kachel in einer Übersicht
ist 200 px breit. Ohne Variante lädt der Browser das volle Bild für eine
Briefmarke, und auf einer Seite mit einem Dutzend Kacheln summiert sich das.
Gerechnet wird deshalb einmal beim Speichern und nicht bei jedem Seitenaufruf.

Das Original bleibt unangetastet — es ist die Belegaufnahme, an der eine
Artbestimmung oder das Nachvollziehen einer Beobachtung hängt. Nur die
Auslieferung in Übersichten wird klein. Mit einer Ausnahme: GPS-Angaben werden
auch aus dem Original entfernt. Aquarienfotos entstehen zu Hause, und die
eigene Adresse hat in einer Datei nichts verloren, die weitergegeben werden
kann.
"""

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

#: EXIF-Tags mit einem Aufnahmezeitpunkt, in absteigender Verlässlichkeit.
#: ``DateTimeOriginal`` ist der Auslösezeitpunkt, ``DateTimeDigitized`` der der
#: Digitalisierung und ``DateTime`` der der letzten Änderung — Letzterer ändert
#: sich beim Bearbeiten und kommt deshalb zuletzt.
_DATE_TAGS = (36867, 36868, 306)

#: Kameras legen die beiden Original-Zeitstempel in den Exif-Unterblock;
#: einfachere Schreiber setzen sie direkt in den Hauptblock. Gelesen werden
#: deshalb beide.
_EXIF_IFD = 0x8769

#: Verweis auf den GPS-Unterblock im Haupt-IFD. Ihn zu löschen nimmt dem Bild
#: den Aufnahmeort. Öffentlich, weil auch die Tests danach sehen.
GPS_IFD = 0x8825

#: EXIF schreibt „2026:05:01 18:30:00“ — Doppelpunkte auch im Datum.
_EXIF_FORMAT = "%Y:%m:%d %H:%M:%S"

#: Längste Kante der Kachelvariante. 400 px trägt eine 200-px-Kachel auch auf
#: einem Bildschirm mit doppelter Pixeldichte.
THUMBNAIL_EDGE = 400

#: Längste Kante der Vorschauvariante — groß genug für die Einzelansicht auf
#: einem ausgewachsenen Bildschirm, klein genug, um nicht das Original zu sein.
PREVIEW_EDGE = 1600

#: Qualität der Varianten. Um 80 herum ist der übliche Punkt, an dem eine
#: weitere Stufe kaum noch Bytes spart, aber sichtbar Artefakte bringt.
VARIANT_QUALITY = 80


def taken_at(upload):
    """Aufnahmezeitpunkt eines hochgeladenen Bildes als naive ``datetime``.

    Gibt ``None`` zurück, wenn die Datei kein Bild ist, keinen EXIF-Block hat
    oder einen unbrauchbaren Zeitpunkt darin führt. Ein fehlender oder kaputter
    EXIF-Block ist keine Störung: die aufrufende Stelle nimmt dann ihren
    eigenen Standardwert.
    """
    payload = _payload(upload)
    if payload is None:
        return None
    try:
        with Image.open(BytesIO(payload)) as image:
            exif = image.getexif()
            blocks = [exif, exif.get_ifd(_EXIF_IFD)]
    except (UnidentifiedImageError, OSError, ValueError, AttributeError):
        return None

    for tag in _DATE_TAGS:
        for block in blocks:
            raw = block.get(tag)
            if not isinstance(raw, str):
                continue
            try:
                return datetime.strptime(raw.strip(), _EXIF_FORMAT)
            except ValueError:
                continue
    return None


# --- Varianten -------------------------------------------------------------


class VariantError(Exception):
    """Ein Bild ließ sich nicht verkleinern."""


@dataclass(frozen=True)
class VariantFields:
    """Wie die Felder am Modell heißen.

    Die Logik ist für alle Bilder dieselbe, die Benennung nicht: ein Foto hat
    ein ``image``, ein Becken ein ``cover_image``. Statt jedes Modell eigene
    Methoden schreiben zu lassen, sagt es hier einmal, wo seine Felder stehen.
    """

    source: str = "image"
    thumbnail: str = "thumbnail"
    preview: str = "preview"
    width: str = "width"
    height: str = "height"


@dataclass(frozen=True)
class Variant:
    """Was ein ``<img>`` braucht: Adresse und der Platz, den es einnimmt.

    ``width``/``height`` können fehlen — bei Bestandsdaten, deren Maße noch
    nicht erfasst sind. Die Vorlage lässt die Attribute dann weg, statt sie
    leer zu setzen.
    """

    url: str
    width: int | None = None
    height: int | None = None


#: Kantenlänge je Variantenfeld — der Bezug zwischen Feld und Größe steht
#: einmal hier und nicht in jeder aufrufenden Stelle.
_EDGES = {"thumbnail": THUMBNAIL_EDGE, "preview": PREVIEW_EDGE}


def scaled_size(width, height, edge):
    """Maße, die eine auf ``edge`` begrenzte Kopie hätte.

    Bildet die Rechnung von :func:`PIL.ImageOps.contain` nach, damit die im
    ``<img>`` angegebenen Maße zu der Datei passen, die der Browser lädt. Ein
    Bild, das schon klein genug ist, behält seine Maße — hochskaliert wird
    nicht.
    """
    if not width or not height:
        return None, None
    if max(width, height) <= edge:
        return width, height
    if width >= height:
        return edge, max(1, round(height / width * edge))
    return max(1, round(width / height * edge)), edge


def variant(instance, kind, fields=None):
    """Anzeigequelle eines Bildes: die Variante ``kind``, sonst das Original.

    Der Rückfall ist kein Notnagel, sondern der Normalfall, solange
    ``generate_thumbnails`` für die Bestandsdaten noch nicht durchgelaufen ist:
    Sichtbar bleibt alles, nur eben noch unverkleinert.
    """
    fields = fields or instance.IMAGE_VARIANTS
    source = getattr(instance, fields.source, None)
    if not source:
        return None
    width = getattr(instance, fields.width, None)
    height = getattr(instance, fields.height, None)
    if kind is not None:
        smaller = getattr(instance, getattr(fields, kind), None)
        if smaller:
            return Variant(smaller.url, *scaled_size(width, height, _EDGES[kind]))
    return Variant(source.url, width, height)


def variant_files(instance, fields=None):
    """Alle Dateien eines Objekts — Original und Varianten."""
    fields = fields or instance.IMAGE_VARIANTS
    names = (fields.source, fields.thumbnail, fields.preview)
    files = (getattr(instance, name, None) for name in names)
    return [file for file in files if file]


def build_variants(instance, fields=None, *, force=False):
    """Erzeugt fehlende Varianten und die Maße. Gibt die geänderten Felder zurück.

    Ohne ``force`` bleibt bestehen, was schon da ist: der Aufruf kostet dann
    nichts und lässt sich beliebig wiederholen.
    """
    fields = fields or instance.IMAGE_VARIANTS
    source = getattr(instance, fields.source, None)
    if not source:
        return []

    wanted = [
        kind
        for kind in ("thumbnail", "preview")
        if force or not getattr(instance, getattr(fields, kind), None)
    ]
    sizes_missing = not getattr(instance, fields.width, None) or not getattr(
        instance, fields.height, None
    )
    if not wanted and not (force or sizes_missing):
        return []

    payload = _payload(source)
    if payload is None:
        return []
    try:
        with Image.open(BytesIO(payload)) as opened:
            opened.load()
            image = _upright(opened)
            changed = []
            if force or sizes_missing:
                setattr(instance, fields.width, image.width)
                setattr(instance, fields.height, image.height)
                changed += [fields.width, fields.height]
            stem = Path(source.name).stem
            for kind in wanted:
                rendered = _render(image, _EDGES[kind])
                if rendered is None:
                    continue
                content, extension = rendered
                field_name = getattr(fields, kind)
                existing = getattr(instance, field_name)
                if existing:
                    # Bei ``--force`` sonst zwei Dateien für dieselbe Variante.
                    existing.delete(save=False)
                getattr(instance, field_name).save(f"{stem}.{extension}", content, save=False)
                changed.append(field_name)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise VariantError(str(exc)) from exc
    return changed


def drop_location(instance, fields=None):
    """Nimmt einem noch nicht gespeicherten Upload seine GPS-Angaben.

    Greift vor dem ersten Schreiben in den Speicher: die Datei mit den
    Koordinaten darin soll gar nicht erst entstehen. Eine bereits gespeicherte
    Datei bleibt unangetastet — dafür gibt es
    :func:`drop_location_in_storage`.
    """
    fields = fields or instance.IMAGE_VARIANTS
    source = getattr(instance, fields.source, None)
    # ``_committed`` ist False, solange die Datei nur am Objekt hängt und noch
    # nicht im Speicher liegt — genau dieser Fall und kein anderer.
    if not source or getattr(source, "_committed", True):
        return False
    cleaned = _without_location(source)
    if cleaned is None:
        return False
    setattr(instance, fields.source, ContentFile(cleaned, name=Path(source.name).name))
    return True


def drop_location_in_storage(instance, fields=None):
    """Dasselbe für ein Original, das schon im Speicher liegt (Bestandsdaten).

    Geschrieben wird zuerst, gelöscht danach: bricht es dazwischen ab, gibt es
    eine Datei zu viel und keine zu wenig.
    """
    fields = fields or instance.IMAGE_VARIANTS
    source = getattr(instance, fields.source, None)
    if not source:
        return False
    cleaned = _without_location(source)
    if cleaned is None:
        return False
    previous = source.name
    source.save(Path(previous).name, ContentFile(cleaned), save=False)
    if source.name != previous:
        source.storage.delete(previous)
    return True


class ImageVariantsMixin:
    """Modelle mit Bild: Varianten entstehen beim Speichern.

    Bewusst kein abstraktes Modell, sondern ein gewöhnlicher Mixin — die
    Variantenfelder heißen je Modell anders und werden dort deklariert. Hier
    steht nur, was mit ihnen passiert.
    """

    #: Wo die Felder dieses Modells stehen. Modelle mit abweichender Benennung
    #: überschreiben das.
    IMAGE_VARIANTS = VariantFields()

    def save(self, *args, **kwargs):
        fields = self.IMAGE_VARIANTS
        update_fields = kwargs.get("update_fields")
        # Ein gezieltes ``save(update_fields=["is_primary"])`` rührt das Bild
        # nicht an und braucht deshalb auch keine Bildverarbeitung.
        touches_image = update_fields is None or fields.source in set(update_fields)
        source = getattr(self, fields.source, None)
        # Ein frischer Upload heißt: was an Varianten dasteht, gehört zu einem
        # anderen Bild und muss neu entstehen.
        uploaded = bool(source) and not getattr(source, "_committed", True)
        if touches_image and uploaded:
            drop_location(self, fields)
        super().save(*args, **kwargs)
        if not touches_image:
            return
        changed = build_variants(self, fields, force=uploaded)
        if changed:
            super().save(update_fields=changed)

    @property
    def thumb(self):
        """Kachelgröße (400 px) — für Raster, Karten und Listen."""
        return variant(self, "thumbnail")

    @property
    def large(self):
        """Vorschaugröße (1600 px) — für die Einzelansicht."""
        return variant(self, "preview")

    @property
    def original(self):
        """Das Original samt seiner Maße."""
        return variant(self, None)


# --- Innereien -------------------------------------------------------------


def _payload(file):
    """Inhalt einer Datei als Bytes; ``None``, wenn sie sich nicht lesen lässt.

    Gearbeitet wird danach auf einer Kopie im Speicher. Das ist ein paar
    Megabyte teurer als ein Dateizeiger, erspart aber die Frage, in welchem
    Zustand ein ``FieldFile`` oder ein Upload hinterher steht — die Datei
    wandert nach dem Lesen oft noch in den Speicher.
    """
    try:
        file.open()
    except (AttributeError, ValueError, OSError, FileNotFoundError):
        pass
    try:
        file.seek(0)
        return file.read()
    except (AttributeError, ValueError, OSError):
        return None
    finally:
        try:
            file.seek(0)
        except (AttributeError, ValueError, OSError):
            pass


def _upright(image):
    """Wendet die EXIF-Orientierung an — sonst liegt ein Hochformat quer."""
    try:
        return ImageOps.exif_transpose(image) or image
    except Exception:  # pragma: no cover - kaputte EXIF-Daten dürfen nichts kosten
        return image


def _render(image, edge):
    """Verkleinerte Kopie als ``(ContentFile, Endung)``.

    WebP spart gegenüber JPEG bei gleicher Qualität noch einmal deutlich; ist
    es in der vorliegenden Pillow-Fassung nicht verfügbar, wird JPEG
    geschrieben. Die Varianten tragen keinen EXIF-Block mehr — weder
    Orientierung (die steckt jetzt in den Pixeln) noch Aufnahmeort.
    """
    # ``contain`` skaliert auch hoch. Die Box wird deshalb auf die vorhandene
    # Kantenlänge begrenzt: ein kleines Bild bleibt, wie es ist.
    box = min(edge, max(image.size))
    copy = ImageOps.contain(image, (box, box), Image.LANCZOS)
    transparent = copy.mode in {"RGBA", "LA", "PA"} or "transparency" in copy.info
    for extension, options in (
        ("webp", {"quality": VARIANT_QUALITY, "method": 4}),
        ("jpg", {"quality": VARIANT_QUALITY, "optimize": True}),
    ):
        mode = "RGBA" if transparent and extension == "webp" else "RGB"
        buffer = BytesIO()
        try:
            copy.convert(mode).save(buffer, format=extension.upper(), **options)
        except (OSError, KeyError, ValueError):
            continue
        return ContentFile(buffer.getvalue()), extension
    return None


def _without_location(file):
    """Bild ohne GPS-Angaben als Bytes; ``None``, wenn keine drin sind.

    Der Rückgabewert ``None`` ist der Normalfall und ausdrücklich erwünscht:
    ohne Koordinaten wird die Datei nicht angefasst und behält Byte für Byte,
    was die Kamera geschrieben hat.
    """
    payload = _payload(file)
    if payload is None:
        return None
    try:
        with Image.open(BytesIO(payload)) as image:
            exif = image.getexif()
            if GPS_IFD not in exif:
                return None
            image.load()
            del exif[GPS_IFD]
            options = {"exif": exif.tobytes()}
            if image.format == "JPEG":
                # Dieselben Quantisierungstabellen wie im Original: das Bild
                # wird neu geschrieben, aber nicht neu gerechnet.
                options |= {"quality": "keep", "subsampling": "keep"}
            buffer = BytesIO()
            image.save(buffer, format=image.format, **options)
    except (UnidentifiedImageError, OSError, ValueError, KeyError, AttributeError):
        return None
    return buffer.getvalue()
