"""Suchadressen fremder Wissensquellen.

Ein automatischer Datenabruf aus dem DRTA-Archiv oder von Flowgrow ist **nicht**
vorgesehen: es gibt keine öffentliche Schnittstelle, die Steckbriefe sind
redaktionelle Inhalte Dritter, und ein Scraper bräche bei jeder Layoutänderung —
auffallen würde das erst beim Nutzer.

Stattdessen baut diese Stelle aus dem wissenschaftlichen Namen eine
Suchadresse. Wer sie öffnet, sieht den fremden Steckbrief, übernimmt daraus, was
er braucht, und speichert den Link als :class:`~catalog.models.SpeciesLink`.

Die Muster stehen in ``settings.CATALOG_SEARCH_SOURCES`` und nicht hier: ändert
eine Quelle ihre Suchadresse, ist das eine Einstellung und keine Codeänderung.
"""

from urllib.parse import quote_plus

from django.conf import settings

#: Platzhalter, den ein Suchmuster für den Suchbegriff trägt.
PLACEHOLDER = "{query}"


def sources_for(kind: str) -> list[dict]:
    """Die hinterlegten Quellen einer Katalogart (``plant`` / ``animal``)."""
    configured = getattr(settings, "CATALOG_SEARCH_SOURCES", None) or {}
    return list(configured.get(kind, []))


def search_links(kind: str, species) -> list[dict]:
    """Suchadressen zu einer Art: ``[{"label": …, "url": …}, …]``.

    Gesucht wird mit dem wissenschaftlichen Namen und nicht mit dem
    Anzeigenamen: eine Fachdatenbank führt *Mikrogeophagus ramirezi*, nicht
    „Schmetterlingsbuntbarsch 'Electric Blue'“ — und nach der Zuchtform zu
    suchen liefert dort nichts.

    Ohne wissenschaftlichen Namen gibt es keine Suche; ein Muster ohne
    Platzhalter wird übersprungen, statt eine Adresse ohne Suchbegriff zu
    öffnen.
    """
    term = (getattr(species, "scientific_name", "") or "").strip()
    if not term:
        return []
    links = []
    for source in sources_for(kind):
        pattern = source.get("url", "")
        if PLACEHOLDER not in pattern:
            continue
        # ``replace`` und nicht ``format``: in einer Suchadresse stehen durchaus
        # geschweifte Klammern, und die wären für ``format`` ein Feldname.
        links.append(
            {
                "label": source.get("label") or source.get("key", "Quelle"),
                "url": pattern.replace(PLACEHOLDER, quote_plus(term)),
            }
        )
    return links
