from django.contrib import admin

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
