"""Aufräumen in der geschützten Ablage.

Django löscht die Datei eines gelöschten Datensatzes nicht mit — aus gutem
Grund: eine zurückgerollte Transaktion holt sie nicht zurück. Für
Gerätedokumente wiegt das andere schwerer. Wer eine Rechnung entfernt, erwartet,
dass sie weg ist; und wer ein Gerät löscht, erst recht, dass nicht die
Bedienungsanleitung samt Rechnung liegen bleibt. Das gilt genauso, wenn das
Löschen als Kaskade vom Gerät kommt — deshalb ``post_delete`` und nicht eine
überschriebene ``delete()``: die läuft bei einer Kaskade gar nicht erst.
"""

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import DeviceDocument

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=DeviceDocument)
def delete_document_file(sender, instance, **kwargs):
    """Entfernt die Datei eines gelöschten Gerätedokuments."""
    if not instance.file:
        return
    try:
        instance.file.storage.delete(instance.file.name)
    except OSError:
        # Eine Datei, die schon nicht mehr da ist, ist kein Grund, das Löschen
        # scheitern zu lassen — der Datensatz ist bereits weg.
        logger.warning("Dokument %s konnte nicht gelöscht werden", instance.file.name)
