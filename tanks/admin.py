from django.contrib import admin

from .models import Parameter, Photo, Tank, TankParameterTarget


class PhotoInline(admin.TabularInline):
    model = Photo
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
    list_display = ("tank", "caption", "taken_on", "position")
    list_filter = ("tank",)


@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "unit", "decimals", "position", "supports_below_detection")
    search_fields = ("name", "key")


@admin.register(TankParameterTarget)
class TankParameterTargetAdmin(admin.ModelAdmin):
    list_display = ("tank", "parameter", "target", "minimum", "maximum")
    list_filter = ("parameter",)
