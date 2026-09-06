"""HTTP-Client für die offizielle Eheim-Digital-REST-API.

Dokumentation der API: https://api.eheimdigital.com/docs/eheim_digital_api/eheim-digital-api
(die Doku selbst steht unter GNU AGPLv3).

Drei Dinge macht dieser Client und sonst nichts:

* **Basic Auth** — werksseitig Benutzer ``api`` mit Passwort ``admin``. Das
  Passwort landet nie in einer Log- oder Fehlermeldung.
* **MAC-Adressierung** — jeder gerätebezogene Request trägt die MAC des
  Zielgeräts im Feld ``to``; das angesprochene Gerät leitet im Mesh weiter.
  Die allgemeinen Endpunkte (``/userdata``, ``/mesh-liste``) dürfen auch ohne
  ``to`` gestellt werden, dann antwortet das Gateway für sich selbst.
* **Fehlerbehandlung mit Timeout** — jeder Fehler kommt als
  :class:`~services.eheim.exceptions.EheimError` zurück, damit ein nicht
  erreichbares Gerät nie eine Seite blockiert.

``/doupdate`` ist gesperrt: ein Firmware-Update aus einem Aquarientagebuch
heraus anzustoßen ist Risiko ohne Nutzen. Der Endpunkt lässt sich auch nicht
über einen frei übergebenen Pfad ansprechen (siehe :data:`BLOCKED_PATHS`).
"""

import base64
import logging
import re

import requests
from django.conf import settings

from .exceptions import (
    EheimAuthError,
    EheimEndpointBlocked,
    EheimNotConfigured,
    EheimResponseError,
    EheimUnreachable,
)

logger = logging.getLogger(__name__)

#: Kurzes Default-Timeout: ein stummes Gerät darf keine Seite aufhalten.
DEFAULT_TIMEOUT = 5
DEFAULT_USERNAME = "api"
#: Werkspasswort. Wird nur zum Erkennen des unveränderten Zustands benutzt.
DEFAULT_PASSWORD = "admin"

#: Endpunkte, die diese Anwendung bewusst nicht anbietet.
BLOCKED_PATHS = frozenset({"/doupdate"})

#: Erste Gerätesoftware mit REST-API.
MIN_FIRMWARE = (2, 0, 1)
FIRMWARE_HINT = (
    "Die REST-API gibt es erst ab Gerätesoftware 2.0.1. Ältere Geräte lassen sich "
    "nicht anbinden — bitte zuerst in der EHEIM-Digital-App aktualisieren."
)

#: Fehlerantworten werden gekürzt weitergereicht.
MAX_ERROR_LENGTH = 300
#: Erst ab dieser Länge wird ein Passwort aus Meldungen gefiltert; kürzere
#: Werte würden als Teilstring harmlose Wörter zerstückeln.
MIN_REDACTION_LENGTH = 4

_MAC_CHARS = re.compile(r"[^0-9A-Fa-f]")


def normalize_mac(value: str) -> str:
    """Bringt eine MAC-Adresse auf die Form ``AA:BB:CC:DD:EE:FF``.

    Akzeptiert die üblichen Schreibweisen (Doppelpunkt, Bindestrich, Punkt,
    ohne Trenner). Was keine zwölf Hex-Zeichen sind, kommt unverändert zurück —
    die Feldvalidierung meldet das, nicht der Client.
    """
    digits = _MAC_CHARS.sub("", str(value or ""))
    if len(digits) != 12:
        return str(value or "").strip()
    return ":".join(digits[index : index + 2] for index in range(0, 12, 2)).upper()


def parse_version(value) -> tuple[int, ...]:
    """Versionsstring wie ``"2.0.1.4"`` als vergleichbares Tupel.

    Nicht lesbare Angaben ergeben ein leeres Tupel.
    """
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts)


def is_supported_firmware(value) -> bool:
    """True, wenn die Gerätesoftware die REST-API hat (>= 2.0.1)."""
    version = parse_version(value)
    if not version:
        return False
    return version >= MIN_FIRMWARE


def default_timeout() -> int:
    return getattr(settings, "EHEIM_TIMEOUT", DEFAULT_TIMEOUT)


class EheimClient:
    """Dünner Request-Wrapper um ein Gerät (oder ein Mesh-Gateway).

    :param host: IP oder Hostname des Geräts im LAN.
    :param mac: MAC des Zielgeräts, wird als ``to`` mitgeschickt.
    :param session: Objekt mit ``request()`` — in Tests ein Fake, sonst
        ``requests``.
    """

    def __init__(
        self,
        host: str,
        username: str = DEFAULT_USERNAME,
        password: str = "",
        *,
        mac: str = "",
        timeout: int | None = None,
        scheme: str = "http",
        session=None,
    ):
        self.host = (host or "").strip().rstrip("/")
        self.username = username or DEFAULT_USERNAME
        self.password = password or ""
        self.mac = normalize_mac(mac)
        self.timeout = timeout if timeout is not None else default_timeout()
        self.scheme = scheme
        self.session = session or requests

    @classmethod
    def for_device(cls, device, **kwargs) -> "EheimClient":
        """Client aus einem :class:`services.models.Device`."""
        return cls(
            device.host,
            device.api_user,
            device.api_password,
            mac=device.mac_address,
            **kwargs,
        )

    def get(self, path: str, *, mac: str | None = None, require_mac: bool = True) -> dict:
        return self._request("GET", path, mac=mac, require_mac=require_mac)

    def post(self, path: str, payload: dict | None = None, *, mac: str | None = None,
             require_mac: bool = True) -> dict:
        return self._request("POST", path, payload=payload, mac=mac, require_mac=require_mac)

    def _request(self, method, path, *, payload=None, mac=None, require_mac=True) -> dict:
        path = "/" + str(path or "").strip().lstrip("/")
        if path.split("?")[0].lower() in BLOCKED_PATHS:
            raise EheimEndpointBlocked(f"Der Endpunkt {path} wird von MyAquaDiary nicht angeboten.")
        if not self.host:
            raise EheimNotConfigured("Für das Gerät ist keine Adresse hinterlegt.")

        target = normalize_mac(mac) if mac is not None else self.mac
        if require_mac and not target:
            raise EheimNotConfigured("Für das Gerät ist keine MAC-Adresse hinterlegt.")

        body = {"to": target, **(payload or {})} if target else dict(payload or {})
        url = f"{self.scheme}://{self.host}{path}"
        logger.debug("Eheim-Request %s %s (to=%s)", method, path, target or "-")

        kwargs = {"params": body} if method == "GET" else {"json": body}
        try:
            response = self.session.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise EheimUnreachable(f"Zeitüberschreitung nach {self.timeout} s ({self.host})") from exc
        except requests.RequestException as exc:
            raise EheimUnreachable(f"Gerät {self.host} nicht erreichbar: {self._redact(exc)}") from exc

        return self._parse(response, path)

    def _parse(self, response, path) -> dict:
        status = getattr(response, "status_code", 0)
        if status in (401, 403):
            raise EheimAuthError(
                "Zugangsdaten wurden abgelehnt — Benutzer und Passwort des Geräts prüfen."
            )
        if status >= 400:
            raise EheimResponseError(f"{path} antwortete mit {status}: {self._describe(response)}")

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            # Schreibende Endpunkte quittieren teils mit leerem Body.
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise EheimResponseError(f"{path} lieferte kein gültiges JSON") from exc
        if isinstance(payload, list):
            return {"items": payload}
        if not isinstance(payload, dict):
            raise EheimResponseError(f"{path} lieferte kein Objekt, sondern {type(payload).__name__}")
        return payload

    def _headers(self) -> dict:
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        }

    def _describe(self, response) -> str:
        return self._redact((getattr(response, "text", "") or ""))[:MAX_ERROR_LENGTH]

    def _redact(self, message) -> str:
        """Stellt sicher, dass das Gerätepasswort nirgends auftaucht."""
        text = str(message)
        if self.password and len(self.password) >= MIN_REDACTION_LENGTH:
            text = text.replace(self.password, "***")
        return text
