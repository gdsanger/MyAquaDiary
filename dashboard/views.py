from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from services.devices import warnings_for


class IndexView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Die Störungen kommen aus dem zuletzt erfassten Messwert und damit
        # ohne Netzzugriff — das Dashboard bleibt schnell, auch wenn gerade
        # ein Gerät nicht antwortet.
        context["warnings"] = warnings_for(self.request.user)
        return context
