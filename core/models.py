from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model, in place from project start to avoid a painful
    AUTH_USER_MODEL migration later on."""

    email = models.EmailField("email address", unique=True)

    display_name = models.CharField(max_length=100, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True)

    timezone = models.CharField(max_length=50, default="Europe/Berlin")
    notify_email = models.BooleanField(
        "Terminerinnerungen per E-Mail", default=True
    )
    notify_lead_days = models.PositiveSmallIntegerField(
        "Vorlaufzeit für Erinnerungen (Tage)", default=2
    )

    can_edit_catalog = models.BooleanField(default=False)

    def __str__(self):
        return self.get_username()

    def get_display_name(self):
        return self.display_name or self.get_username()
