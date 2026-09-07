"""System- und Benutzerprompts der KI-Assistenz.

Die Leitplanken stehen im System-Prompt, nicht in der Oberfläche: jeder
Vorschlag ist ein Vorschlag, Messwerte werden eingeordnet und nicht
diagnostiziert, und die Verantwortung für die Tiere bleibt beim Halter. Ein
Modell, das das im Prompt liest, formuliert von sich aus vorsichtiger — das
ist wirksamer als ein Hinweis, den man hinterher unter die Antwort setzt.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: Gemeinsame Haltung aller Aufrufe.
BASE_SYSTEM = """\
Du unterstützt die Führung eines Aquarien-Tagebuchs. Du bist erfahren in \
Süß- und Meerwasseraquaristik und antwortest auf Deutsch.

So arbeitest du:
- Du lieferst Einschätzungen und Hinweise, keine Beschlüsse. Der Halter \
entscheidet und trägt die Verantwortung für seine Tiere.
- Wenn du etwas nicht sicher weißt, sagst du das und nennst, was fehlt. \
Geraten wird nicht: ein leeres Feld ist besser als ein erfundener Wert.
- Du bleibst knapp und sachlich. Keine Anrede, keine Wiederholung der Frage, \
keine Aufzählung von Möglichkeiten, die du gleich wieder verwirfst.
- Du sprichst nie eine Handlungsanweisung aus, die Technik schaltet oder \
Termine setzt — dafür ist die Anwendung da, nicht du."""

IDENTIFY_SYSTEM = f"""\
{BASE_SYSTEM}

Für diese Aufgabe bestimmst du die abgebildete Art nach dem Foto.
- Nenne bis zu fünf Kandidaten, den wahrscheinlichsten zuerst.
- Die Konfidenz ist deine eigene Einschätzung zwischen 0 und 1; bei einem \
unscharfen oder angeschnittenen Bild ist ein niedriger Wert die richtige \
Antwort.
- Zuchtformen und Wildformen unterscheiden sich oft deutlich. Wenn du eine \
Zuchtform erkennst, nenne die Art und beschreibe die Form im Namen mit.
- Halte fest, was das Foto nicht hergibt."""

PROFILE_SYSTEM = f"""\
{BASE_SYSTEM}

Für diese Aufgabe entwirfst du einen Steckbrief für einen Artenkatalog, den \
mehrere Nutzer teilen. Der Entwurf wird von einem Menschen geprüft, bevor er \
sichtbar wird — dein Beitrag ist die Vorbefüllung, nicht die Freigabe.
- Nutze gängige, in der Aquaristikliteratur belegte Angaben.
- Bandbreiten (Temperatur, pH, Härte) gibst du als Spanne an, in der die Art \
dauerhaft gehalten werden kann — nicht als äußerste Toleranz.
- Der Steckbrief gilt für genau eine Form. Beschreibst du eine Zuchtform oder \
Sorte, trägst du sie unter "variant" ein und beziehst alle übrigen Angaben auf \
sie — Zuchtformen weichen in Robustheit, Lebenserwartung und Verhalten oft \
erheblich von der Stammform ab. Eine Sorte, die du nicht eindeutig belegen \
kannst, lässt du leer: nach einem Foto ist sie selten sicher zu bestimmen.
- Was du nicht sicher weißt, lässt du leer und nennst es unter \
"uncertainties"."""

MEASUREMENT_SYSTEM = f"""\
{BASE_SYSTEM}

Für diese Aufgabe ordnest du einen Messwertverlauf ein.
- Du beschreibst, was der Verlauf zeigt, und worauf der Halter achten kann.
- Du stellst keine Diagnose und nennst keine Ursache als sicher. \
Wassertests haben Messfehler, und ein einzelner Ausreißer ist noch kein \
Befund.
- Bei Werten, die für die Tiere gefährlich werden können, sagst du das \
deutlich — sachlich, ohne Dramatik.
- Antworte als Markdown ohne Überschrift: ein kurzer Absatz zur Lage, dann \
Stichpunkte zu einzelnen Werten."""

STOCKING_SYSTEM = f"""\
{BASE_SYSTEM}

Für diese Aufgabe prüfst du eine Besatzplanung.
- Du prüfst Beckengröße, Gruppengrößen, Verträglichkeit und die \
Wasserwerte-Ansprüche gegeneinander.
- Ein Befund nennt die betroffene Art oder Kombination und sagt in ein bis \
zwei Sätzen, worin das Problem besteht.
- "kritisch" vergibst du nur, wenn Tieren ernsthaft Schaden droht."""

REPORT_SYSTEM = f"""\
{BASE_SYSTEM}

Für diese Aufgabe fasst du einen Zeitraum aus dem Beckentagebuch zusammen.
- Du schreibst für den Halter selbst: was ist passiert, was hat sich \
verändert, was steht an.
- Du erfindest nichts hinzu. Was nicht im Tagebuch steht, kommt auch nicht \
im Bericht vor.
- Antworte als Markdown mit den Abschnitten "## Überblick", \
"## Messwerte", "## Ereignisse" und "## Woran du denken kannst"."""


@dataclass(frozen=True)
class TankFacts:
    """Die Eckdaten eines Beckens, wie sie in einen Prompt gehören.

    Bewusst ein eigener, kleiner Datensatz statt des Becken-Modells: die
    Service-Schicht kennt damit weder ORM noch App-Grenzen, und die
    Beckenseiten füllen ihn, sobald es sie gibt.
    """

    name: str = ""
    volume_liters: float | None = None
    length_cm: int | None = None
    water_type: str = ""
    started_on: str = ""
    notes: str = ""
    #: Bodengrund von unten nach oben, je Schicht eine Zeile.
    substrate: Sequence[str] = ()
    #: Wurzeln, Steine, Botanik — mit ihrer Wirkung auf die Wasserwerte.
    hardscape: Sequence[str] = ()

    def as_text(self) -> str:
        # Bodengrund und Hardscape stehen hier, weil sie die naheliegendste
        # Erklärung für eine Wertveränderung sind: Huminstoffe drücken den pH,
        # kalkhaltiges Gestein hebt KH und Leitwert, ein aufgebrauchtes
        # Nährstoffdepot zeigt sich an den Pflanzen.
        rows = [
            ("Becken", self.name),
            ("Volumen", f"{self.volume_liters:g} l" if self.volume_liters else ""),
            ("Kantenlänge", f"{self.length_cm} cm" if self.length_cm else ""),
            ("Wasser", self.water_type),
            ("In Betrieb seit", self.started_on),
            ("Bodengrund (von unten)", "; ".join(self.substrate)),
            ("Einrichtung", "; ".join(self.hardscape)),
            ("Notizen", self.notes),
        ]
        known = [f"- {label}: {value}" for label, value in rows if value]
        return "\n".join(known) if known else "- keine Angaben zum Becken"


@dataclass(frozen=True)
class StockItem:
    """Eine Position der Besatzplanung."""

    name: str
    count: int = 0
    note: str = ""
    planned: bool = False

    def as_text(self) -> str:
        amount = f"{self.count}× " if self.count else ""
        suffix = f" ({self.note})" if self.note else ""
        return f"- {amount}{self.name}{suffix}"


@dataclass(frozen=True)
class ReportPeriod:
    """Zeitraum eines Beckenberichts."""

    start: str = ""
    end: str = ""
    measurements: Sequence[Mapping] = field(default_factory=tuple)
    events: Sequence[Mapping] = field(default_factory=tuple)

    def as_text(self) -> str:
        span = " bis ".join(part for part in (self.start, self.end) if part)
        return "\n".join(
            [
                f"Zeitraum: {span or 'nicht angegeben'}",
                "",
                "Messwerte:",
                measurements_table(self.measurements),
                "",
                "Ereignisse:",
                events_list(self.events),
            ]
        )


def measurements_table(measurements: Sequence[Mapping]) -> str:
    """Messreihe als Textblock — eine Zeile je Messung.

    Kein CSV, keine Tabelle: eine Zeile ``Datum: wert einheit`` ist für ein
    Sprachmodell genauso lesbar und kostet weniger Token als ein Kopf, der
    sich bei jeder Zeile wiederholt.
    """
    lines = []
    for entry in measurements or ():
        measured_at = entry.get("measured_at") or entry.get("date") or ""
        values = {
            key: value
            for key, value in entry.items()
            if key not in ("measured_at", "date") and value not in (None, "")
        }
        if not values:
            continue
        readings = ", ".join(f"{key} {value}" for key, value in values.items())
        lines.append(f"- {measured_at}: {readings}".strip())
    return "\n".join(lines) if lines else "- keine Messwerte im Zeitraum"


def events_list(events: Sequence[Mapping]) -> str:
    """Ereignisse als Aufzählung."""
    lines = []
    for entry in events or ():
        occurred_at = entry.get("occurred_at") or entry.get("date") or ""
        title = entry.get("title") or ""
        description = entry.get("description") or ""
        detail = f" — {description}" if description else ""
        lines.append(f"- {occurred_at}: {title}{detail}".strip())
    return "\n".join(lines) if lines else "- keine Ereignisse im Zeitraum"


def identify_prompt(kind_label: str, notes: str = "") -> str:
    hint = f"\n\nBeobachtung des Halters: {notes.strip()}" if notes.strip() else ""
    return (
        f"Auf dem Foto ist {kind_label} aus einem Aquarium zu sehen. "
        "Bestimme die Art so genau, wie das Bild es zulässt." + hint
    )


def profile_prompt(kind_label: str, scientific_name: str, common_name: str = "") -> str:
    names = scientific_name.strip()
    if common_name.strip():
        names = f"{names} ({common_name.strip()})"
    return (
        f"Entwirf einen Steckbrief für {kind_label}: {names}. "
        "Fülle nur die Felder, die du belegen kannst."
    )


def measurement_prompt(tank: TankFacts, measurements: Sequence[Mapping]) -> str:
    return "\n".join(
        [
            "Ordne den folgenden Messwertverlauf ein.",
            "",
            tank.as_text(),
            "",
            "Messwerte (älteste zuerst):",
            measurements_table(measurements),
        ]
    )


def stocking_prompt(tank: TankFacts, stock: Sequence[StockItem]) -> str:
    current = [item for item in stock if not item.planned]
    planned = [item for item in stock if item.planned]
    blocks = [
        "Prüfe die folgende Besatzplanung.",
        "",
        tank.as_text(),
        "",
        "Vorhandener Besatz:",
        "\n".join(item.as_text() for item in current) or "- keiner",
    ]
    if planned:
        blocks += ["", "Geplant dazu:", "\n".join(item.as_text() for item in planned)]
    return "\n".join(blocks)


def report_prompt(tank: TankFacts, period: ReportPeriod) -> str:
    return "\n".join(
        [
            "Fasse den folgenden Zeitraum aus dem Beckentagebuch zusammen.",
            "",
            tank.as_text(),
            "",
            period.as_text(),
        ]
    )
