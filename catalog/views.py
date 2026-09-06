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
