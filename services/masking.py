"""Wie ein MCP-Token aussieht — und wie er aus den Protokollen verschwindet.

Der Token darf im Query-String stehen; das ist der Preis dafür, dass eine
Adresse allein genügt und kein Client eine Brücke mit Node.js braucht. Bezahlt
wird er an anderer Stelle: der Query-String bleibt aus den Zugriffsprotokollen
(Gunicorn-Format, Proxy), und was trotzdem irgendwo auftaucht, kürzt
:func:`mask` auf die Erkennung.

Dieses Modul steht bewusst **neben** der Anwendung und nicht in
:mod:`services.mcp`: der Log-Filter wird von ``LOGGING`` geladen, also lange
bevor Django seine Modelle kennt. Ein Import, der irgendwo an ein Modell
gerät, ließe den Prozess beim Start scheitern. Deshalb wohnt hier auch die
Beschreibung des Token-Formats, aus der :mod:`services.models` sie bezieht —
eine Quelle, nicht zwei.
"""

import logging
import re

#: Erkennungszeichen am Anfang jedes Tokens. Macht einen versehentlich in einen
#: Chat kopierten Token als Zugangsdatum erkennbar — und für eine spätere Suche
#: nach geleakten Tokens greifbar.
MCP_TOKEN_PREFIX = "mad_"
#: So viele Zeichen des Zufallsanteils dürfen sichtbar bleiben: genug, um einen
#: Zugang wiederzuerkennen, zu wenig, um sich damit anzumelden.
MCP_TOKEN_HINT_CHARS = 6

#: Erkennt einen Token in beliebigem Text. Der Zufallsanteil ist
#: urlsafe-Base64, also Buchstaben, Ziffern, ``-`` und ``_``.
TOKEN_PATTERN = re.compile(rf"{re.escape(MCP_TOKEN_PREFIX)}[A-Za-z0-9_-]{{8,}}")


def mask(text):
    """Kürzt jeden Token im Text auf seine Erkennung.

    Übrig bleibt genau das, was ohnehin unverschlüsselt in der Datenbank steht
    (``MCPToken.hint``).
    """
    if not text:
        return text
    keep = len(MCP_TOKEN_PREFIX) + MCP_TOKEN_HINT_CHARS
    return TOKEN_PATTERN.sub(lambda found: found.group()[:keep] + "…", text)


class MaskTokens(logging.Filter):
    """Hält Tokens aus den Protokollen — auch aus fremden.

    Der eigene Code maskiert von Hand, aber nicht jede Zeile kommt von uns: bei
    einem 404 protokolliert ``django.request`` den vollen Pfad samt
    Query-String. Der Filter hängt deshalb am Handler und nicht an einzelnen
    Aufrufen.
    """

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                mask(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.args, str):
            record.args = mask(record.args)
        return True
