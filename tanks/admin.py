from django.contrib import admin

from .models import (
    CareTask,
    Event,
    Measurement,
    Parameter,
    Planting,
    Stocking,
    Tank,
    TankParameterTarget,
    TankPhoto,
    TaskCompletion,
)


class TankParameterTargetInline(admin.TabularInline):
    model = TankParameterTarget
    extra = 1


class TankPhotoInline(admin.TabularInline):
    model = TankPhoto
    extra = 1


@admin.register(Tank)
class TankAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "volume_liters", "water_type", "setup_date", "dissolved_on"]
    list_filter = ["water_type", "owner"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [TankParameterTargetInline, TankPhotoInline]


@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "unit", "default_min", "default_max", "is_key_parameter", "sort_order"]
    list_editable = ["is_key_parameter", "sort_order"]
    prepopulated_fields = {"key": ["name"]}


@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ["tank", "parameter", "value", "measured_at"]
    list_filter = ["tank", "parameter"]
    date_hierarchy = "measured_at"


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "tank", "category", "occurred_at"]
    list_filter = ["tank", "category"]


@admin.register(Stocking)
class StockingAdmin(admin.ModelAdmin):
    list_display = ["species", "tank", "quantity", "added_on", "removed_on"]
    list_filter = ["tank"]


@admin.register(Planting)
class PlantingAdmin(admin.ModelAdmin):
    list_display = ["species", "tank", "quantity", "planted_on", "removed_on"]
    list_filter = ["tank"]


class TaskCompletionInline(admin.TabularInline):
    model = TaskCompletion
    extra = 0


@admin.register(CareTask)
class CareTaskAdmin(admin.ModelAdmin):
    list_display = ["title", "tank", "category", "due_on", "interval_days", "is_active"]
    list_filter = ["tank", "category", "is_active"]
    inlines = [TaskCompletionInline]


@admin.register(TankPhoto)
class TankPhotoAdmin(admin.ModelAdmin):
    list_display = ["tank", "taken_on", "caption"]
    list_filter = ["tank"]
