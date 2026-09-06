"""MCP-Server: das Tagebuch als Werkzeugkasten für externe KI-Clients.

Ein eigener Endpunkt (SSE), über den Claude Desktop, Claude Code und andere
MCP-fähige Anwendungen die eigenen Becken lesen und beschreiben können. Der
Server teilt sich Modelle und Service-Schicht mit der Web-App — es gibt keinen
zweiten Datenzugriff und keine zweite Geschäftslogik, sondern nur eine weitere
Oberfläche auf dieselbe Anwendung.

Aufbau:

* :mod:`services.mcp.data` — der einzige Zugang zu den Modellen, immer
  eingegrenzt auf den Inhaber des Tokens.
* :mod:`services.mcp.read` / :mod:`services.mcp.write` — die Werkzeuge.
* :mod:`services.mcp.runner` — Rechte, Ratelimit, Protokoll um jeden Aufruf.
* :mod:`services.mcp.protocol` — JSON-RPC-Schicht des MCP.
* :mod:`services.mcp.views` — SSE-Strom und Nachrichtenendpunkt.

Bewusst **nicht** angeboten: Geräte (weder lesend noch schaltend), Löschen,
Katalogpflege, Benutzer- und Tokenverwaltung. Die Begründungen stehen bei den
jeweiligen Modulen.
"""

from .exceptions import DataModelUnavailable, MCPError, RateLimited, ToolError
from .protocol import PROTOCOL_VERSION, SERVER_NAME, handle_message
from .runner import Context, call_tool

# Import mit Nebenwirkung: die Werkzeuge tragen sich beim Import in die
# Registry ein. Ohne diese Zeile kennt der Server keine Tools.
from . import read as _read  # noqa: F401  isort:skip

__all__ = [
    "Context",
    "DataModelUnavailable",
    "MCPError",
    "PROTOCOL_VERSION",
    "RateLimited",
    "SERVER_NAME",
    "ToolError",
    "call_tool",
    "handle_message",
]
