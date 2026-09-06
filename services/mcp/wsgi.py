"""WSGI-Handler des MCP-Servers.

Derselbe Django-Prozess, dieselben Modelle, dieselbe Service-Schicht — nur ein
anderer URL-Baum und eine kürzere Middleware-Kette. Die URL-Konfiguration wird
je Request über ``request.urlconf`` gesetzt statt global über
``settings.ROOT_URLCONF``: so bleibt die Einstellung der Web-App unangetastet,
und beide Entrypoints lassen sich im selben Testlauf prüfen.
"""

from contextlib import contextmanager

from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler

#: URL-Baum des MCP-Servers: nur der Endpunkt, kein Admin, keine Anwendung.
MCP_URLCONF = "config.mcp_urls"

#: Middleware, die dieser Prozess nicht lädt. Die Debug-Toolbar erwartet ihre
#: eigenen Adressen im URL-Baum — die es hier absichtlich nicht gibt — und in
#: einen Ereignisstrom hätte sie ohnehin nichts einzuhängen.
EXCLUDED_MIDDLEWARE = ("debug_toolbar",)


def usable_middleware(configured) -> list:
    """Die Middleware-Kette des MCP-Prozesses."""
    return [
        entry
        for entry in configured
        if not any(excluded in entry for excluded in EXCLUDED_MIDDLEWARE)
    ]


@contextmanager
def _middleware(chain):
    """Setzt die Kette für die Dauer des Ladens; danach steht wieder alles wie zuvor."""
    original = settings.MIDDLEWARE
    settings.MIDDLEWARE = chain
    try:
        yield
    finally:
        settings.MIDDLEWARE = original


class MCPHandler(WSGIHandler):
    """WSGI-Anwendung, die ausschließlich den MCP-Endpunkt bedient."""

    def __init__(self, *args, **kwargs):
        with _middleware(usable_middleware(settings.MIDDLEWARE)):
            super().__init__(*args, **kwargs)

    def get_response(self, request):
        request.urlconf = MCP_URLCONF
        return super().get_response(request)
