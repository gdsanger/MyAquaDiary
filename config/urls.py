"""URL configuration for the MyAquaDiary project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("becken/", include("tanks.urls")),
    path("katalog/", include("catalog.urls")),
    path("", include("dashboard.urls")),
]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns += [path("__debug__/", include(debug_toolbar.urls))]
    # Titelbilder und Galeriefotos in der Entwicklung direkt ausliefern.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
