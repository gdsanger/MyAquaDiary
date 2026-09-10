from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    # Die Raster-Fragmente und „neu" stehen vor den Slug-Routen, sonst
    # schluckt der Slug-Matcher die Pfade.
    path("pflanzen/", views.PlantListView.as_view(), name="plant-list"),
    path("pflanzen/raster/", views.PlantGridView.as_view(), name="plant-grid"),
    path("pflanzen/neu/", views.PlantSpeciesFormView.as_view(), name="plant-create"),
    path("pflanzen/<slug:slug>/", views.PlantDetailView.as_view(), name="plant-detail"),
    path(
        "pflanzen/<slug:slug>/bearbeiten/",
        views.PlantSpeciesFormView.as_view(),
        name="plant-update",
    ),
    path(
        "pflanzen/<slug:slug>/loeschen/",
        views.PlantSpeciesDeleteView.as_view(),
        name="plant-delete",
    ),
    path(
        "pflanzen/<slug:slug>/bilder/",
        views.PlantImageUploadView.as_view(),
        name="plant-image-upload",
    ),
    path(
        "pflanzen/<slug:slug>/bilder/<int:pk>/primaer/",
        views.PlantImagePrimaryView.as_view(),
        name="plant-image-primary",
    ),
    path(
        "pflanzen/<slug:slug>/bilder/<int:pk>/loeschen/",
        views.PlantImageDeleteView.as_view(),
        name="plant-image-delete",
    ),
    path("pflanzen/<slug:slug>/quellen/", views.PlantLinkView.as_view(), name="plant-links"),
    path(
        "pflanzen/<slug:slug>/quellen/neu/",
        views.PlantLinkFormView.as_view(),
        name="plant-link-create",
    ),
    path(
        "pflanzen/<slug:slug>/quellen/<int:pk>/bearbeiten/",
        views.PlantLinkFormView.as_view(),
        name="plant-link-update",
    ),
    path(
        "pflanzen/<slug:slug>/quellen/<int:pk>/loeschen/",
        views.PlantLinkDeleteView.as_view(),
        name="plant-link-delete",
    ),
    path("tiere/", views.AnimalListView.as_view(), name="animal-list"),
    path("tiere/raster/", views.AnimalGridView.as_view(), name="animal-grid"),
    path("tiere/neu/", views.AnimalSpeciesFormView.as_view(), name="animal-create"),
    path("tiere/<slug:slug>/", views.AnimalDetailView.as_view(), name="animal-detail"),
    path(
        "tiere/<slug:slug>/bearbeiten/",
        views.AnimalSpeciesFormView.as_view(),
        name="animal-update",
    ),
    path(
        "tiere/<slug:slug>/loeschen/",
        views.AnimalSpeciesDeleteView.as_view(),
        name="animal-delete",
    ),
    path(
        "tiere/<slug:slug>/bilder/",
        views.AnimalImageUploadView.as_view(),
        name="animal-image-upload",
    ),
    path(
        "tiere/<slug:slug>/bilder/<int:pk>/primaer/",
        views.AnimalImagePrimaryView.as_view(),
        name="animal-image-primary",
    ),
    path(
        "tiere/<slug:slug>/bilder/<int:pk>/loeschen/",
        views.AnimalImageDeleteView.as_view(),
        name="animal-image-delete",
    ),
    path("tiere/<slug:slug>/quellen/", views.AnimalLinkView.as_view(), name="animal-links"),
    path(
        "tiere/<slug:slug>/quellen/neu/",
        views.AnimalLinkFormView.as_view(),
        name="animal-link-create",
    ),
    path(
        "tiere/<slug:slug>/quellen/<int:pk>/bearbeiten/",
        views.AnimalLinkFormView.as_view(),
        name="animal-link-update",
    ),
    path(
        "tiere/<slug:slug>/quellen/<int:pk>/loeschen/",
        views.AnimalLinkDeleteView.as_view(),
        name="animal-link-delete",
    ),
]
