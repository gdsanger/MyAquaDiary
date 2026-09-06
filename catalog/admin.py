from django.contrib import admin

from .models import CatalogAnimal, CatalogAnimalImage, CatalogPlant, CatalogPlantImage


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


class CatalogPlantImageInline(admin.TabularInline):
    model = CatalogPlantImage
    extra = 1


@admin.register(CatalogPlant)
class CatalogPlantAdmin(admin.ModelAdmin):
    list_display = ("__str__", "common_name", "placement", "difficulty", "light_demand", "verified")
    list_filter = ("placement", "difficulty", "growth_form", "light_demand", "co2_demand", "verified")
    search_fields = ("scientific_name", "cultivar", "common_name", "family")
    prepopulated_fields = {"slug": ("scientific_name", "cultivar")}
    inlines = [CatalogPlantImageInline]


@admin.register(CatalogPlantImage)
class CatalogPlantImageAdmin(admin.ModelAdmin):
    list_display = ("plant", "caption", "is_primary", "position")
    list_filter = ("is_primary",)
from .models import AnimalImage, AnimalSpecies, PlantImage, PlantSpecies


class PlantImageInline(admin.TabularInline):
    model = PlantImage
    extra = 1


class AnimalImageInline(admin.TabularInline):
    model = AnimalImage
    extra = 1


@admin.register(PlantSpecies)
class PlantSpeciesAdmin(admin.ModelAdmin):
    list_display = ["scientific_name", "common_name", "placement", "difficulty", "water_type"]
    list_filter = ["water_type", "difficulty", "placement", "growth_rate", "light_demand"]
    search_fields = ["scientific_name", "common_name"]
    prepopulated_fields = {"slug": ["scientific_name"]}
    inlines = [PlantImageInline]


@admin.register(AnimalSpecies)
class AnimalSpeciesAdmin(admin.ModelAdmin):
    list_display = ["scientific_name", "common_name", "category", "min_group_size", "water_type"]
    list_filter = ["water_type", "difficulty", "category", "temperament"]
    search_fields = ["scientific_name", "common_name"]
    prepopulated_fields = {"slug": ["scientific_name"]}
    inlines = [AnimalImageInline]
