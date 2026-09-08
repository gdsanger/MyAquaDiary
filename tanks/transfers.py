"""Umsetzen von Tieren und Pflanzen zwischen zwei eigenen Becken.

Ein Umzug ist kein Abgang plus ein Neuzugang, sondern **ein** Vorgang. Steht er
als zwei unverbundene Buchungen da, sieht es im Rückblick aus, als wären im
einen Becken Tiere verschwunden und im anderen welche aufgetaucht.

Hier steht dieser Vorgang genau einmal: Formular und MCP-Werkzeuge rufen
dieselben :func:`check`, :func:`hints` und :func:`perform` auf. Eine zweite
Umsetzung neben der Web-App wäre eine zweite Meinung darüber, was ein Umzug
ist — und sie liefe irgendwann auseinander.

**Prüfen, hinweisen, buchen** sind drei getrennte Schritte, weil sie
unterschiedlich verbindlich sind: :func:`check` weist ab (fremdes Becken, mehr
Tiere als vorhanden), :func:`hints` merkt an (Wasserwerte, Artansprüche,
Gruppengröße) und hält niemanden auf. Über einen Umzug in hartes Wasser
entscheidet der Halter, nicht das Programm.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import selectors
from .models import Event, Planting, Stocking, Tank, Transfer

#: Ab welchem Abstand zwischen Quell- und Zielbecken ein Wasserwert angemerkt
#: wird — absolut und je Messgröße, weil die Skalen nicht vergleichbar sind:
#: ein pH-Sprung von 1,0 ist die zehnfache H⁺-Konzentration, 1 °dH ist nichts.
#: Messgrößen ohne Eintrag (NO₂, NO₃, NH₄, PO₄) sind Belastungswerte; sie
#: gehören ins Becken überwacht, sagen aber nichts über einen Umzug.
WATER_THRESHOLDS = {
    "temperatur": Decimal("3"),
    "ph": Decimal("0.5"),
    "kh": Decimal("4"),
    "gh": Decimal("6"),
    "leitwert": Decimal("300"),
}

#: Katalogbereich einer Art -> Messgröße im Becken. Der Steckbrief führt genau
#: diese drei Bereiche; alles Weitere stünde hier ohne Datengrundlage.
SPECIES_RANGES = [
    ("temperatur", "temperature_min", "temperature_max", "temperature_range"),
    ("ph", "ph_min", "ph_max", "ph_range"),
    ("gh", "gh_min", "gh_max", "gh_range"),
]

#: Uhrzeit der Ereignisse zu einem rückwirkend gebuchten Umzug. Der Umzug hat
#: ein Datum, das Ereignis braucht einen Zeitpunkt — die Mittagszeit liegt für
#: jede Zeitzone sicher am richtigen Kalendertag.
BACKDATED_HOUR = time(12, 0)


@dataclass(frozen=True)
class Move:
    """Ein Umzug, bevor er gebucht ist.

    ``entry`` ist der Besatz- oder Pflanzeneintrag im **Quellbecken**; das
    Quellbecken selbst steht daran und wird nicht noch einmal übergeben, sonst
    ließen sich die beiden auseinanderbringen.
    """

    entry: Stocking | Planting
    target_tank: Tank
    quantity: int
    moved_on: date
    note: str = ""

    @property
    def source_tank(self) -> Tank:
        return self.entry.tank

    @property
    def species(self):
        return self.entry.species

    @property
    def kind(self) -> str:
        return Transfer.Kind.ANIMAL if isinstance(self.entry, Stocking) else Transfer.Kind.PLANT

    @property
    def date_field(self) -> str:
        """``added_on`` beim Besatz, ``planted_on`` bei der Bepflanzung."""
        return "added_on" if isinstance(self.entry, Stocking) else "planted_on"

    @property
    def entry_start(self) -> date:
        return getattr(self.entry, self.date_field)


# --------------------------------------------------------------------------
# Prüfung — was abgewiesen wird
# --------------------------------------------------------------------------


def check(move: Move):
    """Die Regeln eines Umzugs. Wirft :class:`ValidationError`, sonst nichts.

    Die Meldungen sind nach Feldnamen des Formulars geschlüsselt; die
    MCP-Schicht macht daraus einen Satz für den Client
    (:mod:`services.mcp.runner`).
    """
    errors: dict[str, str] = {}
    entry = move.entry

    if entry.removed_on is not None:
        errors["__all__"] = "Dieser Eintrag ist bereits abgegangen und lässt sich nicht umsetzen."

    if move.target_tank.pk == entry.tank_id:
        errors["target_tank"] = "Quell- und Zielbecken sind dasselbe Becken."
    elif move.target_tank.owner_id != entry.tank.owner_id:
        # Eine Abgabe an Dritte ist kein Umzug, sondern ein Abgang.
        errors["target_tank"] = "Umgesetzt wird nur zwischen eigenen Becken."
    elif move.target_tank.is_dissolved:
        errors["target_tank"] = "Das Zielbecken ist aufgelöst."

    if move.quantity < 1:
        errors["quantity"] = "Es muss mindestens eines umgesetzt werden."
    elif move.quantity > entry.quantity:
        errors["quantity"] = f"Im Quellbecken sind nur {entry.quantity}."

    if move.moved_on < move.entry_start:
        errors["moved_on"] = "Der Umzug kann nicht vor dem Einsetzen stattgefunden haben."
    elif move.moved_on > timezone.localdate():
        errors["moved_on"] = "Das Datum liegt in der Zukunft."

    if errors:
        raise ValidationError(errors)


# --------------------------------------------------------------------------
# Hinweise — was angemerkt, aber nicht verhindert wird
# --------------------------------------------------------------------------


def _latest_by_key(tanks):
    """``{(tank_id, Parametername): Messwert}`` — je Becken der jüngste Wert."""
    return {
        (row.tank_id, row.parameter.key): row for row in selectors.latest_measurements(tanks)
    }


def water_hints(move: Move, latest) -> list[str]:
    """Wasserwerte, die zwischen den beiden Becken deutlich auseinanderliegen.

    Verglichen wird der jeweils jüngste Wert. Fehlt er in einem der beiden
    Becken, gibt es keinen Hinweis: „nie gemessen“ ist keine Abweichung, und
    eine Warnung ohne Grundlage lernt man schnell zu überlesen.
    """
    found = []
    for key, threshold in WATER_THRESHOLDS.items():
        here = latest.get((move.source_tank.pk, key))
        there = latest.get((move.target_tank.pk, key))
        if here is None or there is None:
            continue
        # Ein „n.n.“ ist keine Zahl; ein Abstand dazu wäre erfunden.
        if here.value is None or there.value is None:
            continue
        if abs(here.value - there.value) < threshold:
            continue
        found.append(
            f"{here.parameter.name}: {here.display_value} im Quellbecken, "
            f"{there.display_value} im Zielbecken — deutlicher Unterschied."
        )
    return found


def species_hints(move: Move, latest) -> list[str]:
    """Werte des Zielbeckens außerhalb dessen, was der Steckbrief nennt."""
    species = move.species
    found = []
    for key, low_field, high_field, range_field in SPECIES_RANGES:
        row = latest.get((move.target_tank.pk, key))
        if row is None or row.value is None:
            continue
        low, high = getattr(species, low_field), getattr(species, high_field)
        if low is None and high is None:
            continue
        if (low is not None and row.value < low) or (high is not None and row.value > high):
            found.append(
                f"{row.parameter.name} im Zielbecken: {row.display_value} — "
                f"{species.display_name} braucht {getattr(species, range_field)}."
            )
    return found


def group_hints(move: Move) -> list[str]:
    """Bleibt im Quellbecken weniger zurück, als die Art an Gruppe braucht?

    Nur für Tiere: eine Pflanze hat keine Gruppengröße. Wird der Bestand ganz
    umgesetzt, bleibt keine Gruppe zurück, die zu klein sein könnte.
    """
    if not isinstance(move.entry, Stocking):
        return []
    remaining = move.entry.quantity - move.quantity
    minimum = move.entry.species.min_group_size
    if remaining < 1 or minimum <= 1 or remaining >= minimum:
        return []
    return [
        f"Im Quellbecken bleiben {remaining} {move.species.display_name} zurück — "
        f"die Mindestgruppengröße liegt bei {minimum}."
    ]


def hints(move: Move) -> list[str]:
    """Alles, was vor dem Umsetzen zu bedenken ist — in Sätzen, ohne Sperre."""
    latest = _latest_by_key([move.source_tank, move.target_tank])
    return [*water_hints(move, latest), *species_hints(move, latest), *group_hints(move)]


# --------------------------------------------------------------------------
# Buchung
# --------------------------------------------------------------------------


def _moment(day: date):
    """Zeitpunkt für ein Ereignis zu einem Tagesdatum."""
    if day == timezone.localdate():
        return timezone.now()
    return timezone.make_aware(datetime.combine(day, BACKDATED_HOUR))


def target_entry(move: Move):
    """Der aktive Eintrag derselben Art im Zielbecken — oder ``None``.

    Der Bestand wird zusammengeführt, nicht dupliziert: zwei Zeilen derselben
    Art in einem Becken wären zwei Bestände, die niemand getrennt führt. Gibt
    es (aus der Zeit vor dieser Funktion) mehrere, gewinnt der älteste; er ist
    der, an dem die Geschichte hängt.
    """
    return (
        type(move.entry)
        .objects.filter(tank=move.target_tank, species=move.species, removed_on__isnull=True)
        .order_by("pk")
        .first()
    )


@transaction.atomic
def perform(move: Move, user=None) -> Transfer:
    """Bucht den Umzug — vollständig oder gar nicht.

    Ein halb ausgeführter Umzug wäre schlimmer als gar keine Funktion: Tiere im
    Quellbecken abgezogen, im Zielbecken nie angekommen. Deshalb hängen die
    fünf Schritte in einer Transaktion zusammen.

    :func:`check` läuft hier noch einmal. Der Aufrufer hat es meist schon
    getan — aber „meist“ ist bei einem Vorgang, der Bestände verschiebt, keine
    Zusicherung.
    """
    check(move)
    entry = move.entry
    species = move.species
    is_animal = move.kind == Transfer.Kind.ANIMAL

    # 1. Quellbecken: Menge verringern, bei null den Abgang buchen.
    entry.quantity -= move.quantity
    if entry.quantity == 0:
        entry.removed_on = move.moved_on
    entry.save(update_fields=["quantity", "removed_on"])

    # 2. Zielbecken: zusammenführen, sonst neu anlegen.
    arrived = target_entry(move)
    if arrived is not None:
        arrived.quantity += move.quantity
        arrived.save(update_fields=["quantity"])
    else:
        arrived = type(entry).objects.create(
            tank=move.target_tank,
            species=species,
            quantity=move.quantity,
            **{move.date_field: move.moved_on},
        )

    # 3. Der Nachweis, der beide Buchungen verbindet.
    transfer = Transfer.objects.create(
        kind=move.kind,
        source_tank=move.source_tank,
        target_tank=move.target_tank,
        animal=species if is_animal else None,
        plant=None if is_animal else species,
        quantity=move.quantity,
        moved_on=move.moved_on,
        note=move.note,
        created_by=user,
    )

    # 4. Je ein Ereignis in beiden Becken, jedes mit Verweis auf das andere.
    #    In der Beckengeschichte steht der Umzug damit auf beiden Seiten.
    occurred_at = _moment(move.moved_on)
    Event.objects.bulk_create(
        [
            Event(
                tank=move.source_tank,
                category=Event.Category.STOCKING,
                title=(
                    f"{move.quantity}× {species.display_name} nach "
                    f"„{move.target_tank.name}“ umgesetzt"
                ),
                description=move.note,
                occurred_at=occurred_at,
                created_by=user,
            ),
            Event(
                tank=move.target_tank,
                category=Event.Category.STOCKING,
                title=(
                    f"{move.quantity}× {species.display_name} aus "
                    f"„{move.source_tank.name}“ übernommen"
                ),
                description=move.note,
                occurred_at=occurred_at,
                created_by=user,
            ),
        ]
    )

    # 5. Der Zieleintrag wird zurückgegeben, ohne ihn erneut zu laden — die
    #    MCP-Schicht gibt ihn mit aus.
    transfer.arrived = arrived
    return transfer


# --------------------------------------------------------------------------
# Herkunft am Zieleintrag
# --------------------------------------------------------------------------


def annotate_origins(entries, tank):
    """Hängt jedem Eintrag seinen Umzug an — ``entry.origin`` oder ``None``.

    Gesucht wird der jüngste Umzug dieser Art in dieses Becken, der in die
    Standzeit des Eintrags fällt. Über den Zeitraum, weil derselbe Besatz
    abgehen und Jahre später neu entstehen kann: der Umzug von damals gehört
    dann nicht an den Eintrag von heute.

    Eine Abfrage für die ganze Liste statt einer je Zeile — ein Becken hat
    Dutzende Einträge, aber die Beckenseite lädt sie alle auf einmal.
    """
    entries = list(entries)
    if not entries:
        return entries

    kind = Transfer.Kind.ANIMAL if isinstance(entries[0], Stocking) else Transfer.Kind.PLANT
    species_field = "animal_id" if kind == Transfer.Kind.ANIMAL else "plant_id"
    date_field = "added_on" if kind == Transfer.Kind.ANIMAL else "planted_on"

    by_species: dict[int, list[Transfer]] = {}
    rows = (
        Transfer.objects.filter(target_tank=tank, kind=kind)
        .select_related("source_tank")
        .order_by("moved_on", "pk")
    )
    for row in rows:
        by_species.setdefault(getattr(row, species_field), []).append(row)

    for entry in entries:
        start = getattr(entry, date_field)
        entry.origin = None
        for row in by_species.get(entry.species_id, ()):
            if row.moved_on < start:
                continue
            if entry.removed_on is not None and row.moved_on > entry.removed_on:
                continue
            # Aufsteigend geordnet — der letzte passende ist der jüngste.
            entry.origin = row
    return entries


def for_user(user):
    """Alle Umzüge des Benutzers, becken- und artübergreifend, neueste zuerst.

    Gefiltert wird über das Quellbecken: beide Becken gehören demselben
    Benutzer, die zweite Bedingung wäre ohne Wirkung.
    """
    return (
        Transfer.objects.filter(source_tank__owner=user)
        .select_related("source_tank", "target_tank", "animal", "plant")
    )
