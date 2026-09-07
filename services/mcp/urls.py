"""Adressen des MCP-Endpunkts.

Eingebunden werden sie ausschließlich vom eigenen Entrypoint
(``config/mcp_urls.py``), nicht von der Web-App: die beiden Oberflächen melden
sich völlig unterschiedlich an, und ein tokenauthentifizierter Endpunkt hat im
URL-Baum der Sitzungs-Anwendung nichts zu suchen.

``/mcp/`` ist der Endpunkt — einer, für alles. Die beiden darunter sind der
alte SSE-Transport und verschwinden, sobald keine Konfiguration mehr auf sie
zeigt (siehe :mod:`services.mcp.views`).
"""

from django.urls import path

from . import views

app_name = "mcp"

urlpatterns = [
    path("", views.endpoint, name="endpoint"),
    path("sse/", views.sse, name="sse"),
    path("messages/", views.messages, name="messages"),
]
