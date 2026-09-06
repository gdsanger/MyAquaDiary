from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    # Kachel-Fragmente — jede lädt eigenständig per HTMX.
    path("kacheln/kennzahlen/", views.KpiTileView.as_view(), name="tile-kpi"),
    path("kacheln/termine/", views.TasksTileView.as_view(), name="tile-tasks"),
    path("kacheln/warnungen/", views.WarningsTileView.as_view(), name="tile-warnings"),
    path("kacheln/aktivitaet/", views.ActivityTileView.as_view(), name="tile-activity"),
    path("kacheln/verlauf/", views.ChartTileView.as_view(), name="tile-chart"),
    path("termine/<int:pk>/quittieren/", views.TaskCompleteView.as_view(), name="task-complete"),
]
