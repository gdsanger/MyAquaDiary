from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import TankForm, TankParameterTargetForm, TankPhotoFormSet
from .models import Parameter, Tank, TankParameterTarget


class TankOwnerQuerysetMixin(LoginRequiredMixin):
    def get_queryset(self):
        return Tank.objects.for_user(self.request.user)


class TankListView(TankOwnerQuerysetMixin, ListView):
    model = Tank
    context_object_name = "tanks"
    template_name = "tanks/tank_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tanks = context["tanks"]
        context["active_tanks"] = [tank for tank in tanks if not tank.is_dissolved]
        context["dissolved_tanks"] = [tank for tank in tanks if tank.is_dissolved]
        return context


class TankDetailView(TankOwnerQuerysetMixin, DetailView):
    model = Tank
    context_object_name = "tank"
    template_name = "tanks/tank_detail.html"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("photos", "targets__parameter")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        targets_by_parameter_id = {
            target.parameter_id: target for target in self.object.targets.all()
        }
        context["parameter_rows"] = [
            {"parameter": parameter, "target": targets_by_parameter_id.get(parameter.id)}
            for parameter in Parameter.objects.all()
        ]
        return context


class TankFormMixin:
    model = Tank
    form_class = TankForm
    template_name = "tanks/tank_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.method == "POST":
            context["photo_formset"] = TankPhotoFormSet(
                self.request.POST, self.request.FILES, instance=self.object
            )
        else:
            context["photo_formset"] = TankPhotoFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        self.object = form.save()
        photo_formset = TankPhotoFormSet(
            self.request.POST, self.request.FILES, instance=self.object
        )
        if not photo_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        photo_formset.save()
        messages.success(self.request, "Becken gespeichert.")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("tanks:detail", kwargs={"slug": self.object.slug})


class TankCreateView(TankOwnerQuerysetMixin, TankFormMixin, CreateView):
    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class TankUpdateView(TankOwnerQuerysetMixin, TankFormMixin, UpdateView):
    pass


class TankDeleteView(TankOwnerQuerysetMixin, DeleteView):
    model = Tank
    context_object_name = "tank"
    template_name = "tanks/tank_confirm_delete.html"
    success_url = reverse_lazy("tanks:list")


class TankParameterTargetEditView(LoginRequiredMixin, View):
    """HTMX-Inline: liefert das Bearbeitungsformular für eine Tank/Parameter-Zeile
    und speichert es bei POST, ohne die restliche Detailseite neu zu laden."""

    def get_tank(self):
        return get_object_or_404(
            Tank.objects.for_user(self.request.user), slug=self.kwargs["slug"]
        )

    def get(self, request, slug, parameter_id):
        tank = self.get_tank()
        parameter = get_object_or_404(Parameter, pk=parameter_id)
        target = TankParameterTarget.objects.filter(tank=tank, parameter=parameter).first()
        form = TankParameterTargetForm(instance=target)
        return render(
            request,
            "tanks/_parameter_target_row_form.html",
            {"tank": tank, "parameter": parameter, "form": form},
        )

    def post(self, request, slug, parameter_id):
        tank = self.get_tank()
        parameter = get_object_or_404(Parameter, pk=parameter_id)
        target = TankParameterTarget.objects.filter(tank=tank, parameter=parameter).first()
        form = TankParameterTargetForm(request.POST, instance=target)
        if form.is_valid():
            target = form.save(commit=False)
            target.tank = tank
            target.parameter = parameter
            target.save()
            return render(
                request,
                "tanks/_parameter_target_row.html",
                {"tank": tank, "parameter": parameter, "target": target},
            )
        return render(
            request,
            "tanks/_parameter_target_row_form.html",
            {"tank": tank, "parameter": parameter, "form": form},
        )


class TankParameterTargetDetailView(LoginRequiredMixin, View):
    """Liefert die Anzeige-Zeile zurück, z. B. wenn die Inline-Bearbeitung
    abgebrochen wird."""

    def get(self, request, slug, parameter_id):
        tank = get_object_or_404(Tank.objects.for_user(request.user), slug=slug)
        parameter = get_object_or_404(Parameter, pk=parameter_id)
        target = TankParameterTarget.objects.filter(tank=tank, parameter=parameter).first()
        return render(
            request,
            "tanks/_parameter_target_row.html",
            {"tank": tank, "parameter": parameter, "target": target},
        )
