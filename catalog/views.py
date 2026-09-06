"""Katalogansichten: Raster mit Suche und Filtern, Detailseiten — und Pflege.

Der Katalog ist der eine Bereich, in dem nicht Eigentümerschaft entscheidet,
sondern ein Recht: ``can_edit_catalog`` (siehe :mod:`catalog.permissions`).
Ansichten und Templates fragen dieselbe Funktion, damit keine Schaltfläche
erscheint, deren Ziel hinterher mit 403 antwortet.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import TemplateView, View

from core.enums import Difficulty, WaterType
from core.views import NavSectionMixin
from tanks.models import species_in_own_tanks

from .forms import AnimalSpeciesForm, PlantSpeciesForm, SpeciesImageUploadForm
from .models import AnimalImage, AnimalSpecies, PlantImage, PlantSpecies
from .permissions import may_edit_catalog


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
                "can_edit_catalog": may_edit_catalog(self.request.user),
                "create_url": reverse(self.create_url_name),
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
    create_url_name = "catalog:plant-create"


class AnimalCatalogMixin(SpeciesFilterMixin, NavSectionMixin):
    model = AnimalSpecies
    nav_section = "animals"
    extra_filter_field = "category"
    extra_filter_label = "Kategorie"
    extra_filter_choices = AnimalSpecies.Category.choices
    list_url_name = "catalog:animal-list"
    grid_url_name = "catalog:animal-grid"
    create_url_name = "catalog:animal-create"


class PlantListView(PlantCatalogMixin, TemplateView):
    template_name = "catalog/plant_list.html"


class PlantGridView(PlantCatalogMixin, TemplateView):
    """Nur das Raster — Antwort auf Sucheingaben und Filterwechsel."""

    template_name = "catalog/partials/grid.html"


class AnimalListView(AnimalCatalogMixin, TemplateView):
    template_name = "catalog/animal_list.html"


class AnimalGridView(AnimalCatalogMixin, TemplateView):
    template_name = "catalog/partials/grid.html"


# --------------------------------------------------------------------------
# Art und Bilder — Zuschnitt je Katalog
# --------------------------------------------------------------------------


class PlantSpeciesMixin:
    model = PlantSpecies
    form_class = PlantSpeciesForm
    image_model = PlantImage
    nav_section = "plants"
    kind = "plant"
    label = "Pflanzenart"
    list_url_name = "catalog:plant-list"
    tank_kwarg = "plant"
    #: Beziehung, über die die Art in Becken steckt.
    usage_related = "plantings"


class AnimalSpeciesMixin:
    model = AnimalSpecies
    form_class = AnimalSpeciesForm
    image_model = AnimalImage
    nav_section = "animals"
    kind = "animal"
    label = "Tierart"
    list_url_name = "catalog:animal-list"
    tank_kwarg = "animal"
    usage_related = "stockings"


def image_rows(species, kind, may_edit):
    """Bilder samt ihren Pflege-Adressen.

    Die Adressen entstehen hier und nicht im Template: welcher Katalog gemeint
    ist, weiß die Ansicht — das Template soll keine URL-Namen zusammensetzen.
    """
    rows = []
    for image in species.images.all():
        row = {"image": image, "primary_url": "", "delete_url": ""}
        if may_edit:
            row["primary_url"] = reverse(
                f"catalog:{kind}-image-primary", args=[species.slug, image.pk]
            )
            row["delete_url"] = reverse(
                f"catalog:{kind}-image-delete", args=[species.slug, image.pk]
            )
        rows.append(row)
    return rows


def gallery_context(request, species, kind, *, confirm_image=None):
    may_edit = may_edit_catalog(request.user)
    return {
        "species": species,
        "image_rows": image_rows(species, kind, may_edit),
        "can_edit_catalog": may_edit,
        "upload_form": SpeciesImageUploadForm() if may_edit else None,
        "upload_url": reverse(f"catalog:{kind}-image-upload", args=[species.slug]),
        "confirm_image": confirm_image,
    }


class SpeciesDetailView(LoginRequiredMixin, NavSectionMixin, TemplateView):
    model = None
    tank_kwarg = None

    def get_context_data(self, slug, **kwargs):
        context = super().get_context_data(**kwargs)
        species = get_object_or_404(self.model.objects.prefetch_related("images"), slug=slug)
        context["species"] = species
        # „In welchen eigenen Becken kommt die Art vor?"
        context["own_tanks"] = species_in_own_tanks(
            self.request.user, **{self.tank_kwarg: species}
        )
        context.update(gallery_context(self.request, species, self.kind))
        context["edit_url"] = reverse(f"catalog:{self.kind}-update", args=[species.slug])
        context["delete_url"] = reverse(f"catalog:{self.kind}-delete", args=[species.slug])
        return context


class PlantDetailView(PlantSpeciesMixin, SpeciesDetailView):
    template_name = "catalog/plant_detail.html"


class AnimalDetailView(AnimalSpeciesMixin, SpeciesDetailView):
    template_name = "catalog/animal_detail.html"


# --------------------------------------------------------------------------
# Katalogpflege
# --------------------------------------------------------------------------


class CatalogEditMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Schreibrecht am Katalog.

    Anonyme Besucher werden angemeldet, angemeldete ohne Recht bekommen 403:
    dass es die Art gibt, ist ohnehin für alle sichtbar — hier fehlt kein
    Zugriff auf fremde Daten, sondern schlicht die Berechtigung.
    """

    def test_func(self):
        return may_edit_catalog(self.request.user)

    @property
    def raise_exception(self):
        return self.request.user.is_authenticated

    def get_species(self):
        return get_object_or_404(self.model, slug=self.kwargs["slug"])


class SpeciesFormView(CatalogEditMixin, View):
    """Art anlegen und bearbeiten — als eigene Seite.

    Ein Steckbrief hat drei Dutzend Felder; ein Overlay wäre dafür der falsche
    Ort. Anders als am Becken gibt es hier auch keinen Reiterkontext, der
    verloren gehen könnte.
    """

    template_name = "catalog/species_form.html"

    def get(self, request, slug=None):
        return self.render_form(self.form_class(instance=self.instance(slug)))

    def post(self, request, slug=None):
        instance = self.instance(slug)
        form = self.form_class(request.POST, instance=instance)
        if not form.is_valid():
            return self.render_form(form, instance)
        species = form.save()
        messages.success(request, f"{species.display_name} gespeichert.")
        return HttpResponseRedirect(species.get_absolute_url())

    def instance(self, slug):
        return self.get_species() if slug else None

    def render_form(self, form, instance=None):
        instance = instance if instance is not None else self.instance(self.kwargs.get("slug"))
        return render(
            self.request,
            self.template_name,
            {
                "form": form,
                "species": instance,
                "nav_section": self.nav_section,
                "title": f"{self.label} {'bearbeiten' if instance else 'anlegen'}",
                "list_url": reverse(self.list_url_name),
            },
        )


class PlantSpeciesFormView(PlantSpeciesMixin, SpeciesFormView):
    pass


class AnimalSpeciesFormView(AnimalSpeciesMixin, SpeciesFormView):
    pass


class SpeciesDeleteView(CatalogEditMixin, View):
    """Art löschen — nur, solange sie in keinem Becken steckt.

    Besatz und Bepflanzung verweisen mit ``PROTECT`` auf den Katalog: eine Art
    aus einem laufenden Becken zu entfernen würde dessen Geschichte zerreißen.
    """

    template_name = "catalog/species_confirm_delete.html"

    def get(self, request, slug):
        return self.render_page(self.get_species())

    def post(self, request, slug):
        species = self.get_species()
        if self.usage_count(species):
            messages.warning(
                request,
                f"{species.display_name} wird in Becken geführt und kann deshalb nicht "
                "gelöscht werden.",
            )
            return HttpResponseRedirect(species.get_absolute_url())
        name = species.display_name
        species.delete()
        messages.success(request, f"{name} aus dem Katalog gelöscht.")
        return HttpResponseRedirect(reverse(self.list_url_name))

    def usage_count(self, species):
        return getattr(species, self.usage_related).count()

    def render_page(self, species):
        return render(
            self.request,
            self.template_name,
            {
                "species": species,
                "label": self.label,
                "usage_count": self.usage_count(species),
                "nav_section": self.nav_section,
                "list_url": reverse(self.list_url_name),
            },
        )


class PlantSpeciesDeleteView(PlantSpeciesMixin, SpeciesDeleteView):
    pass


class AnimalSpeciesDeleteView(AnimalSpeciesMixin, SpeciesDeleteView):
    pass


class SpeciesGalleryView(CatalogEditMixin, View):
    """Basis der Bildpflege: antwortet mit der Galerie, per HTMX als Fragment."""

    template_name = "catalog/partials/gallery.html"

    def render_gallery(self, species, extra=None):
        context = gallery_context(self.request, species, self.kind)
        context.update(extra or {})
        if getattr(self.request, "htmx", False):
            return render(self.request, self.template_name, context)
        return HttpResponseRedirect(species.get_absolute_url())


class SpeciesImageUploadView(SpeciesGalleryView):
    """Mehrere Bilder in einem Durchgang."""

    def post(self, request, slug):
        species = self.get_species()
        form = SpeciesImageUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_gallery(species, {"upload_form": form})
        created = form.save(species, self.image_model)
        if not getattr(request, "htmx", False):
            messages.success(request, f"{len(created)} Bild(er) hochgeladen.")
        return self.render_gallery(species)


class SpeciesImagePrimaryView(SpeciesGalleryView):
    """Primärbild wählen.

    Es kann nur eins geben: die übrigen werden im selben Zug zurückgesetzt,
    sonst entscheidet die Sortierung darüber, welches Bild auf der Karte
    landet.
    """

    def post(self, request, slug, pk):
        species = self.get_species()
        image = get_object_or_404(self.image_model, pk=pk, species=species)
        species.images.exclude(pk=image.pk).update(is_primary=False)
        image.is_primary = True
        image.save(update_fields=["is_primary"])
        return self.render_gallery(species)


class SpeciesImageDeleteView(SpeciesGalleryView):
    """Bild löschen — mit Rückfrage in der Galerie."""

    def get(self, request, slug, pk):
        species = self.get_species()
        image = get_object_or_404(self.image_model, pk=pk, species=species)
        if not getattr(request, "htmx", False):
            return render(
                request,
                "catalog/image_confirm_delete.html",
                {
                    "species": species,
                    "image": image,
                    "nav_section": self.nav_section,
                    "action": reverse(f"catalog:{self.kind}-image-delete", args=[slug, pk]),
                },
            )
        return self.render_gallery(species, {"confirm_image": image})

    def post(self, request, slug, pk):
        species = self.get_species()
        image = get_object_or_404(self.image_model, pk=pk, species=species)
        was_primary = image.is_primary
        image.delete()
        if was_primary:
            # Ohne Primärbild fällt die Anzeige auf das erste Bild zurück —
            # dann soll das auch so markiert sein.
            follower = species.images.first()
            if follower is not None:
                follower.is_primary = True
                follower.save(update_fields=["is_primary"])
        return self.render_gallery(species)


class PlantImageUploadView(PlantSpeciesMixin, SpeciesImageUploadView):
    pass


class AnimalImageUploadView(AnimalSpeciesMixin, SpeciesImageUploadView):
    pass


class PlantImagePrimaryView(PlantSpeciesMixin, SpeciesImagePrimaryView):
    pass


class AnimalImagePrimaryView(AnimalSpeciesMixin, SpeciesImagePrimaryView):
    pass


class PlantImageDeleteView(PlantSpeciesMixin, SpeciesImageDeleteView):
    pass


class AnimalImageDeleteView(AnimalSpeciesMixin, SpeciesImageDeleteView):
    pass
