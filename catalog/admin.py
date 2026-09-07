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
    list_display = [
        "scientific_name", "variant", "common_name", "placement", "difficulty", "water_type"
    ]
    list_filter = [
        "water_type", "difficulty", "is_cultivated_form", "placement", "growth_rate",
        "light_demand",
    ]
    search_fields = ["scientific_name", "variant", "common_name"]
    # Der Slug nimmt die Sorte mit auf, sonst kollidieren Stamm- und Zuchtform.
    prepopulated_fields = {"slug": ["scientific_name", "variant"]}
    inlines = [PlantImageInline]


@admin.register(AnimalSpecies)
class AnimalSpeciesAdmin(admin.ModelAdmin):
    list_display = [
        "scientific_name", "variant", "common_name", "category", "min_group_size", "water_type"
    ]
    list_filter = ["water_type", "difficulty", "is_cultivated_form", "category", "temperament"]
    search_fields = ["scientific_name", "variant", "common_name"]
    prepopulated_fields = {"slug": ["scientific_name", "variant"]}
    inlines = [AnimalImageInline]
