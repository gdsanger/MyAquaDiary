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
]
