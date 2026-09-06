from django.conf import settings
from django.db import models


class TankQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(owner=user)


class Tank(models.Model):
    """A user's aquarium. Ownership is exclusive — sharing between users is
    intentionally out of scope for version 1."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tanks",
    )
    name = models.CharField(max_length=100)
    volume_liters = models.PositiveIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TankQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
