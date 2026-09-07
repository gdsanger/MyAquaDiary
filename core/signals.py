"""Aufräumen, was Django von sich aus liegen lässt.

Ein gelöschter Datensatz nimmt seine Dateien nicht mit — Django trennt das
bewusst, weil eine zurückgerollte Transaktion eine gelöschte Datei nicht
zurückholen kann. Für Bilder in einem privaten Tagebuch wiegt das andere
schwerer: wer ein Foto löscht, erwartet, dass es weg ist, und zwar samt
Kachel und Vorschau. Sonst wächst der Speicher mit jedem Löschvorgang.
"""

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .images import variant_files

logger = logging.getLogger(__name__)


@receiver(post_delete)
def delete_image_files(sender, instance, **kwargs):
    """Entfernt Original und Varianten eines gelöschten Objekts.

    Hängt bewusst an allen Modellen und prüft dann auf ``IMAGE_VARIANTS``:
    so kommt jedes weitere Bildmodell ohne eigene Verdrahtung aus.
    """
    if getattr(sender, "IMAGE_VARIANTS", None) is None:
        return
    for file in variant_files(instance):
        try:
            file.storage.delete(file.name)
        except OSError:
            # Eine Datei, die schon nicht mehr da ist, ist kein Grund, das
            # Löschen scheitern zu lassen — der Datensatz ist bereits weg.
            logger.warning("Bilddatei %s konnte nicht gelöscht werden", file.name)
