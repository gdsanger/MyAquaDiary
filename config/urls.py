"""URL configuration for the MyAquaDiary project."""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("services.urls")),
    path("becken/", include("tanks.urls")),
    path("katalog/", include("catalog.urls")),
    path("", include("dashboard.urls")),
]

# Titelbilder und Galeriefotos ausliefern. Provisorium: NPM kann kein alias,
# deshalb übernimmt das vorerst Django. Ersetzen, sobald ein eigener
# Static-Server davor steht.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns += [path("__debug__/", include(debug_toolbar.urls))]