"""URL-Konfiguration des MCP-Entrypoints.

Bewusst nur der MCP-Endpunkt: kein Admin, kein Login, keine Beckenverwaltung.
Was hier nicht steht, ist über diesen Port nicht erreichbar — auch dann nicht,
wenn der Server versehentlich offen im Netz steht.
"""

from django.urls import include, path, re_path

from services.mcp import views

urlpatterns = [
    # Mit und ohne Schrägstrich. Ohne diese Zeile griffe APPEND_SLASH: die
    # Umleitung ist ein 301, ein POST verlöre dabei seinen Rumpf, und eine
    # Client-Konfiguration ohne Schrägstrich wäre ein stiller Fehlschlag.
    re_path(r"^mcp/?$", views.endpoint),
    path("mcp/", include("services.mcp.urls")),
]
