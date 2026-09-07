from django.urls import path

from . import views

app_name = "tanks"

urlpatterns = [
    path("", views.TankListView.as_view(), name="list"),
    # Feste Pfade stehen vor der Slug-Route, sonst schluckt der Slug-Matcher sie.
    path("neu/", views.TankCreateView.as_view(), name="create"),
    path("<slug:slug>/", views.TankDetailView.as_view(), name="detail"),
    path("<slug:slug>/bearbeiten/", views.TankUpdateView.as_view(), name="update"),
    path("<slug:slug>/aufloesen/", views.TankDissolveView.as_view(), name="dissolve"),
    path("<slug:slug>/loeschen/", views.TankDeleteView.as_view(), name="delete"),
    path("<slug:slug>/reiter/<slug:tab>/", views.TankTabView.as_view(), name="tab"),
    # Messwerte
    path(
        "<slug:slug>/messwerte/neu/",
        views.MeasurementCreateView.as_view(),
        name="measurement-create",
    ),
    path(
        "<slug:slug>/messwerte/<int:pk>/bearbeiten/",
        views.MeasurementUpdateView.as_view(),
        name="measurement-update",
    ),
    path(
        "<slug:slug>/messwerte/<int:pk>/loeschen/",
        views.MeasurementDeleteView.as_view(),
        name="measurement-delete",
    ),
    # Ereignisse
    path("<slug:slug>/ereignisse/neu/", views.EventCreateView.as_view(), name="event-create"),
    path(
        "<slug:slug>/ereignisse/beobachtung/",
        views.EventObservationCreateView.as_view(),
        name="observation-create",
    ),
    path(
        "<slug:slug>/ereignisse/<int:pk>/bearbeiten/",
        views.EventUpdateView.as_view(),
        name="event-update",
    ),
    path(
        "<slug:slug>/ereignisse/<int:pk>/loeschen/",
        views.EventDeleteView.as_view(),
        name="event-delete",
    ),
    # Besatz
    path("<slug:slug>/besatz/neu/", views.StockingCreateView.as_view(), name="stocking-create"),
    path(
        "<slug:slug>/besatz/<int:pk>/bearbeiten/",
        views.StockingUpdateView.as_view(),
        name="stocking-update",
    ),
    path(
        "<slug:slug>/besatz/<int:pk>/abgang/",
        views.StockingRemoveView.as_view(),
        name="stocking-remove",
    ),
    # Bepflanzung
    path("<slug:slug>/pflanzen/neu/", views.PlantingCreateView.as_view(), name="planting-create"),
    path(
        "<slug:slug>/pflanzen/<int:pk>/bearbeiten/",
        views.PlantingUpdateView.as_view(),
        name="planting-update",
    ),
    path(
        "<slug:slug>/pflanzen/<int:pk>/loeschen/",
        views.PlantingDeleteView.as_view(),
        name="planting-delete",
    ),
    # Einrichtung: Bodengrund
    path(
        "<slug:slug>/bodengrund/neu/",
        views.SubstrateLayerCreateView.as_view(),
        name="substrate-create",
    ),
    path(
        "<slug:slug>/bodengrund/<int:pk>/bearbeiten/",
        views.SubstrateLayerUpdateView.as_view(),
        name="substrate-update",
    ),
    path(
        "<slug:slug>/bodengrund/<int:pk>/verschieben/",
        views.SubstrateLayerMoveView.as_view(),
        name="substrate-move",
    ),
    path(
        "<slug:slug>/bodengrund/<int:pk>/erinnerung/",
        views.SubstrateReminderView.as_view(),
        name="substrate-reminder",
    ),
    path(
        "<slug:slug>/bodengrund/<int:pk>/loeschen/",
        views.SubstrateLayerDeleteView.as_view(),
        name="substrate-delete",
    ),
    # Einrichtung: Hardscape
    path("<slug:slug>/hardscape/neu/", views.HardscapeCreateView.as_view(), name="hardscape-create"),
    path(
        "<slug:slug>/hardscape/<int:pk>/bearbeiten/",
        views.HardscapeUpdateView.as_view(),
        name="hardscape-update",
    ),
    path(
        "<slug:slug>/hardscape/<int:pk>/entfernt/",
        views.HardscapeRemoveView.as_view(),
        name="hardscape-remove",
    ),
    path(
        "<slug:slug>/hardscape/<int:pk>/erinnerung/",
        views.HardscapeReminderView.as_view(),
        name="hardscape-reminder",
    ),
    # Termine
    path("<slug:slug>/termine/neu/", views.CareTaskCreateView.as_view(), name="task-create"),
    path(
        "<slug:slug>/termine/<int:pk>/bearbeiten/",
        views.CareTaskUpdateView.as_view(),
        name="task-update",
    ),
    path(
        "<slug:slug>/termine/<int:pk>/aktiv/",
        views.CareTaskToggleView.as_view(),
        name="task-toggle",
    ),
    # Zielbereiche
    path(
        "<slug:slug>/zielbereiche/neu/",
        views.TargetCreateView.as_view(),
        name="target-create",
    ),
    path(
        "<slug:slug>/zielbereiche/<int:pk>/bearbeiten/",
        views.TargetUpdateView.as_view(),
        name="target-update",
    ),
    path(
        "<slug:slug>/zielbereiche/<int:pk>/loeschen/",
        views.TargetDeleteView.as_view(),
        name="target-delete",
    ),
    # Zielbereiche berechneter Größen: über den Schlüssel, nicht über eine
    # Kennung — den Datensatz gibt es erst, wenn die Vorgabe überschrieben wird.
    path(
        "<slug:slug>/zielbereiche/berechnet/<slug:key>/",
        views.DerivedTargetUpdateView.as_view(),
        name="derived-target-update",
    ),
    path(
        "<slug:slug>/zielbereiche/berechnet/<slug:key>/zuruecksetzen/",
        views.DerivedTargetResetView.as_view(),
        name="derived-target-reset",
    ),
    # Fotos
    path("<slug:slug>/fotos/neu/", views.PhotoCreateView.as_view(), name="photo-create"),
    path("<slug:slug>/fotos/<int:pk>/", views.PhotoDetailView.as_view(), name="photo-detail"),
    path(
        "<slug:slug>/fotos/<int:pk>/bearbeiten/",
        views.PhotoUpdateView.as_view(),
        name="photo-update",
    ),
    path(
        "<slug:slug>/fotos/<int:pk>/loeschen/",
        views.PhotoDeleteView.as_view(),
        name="photo-delete",
    ),
]
