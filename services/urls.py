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
    path("geraete/<int:pk>/einlagern/", views.device_store, name="device_store"),
    path("geraete/<int:pk>/einbauen/", views.device_install, name="device_install"),
    path("geraete/<int:pk>/zugang/", views.device_credentials, name="device_credentials"),
    path("geraete/<int:pk>/steuern/<str:action>/", views.device_control, name="device_control"),
    # Titelbild — ein Abschnitt der Detailseite wie die folgenden, nur ohne
    # eigenen Datensatz: es ist ein Feld des Geräts.
    path("geraete/<int:pk>/titelbild/", views.device_cover, name="device_cover"),
    path(
        "geraete/<int:pk>/titelbild/aendern/",
        views.device_cover_edit,
        name="device_cover_edit",
    ),
    path(
        "geraete/<int:pk>/titelbild/entfernen/",
        views.device_cover_delete,
        name="device_cover_delete",
    ),
    # Technische Daten, Dokumente und Links — Abschnitte der Detailseite.
    path(
        "geraete/<int:pk>/technik/",
        views.device_section,
        {"section": "specs"},
        name="device_specs",
    ),
    path("geraete/<int:pk>/technik/neu/", views.device_spec_create, name="device_spec_create"),
    path(
        "geraete/<int:pk>/technik/<int:spec_pk>/bearbeiten/",
        views.device_spec_update,
        name="device_spec_update",
    ),
    path(
        "geraete/<int:pk>/technik/<int:spec_pk>/loeschen/",
        views.device_spec_delete,
        name="device_spec_delete",
    ),
    path(
        "geraete/<int:pk>/dokumente/",
        views.device_section,
        {"section": "documents"},
        name="device_documents",
    ),
    path(
        "geraete/<int:pk>/dokumente/neu/",
        views.device_document_create,
        name="device_document_create",
    ),
    path(
        "geraete/<int:pk>/dokumente/<int:document_pk>/",
        views.device_document,
        name="device_document",
    ),
    path(
        "geraete/<int:pk>/dokumente/<int:document_pk>/bearbeiten/",
        views.device_document_update,
        name="device_document_update",
    ),
    path(
        "geraete/<int:pk>/dokumente/<int:document_pk>/loeschen/",
        views.device_document_delete,
        name="device_document_delete",
    ),
    path(
        "geraete/<int:pk>/links/",
        views.device_section,
        {"section": "links"},
        name="device_links",
    ),
    path("geraete/<int:pk>/links/neu/", views.device_link_create, name="device_link_create"),
    path(
        "geraete/<int:pk>/links/<int:link_pk>/bearbeiten/",
        views.device_link_update,
        name="device_link_update",
    ),
    path(
        "geraete/<int:pk>/links/<int:link_pk>/loeschen/",
        views.device_link_delete,
        name="device_link_delete",
    ),
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
