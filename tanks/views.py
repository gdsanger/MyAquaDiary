from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, ListView

from .models import Tank


class TankListView(LoginRequiredMixin, ListView):
    model = Tank
    context_object_name = "tanks"
    template_name = "tanks/tank_list.html"

    def get_queryset(self):
        return Tank.objects.for_user(self.request.user)


class TankDetailView(LoginRequiredMixin, DetailView):
    model = Tank
    context_object_name = "tank"
    template_name = "tanks/tank_detail.html"

    def get_queryset(self):
        return Tank.objects.for_user(self.request.user)
