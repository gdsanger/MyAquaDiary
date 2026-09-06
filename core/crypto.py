"""Symmetrische Verschlüsselung für Geheimnisse, die in der Datenbank liegen.

Genutzt von :class:`core.fields.EncryptedTextField`. Das Schlüsselmaterial kommt
aus ``settings.FIELD_ENCRYPTION_KEYS``; ist dort nichts konfiguriert, wird ein
Schlüssel aus dem ``SECRET_KEY`` abgeleitet, damit ein frischer Checkout ohne
Zusatzsetup läuft.

Mehrere Schlüssel erlauben Rotation: verschlüsselt wird immer mit dem ersten,
entschlüsselt mit jedem der angegebenen Schlüssel.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


class DecryptionError(Exception):
    """Ein gespeicherter Wert konnte nicht entschlüsselt werden."""


def _normalize_key(material: str) -> bytes:
    """Akzeptiert sowohl fertige Fernet-Keys als auch beliebige Passphrasen."""
    candidate = material.strip().encode()
    try:
        if len(base64.urlsafe_b64decode(candidate)) == 32:
            return candidate
    except (ValueError, TypeError):
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(candidate).digest())


def _configured_keys() -> list[str]:
    configured = getattr(settings, "FIELD_ENCRYPTION_KEYS", None) or []
    if isinstance(configured, str):
        configured = [configured]
    return [key for key in (str(k).strip() for k in configured) if key]


def _fernet() -> MultiFernet:
    keys = _configured_keys() or [settings.SECRET_KEY]
    return MultiFernet([Fernet(_normalize_key(key)) for key in keys])


def encrypt(value: str) -> str:
    """Verschlüsselt einen String und gibt den Fernet-Token als Text zurück."""
    return _fernet().encrypt(str(value).encode()).decode()


def decrypt(token: str) -> str:
    """Entschlüsselt einen Fernet-Token.

    :raises DecryptionError: wenn der Token nicht zum Schlüsselmaterial passt.
    """
    try:
        return _fernet().decrypt(str(token).encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionError("Wert konnte nicht entschlüsselt werden") from exc
