from django.contrib import admin

from .models import CatalogAnimal, CatalogAnimalImage


class CatalogAnimalImageInline(admin.TabularInline):
    model = CatalogAnimalImage
    extra = 1


@admin.register(CatalogAnimal)
class CatalogAnimalAdmin(admin.ModelAdmin):
    list_display = ("__str__", "common_name", "group", "zone", "difficulty", "min_tank_liters", "verified")
    list_filter = ("group", "zone", "difficulty", "diet", "is_line_bred_variant", "verified")
    search_fields = ("scientific_name", "variety", "common_name", "family")
    prepopulated_fields = {"slug": ("scientific_name", "variety")}
    inlines = [CatalogAnimalImageInline]


@admin.register(CatalogAnimalImage)
class CatalogAnimalImageAdmin(admin.ModelAdmin):
    list_display = ("animal", "caption", "sex", "is_primary", "position")
    list_filter = ("is_primary", "sex")
