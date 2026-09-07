"""Kennzeichnung und Protokoll schreibender Zugriffe.

Die Spur von einem Datensatz zurück zum MCP-Aufruf führt über **das
Protokoll**: :func:`access_log` hält Zeitpunkt, Token, Werkzeug, Parameter und
den erzeugten Datensatz fest — auch bei abgewiesenen Aufrufen. Was der Client
geschickt hat, geht dabei durch :func:`services.masking.mask`. Damit lässt sich
die Frage beantworten, die man sich Wochen später stellt: „Wo kommt dieser Wert
her?“

**Am Datensatz selbst** steht die Herkunft derzeit nicht: keines der
Tagebuch-Modelle führt ein ``source``-Feld. :func:`mark_source` bleibt
trotzdem, weil sie am Modell prüft statt am Werkzeug — bekommt ein Modell
später eine Herkunft, tragen die schreibenden Werkzeuge sie ohne weitere
Änderung ein. Wer den Unterschied „aus dem Chatfenster“ / „von Hand“ in der
Oberfläche sehen will, braucht dieses Feld; das ist ein eigenes Item.
"""

from django.core.exceptions import FieldDoesNotExist

from services.masking import mask
from services.models import MCPAccessLog

#: Wert für ein künftiges ``source``-Feld — dieselbe Schreibweise, die die
#: Oberfläche für ``manual``, ``device`` und ``import`` verwenden würde.
SOURCE = "mcp"


def mark_source(instance):
    """Trägt die Herkunft ein, sofern das Modell ein ``source``-Feld hat.

    Geprüft wird am Modell statt am Feldnamen im Werkzeug: nicht jedes Modell
    des Tagebuchs führt eine Herkunft, und ein Werkzeug soll deswegen weder
    scheitern noch eine Sonderbehandlung kennen müssen.
    """
    try:
        instance._meta.get_field("source")
    except FieldDoesNotExist:
        return instance
    instance.source = SOURCE
    return instance


def access_log(context, *, tool, arguments, succeeded, object_ref="", error_message=""):
    """Schreibt einen Protokolleintrag zu einem schreibenden Aufruf.

    Parameter und Fehlertext kommen vom Client und werden maskiert abgelegt:
    über MCP laufen zwar keine Zugangsdaten, aber ein Token, den jemand
    versehentlich als Werkzeugparameter mitschickt, soll nicht in der Datenbank
    stehenbleiben.
    """
    return MCPAccessLog.objects.create(
        token=context.token,
        user=context.user,
        token_name=context.token.name,
        tool=tool,
        arguments=_masked(arguments) if isinstance(arguments, dict) else {},
        object_ref=object_ref,
        succeeded=succeeded,
        error_message=mask(error_message),
    )


def _masked(arguments: dict) -> dict:
    return {key: mask(value) if isinstance(value, str) else value
            for key, value in arguments.items()}
