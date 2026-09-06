from django.urls import path

from . import views

app_name = "services"

urlpatterns = [
    path("geraete/", views.device_list, name="device_list"),
    path("geraete/neu/", views.device_add, name="device_add"),
    path("geraete/suchen/", views.device_discover, name="device_discover"),
    path("geraete/steckdose/", views.shelly_add, name="shelly_add"),
    path("geraete/verbrauch/", views.energy_overview, name="energy_overview"),
    path("geraete/<int:pk>/", views.device_detail, name="device_detail"),
    path("geraete/<int:pk>/status/", views.device_status, name="device_status"),
    path("geraete/<int:pk>/bearbeiten/", views.device_edit, name="device_edit"),
    path("geraete/<int:pk>/zugang/", views.device_credentials, name="device_credentials"),
    path("geraete/<int:pk>/steuern/<str:action>/", views.device_control, name="device_control"),
    path("ki/bestimmen/", views.ai_identify, name="ai_identify"),
    path("ki/vorschlaege/", views.ai_suggestion_list, name="ai_suggestion_list"),
    path("ki/vorschlaege/uebernehmen/", views.ai_suggestion_create, name="ai_suggestion_create"),
    path("ki/vorschlaege/<int:pk>/", views.ai_suggestion_detail, name="ai_suggestion_detail"),
    path(
        "ki/vorschlaege/<int:pk>/steckbrief/",
        views.ai_suggestion_profile,
        name="ai_suggestion_profile",
    ),
    path(
        "ki/vorschlaege/<int:pk>/<str:decision>/",
        views.ai_suggestion_decide,
        name="ai_suggestion_decide",
    ),
    path("mcp/zugaenge/", views.mcp_token_list, name="mcp_token_list"),
    path("mcp/zugaenge/<int:pk>/widerrufen/", views.mcp_token_revoke, name="mcp_token_revoke"),
]
