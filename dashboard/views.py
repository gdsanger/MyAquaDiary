"""Dashboard: Startseite und die einzeln nachladenden Kacheln.

Jede Kachel hat eine eigene URL und wird per HTMX nachgeladen. Die Startseite
selbst führt keine Datenabfragen aus — eine langsame Kachel (etwa eine
Geräteabfrage) hält so weder die Seite noch die übrigen Kacheln auf.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import TemplateView, View

from core.views import NavSectionMixin
from tanks import selectors
from tanks.charts import key_parameter_charts
from tanks.models import CareTask, Stocking, Tank

#: Anzahl der Einträge, die eine Kachel höchstens zeigt. Darüber verweist sie
#: auf die jeweilige Vollansicht.
TILE_LIMIT = 6

#: Sessionschlüssel des zuletzt geöffneten Beckens (im Beckendetail gesetzt).
LAST_TANK_SESSION_KEY = "last_tank_id"

TASKS_TILE_TEMPLATE = "dashboard/partials/tile_tasks.html"


def tasks_tile_context(user, completed_task=None):
    tasks = selectors.open_tasks(user)
    return {
        "tasks": tasks[:TILE_LIMIT],
        "task_overflow": max(len(tasks) - TILE_LIMIT, 0),
        "completed_task": completed_task,
    }

from services.devices import warnings_for


class IndexView(LoginRequiredMixin, NavSectionMixin, TemplateView):
    """Gerüst des Dashboards — nur Platzhalter, die Inhalte kommen per HTMX."""

    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Die Störungen kommen aus dem zuletzt erfassten Messwert und damit
        # ohne Netzzugriff — das Dashboard bleibt schnell, auch wenn gerade
        # ein Gerät nicht antwortet.
        context["warnings"] = warnings_for(self.request.user)
        return context
    nav_section = "dashboard"


class KpiTileView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/partials/tile_kpi.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tanks = Tank.objects.for_user(self.request.user).active()
        totals = tanks.aggregate(tank_count=Count("pk"), total_volume=Sum("volume_liters"))
        stockings = Stocking.objects.filter(tank__in=tanks, removed_on__isnull=True)
        context.update(
            {
                "tank_count": totals["tank_count"] or 0,
                "total_volume": totals["total_volume"] or 0,
                "animal_count": stockings.aggregate(total=Sum("quantity"))["total"] or 0,
                "species_count": stockings.values("species").distinct().count(),
            }
        )
        return context


class TasksTileView(LoginRequiredMixin, TemplateView):
    template_name = TASKS_TILE_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(tasks_tile_context(self.request.user))
        return context


class WarningsTileView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/partials/tile_warnings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = selectors.warnings(self.request.user)
        context["warnings"] = items[:TILE_LIMIT]
        context["warning_overflow"] = max(len(items) - TILE_LIMIT, 0)
        return context


class ActivityTileView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/partials/tile_activity.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["entries"] = selectors.recent_activity(self.request.user, limit=8)
        return context


class ChartTileView(LoginRequiredMixin, TemplateView):
    """Verlauf der Leitparameter des zuletzt genutzten Beckens."""

    template_name = "dashboard/partials/tile_chart.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tank = selectors.last_used_tank(
            self.request.user, self.request.session.get(LAST_TANK_SESSION_KEY)
        )
        context["tank"] = tank
        context["charts"] = key_parameter_charts(tank)
        return context


class TaskCompleteView(LoginRequiredMixin, View):
    """Termin direkt vom Dashboard quittieren.

    Antwortet mit der aktualisierten Terminkachel, damit HTMX nur diesen
    Ausschnitt tauscht. Ohne HTMX (kein JavaScript) führt der reguläre
    Formular-POST zurück auf das Dashboard.
    """

    def post(self, request, pk):
        task = get_object_or_404(CareTask.objects.for_user(request.user), pk=pk)
        task.complete(user=request.user)

        if not getattr(request, "htmx", False):
            messages.success(request, f"„{task.title}“ quittiert.")
            return HttpResponseRedirect(reverse("dashboard:index"))

        return render(request, TASKS_TILE_TEMPLATE, tasks_tile_context(request.user, task))
