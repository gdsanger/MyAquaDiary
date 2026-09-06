from django.contrib import admin

from .models import (
    Event,
    MaintenanceSchedule,
    Measurement,
    MeasurementValue,
    Parameter,
    Photo,
    Tank,
    TankAnimal,
    TankAnimalMovement,
    TankParameterTarget,
    TankPlant,
)


class PhotoInline(admin.TabularInline):
    model = Photo
    fk_name = "tank"
    extra = 1


class TankParameterTargetInline(admin.TabularInline):
    model = TankParameterTarget
    extra = 0


@admin.register(Tank)
class TankAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "water_type", "started_on", "shut_down_on")
    list_filter = ("owner", "water_type")
    search_fields = ("name", "model_name", "biotope")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [PhotoInline, TankParameterTargetInline]


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ("tank", "measurement", "event", "caption", "taken_on", "position")
    list_filter = ("tank",)


@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "unit", "decimals", "position", "supports_below_detection")
    search_fields = ("name", "key")


@admin.register(TankParameterTarget)
class TankParameterTargetAdmin(admin.ModelAdmin):
    list_display = ("tank", "parameter", "target", "minimum", "maximum")
    list_filter = ("parameter",)


class MeasurementValueInline(admin.TabularInline):
    model = MeasurementValue
    extra = 0


@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ("tank", "measured_at", "source")
    list_filter = ("tank", "source")
    inlines = [MeasurementValueInline]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("tank", "occurred_at", "category", "title", "schedule")
    list_filter = ("tank", "category")
    search_fields = ("title", "description")


@admin.register(MaintenanceSchedule)
class MaintenanceScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "tank",
        "title",
        "event_category",
        "interval",
        "next_due_on",
        "is_active",
    )
    list_filter = ("tank", "event_category", "interval", "is_active")


class TankAnimalMovementInline(admin.TabularInline):
    model = TankAnimalMovement
    extra = 0


@admin.register(TankAnimal)
class TankAnimalAdmin(admin.ModelAdmin):
    list_display = ("tank", "animal", "label", "status", "quantity", "added_on")
    list_filter = ("tank", "status")
    search_fields = ("label", "animal__scientific_name", "animal__common_name")
    inlines = [TankAnimalMovementInline]


@admin.register(TankAnimalMovement)
class TankAnimalMovementAdmin(admin.ModelAdmin):
    list_display = ("tank_animal", "direction", "reason", "quantity", "occurred_on", "target_tank")
    list_filter = ("direction", "reason")


@admin.register(TankPlant)
class TankPlantAdmin(admin.ModelAdmin):
    list_display = ("tank", "plant", "status", "quantity", "placement", "identification_certain")
    list_filter = ("tank", "status", "identification_certain")
    search_fields = ("plant__scientific_name", "plant__common_name", "placement")
