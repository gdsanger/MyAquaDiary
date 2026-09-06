"""Wiederverwendbare Modellfelder."""

import logging

from django.db import models

from .crypto import DecryptionError, decrypt, encrypt

logger = logging.getLogger(__name__)


class EncryptedTextField(models.TextField):
    """TextField, dessen Inhalt verschlüsselt in der Datenbank liegt.

    Im Python-Code verhält sich das Feld wie ein normales TextField, in der
    Datenbank steht ein Fernet-Token. Zwei Einschränkungen ergeben sich daraus:

    * Das Feld ist nicht filterbar — jeder Verschlüsselungsvorgang erzeugt
      einen anderen Token, ``filter(feld="x")`` trifft also nie.
    * ``dumpdata`` exportiert den Klartext (nötig für den Round-Trip über
      ``loaddata``); solche Fixtures sind entsprechend zu behandeln.

    Lässt sich ein Wert nicht entschlüsseln — etwa nach einem Wechsel des
    Schlüsselmaterials —, wird ein leerer String zurückgegeben und der Vorfall
    protokolliert. Die Anwendung läuft damit weiter, das betroffene Feature
    verhält sich wie "nicht konfiguriert".
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return encrypt(value)

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return decrypt(value)
        except DecryptionError:
            logger.error(
                "Feld %s konnte nicht entschlüsselt werden — passt das Schlüsselmaterial "
                "(FIELD_ENCRYPTION_KEYS / SECRET_KEY) noch zum Datenbankinhalt?",
                self.name,
            )
            return ""
