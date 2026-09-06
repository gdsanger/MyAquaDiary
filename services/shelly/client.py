"""HTTP-Client für die lokale API von Shelly-Geräten.

Anders als bei Eheim ist die Lage komfortabel: Shelly dokumentiert seine lokale
HTTP-API (https://shelly-api-docs.shelly.cloud/). Gesprochen wird ausschließlich
mit dem Gerät im LAN, **nicht** mit der Shelly Cloud — ohne Cloud-Konto, ohne
Ratelimit und ohne Abhängigkeit von einem Fremddienst.

Zwei Generationen, ein Client:

* **Gen1** (Plug S bis ca. 2022): ``GET /status``, ``GET /relay/0?turn=on``.
  Anmeldung per Basic Auth, sofern am Gerät ein Login gesetzt ist.
* **Gen2+** (Plus Plug S): RPC unter ``GET /rpc/<Methode>``. Anmeldung per
  Digest Auth mit dem festen Benutzer ``admin``.

Der Unterschied endet in :mod:`services.shelly.devices`; wer diesen Client
benutzt, gibt nur Pfad und Parameter an. Erkannt wird die Generation über
``/shelly``, das beide Generationen ohne Anmeldung beantworten.

Schreibende Endpunkte jenseits des Schaltens gibt es hier nicht:
Firmware-Update, Reboot und Werksreset sind gesperrt (:data:`BLOCKED_PATHS`).
Ein Aquarientagebuch hat kein Geschäft damit, und der Schaden wäre größer als
der Nutzen.
"""

import logging

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .exceptions import (
    ShellyAuthError,
    ShellyNotConfigured,
    ShellyResponseError,
    ShellyUnreachable,
)

logger = logging.getLogger(__name__)

#: Kurzes Default-Timeout: ein stummes Gerät darf keine Seite aufhalten.
DEFAULT_TIMEOUT = 5

#: Gen2 kennt genau einen Benutzernamen, der Wert ist nicht wählbar.
GEN2_USERNAME = "admin"

#: Generationen, die diese Anwendung unterstützt.
GEN1 = 1
GEN2 = 2

#: Endpunkte, die diese Anwendung bewusst nicht anbietet — Firmware-Update,
#: Neustart und Werksreset. Gesperrt wird im Client, damit auch ein frei
#: übergebener Pfad nicht daran vorbeikommt.
BLOCKED_PATHS = frozenset(
    {
        "/ota",
        "/reset",
        "/reboot",
        "/rpc/shelly.update",
        "/rpc/shelly.factoryreset",
        "/rpc/shelly.reboot",
        "/rpc/shelly.resetwificonfig",
    }
)

#: Fehlerantworten werden gekürzt weitergereicht.
MAX_ERROR_LENGTH = 300
#: Erst ab dieser Länge wird ein Passwort aus Meldungen gefiltert; kürzere
#: Werte würden als Teilstring harmlose Wörter zerstückeln.
MIN_REDACTION_LENGTH = 4


def default_timeout() -> int:
    return getattr(settings, "SHELLY_TIMEOUT", DEFAULT_TIMEOUT)


class ShellyClient:
    """Dünner Request-Wrapper um eine Shelly-Steckdose im LAN.

    :param host: IP oder Hostname des Geräts.
    :param username: nur für Gen1 relevant; Gen2 meldet sich immer als
        ``admin`` an.
    :param password: leer, solange am Gerät kein Login gesetzt ist — der
        Auslieferungszustand.
    :param generation: bestimmt das Anmeldeverfahren (Gen1 Basic, Gen2 Digest).
        ``None`` heißt "noch nicht erkannt"; ``/shelly`` beantwortet beide
        Generationen ohnehin ohne Anmeldung.
    :param session: Objekt mit ``request()`` — in Tests ein Fake, sonst
        ``requests``.
    """

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        *,
        generation: int | None = None,
        timeout: int | None = None,
        scheme: str = "http",
        session=None,
    ):
        self.host = (host or "").strip().rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.generation = generation
        self.timeout = timeout if timeout is not None else default_timeout()
        self.scheme = scheme
        self.session = session or requests

    @classmethod
    def for_device(cls, device, **kwargs) -> "ShellyClient":
        """Client aus einem :class:`services.models.Device`."""
        kwargs.setdefault("generation", device.generation)
        return cls(device.host, device.api_user, device.api_password, **kwargs)

    def get(self, path: str, params: dict | None = None) -> dict:
        """``GET`` auf einen lokalen Endpunkt. Beide Generationen kommen mit
        GET aus — auch das Schalten, weshalb es hier kein ``post()`` gibt."""
        path = "/" + str(path or "").strip().lstrip("/")
        if path.split("?")[0].lower() in BLOCKED_PATHS:
            raise ShellyResponseError(
                f"Der Endpunkt {path} wird von MyAquaDiary nicht angeboten."
            )
        if not self.host:
            raise ShellyNotConfigured("Für das Gerät ist keine Adresse hinterlegt.")

        url = f"{self.scheme}://{self.host}{path}"
        logger.debug("Shelly-Request GET %s (%s)", path, self.host)
        try:
            response = self.session.request(
                "GET",
                url,
                params=params or {},
                auth=self.auth(),
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise ShellyUnreachable(
                f"Zeitüberschreitung nach {self.timeout} s ({self.host})"
            ) from exc
        except requests.RequestException as exc:
            raise ShellyUnreachable(
                f"Gerät {self.host} nicht erreichbar: {self._redact(exc)}"
            ) from exc

        return self._parse(response, path)

    def auth(self):
        """Anmeldeverfahren passend zur Generation.

        Ohne Passwort gar keins: im Auslieferungszustand ist die lokale API
        offen, und ein leerer Header wäre nur Ballast.
        """
        if not self.password:
            return None
        if self.generation and self.generation >= GEN2:
            return HTTPDigestAuth(self.username or GEN2_USERNAME, self.password)
        return HTTPBasicAuth(self.username or GEN2_USERNAME, self.password)

    def _parse(self, response, path) -> dict:
        status = getattr(response, "status_code", 0)
        if status in (401, 403):
            raise ShellyAuthError(
                "Zugangsdaten wurden abgelehnt — Benutzer und Passwort des Geräts prüfen."
            )
        if status >= 400:
            raise ShellyResponseError(f"{path} antwortete mit {status}: {self._describe(response)}")

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            # Schaltbefehle quittieren teils mit leerem Body.
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise ShellyResponseError(f"{path} lieferte kein gültiges JSON") from exc
        if isinstance(payload, list):
            return {"items": payload}
        if not isinstance(payload, dict):
            raise ShellyResponseError(
                f"{path} lieferte kein Objekt, sondern {type(payload).__name__}"
            )
        return payload

    def _describe(self, response) -> str:
        return self._redact(getattr(response, "text", "") or "")[:MAX_ERROR_LENGTH]

    def _redact(self, message) -> str:
        """Stellt sicher, dass das Gerätepasswort nirgends auftaucht."""
        text = str(message)
        if self.password and len(self.password) >= MIN_REDACTION_LENGTH:
            text = text.replace(self.password, "***")
        return text
