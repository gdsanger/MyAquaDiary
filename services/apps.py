from django.apps import AppConfig


class ServicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services"
    verbose_name = "Services"

    def ready(self):
        # Registriert das Aufräumen der Gerätedokumente beim Löschen.
        from . import signals  # noqa: F401
