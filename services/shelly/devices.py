"""Geräteklassen der Shelly-Anbindung.

Aufbau wie bei Eheim: :class:`ShellyService` kennt den Endpunkt, den **beide**
Generationen beantworten (``/shelly``), und je Generation gibt es eine
Ableitung mit Status und Schaltbefehl.

* :class:`Gen1Service` — Plug S bis ca. 2022: ``/status``, ``/relay/0?turn=…``
* :class:`Gen2Service` — Plus Plug S und neuer: ``/rpc/Switch.GetStatus``,
  ``/rpc/Switch.Set``

Welche Generation vorliegt, wird über ``/shelly`` erkannt und am Gerät
gespeichert; die Unterscheidung endet hier. Views, Templates und Auswertung
sehen nur noch :class:`ShellyStatus` mit Wattstunden, Watt und Grad Celsius.

Diese Schicht kennt bewusst keine Django-Modelle; sie spricht nur HTTP und
liefert Dataclasses. Das Speichern liegt in :mod:`services.devices`.
"""

import logging
from dataclasses import dataclass, field

from .client import GEN1, GEN2, GEN2_USERNAME, ShellyClient
from .convert import (
    parse_bool,
    parse_decimal,
    parse_int,
    watt_hours_to_kilowatt_hours,
    watt_minutes_to_watt_hours,
)
from .exceptions import ShellyResponseError, ShellyUnknownGeneration

logger = logging.getLogger(__name__)

#: Geräteart. Der Wert ist identisch mit ``services.models.Device.Kind`` — das
#: Modell übernimmt ihn von hier, damit es nur eine Quelle gibt.
KIND_SHELLY_PLUG = "shelly_plug"

#: Kanal der Steckdose. Ein Plug S hat genau einen; mehrkanalige Geräte wären
#: eine weitere Ableitung und keine Sonderbehandlung an dieser Stelle.
CHANNEL = 0

GENERATION_LABELS = {GEN1: "Gen1", GEN2: "Gen2+"}


def generation_label(generation) -> str:
    """Generation im Klartext — Unbekanntes bleibt sichtbar."""
    if not generation:
        return ""
    return GENERATION_LABELS.get(generation, f"Gen{generation}")


@dataclass(frozen=True)
class ShellyInfo:
    """Antwort von ``/shelly`` — die Visitenkarte des Geräts."""

    payload: dict
    generation: int
    model: str = ""
    firmware: str = ""
    mac: str = ""
    name: str = ""
    auth_required: bool = False

    @property
    def label(self) -> str:
        return self.name or self.model or self.mac

    @property
    def generation_label(self) -> str:
        return generation_label(self.generation)


@dataclass(frozen=True)
class ShellyStatus:
    """Ausgewerteter Zustand einer Steckdose samt unveränderter Rohantwort.

    Die Rohantwort wandert unverändert in ``DeviceReading.payload``: ändert
    Shelly die Antwort oder belegt ein Feld anders, lässt sich die Historie
    nachträglich auswerten, ohne neu messen zu müssen.

    Einheiten sind hier bereits vereinheitlicht — Watt, Wattstunden, Grad
    Celsius. Die Wattminuten von Gen1 kommen oberhalb dieser Schicht nicht mehr
    vor.
    """

    payload: dict
    generation: int = 0
    is_on: bool | None = None
    power_w: object = None
    energy_total_wh: object = None
    temperature_c: object = None
    voltage_v: object = None
    #: Klartexthinweise des Geräts (Überlast, Übertemperatur).
    alerts: tuple = ()
    #: Messwerte für spätere ``Measurement``-Sätze — Name -> Dezimalwert.
    measurements: dict = field(default_factory=dict)

    @property
    def energy_total_kwh(self):
        return watt_hours_to_kilowatt_hours(self.energy_total_wh)

    @property
    def state_label(self) -> str:
        if self.is_on is None:
            return ""
        return "an" if self.is_on else "aus"

    @property
    def has_alert(self) -> bool:
        return bool(self.alerts)


def pick(payload: dict, *names, default=None):
    """Erster vorhandener Wert aus mehreren Feldnamen.

    Die Felder heißen je nach Generation und Softwarestand unterschiedlich
    (``ison`` vs. ``output``); die Kandidaten stehen deshalb hier statt
    verstreut im Auswertungscode.
    """
    for name in names:
        if isinstance(payload, dict) and name in payload and payload[name] is not None:
            return payload[name]
    return default


def _entry(payload: dict, key: str, index: int = CHANNEL) -> dict:
    """Listeneintrag aus einer Gen1-Antwort (``relays``, ``meters``)."""
    entries = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(entries, list) and len(entries) > index:
        entry = entries[index]
        return entry if isinstance(entry, dict) else {}
    return {}


def detect_generation(payload: dict) -> int:
    """Generation aus einer ``/shelly``-Antwort lesen.

    Gen2 und neuer schreiben die Generation als ``gen`` hinein. Gen1 kennt das
    Feld nicht, meldet aber ``type`` und ``fw`` — das ist das Erkennungsmerkmal.
    """
    generation = parse_int(pick(payload, "gen"))
    if generation:
        return generation
    if pick(payload, "type") or pick(payload, "fw"):
        return GEN1
    raise ShellyUnknownGeneration(
        "Unter dieser Adresse hat kein Shelly-Gerät geantwortet (/shelly ohne Kennung)."
    )


class ShellyService:
    """Basis: der Endpunkt, den beide Generationen beantworten.

    :param device: ein :class:`services.models.Device` (optional, wenn ein
        fertiger Client übergeben wird).
    """

    #: Generation, für die diese Klasse zuständig ist.
    generation = 0
    label = "Shelly"

    def __init__(self, device=None, *, client=None, **client_kwargs):
        if client is None:
            if device is None:
                raise ValueError("ShellyService braucht ein Device oder einen Client")
            client = ShellyClient.for_device(device, **client_kwargs)
        self.device = device
        self.client = client
        if self.generation and not client.generation:
            # Erst mit bekannter Generation kann der Client das richtige
            # Anmeldeverfahren wählen (Gen1 Basic, Gen2 Digest).
            client.generation = self.generation

    # -- gemeinsamer Endpunkt ------------------------------------------------

    def identify(self) -> ShellyInfo:
        """``GET /shelly`` — Generation, Modell und Softwarestand.

        Beide Generationen beantworten den Endpunkt und beide ohne Anmeldung;
        deshalb ist er der Einstieg beim Anbinden eines Geräts.
        """
        payload = self.client.get("/shelly")
        generation = detect_generation(payload)
        return ShellyInfo(
            payload=payload,
            generation=generation,
            model=str(pick(payload, "model", "type", "app", default="") or ""),
            firmware=str(pick(payload, "ver", "fw", "fw_id", default="") or ""),
            mac=str(pick(payload, "mac", default="") or ""),
            name=str(pick(payload, "name", default="") or ""),
            auth_required=bool(parse_bool(pick(payload, "auth_en", "auth", default=False))),
        )

    # -- generationsabhängig -------------------------------------------------

    def read_status(self) -> ShellyStatus:
        raise ShellyUnknownGeneration(
            "Die Generation des Geräts ist nicht erkannt — bitte das Gerät neu anbinden."
        )

    def set_output(self, on: bool) -> ShellyStatus:
        raise ShellyUnknownGeneration(
            "Die Generation des Geräts ist nicht erkannt — bitte das Gerät neu anbinden."
        )


class Gen1Service(ShellyService):
    """Shelly Plug S (Gen1): ``/status`` und ``/relay/0``."""

    generation = GEN1
    label = "Shelly Plug S (Gen1)"

    def read_status(self) -> ShellyStatus:
        payload = self.client.get("/status")
        relay = _entry(payload, "relays")
        meter = _entry(payload, "meters")
        temperature = parse_decimal(
            pick(payload.get("tmp") or {}, "tC", default=pick(payload, "temperature")),
            places="0.1",
        )
        return ShellyStatus(
            payload=payload,
            generation=GEN1,
            is_on=parse_bool(pick(relay, "ison", "is_on")),
            power_w=parse_decimal(pick(meter, "power")),
            # Gen1 zählt in Wattminuten — die einzige echte Eigenheit der API.
            energy_total_wh=watt_minutes_to_watt_hours(pick(meter, "total")),
            temperature_c=temperature,
            alerts=_alerts(
                overpower=parse_bool(pick(relay, "overpower", default=pick(payload, "overpower"))),
                overtemperature=parse_bool(pick(payload, "overtemperature")),
            ),
        )

    def set_output(self, on: bool) -> ShellyStatus:
        """``GET /relay/0?turn=on|off``."""
        payload = self.client.get(f"/relay/{CHANNEL}", {"turn": "on" if on else "off"})
        return ShellyStatus(
            payload=payload,
            generation=GEN1,
            is_on=parse_bool(pick(payload, "ison", "is_on")),
        )


class Gen2Service(ShellyService):
    """Shelly Plus Plug S und neuer: RPC unter ``/rpc/``."""

    generation = GEN2
    label = "Shelly Plus Plug S (Gen2+)"

    def read_status(self) -> ShellyStatus:
        payload = self.client.get("/rpc/Switch.GetStatus", {"id": CHANNEL})
        _raise_on_rpc_error(payload)
        energy = payload.get("aenergy") if isinstance(payload.get("aenergy"), dict) else {}
        temperature = payload.get("temperature") if isinstance(payload.get("temperature"), dict) else {}
        return ShellyStatus(
            payload=payload,
            generation=GEN2,
            is_on=parse_bool(pick(payload, "output")),
            power_w=parse_decimal(pick(payload, "apower")),
            # Gen2 zählt bereits in Wattstunden.
            energy_total_wh=parse_decimal(pick(energy, "total")),
            temperature_c=parse_decimal(pick(temperature, "tC"), places="0.1"),
            voltage_v=parse_decimal(pick(payload, "voltage"), places="0.1"),
            alerts=_alerts(errors=pick(payload, "errors")),
        )

    def set_output(self, on: bool) -> ShellyStatus:
        """``GET /rpc/Switch.Set?id=0&on=true|false``."""
        payload = self.client.get(
            "/rpc/Switch.Set", {"id": CHANNEL, "on": "true" if on else "false"}
        )
        _raise_on_rpc_error(payload)
        # Die Antwort meldet den *vorherigen* Zustand (``was_on``); der neue
        # ist der gesendete.
        return ShellyStatus(payload=payload, generation=GEN2, is_on=bool(on))


#: Registry Generation -> Serviceklasse.
SERVICES: dict[int, type[ShellyService]] = {
    Gen1Service.generation: Gen1Service,
    Gen2Service.generation: Gen2Service,
}


def service_class_for(generation) -> type[ShellyService]:
    """Serviceklasse zu einer Generation.

    Gen3 und Gen4 sprechen dasselbe RPC wie Gen2 und werden deshalb von
    :class:`Gen2Service` bedient; nur eine unbekannte Generation landet auf der
    Basisklasse, die dann sauber meldet, dass sie nichts lesen kann.
    """
    number = parse_int(generation) or 0
    if number >= GEN2:
        return Gen2Service
    return SERVICES.get(number, ShellyService)


def service_for(device, **kwargs) -> ShellyService:
    """Passenden Service zu einem :class:`services.models.Device` bauen.

    Ist die Generation noch nicht bekannt, wird sie über ``/shelly`` erkannt —
    genau ein zusätzlicher Request, und zwar nur beim ersten Kontakt. Danach
    steht sie am Gerät (siehe :func:`services.devices.store_reading`).
    """
    generation = parse_int(getattr(device, "generation", None))
    if not generation:
        generation = ShellyService(device, **kwargs).identify().generation
    return service_class_for(generation)(device, generation=generation, **kwargs)


def _alerts(*, overpower=None, overtemperature=None, errors=None) -> tuple:
    """Meldungen des Geräts im Klartext.

    Eine Steckdose kennt keine Fehlercodes wie ein Eheim-Filter, aber sie meldet
    Überlast und Übertemperatur — beides will man an einer Heizung oder einem
    Filter sofort sehen.
    """
    texts = []
    if overpower:
        texts.append("Überlast — die Steckdose hat abgeschaltet.")
    if overtemperature:
        texts.append("Übertemperatur — die Steckdose hat abgeschaltet.")
    for error in errors or ():
        texts.append(f"Gerätemeldung: {error}")
    return tuple(texts)


def _raise_on_rpc_error(payload: dict) -> None:
    """Gen2 quittiert Fehler mit ``200`` und einem ``error``-Objekt im Body."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or "unbekannter Fehler"
        raise ShellyResponseError(f"Die Steckdose meldet einen Fehler: {message}")


__all__ = [
    "CHANNEL",
    "GEN1",
    "GEN2",
    "GEN2_USERNAME",
    "GENERATION_LABELS",
    "Gen1Service",
    "Gen2Service",
    "KIND_SHELLY_PLUG",
    "ShellyInfo",
    "ShellyService",
    "ShellyStatus",
    "detect_generation",
    "generation_label",
    "service_class_for",
    "service_for",
]
