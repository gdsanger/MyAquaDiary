"""Abgleich mit dem Artenkatalog.

Der Katalog (``catalog.CatalogAnimal`` / ``catalog.CatalogPlant``) liegt in
einem anderen Schritt des Epics. Damit die Bestimmung schon vorher vollständig
ist, wird er hier über :func:`django.apps.apps.get_model` aufgelöst statt
importiert: fehlt er noch, liefert der Abgleich einfach keine Treffer, und
sobald er da ist, greift dieselbe Abfrage ohne weitere Änderung.

Zwei Richtungen:

* :func:`find_matches` — gibt es die bestimmte Art schon im Katalog?
* :func:`publish` — einen bestätigten Steckbrief in den Katalog übernehmen.
"""

import logging
from dataclasses import dataclass

from django.apps import apps
from django.db import DatabaseError

logger = logging.getLogger(__name__)

#: Vorschlagsart -> Katalogmodell.
MODELS = {"animal": "CatalogAnimal", "plant": "CatalogPlant"}
#: Feld, das die Sortenbezeichnung trägt. In beiden Katalogmodellen dasselbe:
#: ``variant`` (siehe ``catalog.Species``). Botanisch wären ``cultivar`` und
#: ``variety`` getrennt sauberer — zwei Namen für dieselbe Sache brächten hier
#: aber einen zweiten Codepfad je Art ein.
VARIANT_FIELD = "variant"
#: So viele Treffer zeigt die Bestimmungsseite je Kandidat.
MATCH_LIMIT = 3


@dataclass(frozen=True)
class CatalogMatch:
    """Ein Katalogeintrag, der zu einem Kandidaten passt."""

    pk: int
    label: str
    scientific_name: str
    verified: bool
    exact: bool

    @property
    def status_label(self) -> str:
        return "bestätigt" if self.verified else "Entwurf"


def model_for(kind: str):
    """Das Katalogmodell einer Vorschlagsart — ``None``, solange es fehlt."""
    name = MODELS.get(kind)
    if not name:
        return None
    try:
        return apps.get_model("catalog", name)
    except LookupError:
        # Der Katalog kommt später im Epic; bis dahin ist das der Normalfall.
        return None


def is_available(kind: str) -> bool:
    return model_for(kind) is not None


def find_matches(kind: str, scientific_name: str, common_name: str = "") -> list[CatalogMatch]:
    """Katalogeinträge zu einem bestimmten Namen.

    Zuerst der wissenschaftliche Name (das ist die fachliche Identität), dann
    der deutsche Name als Notnagel. Ohne Katalog: leere Liste.
    """
    model = model_for(kind)
    scientific_name = (scientific_name or "").strip()
    common_name = (common_name or "").strip()
    if model is None or not (scientific_name or common_name):
        return []

    try:
        exact = list(model.objects.filter(scientific_name__iexact=scientific_name)[:MATCH_LIMIT]) if scientific_name else []
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
        verified=bool(getattr(entry, "verified", False)),
        exact=exact,
    )


def publish(suggestion) -> str:
    """Übernimmt einen bestätigten Entwurf in den Katalog.

    :returns: Referenz auf den angelegten Eintrag (``catalog.CatalogAnimal:12``)
        oder ein leerer String, wenn der Katalog noch nicht da ist. Der
        Entwurf bleibt in beiden Fällen erhalten, die Übernahme lässt sich
        also nachholen.
    """
    model = model_for(suggestion.kind)
    if model is None:
        logger.info(
            "Vorschlag %s bestätigt, der Katalog ist aber noch nicht angebunden",
            suggestion.pk,
        )
        return ""

    payload = _catalog_fields(model, suggestion)
    if not payload.get("scientific_name"):
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
        payload.setdefault("created_by", suggestion.user)
        # Bestätigt heißt bestätigt: ein Mensch hat den Entwurf geprüft.
        payload["verified"] = True
        entry = model.objects.create(**payload)
    except DatabaseError:
        logger.exception("Katalogeintrag zu Vorschlag %s konnte nicht angelegt werden", suggestion.pk)
        return ""
    return f"{model._meta.label}:{entry.pk}"


def _catalog_fields(model, suggestion) -> dict:
    """Nur die Felder übernehmen, die das Katalogmodell wirklich kennt.

    Der Entwurf trägt zusätzlich Felder wie ``uncertainties``, die im Prompt
    Sinn ergeben, im Katalog aber nichts zu suchen haben.
    """
    known = {field.name for field in model._meta.get_fields() if hasattr(field, "attname")}
    payload = {
        key: value
        for key, value in (suggestion.payload or {}).items()
        if key in known and value not in (None, "")
    }
    payload["scientific_name"] = (
        payload.get("scientific_name") or suggestion.scientific_name or ""
    ).strip()
    if suggestion.common_name and not payload.get("common_name"):
        payload["common_name"] = suggestion.common_name
    return payload
