"""Anbindung von EHEIM-Digital-Geräten über die offizielle REST-API.

Doku: https://api.eheimdigital.com/docs/eheim_digital_api/eheim-digital-api

Diese Schicht ist reine HTTP-Kommunikation ohne Django-Modelle; das Speichern
von Messwerten und das Protokoll liegen in :mod:`services.devices`.

Öffentliche Schnittstelle::

    from services.eheim import EheimClient, ClassicVarioService, service_for

    service = service_for(device)          # passend zum Device.Kind
    status = service.read_status()         # ausgewertet + Rohantwort
    service.set_manual(70)                 # Schreiben (Modus 16)
"""

from .client import (
    BLOCKED_PATHS,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    FIRMWARE_HINT,
    MIN_FIRMWARE,
    EheimClient,
    is_supported_firmware,
    normalize_mac,
    parse_version,
)
from .convert import (
    TemperatureUnit,
    convert_temperature,
    format_minutes,
    from_minutes,
    from_tenths,
    parse_api_bool,
    to_api_bool,
    to_minutes,
    to_tenths,
)
from .devices import (
    ERROR_AIR_IN_FILTER,
    ERROR_LABELS,
    ERROR_NONE,
    ERROR_ROTOR_BLOCKED,
    KIND_CLASSICVARIO,
    KIND_EHEIM_OTHER,
    PUMP_MODE_BIO,
    PUMP_MODE_LABELS,
    PUMP_MODE_MANUAL,
    PUMP_MODE_PULSE,
    ClassicVarioService,
    ClassicVarioStatus,
    DeviceStatus,
    EheimService,
    MeshDevice,
    service_class_for,
    service_for,
)
from .exceptions import (
    EheimAuthError,
    EheimEndpointBlocked,
    EheimError,
    EheimFirmwareTooOld,
    EheimNotConfigured,
    EheimResponseError,
    EheimUnreachable,
)

__all__ = [
    "BLOCKED_PATHS",
    "ClassicVarioService",
    "ClassicVarioStatus",
    "DEFAULT_PASSWORD",
    "DEFAULT_USERNAME",
    "DeviceStatus",
    "ERROR_AIR_IN_FILTER",
    "ERROR_LABELS",
    "ERROR_NONE",
    "ERROR_ROTOR_BLOCKED",
    "EheimAuthError",
    "EheimClient",
    "EheimEndpointBlocked",
    "EheimError",
    "EheimFirmwareTooOld",
    "EheimNotConfigured",
    "EheimResponseError",
    "EheimService",
    "EheimUnreachable",
    "FIRMWARE_HINT",
    "KIND_CLASSICVARIO",
    "KIND_EHEIM_OTHER",
    "MIN_FIRMWARE",
    "MeshDevice",
    "PUMP_MODE_BIO",
    "PUMP_MODE_LABELS",
    "PUMP_MODE_MANUAL",
    "PUMP_MODE_PULSE",
    "TemperatureUnit",
    "convert_temperature",
    "format_minutes",
    "from_minutes",
    "from_tenths",
    "is_supported_firmware",
    "normalize_mac",
    "parse_api_bool",
    "parse_version",
    "service_class_for",
    "service_for",
    "to_api_bool",
    "to_minutes",
    "to_tenths",
]
