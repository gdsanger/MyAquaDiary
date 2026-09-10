"""Abgleich mit dem Artenkatalog.

Zwei Richtungen:

* :func:`find_matches` — gibt es die bestimmte Art schon im Katalog?
* :func:`publish` — einen bestätigten Steckbrief in den Katalog übernehmen.

Die Katalogmodelle werden importiert, nicht über ``apps.get_model`` gesucht.
Solange der Katalog noch ausstand, war die späte Bindung richtig; jetzt
verdeckt sie nur noch Tippfehler, denn ein falscher Modellname ist von „gibt
es noch nicht“ nicht zu unterscheiden. Ein Fehler beim Start ist besser als
ein Abgleich, der monatelang stillschweigend nichts findet.

Der Entwurf spricht die Sprache des Prompts (``temp_min_c``,
``min_tank_liters``), der Katalog die des Datenmodells (``temperature_min``,
``min_tank_volume_l``). :data:`FIELD_NAMES` und :data:`FIELD_VALUES` sind die
Übersetzung dazwischen — an einer Stelle statt verteilt über die Aufrufer, und
alt gespeicherte Entwürfe wandern damit genauso in den Katalog wie neue.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import DatabaseError, models

from catalog.models import AnimalSpecies, PlantSpecies, normalize_variant, unique_slug

logger = logging.getLogger(__name__)

#: Vorschlagsart -> Katalogmodell.
MODELS = {"animal": AnimalSpecies, "plant": PlantSpecies}
#: Feld, das die Sortenbezeichnung trägt. In beiden Katalogmodellen dasselbe:
#: ``variant`` (siehe ``catalog.Species``). Botanisch wären ``cultivar`` und
#: ``variety`` getrennt sauberer — zwei Namen für dieselbe Sache brächten hier
#: aber einen zweiten Codepfad je Art ein.
VARIANT_FIELD = "variant"
#: So viele Treffer zeigt die Bestimmungsseite je Kandidat.
MATCH_LIMIT = 3

#: Steckbrief-Feld -> Katalogfeld, nur wo die Namen auseinandergehen. Alles
#: andere heißt hier wie dort gleich; was der Katalog gar nicht kennt
#: (``family``, ``care_notes``, ``uncertainties``), bleibt am Vorschlag stehen
#: und ist dort weiter zu lesen.
FIELD_NAMES = {
    "temp_min_c": "temperature_min",
    "temp_max_c": "temperature_max",
    "size_max_cm": "adult_size_cm",
    "min_tank_liters": "min_tank_volume_l",
    "height_max_cm": "max_height_cm",
    "group": "category",
    "co2_demand": "co2_required",
    # Der Entwurf nennt das Herkunftsgebiet als Freitext; das Auswahlfeld
    # ``origin_region`` bleibt dem Menschen, der den Entwurf übernimmt. Aus
    # „Südamerika, Orinoco-Einzug" ein Gebiet zu raten hieße, eine Angabe zu
    # erfinden, die hinterher filterbar aussieht.
    "origin": "origin_detail",
    "social_behavior": "social_structure",
}

#: Katalogfeld -> {Wert im Entwurf: Wert im Katalog}. Die Auswahllisten des
#: Prompts sind teils deutsch, teils gröber als die des Katalogs. ``co2_demand``
#: ist ein Bedarf in drei Stufen, ``co2_required`` eine Ja-Nein-Frage: nur „viel“
#: heißt, dass es ohne CO₂ nicht geht.
FIELD_VALUES = {
    "category": {
        "fisch": "fish",
        "garnele": "shrimp",
        "krebs": "crayfish",
        "schnecke": "snail",
        "muschel": "mussel",
    },
    # Ältere Entwürfe kennen „demanding“, der Katalog nur „hard“.
    "difficulty": {"demanding": "hard"},
    "co2_required": {"low": False, "medium": False, "high": True},
    "social_structure": {
        "einzeln": "solitary",
        "paar": "pair",
        "harem": "harem",
        "gruppe": "group",
        "schwarm": "shoal",
    },
    # Der Prompt kennt drei Bereiche, der Katalog sechs: die feineren bleiben
    # dem Menschen. „mitte“ auf ``middle`` abzubilden ist keine Verfeinerung,
    # sondern dieselbe Aussage.
    "zone": {"boden": "bottom", "mitte": "middle", "oberflaeche": "surface"},
    # „aufwuchs“ ist eine Ernährung von pflanzlichem Aufwuchs. Der Katalog
    # führt die Feinheit bewusst nicht — sie gehört in die Beschreibung.
    "diet": {
        "allesfresser": "omnivore",
        "fleisch": "carnivore",
        "pflanzen": "herbivore",
        "aufwuchs": "herbivore",
    },
}


@dataclass(frozen=True)
class CatalogMatch:
    """Ein Katalogeintrag, der zu einem Kandidaten passt."""

    pk: int
    label: str
    scientific_name: str
    url: str
    exact: bool

    @property
    def status_label(self) -> str:
        """Woran der Treffer hängt — am Namen selbst oder nur an einem Teil.

        Der Katalog kennt keinen Entwurfszustand: was drinsteht, ist gepflegt.
        Zu unterscheiden ist deshalb nicht Entwurf von bestätigt, sondern
        genauer Treffer von bloßer Namensähnlichkeit.
        """
        return "genauer Treffer" if self.exact else "ähnlicher Name"


def model_for(kind: str):
    """Das Katalogmodell einer Vorschlagsart.

    :raises KeyError: bei einer Art, die es nicht gibt. Das wäre ein Fehler im
        Aufrufer und keiner, den ein leeres Ergebnis verstecken sollte.
    """
    return MODELS[kind]


def find_matches(kind: str, scientific_name: str, common_name: str = "") -> list[CatalogMatch]:
    """Katalogeinträge zu einem bestimmten Namen.

    Zuerst der wissenschaftliche Name (das ist die fachliche Identität), dann
    der deutsche Name als Notnagel. Stamm- und Zuchtformen derselben Art sind
    eigene Einträge und tauchen deshalb einzeln auf.
    """
    model = model_for(kind)
    scientific_name = (scientific_name or "").strip()
    common_name = (common_name or "").strip()
    if not (scientific_name or common_name):
        return []

    try:
        exact = (
            list(model.objects.filter(scientific_name__iexact=scientific_name)[:MATCH_LIMIT])
            if scientific_name
            else []
        )
        matches = [_match(entry, exact=True) for entry in exact]
        if len(matches) < MATCH_LIMIT:
            found = {entry.pk for entry in exact}
            for entry in _fuzzy(model, scientific_name, common_name, found):
                matches.append(_match(entry, exact=False))
                if len(matches) >= MATCH_LIMIT:
                    break
    except DatabaseError:
        # Katalogtabellen noch nicht migriert — kein Grund, die Bestimmung
        # scheitern zu lassen.
        logger.warning("Katalogabgleich nicht möglich — Tabellen fehlen")
        return []
    return matches


def _fuzzy(model, scientific_name, common_name, exclude):
    queryset = model.objects.none()
    if scientific_name:
        queryset = model.objects.filter(scientific_name__icontains=scientific_name)
    if common_name:
        queryset = queryset | model.objects.filter(common_name__icontains=common_name)
    return queryset.exclude(pk__in=exclude).distinct()[:MATCH_LIMIT]


def _match(entry, *, exact: bool) -> CatalogMatch:
    return CatalogMatch(
        pk=entry.pk,
        label=str(entry),
        scientific_name=entry.scientific_name,
        url=entry.get_absolute_url(),
        exact=exact,
    )


def publish(suggestion) -> str:
    """Übernimmt einen bestätigten Entwurf in den Katalog.

    :returns: Referenz auf den Eintrag (``catalog.AnimalSpecies:12``) oder ein
        leerer String, wenn dem Entwurf der wissenschaftliche Name fehlt oder
        die Datenbank den Eintrag nicht annimmt. Der Entwurf bleibt in beiden
        Fällen erhalten, die Übernahme lässt sich also nachholen.
    """
    model = model_for(suggestion.kind)
    payload = _catalog_fields(model, suggestion)
    if not payload.get("scientific_name"):
        logger.warning(
            "Vorschlag %s hat keinen wissenschaftlichen Namen — nichts zu übernehmen",
            suggestion.pk,
        )
        return ""

    # Gesucht wird wie die Datenbank prüft: Name und Sorte gemeinsam, beides
    # ohne Rücksicht auf Groß- und Kleinschreibung.
    lookup = {
        "scientific_name__iexact": payload["scientific_name"],
        f"{VARIANT_FIELD}__iexact": payload.get(VARIANT_FIELD, ""),
    }
    try:
        existing = model.objects.filter(**lookup).first()
        if existing is not None:
            return f"{model._meta.label}:{existing.pk}"
        payload["slug"] = unique_slug(
            model, payload["scientific_name"], payload.get(VARIANT_FIELD, "")
        )
        entry = model.objects.create(**payload)
    except (DatabaseError, ValueError):
        logger.exception(
            "Katalogeintrag zu Vorschlag %s konnte nicht angelegt werden", suggestion.pk
        )
        return ""
    return f"{model._meta.label}:{entry.pk}"


def _catalog_fields(model, suggestion) -> dict:
    """Der Entwurf, übersetzt in die Felder des Katalogmodells.

    Übernommen wird nur, was das Modell kennt *und* annimmt: ein Wert außerhalb
    der Auswahlliste oder eine Zahl, die nicht in die Spalte passt, fliegt mit
    einem Logeintrag raus. Lieber ein Steckbrief ohne diese Angabe als einer,
    der sich nicht speichern lässt.
    """
    fields = {
        field.name: field
        for field in model._meta.concrete_fields
        # ``slug`` setzt die Übernahme selbst, er ist keine Angabe der KI.
        if field.editable and field.name != "slug"
    }
    payload = {}
    for key, value in (suggestion.payload or {}).items():
        field = fields.get(FIELD_NAMES.get(key, key))
        if field is None or value in (None, "") or isinstance(value, (list, dict)):
            continue
        cleaned = _clean(field, value, suggestion)
        if cleaned is not None:
            payload[field.name] = cleaned

    payload["scientific_name"] = _cut(
        fields["scientific_name"],
        (payload.get("scientific_name") or suggestion.scientific_name or "").strip(),
    )
    if suggestion.common_name and not payload.get("common_name"):
        payload["common_name"] = _cut(fields["common_name"], suggestion.common_name)
    # Dieselbe Schreibweise wie aus dem Formular, sonst steht dieselbe Sorte
    # zweimal im Katalog — einmal mit und einmal ohne Hochkomma.
    if VARIANT_FIELD in payload:
        payload[VARIANT_FIELD] = normalize_variant(payload[VARIANT_FIELD])
    return payload


def _clean(field, value, suggestion):
    """Ein Wert, wie das Katalogfeld ihn annimmt — sonst ``None``."""
    if isinstance(value, str):
        value = FIELD_VALUES.get(field.name, {}).get(value, value)
    value = _cut(field, value)
    try:
        if isinstance(field, models.DecimalField):
            value = _rounded(field, value)
        return field.clean(value, None)
    except (ValidationError, ArithmeticError, TypeError, ValueError) as exc:
        reason = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        logger.warning(
            "Vorschlag %s: %r passt nicht in %s (%s)", suggestion.pk, value, field.name, reason
        )
        return None


def _rounded(field, value):
    """Auf die Nachkommastellen der Spalte runden.

    Ohne das Runden scheitert schon eine glatte Angabe: aus ``22.0`` wird über
    den Umweg des Fließkommawerts ``Decimal('22.00')``, und zwei
    Nachkommastellen nimmt eine ``numeric(4,1)``-Spalte nicht an.
    """
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-field.decimal_places))


def _cut(field, value):
    """Text auf die Spaltenbreite kürzen — abgeschnitten ist besser als weg."""
    if isinstance(value, str) and field.max_length:
        return value[: field.max_length]
    return value
