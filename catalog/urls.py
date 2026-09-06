from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    # Die Raster-Fragmente stehen vor den Slug-Routen, sonst schluckt der
    # Slug-Matcher die Pfade.
    path("pflanzen/", views.PlantListView.as_view(), name="plant-list"),
    path("pflanzen/raster/", views.PlantGridView.as_view(), name="plant-grid"),
    path("pflanzen/<slug:slug>/", views.PlantDetailView.as_view(), name="plant-detail"),
    path("tiere/", views.AnimalListView.as_view(), name="animal-list"),
    path("tiere/raster/", views.AnimalGridView.as_view(), name="animal-grid"),
    path("tiere/<slug:slug>/", views.AnimalDetailView.as_view(), name="animal-detail"),
]
