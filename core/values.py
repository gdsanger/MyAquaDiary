"""Wie ein Zahlenwert und ein Zielbereich als Text aussehen.

Gemessene und berechnete Größen teilen sich diese Darstellung: „18,9" ist
dieselbe Zahl, ob sie aus einem Tröpfchentest kommt oder aus KH und pH
gerechnet ist. Stünde die Formatierung zweimal im Code, liefe sie
irgendwann auseinander — und dann steht derselbe Zielbereich an zwei Stellen
des Bildschirms unterschiedlich da.

Erwartet werden ``decimals`` und ``unit`` an der erbenden Klasse: bei
:class:`tanks.models.Parameter` sind das Felder, bei
:class:`tanks.derived.DerivedParameter` Attribute des Datensatzes.
"""


class ValueFormatMixin:
    """Wert und Zielbereich als deutscher Text."""

    def format_value(self, value):
        if value is None:
            return "—"
        return f"{value:.{self.decimals}f}".replace(".", ",")

    def format_range(self, minimum, maximum):
        """Zielbereich als Text, z. B. „6,5–7,5" oder „bis 0,2 mg/l"."""
        unit = f" {self.unit}" if self.unit else ""
        if minimum is None and maximum is None:
            return "—"
        if minimum is not None and maximum is not None:
            return f"{self.format_value(minimum)}–{self.format_value(maximum)}{unit}"
        if minimum is not None:
            return f"ab {self.format_value(minimum)}{unit}"
        return f"bis {self.format_value(maximum)}{unit}"
