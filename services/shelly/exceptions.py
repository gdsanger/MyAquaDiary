"""Fehlerklassen der Shelly-Anbindung.

Aufgebaut wie die der Eheim-Anbindung: alles erbt von :class:`ShellyError`,
damit ein Aufrufer, der nur "hat nicht geklappt" wissen muss, genau eine Klasse
fängt. Bewusst eine eigene Hierarchie und keine gemeinsame Basis mit Eheim —
die beiden APIs haben nichts miteinander zu tun, und die Schicht darüber
(:mod:`services.devices`) fängt ohnehin beide.
"""


class ShellyError(Exception):
    """Basisklasse: die Kommunikation mit der Steckdose ist fehlgeschlagen."""


class ShellyNotConfigured(ShellyError):
    """Es fehlen Angaben, ohne die kein Request möglich ist (Adresse)."""


class ShellyUnreachable(ShellyError):
    """Steckdose nicht erreichbar — Timeout, DNS, Verbindung abgelehnt."""


class ShellyAuthError(ShellyError):
    """Die Anmeldung wurde abgelehnt (401/403)."""


class ShellyResponseError(ShellyError):
    """Die Steckdose hat geantwortet, aber nicht verwertbar."""


class ShellyUnknownGeneration(ShellyError):
    """``/shelly`` hat geantwortet, aber mit einer unbekannten Generation."""
