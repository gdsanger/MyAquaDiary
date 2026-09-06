"""Administration der Service-Konfiguration."""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from .forms import DeviceForm, MailConfigForm, TestMailForm
from .graph import GraphMailService, render_mail
from .models import Device, DeviceEvent, DeviceReading, MailConfig, MailLog


@admin.register(MailConfig)
class MailConfigAdmin(admin.ModelAdmin):
    """Singleton-Admin: anlegen nur, solange keine Konfiguration existiert."""

    form = MailConfigForm
    change_form_template = "admin/services/mailconfig/change_form.html"
    fieldsets = [
        (None, {"fields": ["is_active", "status"]}),
        (
            "App-Registrierung",
            {
                "fields": ["tenant_id", "client_id", "client_secret"],
                "description": "Client-Credentials-Flow mit der Application-Permission Mail.Send.",
            },
        ),
        ("Absender", {"fields": ["sender_address", "sender_name", "reply_to"]}),
        ("Verwaltung", {"fields": ["updated_at"]}),
    ]
    readonly_fields = ["status", "updated_at"]

    @admin.display(description="Status")
    def status(self, obj):
        if obj is not None and obj.is_configured:
            return format_html('<span style="color:#4caf7d;">Mailversand aktiv</span>')
        return format_html(
            '<span style="color:#d2483b;">Nicht konfiguriert – Mailfunktionen sind ausgeblendet</span>'
        )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not MailConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Der Singleton wird über "Mailversand aktiv" abgeschaltet, nicht gelöscht.
        return False

    def get_changeform_initial_data(self, request):
        """Neuanlage mit den Environment-Werten vorbelegen — ohne das Secret."""
        initial = MailConfig.defaults_from_env()
        initial.pop("client_secret", None)
        return initial

    def get_urls(self):
        return [
            path(
                "testmail/",
                self.admin_site.admin_view(self.test_mail_view),
                name="services_mailconfig_testmail",
            ),
            *super().get_urls(),
        ]

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = {**(extra_context or {}), "mail_configured": MailConfig.load().is_configured}
        return super().changeform_view(request, object_id, form_url, extra_context)

    def test_mail_view(self, request):
        """Verschickt eine Testmail an eine frei wählbare Adresse."""
        if not self.has_change_permission(request):
            raise PermissionDenied

        config = MailConfig.load()
        changelist_url = reverse("admin:services_mailconfig_changelist")

        if not config.is_configured:
            self.message_user(
                request,
                "Der Mailversand ist nicht konfiguriert — es kann keine Testmail verschickt werden.",
                level=messages.WARNING,
            )
            return redirect(changelist_url)

        form = TestMailForm(request.POST or None, initial={"recipient": request.user.email})
        if request.method == "POST" and form.is_valid():
            recipient = form.cleaned_data["recipient"]
            rendered = render_mail("test_mail", {"sender_address": config.sender_address})
            result = GraphMailService(config).send(
                recipient,
                rendered.subject,
                rendered.html,
                rendered.text,
                template="test_mail",
            )
            if result:
                self.message_user(request, f"Testmail an {recipient} versendet.", level=messages.SUCCESS)
            else:
                self.message_user(
                    request,
                    f"Testmail an {recipient} fehlgeschlagen: {result.error}",
                    level=messages.ERROR,
                )
            return redirect(changelist_url)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Testmail senden",
            "form": form,
            "config": config,
        }
        return TemplateResponse(request, "admin/services/mailconfig/test_mail.html", context)


@admin.register(MailLog)
class MailLogAdmin(admin.ModelAdmin):
    """Reines Leseprotokoll — Einträge entstehen nur beim Versand."""

    list_display = ["created_at", "status", "recipients", "subject", "template"]
    list_filter = ["status", "template", "created_at"]
    search_fields = ["recipients", "subject"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "recipients", "subject", "template", "status", "error"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    """Geräteverwaltung für den Betrieb — die Pflege durch Benutzer läuft über
    die Geräteseiten, nicht über den Admin."""

    form = DeviceForm
    list_display = ["name", "owner", "kind", "host", "mac_address", "firmware", "is_active", "last_seen"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "mac_address", "host"]
    readonly_fields = ["last_seen", "created_at", "password_status"]
    fieldsets = [
        (None, {"fields": ["owner", "name", "kind", "is_active"]}),
        ("Netz", {"fields": ["host", "mac_address", "firmware"]}),
        (
            "Zugang",
            {
                "fields": ["username", "password", "password_status"],
                "description": "Werksseitig api / admin. Das Passwort liegt verschlüsselt in der "
                "Datenbank und wird nie angezeigt.",
            },
        ),
        ("Verwaltung", {"fields": ["last_seen", "created_at"]}),
    ]

    @admin.display(description="Passwort")
    def password_status(self, obj):
        if obj is None or not obj.pk:
            return "—"
        if obj.uses_default_password:
            return format_html('<span style="color:#d2483b;">unverändertes Werkspasswort</span>')
        return format_html('<span style="color:#4caf7d;">eigenes Passwort gesetzt</span>')

    def get_form(self, request, obj=None, **kwargs):
        # Der Besitzer gehört zum Modell, nicht zum Benutzerformular der App.
        kwargs["fields"] = ["owner", "name", "kind", "is_active", "host", "mac_address",
                            "firmware", "username", "password"]
        return super().get_form(request, obj, **kwargs)


@admin.register(DeviceReading)
class DeviceReadingAdmin(admin.ModelAdmin):
    """Reines Leseprotokoll — Messwerte entstehen nur durch Abfragen."""

    list_display = ["read_at", "device", "is_on", "rpm_percent", "pump_mode", "error_code"]
    list_filter = ["device", "error_code"]
    date_hierarchy = "read_at"
    readonly_fields = ["device", "read_at", "payload", "rpm_percent", "pump_mode", "error_code",
                       "service_due_in", "is_on"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DeviceEvent)
class DeviceEventAdmin(admin.ModelAdmin):
    """Protokoll der schreibenden Aktionen."""

    list_display = ["occurred_at", "device", "title", "user", "succeeded"]
    list_filter = ["succeeded", "action", "device"]
    search_fields = ["title", "description"]
    date_hierarchy = "occurred_at"
    readonly_fields = ["device", "user", "occurred_at", "action", "title", "description", "succeeded"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
