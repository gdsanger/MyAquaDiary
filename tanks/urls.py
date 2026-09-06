from django.urls import path

from . import views

app_name = "tanks"

urlpatterns = [
    path("", views.TankListView.as_view(), name="list"),
    path("<slug:slug>/", views.TankDetailView.as_view(), name="detail"),
    path("<slug:slug>/reiter/<slug:tab>/", views.TankTabView.as_view(), name="tab"),
]
