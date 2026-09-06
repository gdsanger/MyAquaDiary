"""Adressen des MCP-Endpunkts.

Eingebunden werden sie ausschließlich vom eigenen Entrypoint
(``config/mcp_urls.py``), nicht von der Web-App: die beiden Oberflächen melden
sich völlig unterschiedlich an, und ein tokenauthentifizierter Endpunkt hat im
URL-Baum der Sitzungs-Anwendung nichts zu suchen.
"""

from django.urls import path

from . import views

app_name = "mcp"

urlpatterns = [
    path("sse/", views.sse, name="sse"),
    path("messages/", views.messages, name="messages"),
]
