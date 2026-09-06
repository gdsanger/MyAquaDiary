"""WSGI-Entrypoint des MCP-Servers.

Startet den MCP-Endpunkt als eigenen Dienst neben der Web-App::

    gunicorn config.wsgi_mcp:application \\
        --bind 0.0.0.0:8001 --worker-class gthread --threads 16 --timeout 0

Die Optionen sind kein Beiwerk: Ein SSE-Strom belegt seinen Worker, solange der
Client verbunden ist. Mit Threads bedient ein Prozess mehrere Ströme, und
``--timeout 0`` verhindert, dass gunicorn eine ruhige, aber völlig gesunde
Verbindung für aufgehängt hält.

Ein Prozess ist Absicht, kein Kompromiss: die offenen Sitzungen liegen im
Arbeitsspeicher (siehe :mod:`services.mcp.sessions`).
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup(set_prefix=False)

# Erst nach django.setup(): der Handler zieht die Modelle der Anwendung nach.
from services.mcp.wsgi import MCPHandler  # noqa: E402

application = MCPHandler()
