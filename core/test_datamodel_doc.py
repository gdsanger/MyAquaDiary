"""Hält ``docs/datenmodell.md`` und die Modelle beieinander.

Die Referenz gibt es, weil die Entwürfe aus #1214–#1219 in Prompts und Code
weitergelebt haben, obwohl die Umsetzung andere Namen gewählt hatte — bis es in
der MCP-Schicht an ``shut_down_on`` und ``biotope`` scheiterte (#1236). Eine
zweite Beschreibung des Modells, die niemand prüft, macht diesen Fehler nur an
neuer Stelle wieder. Deshalb wird sie geprüft.

Verglichen werden ausschließlich **Feldnamen**. Auswahllisten, ``related_name``
und Indizes stehen in der Datei als Erläuterung; sie hier mitzuprüfen hieße,
das Modell ein zweites Mal zu schreiben.
"""

import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase

#: Die Apps des Projekts. Fremde Apps (auth, contenttypes) gehören nicht in
#: eine Referenz über das eigene Datenmodell.
DOCUMENTED_APPS = ["core", "catalog", "tanks", "services", "dashboard"]

DOC_PATH = Path(settings.BASE_DIR) / "docs" / "datenmodell.md"

#: „### `tanks.Tank` — erbt `CoverImageMixin`“ bzw. „### `Species` (abstrakt)“.
#: Ein Abschnitt ohne Punkt im Namen ist eine abstrakte Basis und hat kein
#: Modell; er wird nur über ``erbt`` eingesetzt.
HEADING = re.compile(
    r"^###\s+`(?P<name>[\w.]+)`"
    r"(?:\s*\((?P<abstract>abstrakt)\))?"
    r"(?:\s*—\s*erbt\s+`(?P<base>[\w.]+)`)?"
    r"\s*$"
)

FENCE = "```"


def parse_doc(text):
    """Feldblöcke aus der Referenz lesen.

    Gibt ``{Abschnittsname: (Basis oder None, [Feldnamen])}`` zurück. Je
    Abschnitt zählt der erste Codeblock; ein Abschnitt ohne Block hat keine
    Felder (``catalog.CatalogPermission``).
    """
    sections = {}
    current = None
    base = None
    fields = None
    in_block = False
    capturing = False

    for line in text.splitlines():
        if in_block:
            if line.startswith(FENCE):
                in_block = False
                capturing = False
                continue
            entry = line.strip()
            if capturing and entry and not entry.startswith("#"):
                fields.append(entry.split()[0])
            continue

        if line.startswith(FENCE):
            in_block = True
            # Nur der erste Block eines Abschnitts ist die Feldliste; ein
            # zweiter wäre ein Beispiel und wird überlesen.
            capturing = current is not None and fields is None
            if capturing:
                fields = []
            continue

        if line.startswith("#"):
            if current is not None:
                sections[current] = (base, fields or [])
            match = HEADING.match(line)
            current = match.group("name") if match else None
            base = match.group("base") if match else None
            fields = None

    if current is not None:
        sections[current] = (base, fields or [])
    return sections


def documented_fields(sections, name):
    """Felder eines Abschnitts samt denen seiner Basis."""
    base, fields = sections[name]
    if base is None:
        return list(fields)
    return documented_fields(sections, base) + list(fields)


def model_fields(model):
    """Eigene Spalten eines Modells — ohne Rückwärtsbeziehungen und ohne ``id``.

    Der Fremdschlüssel steht unter seinem Feldnamen (``tank``), nicht als
    ``tank_id``: So heißt er im Modell, und so wird er in Abfragen geschrieben.
    """
    names = []
    for field in model._meta.get_fields():
        if not getattr(field, "concrete", False):
            continue
        if field.auto_created and field.primary_key:
            continue
        names.append(field.name)
    return names


class DatamodelDocTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sections = parse_doc(DOC_PATH.read_text(encoding="utf-8"))
        cls.models = {
            f"{config.label}.{model.__name__}": model
            for config in map(apps.get_app_config, DOCUMENTED_APPS)
            for model in config.get_models()
        }

    def test_every_model_is_documented(self):
        """Ein neues Modell fällt hier auf, nicht erst beim nächsten Umbau."""
        missing = sorted(set(self.models) - set(self.sections))
        self.assertEqual(
            missing, [], f"Nicht in docs/datenmodell.md beschrieben: {missing}"
        )

    def test_no_documented_model_is_gone(self):
        """Abschnitte mit Punkt im Namen müssen ein Modell haben.

        Ohne diese Prüfung überlebt ein umbenanntes Modell als Abschnitt, den
        niemand mehr findet — genau die Halbwahrheit, gegen die die Datei da ist.
        """
        stale = sorted(
            name for name in self.sections if "." in name and name not in self.models
        )
        self.assertEqual(stale, [], f"Kein Modell (mehr) zu diesem Abschnitt: {stale}")

    def test_fields_match_the_models(self):
        for label, model in sorted(self.models.items()):
            with self.subTest(model=label):
                if label not in self.sections:
                    continue  # Meldet bereits test_every_model_is_documented.
                documented = documented_fields(self.sections, label)
                self.assertEqual(
                    len(documented),
                    len(set(documented)),
                    "Feld doppelt in der Referenz beschrieben",
                )
                actual = model_fields(model)
                self.assertEqual(
                    sorted(documented),
                    sorted(actual),
                    "docs/datenmodell.md weicht vom Modell ab — fehlend: "
                    f"{sorted(set(actual) - set(documented))}, "
                    f"überzählig: {sorted(set(documented) - set(actual))}",
                )

    def test_abstract_sections_are_used(self):
        """Eine Basis ohne Erben ist ein Rest — er soll auffallen."""
        used = {base for base, _ in self.sections.values() if base}
        unused = sorted(
            name for name in self.sections if "." not in name and name not in used
        )
        self.assertEqual(unused, [], f"Abschnitt erbt an niemanden weiter: {unused}")
