"""Was sich aus einem hochgeladenen Bild ablesen lässt.

Bisher nur der Aufnahmezeitpunkt. Er steht im EXIF-Block, den praktisch jede
Kamera und jedes Telefon schreibt — wer ein Foto vom Vortag nachträgt, soll das
Datum nicht von Hand suchen müssen.
"""

from datetime import datetime

from PIL import Image, UnidentifiedImageError

#: EXIF-Tags mit einem Aufnahmezeitpunkt, in absteigender Verlässlichkeit.
#: ``DateTimeOriginal`` ist der Auslösezeitpunkt, ``DateTimeDigitized`` der der
#: Digitalisierung und ``DateTime`` der der letzten Änderung — Letzterer ändert
#: sich beim Bearbeiten und kommt deshalb zuletzt.
_DATE_TAGS = (36867, 36868, 306)

#: Kameras legen die beiden Original-Zeitstempel in den Exif-Unterblock;
#: einfachere Schreiber setzen sie direkt in den Hauptblock. Gelesen werden
#: deshalb beide.
_EXIF_IFD = 0x8769

#: EXIF schreibt „2026:05:01 18:30:00“ — Doppelpunkte auch im Datum.
_EXIF_FORMAT = "%Y:%m:%d %H:%M:%S"


def taken_at(upload):
    """Aufnahmezeitpunkt eines hochgeladenen Bildes als naive ``datetime``.

    Gibt ``None`` zurück, wenn die Datei kein Bild ist, keinen EXIF-Block hat
    oder einen unbrauchbaren Zeitpunkt darin führt. Ein fehlender oder kaputter
    EXIF-Block ist keine Störung: die aufrufende Stelle nimmt dann ihren
    eigenen Standardwert.
    """
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            exif = image.getexif()
            blocks = [exif, exif.get_ifd(_EXIF_IFD)]
    except (UnidentifiedImageError, OSError, ValueError, AttributeError):
        return None
    finally:
        # Die Datei wandert danach weiter in den Speicher — sie muss dafür
        # wieder am Anfang stehen.
        try:
            upload.seek(0)
        except (OSError, ValueError):
            pass

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
