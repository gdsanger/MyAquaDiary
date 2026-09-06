"""Beckenübersicht und Beckendetail mit Registerkarten."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from core.views import NavSectionMixin

from . import selectors
from .charts import key_parameter_charts
from .models import Tank

#: Registerkarten des Beckendetails in Anzeigereihenfolge.
TABS = [
    ("uebersicht", "Übersicht"),
    ("messwerte", "Messwerte"),
    ("ereignisse", "Ereignisse"),
    ("besatz", "Besatz"),
    ("pflanzen", "Pflanzen"),
    ("termine", "Termine"),
    ("geraete", "Geräte"),
    ("galerie", "Galerie"),
]
TAB_KEYS = [key for key, _ in TABS]
DEFAULT_TAB = TAB_KEYS[0]

#: Sessionschlüssel für „zuletzt genutztes Becken" auf dem Dashboard.
LAST_TANK_SESSION_KEY = "last_tank_id"

MEASUREMENT_PAGE_SIZE = 50


class TankListView(LoginRequiredMixin, NavSectionMixin, TemplateView):
    """Kartenraster aller Becken, aufgelöste getrennt darunter."""

    template_name = "tanks/tank_list.html"
    nav_section = "tanks"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tanks = Tank.objects.for_user(self.request.user).with_overview()
        context["active_tanks"] = list(tanks.active())
        context["dissolved_tanks"] = list(tanks.dissolved().order_by("-dissolved_on"))
        return context


def _get_tank(request, slug):
    return get_object_or_404(Tank.objects.for_user(request.user), slug=slug)


def tab_context(tank, tab):
    """Daten der gewählten Registerkarte — nur die, die sie wirklich braucht."""
    if tab == "uebersicht":
        return {
            "measurements": selectors.tank_measurement_overview(tank),
            "open_tasks": selectors.open_tasks_for_tank(tank),
            "photo": tank.photos.first(),
        }
    if tab == "messwerte":
        measurements = selectors.tank_measurements(tank, limit=MEASUREMENT_PAGE_SIZE)
        return {
            "measurements": measurements,
            "charts": key_parameter_charts(tank),
            "page_size": MEASUREMENT_PAGE_SIZE,
        }
    if tab == "ereignisse":
        return {"events": tank.events.all()[:100]}
    if tab == "besatz":
        return {"stockings": tank.stockings.select_related("species").prefetch_related("species__images")}
    if tab == "pflanzen":
        return {"plantings": tank.plantings.select_related("species").prefetch_related("species__images")}
    if tab == "termine":
        return {"tasks": tank.tasks.order_by("-is_active", "due_on")}
    if tab == "geraete":
        return {"devices": tank.devices.all()}
    if tab == "galerie":
        return {"photos": tank.photos.all()}
    return {}


def detail_context(request, tank, tab):
    context = {
        "tank": tank,
        "tabs": TABS,
        "active_tab": tab,
        "tab_template": f"tanks/tabs/{tab}.html",
        "nav_section": "tanks",
    }
    context.update(tab_context(tank, tab))
    return context


class TankDetailView(LoginRequiredMixin, TemplateView):
    """Beckendetail. Die Registerkarte kommt aus ``?reiter=`` — so ist jeder
    Reiter auch ohne JavaScript direkt verlinkbar."""

    template_name = "tanks/tank_detail.html"

    def get(self, request, slug, **kwargs):
        tank = _get_tank(request, slug)
        request.session[LAST_TANK_SESSION_KEY] = tank.pk
        tab = request.GET.get("reiter", DEFAULT_TAB)
        if tab not in TAB_KEYS:
            tab = DEFAULT_TAB
        return render(request, self.template_name, detail_context(request, tank, tab))


class TankTabView(LoginRequiredMixin, TemplateView):
    """HTMX-Fragment einer Registerkarte: Reiterleiste plus Inhalt."""

    template_name = "tanks/partials/tab_area.html"

    def get(self, request, slug, tab, **kwargs):
        if tab not in TAB_KEYS:
            raise Http404("Unbekannte Registerkarte")
        tank = _get_tank(request, slug)
        request.session[LAST_TANK_SESSION_KEY] = tank.pk
        return render(request, self.template_name, detail_context(request, tank, tab))
