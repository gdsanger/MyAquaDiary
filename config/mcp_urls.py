"""URL-Konfiguration des MCP-Entrypoints.

Bewusst nur der MCP-Endpunkt: kein Admin, kein Login, keine Beckenverwaltung.
Was hier nicht steht, ist über diesen Port nicht erreichbar — auch dann nicht,
wenn der Server versehentlich offen im Netz steht.
"""

from django.urls import include, path

urlpatterns = [
    path("mcp/", include("services.mcp.urls")),
]
