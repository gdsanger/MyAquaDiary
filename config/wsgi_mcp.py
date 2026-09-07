"""WSGI-Entrypoint des MCP-Servers.

Startet den MCP-Endpunkt als eigenen Dienst neben der Web-App::

    gunicorn config.wsgi_mcp:application \\
        --bind 0.0.0.0:8001 --workers 3 \\
        --access-logformat '%(h)s "%(r)s" %(s)s %(b)s %(M)sms'

Zwei Dinge sind daran Absicht:

**Mehrere Worker sind erlaubt.** Der Transport *Streamable HTTP* antwortet
direkt auf den POST; es gibt keinen Zustand im Prozess, den ein zweiter Worker
nicht kennen würde. Nur solange der alte SSE-Transport noch bedient wird
(``MCP_LEGACY_SSE``), hängen dessen offene Sitzungen im Arbeitsspeicher und
verlangen einen einzelnen Prozess.

**Das Zugriffsprotokoll lässt den Query-String weg.** Im Standardformat steckt
``%(q)s`` — und darin stünde der Token. Dasselbe gilt für den Proxy davor.

Der eigene Entrypoint bleibt, was er war: die Trennung der Oberflächen. Admin,
Login und Beckenverwaltung sind über diesen Port nicht erreichbar.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup(set_prefix=False)

# Erst nach django.setup(): der Handler zieht die Modelle der Anwendung nach.
from services.mcp.wsgi import MCPHandler  # noqa: E402

application = MCPHandler()
