"""Bindeglied zwischen den Gerätemodellen und der Eheim-Anbindung.

Hier — und nur hier — treffen Django-Modelle und HTTP-Schicht aufeinander:
Status lesen und als :class:`~services.models.DeviceReading` ablegen, Befehle
ausführen und protokollieren, Warnungen für das Dashboard zusammenstellen.

Aufrufer bekommen entweder ein Ergebnisobjekt oder ``None`` — ein nicht
erreichbares Gerät darf weder eine Seite noch einen Cron-Lauf aufhalten.
"""

import logging
from dataclasses import dataclass

from django.db.models import OuterRef, Subquery
from django.utils import timezone

from .eheim import (
    ClassicVarioService,
    DeviceStatus,
    EheimError,
    EheimFirmwareTooOld,
    FIRMWARE_HINT,
    error_text,
    is_supported_firmware,
    service_for,
)
from .eheim.convert import format_minutes, to_minutes
from .models import Device, DeviceEvent, DeviceReading

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    """Ergebnis einer schreibenden Aktion. Truthy genau dann, wenn ausgeführt."""

    ok: bool
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class ProbeResult:
    """Ergebnis einer Statusabfrage. Truthy genau dann, wenn gelesen wurde."""

    status: DeviceStatus | None = None
    reading: DeviceReading | None = None
    error: str = ""

    def __bool__(self) -> bool:
        return self.status is not None


# --------------------------------------------------------------------------
# Lesen
# --------------------------------------------------------------------------


def read_status(device: Device) -> DeviceStatus:
    """Status eines Geräts lesen. Wirft :class:`EheimError`."""
    return service_for(device).read_status()


def store_reading(device: Device, status: DeviceStatus) -> DeviceReading:
    """Status als Messwert ablegen und ``last_seen`` fortschreiben."""
    reading = DeviceReading.objects.create(
        device=device,
        read_at=timezone.now(),
        payload=status.payload or {},
        rpm_percent=_non_negative(status.rpm_percent),
        pump_mode=_non_negative(status.pump_mode),
        error_code=_non_negative(status.error_code),
        service_due_in=_non_negative(status.service_due_in),
        is_on=status.is_on,
    )
    device.last_seen = reading.read_at
    device.save(update_fields=["last_seen"])
    return reading


def probe(device: Device) -> ProbeResult:
    """Status lesen und speichern — ohne Ausnahme nach außen.

    Die Variante für Views und den Cron-Lauf: ein stummes Gerät führt zu einer
    Logzeile und einem Fehlertext, nicht zu einer Fehlerseite oder einem
    abgebrochenen Command.
    """
    if not device.is_active:
        return ProbeResult(error="Das Gerät ist deaktiviert.")
    try:
        status = read_status(device)
    except EheimError as exc:
        logger.info("Gerät %s nicht abfragbar: %s", device, exc)
        return ProbeResult(error=str(exc))
    except Exception:  # pragma: no cover - Notnagel, darf nichts blockieren
        logger.exception("Unerwarteter Fehler beim Abfragen von Gerät %s", device)
        return ProbeResult(error="Unerwarteter Fehler beim Abfragen des Geräts.")
    return ProbeResult(status=status, reading=store_reading(device, status))


def check_firmware(device: Device) -> str:
    """Softwarestand lesen, am Gerät speichern und prüfen.

    :raises EheimFirmwareTooOld: wenn das Gerät älter als 2.0.1 ist — dann gibt
        es die REST-API schlicht nicht.
    """
    firmware = service_for(device).firmware()
    if firmware and firmware != device.firmware:
        device.firmware = firmware[:40]
        device.save(update_fields=["firmware"])
    if firmware and not is_supported_firmware(firmware):
        raise EheimFirmwareTooOld(f"Gerätesoftware {firmware}: {FIRMWARE_HINT}")
    return firmware


# --------------------------------------------------------------------------
# Schreiben — welche Aktionen es gibt, steht in services.forms.CONTROL_FORMS.
# Ein Firmware-Update ist keine davon; der Client sperrt ``/doupdate`` zudem.
# --------------------------------------------------------------------------


def execute(device: Device, action: str, params: dict | None = None, *, user=None) -> CommandResult:
    """Führt eine schreibende Aktion aus und protokolliert sie.

    Protokolliert wird in jedem Fall — auch der fehlgeschlagene Versuch, sonst
    fehlt im Nachhinein genau die Zeile, die man sucht.
    """
    params = params or {}
    if not device.is_active:
        return CommandResult(False, "Das Gerät ist deaktiviert.")

    title = _action_label(action)
    try:
        title = describe(action, params)
        _apply(device, action, params)
    except (EheimError, ValueError, KeyError) as exc:
        message = str(exc)
        record_event(
            device,
            action=action,
            title=f"{_action_label(action)} fehlgeschlagen",
            description=message,
            user=user,
            succeeded=False,
        )
        logger.info("Befehl %s an Gerät %s fehlgeschlagen: %s", action, device, message)
        return CommandResult(False, message)

    record_event(device, action=action, title=title, user=user)
    device.last_seen = timezone.now()
    device.save(update_fields=["last_seen"])
    return CommandResult(True, title)


def describe(action: str, params: dict | None = None) -> str:
    """Klartext eines Befehls — für den Bestätigungsdialog und das Protokoll.

    Beides aus derselben Funktion: was der Bestätigungsdialog ankündigt, steht
    hinterher wortgleich im Protokoll.
    """
    params = params or {}
    if action == "on":
        return "Filter eingeschaltet"
    if action == "off":
        return "Filter ausgeschaltet"
    if action == "manual":
        return f"Manueller Modus, {params.get('speed_percent')} %"
    if action == "bio":
        return (
            f"Bio-Modus: Tag ab {_time_label(params['day_start'])} mit {params['day_speed']} %, "
            f"Nacht ab {_time_label(params['night_start'])} mit {params['night_speed']} %"
        )
    if action == "pulse":
        return (
            f"Pulse-Modus: {params.get('high_speed')} % für {params.get('high_seconds')} s, "
            f"{params.get('low_speed')} % für {params.get('low_seconds')} s"
        )
    raise ValueError(f"Unbekannte Aktion: {action}")


def _apply(device: Device, action: str, params: dict) -> None:
    """Schickt den Befehl ans Gerät."""
    service = service_for(device)
    if not isinstance(service, ClassicVarioService):
        raise ValueError("Für diesen Gerätetyp gibt es keine Steuerung.")

    if action in ("on", "off"):
        service.set_active(action == "on")
    elif action == "manual":
        service.set_manual(params["speed_percent"])
    elif action == "bio":
        service.set_bio(
            params["day_speed"], params["night_speed"], params["day_start"], params["night_start"]
        )
    elif action == "pulse":
        service.set_pulse(
            params["high_speed"], params["low_speed"], params["high_seconds"], params["low_seconds"]
        )
    else:
        raise ValueError(f"Unbekannte Aktion: {action}")


ACTION_LABELS = {
    "on": "Einschalten",
    "off": "Ausschalten",
    "manual": "Manueller Modus",
    "bio": "Bio-Modus",
    "pulse": "Pulse-Modus",
    "changeauth": "Zugangsdaten ändern",
}


def _action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)


def _time_label(value) -> str:
    """Uhrzeit für das Protokoll — der Umweg über Minuten hält die
    Schreibweise identisch mit dem, was ans Gerät geht."""
    return format_minutes(to_minutes(value))


def change_password(device: Device, password: str, *, user=None) -> CommandResult:
    """Gerätepasswort über ``/changeauth`` ändern und lokal übernehmen.

    Das Werkspasswort im LAN stehen zu lassen ist kein guter Zustand; deshalb
    gehört das Ändern zur Einrichtung dazu. Das Passwort selbst taucht weder im
    Protokoll noch im Log auf.
    """
    try:
        service_for(device).change_auth(password, device.api_user)
    except (EheimError, ValueError) as exc:
        message = str(exc)
        record_event(
            device,
            action="changeauth",
            title="Zugangsdaten ändern fehlgeschlagen",
            description=message,
            user=user,
            succeeded=False,
        )
        return CommandResult(False, message)

    device.set_credentials(device.api_user, password)
    device.save(update_fields=["credentials"])
    record_event(device, action="changeauth", title="Zugangsdaten des Geräts geändert", user=user)
    return CommandResult(True, "Zugangsdaten des Geräts geändert")


# --------------------------------------------------------------------------
# Protokoll
# --------------------------------------------------------------------------


def record_event(device: Device, *, action: str, title: str, description: str = "", user=None,
                 succeeded: bool = True) -> DeviceEvent:
    """Schreibt das Geräte-Ereignis.

    Einzige Stelle, an der das Protokoll entsteht: sobald das Becken-Modell im
    Epic liegt, kommt hier zusätzlich ein ``tanks.Event`` der Kategorie
    *Technik* dazu, ohne dass ein Aufrufer sich ändert.
    """
    return DeviceEvent.objects.create(
        device=device,
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action[:40],
        title=title[:200],
        description=description,
        succeeded=succeeded,
    )


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceWarning:
    """Fehlermeldung eines Geräts für die Dashboard-Anzeige."""

    device: Device
    error_code: int
    error_text: str
    read_at: object

    @property
    def message(self) -> str:
        return f"{self.device.name}: {self.error_text}"


def warnings_for(user) -> list[DeviceWarning]:
    """Geräte des Benutzers, deren letzte Messung einen Fehlercode meldet.

    Fehlercode 1 (Rotor blockiert) und 2 (Luft im Filter) sind Zustände, die
    man sofort sehen will — deshalb eine Abfrage über den jeweils letzten
    Messwert statt über alle.
    """
    latest = DeviceReading.objects.filter(device=OuterRef("pk")).order_by("-read_at")
    devices = (
        Device.objects.filter(owner=user, is_active=True)
        .annotate(
            latest_error=Subquery(latest.values("error_code")[:1]),
            latest_read_at=Subquery(latest.values("read_at")[:1]),
        )
        .filter(latest_error__gt=0)
    )
    return [
        DeviceWarning(
            device=device,
            error_code=device.latest_error,
            error_text=error_text(device.latest_error),
            read_at=device.latest_read_at,
        )
        for device in devices
    ]


def _non_negative(value):
    """Negative oder unsinnige Werte werden nicht gespeichert — die Modellfelder
    sind ``PositiveSmallInteger``, und ein Ausreißer ist kein Grund für einen
    Datenbankfehler mitten im Cron-Lauf."""
    if value is None or value < 0:
        return None
    return value
