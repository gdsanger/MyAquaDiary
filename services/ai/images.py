"""Bilder für den Versand an Claude aufbereiten.

Bilder sind der teure Teil der Bilderkennung — ein Foto aus einer Handykamera
kostet ein Vielfaches der Token, die eine Artbestimmung braucht. Deshalb wird
jedes Bild vor dem Versand auf eine sinnvolle Kantenlänge heruntergerechnet
und als JPEG neu kodiert.

Nebeneffekt, der hier ausdrücklich erwünscht ist: das neu kodierte Bild trägt
keine EXIF-Daten mehr. Ein Aufnahmeort geht damit nicht an einen externen
Dienst — das Original bleibt davon unberührt, verarbeitet wird nur die Kopie.
"""

import base64
import io
import logging
from dataclasses import dataclass

from django.conf import settings
from PIL import Image, UnidentifiedImageError

from .exceptions import AIError

logger = logging.getLogger(__name__)

#: Fallback, wenn ``settings.AI_IMAGE_MAX_EDGE`` fehlt.
DEFAULT_MAX_EDGE = 1024
#: JPEG-Qualität der verkleinerten Kopie. 85 ist der übliche Kompromiss aus
#: Dateigröße und sichtbaren Artefakten.
JPEG_QUALITY = 85
#: Größere Uploads werden gar nicht erst geöffnet.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MEDIA_TYPE = "image/jpeg"


@dataclass(frozen=True)
class PreparedImage:
    """Ein versandfertiges Bild samt der Zahlen für die Anzeige."""

    data: str
    """Base64-kodiertes JPEG."""
    media_type: str = MEDIA_TYPE
    width: int = 0
    height: int = 0
    size_bytes: int = 0

    def as_content_block(self) -> dict:
        """Content-Block, wie ihn die Messages-API erwartet."""
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": self.media_type, "data": self.data},
        }


def max_edge() -> int:
    return int(getattr(settings, "AI_IMAGE_MAX_EDGE", DEFAULT_MAX_EDGE) or DEFAULT_MAX_EDGE)


def prepare_image(image_file, edge: int | None = None) -> PreparedImage:
    """Verkleinert ein hochgeladenes Bild und kodiert es für den Versand.

    :param image_file: Datei-Objekt (z. B. ein ``UploadedFile``).
    :param edge: Längste Kante in Pixeln; ohne Angabe ``AI_IMAGE_MAX_EDGE``.
    :raises AIError: wenn die Datei zu groß oder kein lesbares Bild ist.
    """
    limit = edge or max_edge()

    size = getattr(image_file, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise AIError(
            f"Das Bild ist zu groß ({size // (1024 * 1024)} MB). "
            f"Erlaubt sind {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    try:
        image_file.seek(0)
        with Image.open(image_file) as image:
            image.load()
            # EXIF-Orientierung anwenden, sonst steht ein Hochformatfoto quer
            # im Prompt und die Bestimmung wird unnötig schwer.
            image = _apply_exif_orientation(image)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((limit, limit), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            width, height = image.size
    except UnidentifiedImageError as exc:
        raise AIError("Die Datei konnte nicht als Bild gelesen werden.") from exc
    except OSError as exc:
        logger.warning("Bild konnte nicht aufbereitet werden: %s", exc)
        raise AIError("Das Bild konnte nicht verarbeitet werden.") from exc
    finally:
        try:
            image_file.seek(0)
        except (OSError, ValueError):  # pragma: no cover - geschlossene Datei
            pass

    payload = buffer.getvalue()
    return PreparedImage(
        data=base64.standard_b64encode(payload).decode("ascii"),
        width=width,
        height=height,
        size_bytes=len(payload),
    )


def _apply_exif_orientation(image: Image.Image) -> Image.Image:
    """Dreht das Bild gemäß EXIF-Orientierung — best effort."""
    try:
        from PIL import ImageOps

        return ImageOps.exif_transpose(image) or image
    except Exception:  # pragma: no cover - kaputte EXIF-Daten dürfen nichts kosten
        return image
