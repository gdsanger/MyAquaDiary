"""Anbindung von Shelly-Steckdosen über die lokale HTTP-API.

Doku: https://shelly-api-docs.shelly.cloud/ — Gen1 und Gen2+ sind dokumentiert
und stabil, es braucht also weder Reverse Engineering noch die Shelly Cloud.
Gesprochen wird ausschließlich mit dem Gerät im LAN.

Diese Schicht ist reine HTTP-Kommunikation ohne Django-Modelle; das Speichern
von Messwerten und das Protokoll liegen in :mod:`services.devices`, die
Verbrauchsauswertung in :mod:`services.energy`.

Öffentliche Schnittstelle::

    from services.shelly import ShellyClient, ShellyService, service_for

    info = ShellyService(client=ShellyClient("192.168.1.60")).identify()
    service = service_for(device)          # passend zur Generation
    status = service.read_status()         # ausgewertet + Rohantwort
    service.set_output(True)               # Schalten
"""

from .client import (
    BLOCKED_PATHS,
    GEN1,
    GEN2,
    GEN2_USERNAME,
    ShellyClient,
    default_timeout,
)
from .convert import (
    parse_bool,
    parse_decimal,
    parse_int,
    watt_hours_to_kilowatt_hours,
    watt_minutes_to_watt_hours,
)
from .devices import (
    CHANNEL,
    GENERATION_LABELS,
    KIND_SHELLY_PLUG,
    Gen1Service,
    Gen2Service,
    ShellyInfo,
    ShellyService,
    ShellyStatus,
    detect_generation,
    generation_label,
    service_class_for,
    service_for,
)
from .exceptions import (
    ShellyAuthError,
    ShellyError,
    ShellyNotConfigured,
    ShellyResponseError,
    ShellyUnknownGeneration,
    ShellyUnreachable,
)

__all__ = [
    "BLOCKED_PATHS",
    "CHANNEL",
    "GEN1",
    "GEN2",
    "GEN2_USERNAME",
    "GENERATION_LABELS",
    "Gen1Service",
    "Gen2Service",
    "KIND_SHELLY_PLUG",
    "ShellyAuthError",
    "ShellyClient",
    "ShellyError",
    "ShellyInfo",
    "ShellyNotConfigured",
    "ShellyResponseError",
    "ShellyService",
    "ShellyStatus",
    "ShellyUnknownGeneration",
    "ShellyUnreachable",
    "default_timeout",
    "detect_generation",
    "generation_label",
    "parse_bool",
    "parse_decimal",
    "parse_int",
    "service_class_for",
    "service_for",
    "watt_hours_to_kilowatt_hours",
    "watt_minutes_to_watt_hours",
]
