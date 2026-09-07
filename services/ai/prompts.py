"""System- und Benutzerprompts der KI-Assistenz.

Die Leitplanken stehen im System-Prompt, nicht in der Oberfläche: jeder
Vorschlag ist ein Vorschlag, Messwerte werden eingeordnet und nicht
diagnostiziert, und die Verantwortung für die Tiere bleibt beim Halter. Ein
Modell, das das im Prompt liest, formuliert von sich aus vorsichtiger — das
ist wirksamer als ein Hinweis, den man hinterher unter die Antwort setzt.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

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

SERIES_SYSTEM = f"""\
{BASE_SYSTEM}

Für diese Aufgabe ordnest du eine frisch erfasste Messreihe im Zusammenhang \
ein — so, wie es ein erfahrener Aquarianer im Gespräch täte.

Woran du dich hältst:
- Du siehst den Verlauf, nicht nur den Einzelwert: was hat sich verändert, in \
welche Richtung, seit wann.
- Du stellst Zusammenhänge her. Welche Veränderung erklärt sich aus welchem \
Ereignis? Ein KH-Anstieg nach „Osmosewasser nachgefüllt" heißt etwas anderes \
als einer ohne.
- Du benennst die rechnerischen Kopplungen, wo sie tragen: CO₂ folgt aus KH \
und pH; fällt die KH bei gleichem CO₂, fällt der pH mit; bei Verdunstung \
laufen Leitfähigkeit und Härte gemeinsam nach oben.
- Du gleichst gegen die Zielbereiche des Beckens ab und gegen die Ansprüche \
des Besatzes. Ob 27 °C in Ordnung sind, hängt daran, welche Arten im Becken \
sind; was an Tag 3 nach dem Einrichten normal ist, ist es an Tag 300 nicht.
- Du sagst, wo etwas zu tun ist — und sagst genauso deutlich, wenn nichts zu \
tun ist. „Alles im Rahmen, weitermessen" ist eine vollständige Antwort.
- Du stellst keine Diagnose und behandelst nicht. Einordnen und Hinweise \
geben, mehr nicht.
- Du beruhigst nicht um jeden Preis. Ist ein Wert für die Tiere gefährlich, \
sagst du das zuerst und ohne Weichzeichner — sachlich, ohne Dramatik.
- Du erfindest nichts. Was nicht in den Daten steht, kommt in der Auswertung \
nicht vor; fehlt dir etwas, benennst du die Lücke.

Antworte als Markdown ohne Überschrift: ein kurzer Absatz zur Lage, dann \
Stichpunkte zu den einzelnen Werten und Zusammenhängen, zuletzt — falls nötig \
— ein Satz dazu, worauf zu achten ist."""

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
    #: Alter seit der Einrichtung, ausgeschrieben („14 Monate"). Bleibt aus der
    #: Prüfsumme heraus: es ändert sich täglich, die Datenlage nicht.
    age: str = ""
    technic: str = ""
    targets: str = ""
    notes: str = ""

    def as_text(self) -> str:
        rows = [
            ("Becken", self.name),
            ("Volumen", f"{self.volume_liters:g} l" if self.volume_liters else ""),
            ("Kantenlänge", f"{self.length_cm} cm" if self.length_cm else ""),
            ("Wasser", self.water_type),
            ("In Betrieb seit", self.started_on),
            ("Alter", self.age),
            ("Technik", self.technic),
            ("Zielbereiche", self.targets),
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


@dataclass(frozen=True)
class Reading:
    """Ein einzelner Messwert samt Zielabgleich.

    Werte und Zielbereiche kommen fertig formatiert an: das Runden gehört
    zum Parameter (er kennt seine Nachkommastellen), nicht in den Prompt.
    """

    name: str
    value: str
    unit: str = ""
    target: str = ""
    status: str = ""

    def as_text(self) -> str:
        amount = f"{self.value} {self.unit}".strip()
        remarks = [text for text in (f"Ziel {self.target}" if self.target else "", self.status) if text]
        suffix = f" ({', '.join(remarks)})" if remarks else ""
        return f"- {self.name}: {amount}{suffix}"


@dataclass(frozen=True)
class Series:
    """Eine Messreihe — alles, was an einem Becken zu einem Zeitpunkt gemessen wurde."""

    measured_at: str
    readings: Sequence[Reading] = field(default_factory=tuple)
    #: CO₂ in mg/l, aus KH und pH gerechnet. Leer, wenn eines von beidem fehlt.
    co2: str = ""
    note: str = ""

    def as_block(self) -> str:
        """Ausführlich, für die Messreihe, um die es geht."""
        lines = [f"Gemessen am {self.measured_at}:"]
        lines += [reading.as_text() for reading in self.readings] or ["- keine Werte"]
        if self.co2:
            lines.append(f"- CO₂ (aus KH und pH gerechnet): {self.co2} mg/l")
        if self.note:
            lines.append(f"- Notiz des Halters: {self.note}")
        return "\n".join(lines)

    def as_line(self) -> str:
        """Knapp, für den Verlauf — eine Zeile je Reihe."""
        values = "; ".join(
            f"{reading.name} {reading.value} {reading.unit}".strip() for reading in self.readings
        )
        co2 = f"; CO₂ {self.co2} mg/l" if self.co2 else ""
        return f"- {self.measured_at}: {values or 'keine Werte'}{co2}"


@dataclass(frozen=True)
class DiaryEvent:
    """Ein Ereignis aus dem Beckentagebuch."""

    occurred_at: str
    title: str
    category: str = ""
    description: str = ""

    def as_text(self) -> str:
        category = f" [{self.category}]" if self.category else ""
        detail = f" — {self.description}" if self.description else ""
        return f"- {self.occurred_at}{category}: {self.title}{detail}"


@dataclass(frozen=True)
class MeasurementContext:
    """Alles, was die Auswertung einer Messreihe braucht.

    Die Punkte 2 bis 5 sind nicht Beiwerk: ohne Verlauf, Ereignisse,
    Stammdaten und Besatz kann ein Modell nur allgemeine Sätze bilden, die
    überall und nirgends gelten.
    """

    tank: TankFacts
    current: Series
    history: Sequence[Series] = field(default_factory=tuple)
    events: Sequence[DiaryEvent] = field(default_factory=tuple)
    stock: Sequence[StockItem] = field(default_factory=tuple)

    def as_text(self) -> str:
        return "\n".join(
            [
                "Beckenstammdaten:",
                self.tank.as_text(),
                "",
                "Aktuelle Messreihe:",
                self.current.as_block(),
                "",
                "Messreihen der letzten Tage (älteste zuerst):",
                _lines(series.as_line() for series in self.history) or "- keine weiteren Messreihen",
                "",
                "Letzte Ereignisse (älteste zuerst):",
                _lines(event.as_text() for event in self.events) or "- keine Ereignisse erfasst",
                "",
                "Besatz und Bestand mit den Ansprüchen aus dem Katalog:",
                _lines(item.as_text() for item in self.stock) or "- nichts erfasst",
            ]
        )

    def fingerprint(self) -> str:
        """Prüfsumme über die Datenlage.

        Stimmt sie noch, ist eine vorhandene Auswertung die Antwort auf
        dieselbe Frage. Das Alter des Beckens bleibt außen vor — es wächst
        täglich, ohne dass sich an den Daten etwas geändert hätte.
        """
        payload = asdict(self)
        payload["tank"].pop("age", None)
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _lines(parts) -> str:
    return "\n".join(part for part in parts if part)


def series_prompt(context: MeasurementContext) -> str:
    return "\n".join(
        [
            "Ordne die folgende Messreihe im Zusammenhang ein.",
            "",
            context.as_text(),
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
