from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import CatalogAnimalForm, CatalogAnimalImageFormSet
from .models import AnimalGroup, CatalogAnimal, Difficulty, Zone


class CatalogEditPermissionMixin(UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_staff or user.can_edit_catalog)


class CatalogAnimalListView(LoginRequiredMixin, ListView):
    model = CatalogAnimal
    context_object_name = "animals"
    template_name = "catalog/catalog_animal_list.html"
    paginate_by = 24

    def get_queryset(self):
        queryset = CatalogAnimal.objects.prefetch_related("images")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(scientific_name__icontains=query)
                | Q(variety__icontains=query)
                | Q(common_name__icontains=query)
            )
        group = self.request.GET.get("group", "")
        if group:
            queryset = queryset.filter(group=group)
        zone = self.request.GET.get("zone", "")
        if zone:
            queryset = queryset.filter(zone=zone)
        difficulty = self.request.GET.get("difficulty", "")
        if difficulty:
            queryset = queryset.filter(difficulty=difficulty)
        max_liters = self.request.GET.get("max_liters", "")
        if max_liters.isdigit():
            queryset = queryset.filter(min_tank_liters__lte=int(max_liters))
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
        context["query"] = self.request.GET.get("q", "")
        context["group_choices"] = AnimalGroup.choices
        context["zone_choices"] = Zone.choices
        context["difficulty_choices"] = Difficulty.choices
        context["selected_group"] = self.request.GET.get("group", "")
        context["selected_zone"] = self.request.GET.get("zone", "")
        context["selected_difficulty"] = self.request.GET.get("difficulty", "")
        context["max_liters"] = self.request.GET.get("max_liters", "")
        return context


class CatalogAnimalDetailView(LoginRequiredMixin, DetailView):
    model = CatalogAnimal
    context_object_name = "animal"
    template_name = "catalog/catalog_animal_detail.html"

    def get_queryset(self):
        return CatalogAnimal.objects.prefetch_related("images")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_edit"] = user.is_authenticated and (user.is_staff or user.can_edit_catalog)
        return context


class CatalogAnimalFormMixin:
    model = CatalogAnimal
    form_class = CatalogAnimalForm
    template_name = "catalog/catalog_animal_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.method == "POST":
            context["image_formset"] = CatalogAnimalImageFormSet(
                self.request.POST, self.request.FILES, instance=self.object
            )
        else:
            context["image_formset"] = CatalogAnimalImageFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        self.object = form.save()
        image_formset = CatalogAnimalImageFormSet(
            self.request.POST, self.request.FILES, instance=self.object
        )
        if not image_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        image_formset.save()
        messages.success(self.request, "Tierart gespeichert.")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("catalog:animal-detail", kwargs={"slug": self.object.slug})


class CatalogAnimalCreateView(
    LoginRequiredMixin, CatalogEditPermissionMixin, CatalogAnimalFormMixin, CreateView
):
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class CatalogAnimalUpdateView(
    LoginRequiredMixin, CatalogEditPermissionMixin, CatalogAnimalFormMixin, UpdateView
):
    pass
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
