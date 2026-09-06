from django.urls import path

from . import views

app_name = "tanks"

urlpatterns = [
    path("", views.TankListView.as_view(), name="list"),
    path("<int:pk>/", views.TankDetailView.as_view(), name="detail"),
]
