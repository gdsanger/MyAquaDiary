from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from .forms import ProfileForm, UserRegistrationForm


class RegisterView(CreateView):
    form_class = UserRegistrationForm
    template_name = "registration/register.html"
    success_url = reverse_lazy("dashboard:index")

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class ProfileView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "registration/profile.html"
    success_url = reverse_lazy("profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profil gespeichert.")
        return super().form_valid(form)
"""Gemeinsame View-Bausteine."""


class NavSectionMixin:
    """Markiert den aktiven Punkt in der Hauptnavigation.

    Die Zuordnung über einen expliziten Namen statt über ``resolver_match``
    ist auch für HTMX-Fragmente korrekt, die unter eigenen URLs liegen.
    """

    nav_section = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("nav_section", self.nav_section)
        return context
