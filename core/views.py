"""Gemeinsame View-Bausteine."""


class NavSectionMixin:
    """Markiert den aktiven Punkt in der Hauptnavigation.

    Die Zuordnung über einen expliziten Namen statt über ``resolver_match``
    ist auch für HTMX-Fragmente korrekt, die unter eigenen URLs liegen.
    """

    nav_section = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("nav_section", self.nav_section)
        return context
