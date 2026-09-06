from django.contrib import admin

from .models import Tank


@admin.register(Tank)
class TankAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "volume_liters", "created_at")
    list_filter = ("owner",)
    search_fields = ("name",)
