from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Präferenzen",
            {
                "fields": (
                    "display_name",
                    "avatar",
                    "timezone",
                    "notify_email",
                    "notify_lead_days",
                    "can_edit_catalog",
                )
            },
        ),
    )
