"""WSGI-Handler des MCP-Servers.

Derselbe Django-Prozess, dieselben Modelle, dieselbe Service-Schicht — nur eine
andere URL-Konfiguration. Gesetzt wird sie je Request über ``request.urlconf``
statt global über ``settings.ROOT_URLCONF``: so bleibt die Einstellung der
Web-App unangetastet, und beide Entrypoints lassen sich im selben Testlauf
prüfen.
"""

from django.core.handlers.wsgi import WSGIHandler

#: URL-Baum des MCP-Servers: nur der Endpunkt, kein Admin, keine Anwendung.
MCP_URLCONF = "config.mcp_urls"


class MCPHandler(WSGIHandler):
    """WSGI-Anwendung, die ausschließlich den MCP-Endpunkt bedient."""

    def get_response(self, request):
        request.urlconf = MCP_URLCONF
        return super().get_response(request)
