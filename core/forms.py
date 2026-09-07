"""Formularbausteine, die sich mehrere Apps teilen.

Hier steht nur, was ohne Modellbezug auskommt: das Bootstrap-Markup, die
Eingabefelder für Datum und Zeitpunkt und der Mehrfach-Upload. Die Formulare
selbst liegen bei ihrer App.
"""

from django import forms


class BootstrapMixin:
    """Setzt die Bootstrap-Klassen auf allen Widgets.

    Die Anwendung nutzt Bootstrap 5.3 ohne crispy-forms; die Klassen einmal
    hier zu setzen ist weniger Wiederholung als in jedem Template.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")


class DateInput(forms.DateInput):
    """``<input type="date">`` — auf dem Telefon der native Datumswähler."""

    input_type = "date"

    def __init__(self, attrs=None, format=None):  # noqa: A002 — Django-Signatur
        super().__init__(attrs=attrs, format=format or "%Y-%m-%d")


class DateTimeInput(forms.DateTimeInput):
    """``<input type="datetime-local">`` für Zeitpunkte."""

    input_type = "datetime-local"

    def __init__(self, attrs=None, format=None):  # noqa: A002 — Django-Signatur
        super().__init__(attrs=attrs, format=format or "%Y-%m-%dT%H:%M")


class DateField(forms.DateField):
    """Datumsfeld mit nativem Datumswähler.

    Das Widget liefert ISO-Datum zurück; die deutschen Eingabeformate bleiben
    zusätzlich zulässig, damit ein ohne JavaScript getipptes „01.05.2026" nicht
    abgewiesen wird.
    """

    widget = DateInput

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", ["%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"])
        super().__init__(**kwargs)


class DateTimeField(forms.DateTimeField):
    """Zeitpunktfeld mit nativem Datums- und Uhrzeitwähler.

    ``datetime-local`` schickt ``2026-05-01T18:30`` — ein Format, das in den
    deutschen Standardformaten nicht vorkommt und deshalb hier ausdrücklich
    stehen muss.
    """

    widget = DateTimeInput

    def __init__(self, **kwargs):
        kwargs.setdefault(
            "input_formats",
            [
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%d.%m.%Y %H:%M",
            ],
        )
        super().__init__(**kwargs)


class MultipleFileInput(forms.ClearableFileInput):
    """Dateiauswahl, die mehrere Dateien annimmt."""

    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    """Mehrere Bilder in einem Feld.

    Django prüft je Feld genau eine Datei. Für den Mehrfach-Upload wird
    deshalb jede ausgewählte Datei einzeln durch die Bildprüfung geschickt;
    zurück kommt eine Liste. Ein defektes Bild in der Auswahl bricht den
    ganzen Upload ab — lieber gar nichts anlegen als die halbe Auswahl.

    Auch ohne Auswahl kommt eine Liste zurück (dann eine leere): ein optionales
    Bildfeld — etwa am Ereignisformular — soll den Aufrufer nicht zwingen,
    ``None`` und „nichts ausgewählt“ zu unterscheiden.
    """

    widget = MultipleFileInput

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auf dem Telefon öffnet das die Auswahl zwischen Kamera und Galerie.
        # Bewusst ohne ``capture``: das erzwingt die Kamera und schließt damit
        # die Mehrfachauswahl aus.
        self.widget.attrs.setdefault("accept", "image/*")

    def clean(self, data, initial=None):
        check = super().clean
        if not isinstance(data, (list, tuple)):
            data = [] if data in self.empty_values else [data]
        if not data:
            # Ohne Auswahl entscheidet die reguläre Pflichtfeldprüfung.
            check(None, initial)
            return []
        return [check(item, initial) for item in data]
