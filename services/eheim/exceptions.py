"""Fehlerklassen der Eheim-Anbindung.

Alle Fehler der Anbindung erben von :class:`EheimError`; ein Aufrufer, der nur
"hat nicht geklappt" wissen muss, fängt diese eine Klasse.
"""


class EheimError(Exception):
    """Basisklasse: die Kommunikation mit dem Gerät ist fehlgeschlagen."""


class EheimNotConfigured(EheimError):
    """Es fehlen Angaben, ohne die kein Request möglich ist (Host, MAC)."""


class EheimUnreachable(EheimError):
    """Gerät nicht erreichbar — Timeout, DNS, Verbindung abgelehnt."""


class EheimAuthError(EheimError):
    """Basic-Auth wurde abgelehnt (401/403)."""


class EheimResponseError(EheimError):
    """Das Gerät hat geantwortet, aber nicht verwertbar (Status, kaputtes JSON)."""


class EheimEndpointBlocked(EheimError):
    """Der Endpunkt ist in dieser Anwendung bewusst gesperrt (``/doupdate``)."""


class EheimFirmwareTooOld(EheimError):
    """Die Gerätesoftware ist älter als die erste Version mit REST-API."""
