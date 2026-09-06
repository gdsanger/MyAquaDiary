"""Katalogansichten: Raster mit Suche und Filtern, dazu die Detailseiten."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import TemplateView

from core.enums import Difficulty, WaterType
from core.views import NavSectionMixin
from tanks.models import species_in_own_tanks

from .models import AnimalSpecies, PlantSpecies


class SpeciesFilterMixin(LoginRequiredMixin):
    """Suche und Filter für beide Kataloge.

    Die Filterwerte kommen aus dem Query-String, damit dieselbe URL die
    Vollseite und — über das HTMX-Fragment — nur das Raster liefern kann.
    """

    model = None
    #: Zusätzliches Auswahlfeld neben Wassertyp und Anspruch.
    extra_filter_field = None
    extra_filter_label = ""
    extra_filter_choices = ()

    def get_filters(self):
        params = self.request.GET
        return {
            "q": params.get("q", "").strip(),
            "water_type": params.get("wassertyp", ""),
            "difficulty": params.get("anspruch", ""),
            "extra": params.get("filter", ""),
        }

    def get_species(self, filters):
        queryset = self.model.objects.with_images().search(filters["q"])
        if filters["water_type"] in WaterType.values:
            queryset = queryset.filter(water_type=filters["water_type"])
        if filters["difficulty"] in Difficulty.values:
            queryset = queryset.filter(difficulty=filters["difficulty"])
        valid_extra = [value for value, _ in self.extra_filter_choices]
        if self.extra_filter_field and filters["extra"] in valid_extra:
            queryset = queryset.filter(**{self.extra_filter_field: filters["extra"]})
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.get_filters()
        species = list(self.get_species(filters))
        context.update(
            {
                "filters": filters,
                "species_list": species,
                "result_count": len(species),
                "water_types": WaterType.choices,
                "difficulties": Difficulty.choices,
                "extra_filter_label": self.extra_filter_label,
                "extra_filter_choices": self.extra_filter_choices,
                "list_url": reverse(self.list_url_name),
                "grid_url": reverse(self.grid_url_name),
            }
        )
        return context


class PlantCatalogMixin(SpeciesFilterMixin, NavSectionMixin):
    model = PlantSpecies
    nav_section = "plants"
    extra_filter_field = "placement"
    extra_filter_label = "Standort"
    extra_filter_choices = PlantSpecies.Placement.choices
    list_url_name = "catalog:plant-list"
    grid_url_name = "catalog:plant-grid"


class AnimalCatalogMixin(SpeciesFilterMixin, NavSectionMixin):
    model = AnimalSpecies
    nav_section = "animals"
    extra_filter_field = "category"
    extra_filter_label = "Kategorie"
    extra_filter_choices = AnimalSpecies.Category.choices
    list_url_name = "catalog:animal-list"
    grid_url_name = "catalog:animal-grid"


class PlantListView(PlantCatalogMixin, TemplateView):
    template_name = "catalog/plant_list.html"


class PlantGridView(PlantCatalogMixin, TemplateView):
    """Nur das Raster — Antwort auf Sucheingaben und Filterwechsel."""

    template_name = "catalog/partials/grid.html"


class AnimalListView(AnimalCatalogMixin, TemplateView):
    template_name = "catalog/animal_list.html"


class AnimalGridView(AnimalCatalogMixin, TemplateView):
    template_name = "catalog/partials/grid.html"


class SpeciesDetailView(LoginRequiredMixin, NavSectionMixin, TemplateView):
    model = None
    tank_kwarg = None

    def get_context_data(self, slug, **kwargs):
        context = super().get_context_data(**kwargs)
        species = get_object_or_404(self.model.objects.prefetch_related("images"), slug=slug)
        context["species"] = species
        context["images"] = list(species.images.all())
        # „In welchen eigenen Becken kommt die Art vor?"
        context["own_tanks"] = species_in_own_tanks(
            self.request.user, **{self.tank_kwarg: species}
        )
        return context


class PlantDetailView(SpeciesDetailView):
    template_name = "catalog/plant_detail.html"
    model = PlantSpecies
    tank_kwarg = "plant"
    nav_section = "plants"


class AnimalDetailView(SpeciesDetailView):
    template_name = "catalog/animal_detail.html"
    model = AnimalSpecies
    tank_kwarg = "animal"
    nav_section = "animals"
