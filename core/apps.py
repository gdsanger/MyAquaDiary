from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Registriert das Aufräumen der Bilddateien beim Löschen.
        from . import signals  # noqa: F401
