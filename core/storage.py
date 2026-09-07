"""Ablage für Dateien, die nicht jeder abrufen darf.

``MEDIA_ROOT`` wird ausgeliefert, wie es dasteht: wer die Adresse einer Datei
kennt, bekommt sie — ohne Anmeldung, ohne Prüfung, wem sie gehört. Für ein
Beckenfoto ist das hinnehmbar, für die Rechnung eines Geräts nicht: dort stehen
Name, Anschrift und Zahlungsdaten. Eine nicht erratbare Adresse ist dafür kein
Ersatz, sondern nur eine Hoffnung.

Deshalb liegen solche Dateien woanders — unter ``PRIVATE_MEDIA_ROOT``, außerhalb
von ``MEDIA_ROOT`` und damit außerhalb dessen, was ausgeliefert wird. Der einzige
Weg zu ihnen führt über eine Ansicht, die vorher die Eigentümerschaft prüft
(siehe :func:`services.views.device_document`).
"""

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property


@deconstructible
class PrivateStorage(FileSystemStorage):
    """Dateiablage außerhalb von ``MEDIA_ROOT`` und ohne öffentliche Adresse.

    :meth:`url` wirft bewusst: eine Datei von hier hat keine Adresse, unter der
    ein Browser sie direkt holen könnte. Wer eine baut, hat den Zugriffsschutz
    umgangen — und soll das an Ort und Stelle merken, nicht erst im Betrieb.
    """

    def __init__(self, location=None, base_url=None, **kwargs):
        super().__init__(location=location, base_url=base_url, **kwargs)

    @cached_property
    def base_location(self):
        return self._value_or_setting(self._location, settings.PRIVATE_MEDIA_ROOT)

    def _clear_cached_properties(self, setting, **kwargs):
        """Auch auf ``PRIVATE_MEDIA_ROOT`` hören, nicht nur auf ``MEDIA_ROOT``.

        Django leert den Zwischenspeicher der Ablage, wenn sich eine seiner
        eigenen Einstellungen ändert. Unsere kennt es nicht — ohne diese Zeilen
        bliebe ein ``override_settings`` im Test wirkungslos, und die Dateien
        landeten in der echten Ablage.
        """
        super()._clear_cached_properties(setting, **kwargs)
        if setting == "PRIVATE_MEDIA_ROOT":
            self.__dict__.pop("base_location", None)
            self.__dict__.pop("location", None)

    def url(self, name):
        raise ValueError(
            "Dateien in der geschützten Ablage haben keine öffentliche Adresse; "
            "sie werden nur über eine Ansicht mit Eigentümerprüfung ausgeliefert."
        )


#: Eine Instanz reicht — die Ablage hat keinen Zustand.
private_storage = PrivateStorage()
