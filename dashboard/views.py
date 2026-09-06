from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from tanks.models import MaintenanceSchedule


class IndexView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        schedules = (
            MaintenanceSchedule.objects.filter(tank__owner=self.request.user, is_active=True)
            .select_related("tank")
            .order_by("next_due_on")
        )
        context["due_schedules"] = [schedule for schedule in schedules if schedule.is_due]
        context["upcoming_schedules"] = [
            schedule for schedule in schedules if schedule.is_upcoming
        ]
        return context
