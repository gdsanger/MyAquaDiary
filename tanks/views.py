import csv

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.forms import inlineformset_factory
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import (
    EventForm,
    EventPhotoFormSet,
    MaintenanceScheduleForm,
    MeasurementForm,
    MeasurementPhotoFormSet,
    MeasurementValueForm,
    TankForm,
    TankParameterTargetForm,
    TankPhotoFormSet,
)
from .models import (
    Event,
    MaintenanceSchedule,
    Measurement,
    MeasurementValue,
    Parameter,
    Tank,
    TankParameterTarget,
)


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


class TankHistoryView(TankOwnerQuerysetMixin, DetailView):
    """Chronologische Beckenhistorie aus Ereignissen und Messungen gemeinsam,
    da beide zusammen erzählen, was am Becken passiert ist."""

    model = Tank
    context_object_name = "tank"
    template_name = "tanks/tank_history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = [
            {"kind": "event", "at": event.occurred_at, "object": event}
            for event in self.object.events.all()
        ] + [
            {"kind": "measurement", "at": measurement.measured_at, "object": measurement}
            for measurement in self.object.measurements.all()
        ]
        context["history"] = sorted(entries, key=lambda entry: entry["at"], reverse=True)
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


def build_measurement_value_formset(tank, measurement, data=None, files=None):
    """Baut den Formset für die Messwerte: eine vorbelegte Zeile je
    Zielparameter des Beckens, der in dieser Messung noch keinen Wert hat,
    plus ein paar leere Zeilen zum freien Hinzufügen weiterer Parameter."""

    existing_parameter_ids = (
        set(measurement.values.values_list("parameter_id", flat=True))
        if measurement and measurement.pk
        else set()
    )
    missing_target_parameter_ids = [
        target.parameter_id
        for target in tank.targets.all()
        if target.parameter_id not in existing_parameter_ids
    ]
    formset_class = inlineformset_factory(
        Measurement,
        MeasurementValue,
        form=MeasurementValueForm,
        extra=len(missing_target_parameter_ids) + 2,
        can_delete=True,
    )
    if data is not None:
        return formset_class(data, files, instance=measurement)
    initial = [{"parameter": parameter_id} for parameter_id in missing_target_parameter_ids]
    return formset_class(instance=measurement, initial=initial)


class MeasurementOwnerMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        self.tank = get_object_or_404(Tank.objects.for_user(request.user), slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)


class MeasurementQuerysetMixin(MeasurementOwnerMixin):
    def get_queryset(self):
        return Measurement.objects.filter(tank=self.tank)


class MeasurementListView(MeasurementQuerysetMixin, ListView):
    model = Measurement
    context_object_name = "measurements"
    template_name = "tanks/measurement_list.html"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("values__parameter", "photos")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        measurements = list(context["measurements"])
        for measurement in measurements:
            measurement.value_map = {value.parameter_id: value for value in measurement.values.all()}
        context["measurements"] = measurements
        context["tank"] = self.tank
        context["parameters"] = Parameter.objects.filter(
            measurement_values__measurement__tank=self.tank
        ).distinct()
        return context


class MeasurementFormMixin:
    model = Measurement
    form_class = MeasurementForm
    template_name = "tanks/measurement_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        if self.request.method == "POST":
            context["value_formset"] = build_measurement_value_formset(
                self.tank, self.object, data=self.request.POST
            )
            context["photo_formset"] = MeasurementPhotoFormSet(
                self.request.POST, self.request.FILES, instance=self.object
            )
        else:
            context["value_formset"] = build_measurement_value_formset(self.tank, self.object)
            context["photo_formset"] = MeasurementPhotoFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        was_new = self.object is None
        form.instance.tank = self.tank
        forms_are_valid = False
        with transaction.atomic():
            self.object = form.save()

            value_formset = build_measurement_value_formset(
                self.tank, self.object, data=self.request.POST
            )
            photo_formset = MeasurementPhotoFormSet(
                self.request.POST, self.request.FILES, instance=self.object
            )
            forms_are_valid = value_formset.is_valid() and photo_formset.is_valid()
            if forms_are_valid:
                value_formset.save()
                for photo in photo_formset.save(commit=False):
                    photo.tank = self.tank
                    photo.measurement = self.object
                    photo.save()
                for photo in photo_formset.deleted_objects:
                    photo.delete()
            else:
                transaction.set_rollback(True)

        if not forms_are_valid:
            if was_new:
                self.object = None
            return self.render_to_response(self.get_context_data(form=form))

        messages.success(self.request, "Messung gespeichert.")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("tanks:measurement-list", kwargs={"slug": self.tank.slug})


class MeasurementCreateView(MeasurementOwnerMixin, MeasurementFormMixin, CreateView):
    pass


class MeasurementUpdateView(MeasurementQuerysetMixin, MeasurementFormMixin, UpdateView):
    pass


class MeasurementDeleteView(MeasurementQuerysetMixin, DeleteView):
    model = Measurement
    context_object_name = "measurement"
    template_name = "tanks/measurement_confirm_delete.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        return context

    def get_success_url(self):
        return reverse("tanks:measurement-list", kwargs={"slug": self.tank.slug})


class MeasurementChartDataView(LoginRequiredMixin, View):
    def get(self, request, slug):
        tank = get_object_or_404(Tank.objects.for_user(request.user), slug=slug)
        parameter = get_object_or_404(Parameter, key=request.GET.get("parameter"))

        values = (
            MeasurementValue.objects.filter(
                measurement__tank=tank, parameter=parameter, value__isnull=False
            )
            .select_related("measurement")
            .order_by("measurement__measured_at")
        )
        date_from = parse_date(request.GET.get("von") or "")
        date_to = parse_date(request.GET.get("bis") or "")
        if date_from:
            values = values.filter(measurement__measured_at__date__gte=date_from)
        if date_to:
            values = values.filter(measurement__measured_at__date__lte=date_to)

        return JsonResponse(
            {
                "labels": [
                    value.measurement.measured_at.strftime("%Y-%m-%d %H:%M") for value in values
                ],
                "values": [float(value.value) for value in values],
                "unit": parameter.unit,
            }
        )


class MeasurementExportView(LoginRequiredMixin, View):
    def get(self, request, slug):
        tank = get_object_or_404(Tank.objects.for_user(request.user), slug=slug)
        measurements = (
            Measurement.objects.filter(tank=tank)
            .prefetch_related("values__parameter")
            .order_by("measured_at")
        )
        parameters = list(
            Parameter.objects.filter(measurement_values__measurement__tank=tank).distinct()
        )

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{tank.slug}-messungen.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Datum", "Quelle", "Notiz"]
            + [f"{p.name} ({p.unit})" if p.unit else p.name for p in parameters]
            + ["CO2 (mg/l, berechnet)"]
        )
        for measurement in measurements:
            values_by_parameter = {
                value.parameter_id: value for value in measurement.values.all()
            }
            row = [
                measurement.measured_at.strftime("%Y-%m-%d %H:%M"),
                measurement.get_source_display(),
                measurement.note,
            ]
            for parameter in parameters:
                value = values_by_parameter.get(parameter.id)
                if value is None:
                    row.append("")
                elif value.below_detection:
                    row.append("n.n.")
                else:
                    row.append(str(value.value))
            co2 = measurement.co2_mg_l
            row.append(str(co2) if co2 is not None else "")
            writer.writerow(row)
        return response


class EventOwnerMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        self.tank = get_object_or_404(Tank.objects.for_user(request.user), slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)


class EventQuerysetMixin(EventOwnerMixin):
    def get_queryset(self):
        return Event.objects.filter(tank=self.tank)


class EventListView(EventQuerysetMixin, ListView):
    model = Event
    context_object_name = "events"
    template_name = "tanks/event_list.html"

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("photos")
        category = self.request.GET.get("kategorie")
        if category:
            queryset = queryset.filter(category=category)
        date_from = parse_date(self.request.GET.get("von") or "")
        date_to = parse_date(self.request.GET.get("bis") or "")
        if date_from:
            queryset = queryset.filter(occurred_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(occurred_at__date__lte=date_to)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        context["categories"] = Event.Category.choices
        context["selected_category"] = self.request.GET.get("kategorie", "")
        context["date_from"] = self.request.GET.get("von", "")
        context["date_to"] = self.request.GET.get("bis", "")
        return context


class EventFormMixin:
    model = Event
    form_class = EventForm
    template_name = "tanks/event_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        if self.request.method == "POST":
            context["photo_formset"] = EventPhotoFormSet(
                self.request.POST, self.request.FILES, instance=self.object
            )
        else:
            context["photo_formset"] = EventPhotoFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        was_new = self.object is None
        form.instance.tank = self.tank
        forms_are_valid = False
        with transaction.atomic():
            self.object = form.save()

            photo_formset = EventPhotoFormSet(
                self.request.POST, self.request.FILES, instance=self.object
            )
            forms_are_valid = photo_formset.is_valid()
            if forms_are_valid:
                for photo in photo_formset.save(commit=False):
                    photo.tank = self.tank
                    photo.event = self.object
                    photo.save()
                for photo in photo_formset.deleted_objects:
                    photo.delete()
            else:
                transaction.set_rollback(True)

        if not forms_are_valid:
            if was_new:
                self.object = None
            return self.render_to_response(self.get_context_data(form=form))

        messages.success(self.request, "Ereignis gespeichert.")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("tanks:event-list", kwargs={"slug": self.tank.slug})


class EventCreateView(EventOwnerMixin, EventFormMixin, CreateView):
    """Ein Ereignis kann frei angelegt werden oder — über `?termin=<id>` —
    aus einem fälligen Wartungstermin heraus, der dabei quittiert wird."""

    def get_schedule(self):
        schedule_id = self.request.POST.get("schedule") or self.request.GET.get("termin")
        if not schedule_id:
            return None
        return get_object_or_404(MaintenanceSchedule, pk=schedule_id, tank=self.tank)

    def get_initial(self):
        initial = super().get_initial()
        schedule = self.get_schedule()
        if schedule:
            initial["title"] = schedule.title
            initial["category"] = schedule.event_category
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["schedule"] = self.get_schedule()
        return context

    def form_valid(self, form):
        schedule = self.get_schedule()
        form.instance.schedule = schedule
        response = super().form_valid(form)
        if schedule and self.object is not None:
            schedule.mark_done(timezone.localtime(self.object.occurred_at).date())
        return response


class EventUpdateView(EventQuerysetMixin, EventFormMixin, UpdateView):
    pass


class EventDeleteView(EventQuerysetMixin, DeleteView):
    model = Event
    context_object_name = "event"
    template_name = "tanks/event_confirm_delete.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        return context

    def get_success_url(self):
        return reverse("tanks:event-list", kwargs={"slug": self.tank.slug})


class ScheduleOwnerMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        self.tank = get_object_or_404(Tank.objects.for_user(request.user), slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)


class ScheduleQuerysetMixin(ScheduleOwnerMixin):
    def get_queryset(self):
        return MaintenanceSchedule.objects.filter(tank=self.tank)


class ScheduleListView(ScheduleQuerysetMixin, ListView):
    model = MaintenanceSchedule
    context_object_name = "schedules"
    template_name = "tanks/schedule_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        return context


class ScheduleFormMixin:
    model = MaintenanceSchedule
    form_class = MaintenanceScheduleForm
    template_name = "tanks/schedule_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        return context

    def form_valid(self, form):
        form.instance.tank = self.tank
        messages.success(self.request, "Termin gespeichert.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("tanks:schedule-list", kwargs={"slug": self.tank.slug})


class ScheduleCreateView(ScheduleOwnerMixin, ScheduleFormMixin, CreateView):
    pass


class ScheduleUpdateView(ScheduleQuerysetMixin, ScheduleFormMixin, UpdateView):
    pass


class ScheduleDeleteView(ScheduleQuerysetMixin, DeleteView):
    model = MaintenanceSchedule
    context_object_name = "schedule"
    template_name = "tanks/schedule_confirm_delete.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tank"] = self.tank
        return context

    def get_success_url(self):
        return reverse("tanks:schedule-list", kwargs={"slug": self.tank.slug})
