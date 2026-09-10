from django.contrib import admin

from .models import AnimalImage, AnimalSpecies, PlantImage, PlantSpecies, SpeciesLink


class PlantImageInline(admin.TabularInline):
    model = PlantImage
    extra = 1


class AnimalImageInline(admin.TabularInline):
    model = AnimalImage
    extra = 1


class SpeciesLinkInline(admin.TabularInline):
    """Quellenlinks am Steckbrief.

    ``fk_name`` steht in den Erben: das Modell hat zwei Fremdschlüssel in den
    Katalog, und der Admin kann nicht raten, welcher gemeint ist.
    """

    model = SpeciesLink
    extra = 1
    fields = ["kind", "title", "url", "position"]


class PlantLinkInline(SpeciesLinkInline):
    fk_name = "plant"


class AnimalLinkInline(SpeciesLinkInline):
    fk_name = "animal"


@admin.register(PlantSpecies)
class PlantSpeciesAdmin(admin.ModelAdmin):
    list_display = [
        "scientific_name", "variant", "common_name", "placement", "difficulty", "water_type"
    ]
    list_filter = [
        "water_type", "difficulty", "is_cultivated_form", "placement", "growth_rate",
        "light_demand", "origin_region", "growth_form_water",
    ]
    search_fields = ["scientific_name", "variant", "common_name"]
    # Der Slug nimmt die Sorte mit auf, sonst kollidieren Stamm- und Zuchtform.
    prepopulated_fields = {"slug": ["scientific_name", "variant"]}
    inlines = [PlantImageInline, PlantLinkInline]


@admin.register(AnimalSpecies)
class AnimalSpeciesAdmin(admin.ModelAdmin):
    list_display = [
        "scientific_name", "variant", "common_name", "category", "min_group_size", "water_type"
    ]
    list_filter = [
        "water_type", "difficulty", "is_cultivated_form", "category", "temperament",
        "origin_region", "zone", "diet", "social_structure",
    ]
    search_fields = ["scientific_name", "variant", "common_name"]
    prepopulated_fields = {"slug": ["scientific_name", "variant"]}
    inlines = [AnimalImageInline, AnimalLinkInline]
