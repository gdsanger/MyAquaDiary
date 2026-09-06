from django.urls import path

from . import views

app_name = "services"

urlpatterns = [
    path("geraete/", views.device_list, name="device_list"),
    path("geraete/suchen/", views.device_discover, name="device_discover"),
    path("geraete/steckdose/", views.shelly_add, name="shelly_add"),
    path("geraete/verbrauch/", views.energy_overview, name="energy_overview"),
    path("geraete/<int:pk>/", views.device_detail, name="device_detail"),
    path("geraete/<int:pk>/status/", views.device_status, name="device_status"),
    path("geraete/<int:pk>/bearbeiten/", views.device_edit, name="device_edit"),
    path("geraete/<int:pk>/zugang/", views.device_credentials, name="device_credentials"),
    path("geraete/<int:pk>/steuern/<str:action>/", views.device_control, name="device_control"),
]
