"""Der MCP-Server als eigener Prozess — für die Entwicklung.

    python manage.py run_mcp_server 0.0.0.0:8001

Verhält sich wie ``runserver``, bedient aber ausschließlich den MCP-Endpunkt.
Im Betrieb übernimmt gunicorn diese Rolle (siehe ``config/wsgi_mcp.py``); für
die Entwicklung ist ein Befehl neben ``runserver`` das Naheliegendste.
"""

from django.core.management.commands.runserver import Command as RunserverCommand

from services.mcp.wsgi import MCPHandler


class Command(RunserverCommand):
    help = "Startet den MCP-Endpunkt (SSE) als eigenen Server."
    default_port = "8001"

    def get_handler(self, *args, **options):
        """Der MCP-Handler statt der Web-App.

        Ohne Statik-Auslieferung: der MCP-Endpunkt liefert JSON und einen
        Ereignisstrom, keine Dateien.
        """
        return MCPHandler()
