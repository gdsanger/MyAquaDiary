from django.urls import path

from . import views

app_name = "tanks"

urlpatterns = [
    path("", views.TankListView.as_view(), name="list"),
    path("neu/", views.TankCreateView.as_view(), name="create"),
    path("<slug:slug>/", views.TankDetailView.as_view(), name="detail"),
    path("<slug:slug>/bearbeiten/", views.TankUpdateView.as_view(), name="update"),
    path("<slug:slug>/loeschen/", views.TankDeleteView.as_view(), name="delete"),
    path(
        "<slug:slug>/parameter/<int:parameter_id>/bearbeiten/",
        views.TankParameterTargetEditView.as_view(),
        name="target-edit",
    ),
    path(
        "<slug:slug>/parameter/<int:parameter_id>/",
        views.TankParameterTargetDetailView.as_view(),
        name="target-detail",
    ),
    path(
        "<slug:slug>/messungen/",
        views.MeasurementListView.as_view(),
        name="measurement-list",
    ),
    path(
        "<slug:slug>/messungen/neu/",
        views.MeasurementCreateView.as_view(),
        name="measurement-create",
    ),
    path(
        "<slug:slug>/messungen/<int:pk>/bearbeiten/",
        views.MeasurementUpdateView.as_view(),
        name="measurement-update",
    ),
    path(
        "<slug:slug>/messungen/<int:pk>/loeschen/",
        views.MeasurementDeleteView.as_view(),
        name="measurement-delete",
    ),
    path(
        "<slug:slug>/messungen/export.csv",
        views.MeasurementExportView.as_view(),
        name="measurement-export",
    ),
    path(
        "<slug:slug>/messungen/diagramm-daten/",
        views.MeasurementChartDataView.as_view(),
        name="measurement-chart-data",
    ),
]
