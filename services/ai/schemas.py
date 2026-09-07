"""JSON-Schemata der KI-Antworten.

Die Steckbriefe fragen mehr ab, als der Katalog speichert: Herkunft, Familie
oder Verträglichkeit stehen am Vorschlag und sind dort zu lesen, im Katalog
haben sie kein Feld. Wo es eines gibt, heißt es hier möglichst gleich; die
verbleibenden Unterschiede in Namen und Auswahllisten übersetzt
:mod:`services.ai.catalog` beim Übernehmen an genau einer Stelle.

Alle Objekte setzen ``additionalProperties: false`` und listen jedes Feld unter
``required`` — beides verlangen Structured Outputs. Felder, die das Modell
nicht kennt, kommen als leerer String bzw. ``null`` zurück; das ist besser als
ein geratener Wert.
"""

#: Auswahllisten. Wo der Katalog dasselbe Feld führt, sind die Werte wortgleich
#: zu seinen TextChoices — sonst fiele die Angabe beim Übernehmen weg.
ANIMAL_GROUPS = ["fisch", "garnele", "krebs", "schnecke", "muschel"]
SOCIAL_BEHAVIORS = ["einzeln", "paar", "harem", "gruppe", "schwarm"]
ZONES = ["boden", "mitte", "oberflaeche"]
DIFFICULTIES = ["easy", "medium", "hard"]
DIETS = ["allesfresser", "fleisch", "pflanzen", "aufwuchs"]
GROWTH_FORMS = ["stem", "rosette", "epiphyte", "ground_cover", "floating", "moss"]
PLACEMENTS = ["foreground", "midground", "background", "floating", "epiphyte"]
GROWTH_RATES = ["slow", "medium", "fast"]
DEMANDS = ["low", "medium", "high"]


def _string(description, enum=None):
    field = {"type": "string", "description": description}
    if enum:
        field["enum"] = enum
    return field


def _number(description):
    return {"type": ["number", "null"], "description": description}


def _integer(description):
    return {"type": ["integer", "null"], "description": description}


def _boolean(description):
    return {"type": "boolean", "description": description}


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


#: Bestimmung aus einem Foto — mehrere Kandidaten, absteigend nach Konfidenz.
IDENTIFICATION_SCHEMA = _object(
    {
        "candidates": {
            "type": "array",
            "description": "Höchstens fünf Kandidaten, der wahrscheinlichste zuerst.",
            "items": _object(
                {
                    "scientific_name": _string("Wissenschaftlicher Name, z. B. Poecilia reticulata."),
                    "common_name": _string("Gebräuchlicher deutscher Name, sonst leer."),
                    "confidence": {
                        "type": "number",
                        "description": "Eigene Sicherheit zwischen 0 und 1.",
                    },
                    "reasoning": _string("Ein bis zwei Sätze: woran erkennbar."),
                    "distinguishing_features": _string(
                        "Woran sich dieser Kandidat von den anderen unterscheiden lässt."
                    ),
                }
            ),
        },
        "image_notes": _string(
            "Was das Foto nicht hergibt — Unschärfe, Anschnitt, fehlende Merkmale. Sonst leer."
        ),
    }
)

#: Steckbrief-Entwurf Tier — Feldnamen wie in catalog.AnimalSpecies.
ANIMAL_PROFILE_SCHEMA = _object(
    {
        "scientific_name": _string("Wissenschaftlicher Name."),
        "common_name": _string("Deutscher Name, sonst leer."),
        "variant": _string(
            "Zuchtform oder Sorte, z. B. Electric Blue. Nur wenn die Form eindeutig "
            "belegt ist; im Zweifel leer und unter uncertainties nennen."
        ),
        "is_cultivated_form": _boolean(
            "True, wenn die unter variant genannte Form durch Selektion entstanden "
            "ist und so in der Natur nicht vorkommt. Bei der Stammform false."
        ),
        "group": _string("Tiergruppe.", ANIMAL_GROUPS),
        "family": _string("Familie, sonst leer."),
        "origin": _string("Herkunftsgebiet, sonst leer."),
        "size_max_cm": _number("Endgröße in Zentimetern."),
        "min_tank_liters": _integer("Mindestvolumen in Litern."),
        "min_tank_length_cm": _integer("Mindestkantenlänge in Zentimetern."),
        "min_group_size": _integer("Mindestgruppengröße; 1 bei Einzelhaltung."),
        "social_behavior": _string("Sozialverhalten.", SOCIAL_BEHAVIORS),
        "zone": _string("Bevorzugte Schwimmzone.", ZONES),
        "difficulty": _string("Haltungsanspruch.", DIFFICULTIES),
        "lifespan_years": _number("Lebenserwartung in Jahren."),
        "temp_min_c": _number("Untere Temperaturgrenze in Grad Celsius."),
        "temp_max_c": _number("Obere Temperaturgrenze in Grad Celsius."),
        "ph_min": _number("Unterer pH-Wert."),
        "ph_max": _number("Oberer pH-Wert."),
        "gh_min": _number("Untere Gesamthärte in Grad deutscher Härte."),
        "gh_max": _number("Obere Gesamthärte in Grad deutscher Härte."),
        "diet": _string("Ernährungstyp.", DIETS),
        "description": _string("Zwei bis vier Sätze Beschreibung."),
        "care_notes": _string("Hinweise zur Haltung."),
        "compatibility_notes": _string("Verträglichkeit mit anderem Besatz."),
        "warning": _string("Was schiefgehen kann, sonst leer."),
        "uncertainties": _string("Wo die Angaben unsicher sind. Sonst leer."),
    }
)

#: Steckbrief-Entwurf Pflanze — Feldnamen wie in catalog.PlantSpecies.
PLANT_PROFILE_SCHEMA = _object(
    {
        "scientific_name": _string("Wissenschaftlicher Name."),
        "common_name": _string("Deutscher Name, sonst leer."),
        "variant": _string(
            "Sorte, z. B. Flamingo oder Red Ruby. Nur wenn die Sorte eindeutig belegt "
            "ist; im Zweifel leer und unter uncertainties nennen."
        ),
        "is_cultivated_form": _boolean(
            "True, wenn die unter variant genannte Sorte gezüchtet ist und so in der "
            "Natur nicht vorkommt. Bei der Stammform false."
        ),
        "family": _string("Familie, sonst leer."),
        "origin": _string("Herkunftsgebiet, sonst leer."),
        "growth_form": _string("Wuchsform.", GROWTH_FORMS),
        "placement": _string("Platzierung im Becken.", PLACEMENTS),
        "difficulty": _string("Pflegeanspruch.", DIFFICULTIES),
        "growth_rate": _string("Wuchsgeschwindigkeit.", GROWTH_RATES),
        "light_demand": _string("Lichtbedarf.", DEMANDS),
        "co2_demand": _string("CO2-Bedarf.", DEMANDS),
        "height_min_cm": _integer("Untere Wuchshöhe in Zentimetern."),
        "height_max_cm": _integer("Obere Wuchshöhe in Zentimetern."),
        "temp_min_c": _number("Untere Temperaturgrenze in Grad Celsius."),
        "temp_max_c": _number("Obere Temperaturgrenze in Grad Celsius."),
        "ph_min": _number("Unterer pH-Wert."),
        "ph_max": _number("Oberer pH-Wert."),
        "propagation": _string("Vermehrung."),
        "description": _string("Zwei bis vier Sätze Beschreibung."),
        "care_notes": _string("Hinweise zur Pflege."),
        "warning": _string("Was schiefgehen kann, sonst leer."),
        "uncertainties": _string("Wo die Angaben unsicher sind. Sonst leer."),
    }
)

#: Besatzprüfung — Befunde statt Freitext, damit die Seite sie sortieren kann.
STOCKING_SCHEMA = _object(
    {
        "verdict": _string(
            "Gesamteinschätzung.", ["unbedenklich", "mit_einschraenkungen", "bedenklich"]
        ),
        "summary": _string("Zwei bis drei Sätze Zusammenfassung."),
        "findings": {
            "type": "array",
            "description": "Einzelne Befunde, die wichtigsten zuerst.",
            "items": _object(
                {
                    "topic": _string(
                        "Worum es geht.",
                        ["beckengroesse", "gruppengroesse", "vertraeglichkeit", "wasserwerte", "sonstiges"],
                    ),
                    "severity": _string("Gewicht des Befunds.", ["hinweis", "warnung", "kritisch"]),
                    "subject": _string("Betroffene Art oder Kombination."),
                    "message": _string("Der Befund in ein bis zwei Sätzen."),
                }
            ),
        },
        "open_questions": _string("Was zur Beurteilung fehlt. Sonst leer."),
    }
)
