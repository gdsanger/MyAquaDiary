"""Geräteklassen der Eheim-Anbindung.

Aufbau: :class:`EheimService` kennt ausschließlich die **allgemeinen**
Endpunkte, die jedes Eheim-Digital-Gerät hat (``/userdata``, ``/mesh-liste``,
``/brightness-status-led``, ``/changeauth``). Je Gerätetyp kommt eine Ableitung
dazu, die ihren Status-Endpunkt liest und ihre Schreibbefehle anbietet — in v1
ist das :class:`ClassicVarioService`.

Ein weiterer Gerätetyp (reeflexUV+e, pHcontrol+e, thermocontrol+e, …) braucht
damit genau zwei Dinge: eine Ableitung mit ``kind`` und ``read_status()`` sowie
den passenden Eintrag in ``Device.Kind``. Geräte, die Messwerte liefern, füllen
``DeviceStatus.measurements`` — daraus lassen sich später ``Measurement``-Sätze
erzeugen, ohne an dieser Schicht etwas zu ändern.

Diese Schicht kennt bewusst keine Django-Modelle; sie spricht nur HTTP und
liefert Dataclasses. Das Speichern liegt in :mod:`services.devices`.

``/doupdate`` gibt es hier nicht und soll es nicht geben — der Client sperrt
den Pfad zusätzlich.
"""

import logging
from dataclasses import dataclass, field

from .client import DEFAULT_USERNAME, EheimClient, is_supported_firmware, normalize_mac
from .convert import (
    format_minutes,
    parse_api_bool,
    parse_int,
    parse_minutes,
    to_api_bool,
    to_minutes,
)

logger = logging.getLogger(__name__)

#: Gerätearten. Die Werte sind identisch mit ``services.models.Device.Kind`` —
#: das Modell übernimmt sie von hier, damit es nur eine Quelle gibt.
KIND_CLASSICVARIO = "eheim_classicvario"
KIND_EHEIM_OTHER = "eheim_other"

#: Pumpenmodi des classicVARIO+e laut API-Dokumentation.
PUMP_MODE_BIO = 4
PUMP_MODE_PULSE = 8
PUMP_MODE_MANUAL = 16
PUMP_MODE_LABELS = {
    PUMP_MODE_BIO: "Bio-Modus",
    PUMP_MODE_PULSE: "Pulse-Modus",
    PUMP_MODE_MANUAL: "Manueller Modus",
}

#: Fehlercodes laut API-Dokumentation.
ERROR_NONE = 0
ERROR_ROTOR_BLOCKED = 1
ERROR_AIR_IN_FILTER = 2
ERROR_LABELS = {
    ERROR_NONE: "kein Fehler",
    ERROR_ROTOR_BLOCKED: "Rotor blockiert",
    ERROR_AIR_IN_FILTER: "Luft im Filter",
}

MIN_SPEED_PERCENT = 0
MAX_SPEED_PERCENT = 100


def error_text(code) -> str:
    """Fehlercode im Klartext — unbekannte Codes bleiben sichtbar."""
    if code is None:
        return ""
    return ERROR_LABELS.get(code, f"unbekannter Fehler ({code})")


def mode_label(code) -> str:
    """Pumpenmodus im Klartext."""
    if code is None:
        return ""
    return PUMP_MODE_LABELS.get(code, f"unbekannter Modus ({code})")


@dataclass(frozen=True)
class DeviceStatus:
    """Ausgewerteter Gerätestatus samt unveränderter Rohantwort.

    Die Rohantwort wandert unverändert in ``DeviceReading.payload``: erweitert
    Eheim die Antwort oder belegt ein Feld anders, lässt sich die Historie
    nachträglich auswerten, ohne neu messen zu müssen.
    """

    payload: dict
    is_on: bool | None = None
    rpm_percent: int | None = None
    pump_mode: int | None = None
    error_code: int | None = None
    service_due_in: int | None = None
    #: Messwerte künftiger Gerätetypen (pH, Temperatur) — Name -> Dezimalwert.
    measurements: dict = field(default_factory=dict)

    @property
    def has_error(self) -> bool:
        return bool(self.error_code)

    @property
    def error_text(self) -> str:
        return error_text(self.error_code)

    @property
    def mode_label(self) -> str:
        return mode_label(self.pump_mode)

    @property
    def service_due_in_days(self):
        if self.service_due_in is None:
            return None
        return round(self.service_due_in / 24)


@dataclass(frozen=True)
class ClassicVarioStatus(DeviceStatus):
    """Status des classicVARIO+e — zusätzlich die Tag-/Nachtwerte."""

    manual_speed: int | None = None
    day_speed: int | None = None
    night_speed: int | None = None
    day_start: int | None = None
    night_start: int | None = None

    @property
    def day_start_label(self) -> str:
        return format_minutes(self.day_start) if self.day_start is not None else ""

    @property
    def night_start_label(self) -> str:
        return format_minutes(self.night_start) if self.night_start is not None else ""


@dataclass(frozen=True)
class MeshDevice:
    """Ein im Mesh gefundenes Gerät."""

    mac: str
    name: str = ""
    firmware: str = ""
    kind: str = KIND_EHEIM_OTHER
    payload: dict = field(default_factory=dict)

    @property
    def firmware_supported(self) -> bool:
        return is_supported_firmware(self.firmware)

    @property
    def label(self) -> str:
        return self.name or self.mac


def pick(payload: dict, *names, default=None):
    """Erster vorhandener Wert aus mehreren Feldnamen.

    Die API schreibt einzelne Felder je nach Softwarestand unterschiedlich
    (``filterActive`` vs. ``filter_active``); die Kandidaten stehen deshalb
    hier statt verstreut im Auswertungscode.
    """
    for name in names:
        if isinstance(payload, dict) and name in payload and payload[name] is not None:
            return payload[name]
    return default


class EheimService:
    """Basis aller Eheim-Geräte: die allgemeinen Endpunkte.

    :param device: ein :class:`services.models.Device` (optional, wenn ein
        fertiger Client übergeben wird).
    """

    #: Wert aus ``Device.Kind``, für die Registry.
    kind = KIND_EHEIM_OTHER
    #: Anzeigename des Gerätetyps.
    label = "Eheim-Gerät"

    def __init__(self, device=None, *, client=None, **client_kwargs):
        if client is None:
            if device is None:
                raise ValueError("EheimService braucht ein Device oder einen Client")
            client = EheimClient.for_device(device, **client_kwargs)
        self.device = device
        self.client = client

    # -- allgemeine Endpunkte ------------------------------------------------

    def userdata(self, mac: str | None = None) -> dict:
        """Gerätekonfiguration (``GET /userdata``)."""
        return self.client.get("/userdata", mac=mac, require_mac=False)

    def mesh_list(self) -> list[str]:
        """MAC-Adressen aller Geräte im Mesh (``GET /mesh-liste``)."""
        payload = self.client.get("/mesh-liste", require_mac=False)
        candidates = pick(payload, "clientList", "mesh", "items", "devices", default=[])
        macs = []
        for entry in candidates or []:
            mac = entry.get("mac") if isinstance(entry, dict) else entry
            mac = normalize_mac(mac)
            if mac and mac not in macs:
                macs.append(mac)
        return macs

    def discover(self) -> list[MeshDevice]:
        """Mesh-Liste holen und je Gerät die ``/userdata`` dazu lesen.

        Antwortet ein einzelnes Gerät nicht, fehlen nur dessen Details — die
        Suche insgesamt läuft weiter.
        """
        found = []
        for mac in self.mesh_list():
            try:
                payload = self.userdata(mac=mac)
            except Exception as exc:  # eine stumme Node darf die Suche nicht kippen
                logger.info("Eheim-Gerät %s antwortet nicht auf /userdata: %s", mac, exc)
                found.append(MeshDevice(mac=mac))
                continue
            found.append(
                MeshDevice(
                    mac=mac,
                    name=str(pick(payload, "name", "aqName", "title", default="") or "").strip(),
                    firmware=str(pick(payload, "version", "sw_version", "firmware", default="") or ""),
                    kind=classify(payload),
                    payload=payload,
                )
            )
        return found

    def set_status_led_brightness(self, percent: int) -> dict:
        """Helligkeit der Status-LED (``POST /brightness-status-led``)."""
        return self.client.post("/brightness-status-led", {"brightness": _percent(percent)})

    def change_auth(self, password: str, username: str = DEFAULT_USERNAME) -> dict:
        """Zugangsdaten des Geräts ändern (``POST /changeauth``).

        Das Werkspasswort im LAN stehen zu lassen ist kein guter Zustand —
        deshalb ist das Ändern Teil der Anbindung und nicht optional gedacht.
        """
        if not password:
            raise ValueError("Ein leeres Passwort ist nicht zulässig")
        return self.client.post("/changeauth", {"user": username, "password": password})

    # /doupdate gibt es hier bewusst nicht: ein Firmware-Update aus dem
    # Tagebuch heraus anzustoßen ist unnötiges Risiko ohne Nutzen.

    # -- gerätespezifisch ----------------------------------------------------

    def read_status(self) -> DeviceStatus:
        """Status lesen. Die Basis kann nur die Gerätekonfiguration."""
        return DeviceStatus(payload=self.userdata())

    def firmware(self) -> str:
        """Softwarestand des Geräts laut ``/userdata``."""
        payload = self.userdata()
        return str(pick(payload, "version", "sw_version", "firmware", default="") or "")


class ClassicVarioService(EheimService):
    """classicVARIO+e — Status lesen und Pumpenmodi setzen."""

    kind = KIND_CLASSICVARIO
    label = "Eheim classicVARIO+e"

    def read_status(self) -> ClassicVarioStatus:
        payload = self.client.get("/classicvario")
        return ClassicVarioStatus(
            payload=payload,
            is_on=parse_api_bool(pick(payload, "filterActive", "filter_active", "active")),
            rpm_percent=parse_int(pick(payload, "rel_speed", "relSpeed", "currentSpeed")),
            pump_mode=parse_int(pick(payload, "pumpMode", "filterMode", "pump_mode")),
            error_code=parse_int(pick(payload, "errorCode", "error_code")),
            service_due_in=parse_int(pick(payload, "serviceHour", "service_hour", "serviceHours")),
            manual_speed=parse_int(pick(payload, "rel_manual_motor_speed", "manualSpeed")),
            day_speed=parse_int(pick(payload, "rel_motor_speed_day", "daySpeed")),
            night_speed=parse_int(pick(payload, "rel_motor_speed_night", "nightSpeed")),
            day_start=parse_minutes(pick(payload, "start_time_day", "startTime_day")),
            night_start=parse_minutes(pick(payload, "start_time_night", "startTime_night")),
        )

    def set_active(self, active: bool) -> dict:
        """Filter ein- oder ausschalten (``POST /classic-vario-active``)."""
        return self.client.post("/classic-vario-active", {"active": to_api_bool(active)})

    def set_manual(self, speed_percent: int) -> dict:
        """Manueller Modus mit fester Drehzahl (``POST /classic-vario-manual``)."""
        return self.client.post(
            "/classic-vario-manual",
            {"rel_manual_motor_speed": _percent(speed_percent)},
        )

    def set_bio(self, day_speed: int, night_speed: int, day_start, night_start) -> dict:
        """Bio-Modus mit Tag-/Nachtphase (``POST /classic-vario-bio``).

        Die Uhrzeiten kommen als ``datetime.time`` oder ``"11:00"`` herein und
        gehen als Minuten seit Mitternacht hinaus.
        """
        return self.client.post(
            "/classic-vario-bio",
            {
                "rel_motor_speed_day": _percent(day_speed),
                "rel_motor_speed_night": _percent(night_speed),
                "start_time_day": to_minutes(day_start),
                "start_time_night": to_minutes(night_start),
            },
        )

    def set_pulse(self, high_speed: int, low_speed: int, high_seconds: int, low_seconds: int) -> dict:
        """Pulse-Modus (``POST /classic-vario-pulse``)."""
        return self.client.post(
            "/classic-vario-pulse",
            {
                "rel_motor_speed_high": _percent(high_speed),
                "rel_motor_speed_low": _percent(low_speed),
                "pulse_time_high": _seconds(high_seconds),
                "pulse_time_low": _seconds(low_seconds),
            },
        )


#: Registry Gerätetyp -> Serviceklasse.
SERVICES: dict[str, type[EheimService]] = {
    ClassicVarioService.kind: ClassicVarioService,
    EheimService.kind: EheimService,
}


def service_class_for(kind: str) -> type[EheimService]:
    """Serviceklasse zu einem ``Device.Kind``; Fallback ist die Basisklasse."""
    return SERVICES.get(str(kind), EheimService)


def service_for(device, **kwargs) -> EheimService:
    """Passenden Service zu einem :class:`services.models.Device` bauen."""
    return service_class_for(device.kind)(device, **kwargs)


def classify(userdata: dict) -> str:
    """Gerätetyp aus einer ``/userdata``-Antwort raten.

    Bewusst über den Namen und nicht über einen numerischen Typcode: die
    Codetabelle steht nicht in der öffentlichen Doku, und ein falsch geratener
    Code wäre schlimmer als die Voreinstellung "sonstiges", die beim Anlegen
    korrigiert werden kann.
    """
    haystack = " ".join(
        str(pick(userdata, key, default="") or "") for key in ("title", "name", "type", "model")
    ).lower()
    if "vario" in haystack:
        return KIND_CLASSICVARIO
    return KIND_EHEIM_OTHER


def _percent(value) -> int:
    percent = parse_int(value)
    if percent is None or not MIN_SPEED_PERCENT <= percent <= MAX_SPEED_PERCENT:
        raise ValueError(f"Drehzahl muss {MIN_SPEED_PERCENT}–{MAX_SPEED_PERCENT} % sein, nicht {value!r}")
    return percent


def _seconds(value) -> int:
    seconds = parse_int(value)
    if seconds is None or seconds <= 0:
        raise ValueError(f"Dauer muss eine positive Sekundenzahl sein, nicht {value!r}")
    return seconds
