from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model, in place from project start to avoid a painful
    AUTH_USER_MODEL migration later on."""

    email = models.EmailField("email address", unique=True)

    def __str__(self):
        return self.get_username()
