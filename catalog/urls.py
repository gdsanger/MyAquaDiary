from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("tiere/", views.CatalogAnimalListView.as_view(), name="animal-list"),
    path("tiere/neu/", views.CatalogAnimalCreateView.as_view(), name="animal-create"),
    path("tiere/<slug:slug>/", views.CatalogAnimalDetailView.as_view(), name="animal-detail"),
    path(
        "tiere/<slug:slug>/bearbeiten/",
        views.CatalogAnimalUpdateView.as_view(),
        name="animal-update",
    ),
]
