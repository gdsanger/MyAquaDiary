"""Administration der Service-Konfiguration."""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .ai import budget_status
from .forms import AIConfigForm, DeviceForm, MailConfigForm, TestMailForm
from .graph import GraphMailService, render_mail
from .models import (
    AIConfig,
    AISuggestion,
    AIUsageLog,
    Device,
    DeviceEvent,
    DeviceReading,
    MailConfig,
    MailLog,
    MCPAccessLog,
    MCPToken,
    MeasurementAnalysis,
)


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
    list_display = ["name", "owner", "tank", "kind", "host", "mac_address", "firmware",
                    "is_active", "last_seen"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "mac_address", "host", "tank__name"]
    readonly_fields = ["last_seen", "created_at", "password_status"]
    fieldsets = [
        (None, {"fields": ["owner", "name", "kind", "tank", "is_active"]}),
        ("Netz", {"fields": ["host", "mac_address", "firmware", "generation"]}),
        (
            "Zugang",
            {
                "fields": ["username", "password", "password_status"],
                "description": "Werksseitig api / admin. Das Passwort liegt verschlüsselt in der "
                "Datenbank und wird nie angezeigt.",
            },
        ),
        (
            "Gerät",
            {
                "fields": ["manufacturer", "model_name", "installed_on",
                           "maintenance_interval_days", "last_maintenance_on"],
                "description": "Angaben, die jedes Gerät hat — auch eines ohne Anbindung.",
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
        kwargs["fields"] = ["owner", "name", "kind", "tank", "is_active", "host",
                            "mac_address", "firmware", "generation", "username", "password",
                            "manufacturer", "model_name", "installed_on",
                            "maintenance_interval_days", "last_maintenance_on"]
        return super().get_form(request, obj, **kwargs)


@admin.register(DeviceReading)
class DeviceReadingAdmin(admin.ModelAdmin):
    """Reines Leseprotokoll — Messwerte entstehen nur durch Abfragen."""

    list_display = ["read_at", "device", "is_on", "rpm_percent", "pump_mode", "power_w",
                    "energy_total_wh", "error_code"]
    list_filter = ["device", "error_code"]
    date_hierarchy = "read_at"
    readonly_fields = ["device", "read_at", "payload", "rpm_percent", "pump_mode", "error_code",
                       "service_due_in", "is_on", "power_w", "energy_total_wh", "temperature_c"]

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


@admin.register(AIConfig)
class AIConfigAdmin(admin.ModelAdmin):
    """Singleton-Admin: anlegen nur, solange keine Konfiguration existiert."""

    form = AIConfigForm
    fieldsets = [
        (None, {"fields": ["is_enabled", "status"]}),
        (
            "Zugang",
            {
                "fields": ["api_key", "model_name"],
                "description": "Der Key liegt verschlüsselt in der Datenbank und wird "
                "weder angezeigt noch protokolliert.",
            },
        ),
        (
            "Kostenkontrolle",
            {
                "fields": ["monthly_token_budget", "per_user_daily_limit", "usage"],
                "description": "Bilderkennung ist der teure Teil. Ist eine Grenze "
                "erreicht, werden weitere Aufrufe abgelehnt und dem Benutzer erklärt.",
            },
        ),
        ("Verwaltung", {"fields": ["updated_at"]}),
    ]
    readonly_fields = ["status", "usage", "updated_at"]

    @admin.display(description="Status")
    def status(self, obj):
        if obj is not None and obj.is_configured:
            return format_html('<span style="color:#4caf7d;">KI-Funktionen aktiv</span>')
        return format_html(
            '<span style="color:#d2483b;">Nicht konfiguriert – KI-Funktionen sind ausgeblendet</span>'
        )

    @admin.display(description="Verbrauch")
    def usage(self, obj):
        """Monatsverbrauch und Kosten auf einen Blick."""
        if obj is None or not obj.pk:
            return "—"
        current = budget_status(config=obj)
        spent = AIUsageLog.objects.filter(
            created_at__gte=timezone.localtime().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        ).aggregate(total=Sum("total_cost_usd"))["total"] or 0
        limit = f" von {current.monthly_limit}" if current.monthly_limit else " (ohne Grenze)"
        return format_html(
            "{} Token{} in diesem Monat · {} USD",
            current.monthly_used,
            limit,
            f"{spent:.2f}",
        )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not AIConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Abgeschaltet wird über "KI-Funktionen aktiv", nicht durch Löschen.
        return False

    def get_changeform_initial_data(self, request):
        """Neuanlage mit den Environment-Werten vorbelegen — ohne den Key."""
        initial = AIConfig.defaults_from_env()
        initial.pop("api_key", None)
        return initial


@admin.register(AIUsageLog)
class AIUsageLogAdmin(admin.ModelAdmin):
    """Reines Leseprotokoll — Einträge entstehen nur durch Aufrufe."""

    list_display = ["created_at", "action", "user", "model_name", "prompt_tokens",
                    "completion_tokens", "total_cost_usd", "duration_ms", "success"]
    list_filter = ["success", "action", "model_name", "created_at"]
    search_fields = ["user__username", "user__email"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "user", "action", "model_name", "prompt_tokens",
                       "completion_tokens", "total_cost_usd", "duration_ms", "success",
                       "error_message"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MeasurementAnalysis)
class MeasurementAnalysisAdmin(admin.ModelAdmin):
    """Einblick in die Messreihen-Auswertungen — ausgelöst wird am Becken."""

    list_display = ["created_at", "measurement", "status", "model_name", "usage_log"]
    list_filter = ["status", "model_name", "created_at"]
    search_fields = ["measurement__tank__name"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "completed_at", "measurement", "status", "text",
                       "model_name", "context_hash", "error_message", "usage_log"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AISuggestion)
class AISuggestionAdmin(admin.ModelAdmin):
    """Einblick in die Vorschläge. Entschieden wird in der Anwendung, nicht hier
    — die Bestätigung gehört zu dem Benutzer, der den Vorschlag angefordert
    hat."""

    list_display = ["created_at", "label", "kind", "user", "status", "confidence", "catalog_ref"]
    list_filter = ["status", "kind", "created_at"]
    search_fields = ["scientific_name", "common_name"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "decided_at", "user", "kind", "scientific_name",
                       "common_name", "confidence", "reasoning", "payload", "status",
                       "catalog_ref"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Vorschlag")
    def label(self, obj):
        return obj.label


@admin.register(MCPToken)
class MCPTokenAdmin(admin.ModelAdmin):
    """Einblick und Notbremse.

    Angelegt werden Tokens in der Anwendung, nicht hier: nur dort wird der
    Klartext einmalig ausgegeben, und ein Token gehört dem Benutzer, der ihn
    benutzt. Was der Admin kann, ist widerrufen — der einzige Eingriff, den
    ein Betreiber im Zweifel wirklich braucht.
    """

    list_display = ["name", "user", "hint", "access_label", "status_label",
                    "created_at", "last_used_at", "expires_at"]
    list_filter = ["allow_write", "created_at"]
    search_fields = ["name", "user__username", "user__email"]
    readonly_fields = ["user", "name", "hint", "allow_write", "created_at",
                       "last_used_at", "expires_at", "revoked_at"]
    actions = ["revoke_tokens"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Zugriff")
    def access_label(self, obj):
        return obj.access_label

    @admin.display(description="Status")
    def status_label(self, obj):
        return obj.status_label

    @admin.action(description="Ausgewählte Tokens widerrufen")
    def revoke_tokens(self, request, queryset):
        revoked = 0
        for token in queryset:
            if not token.is_revoked:
                token.revoke()
                revoked += 1
        self.message_user(request, f"{revoked} Token widerrufen.", messages.SUCCESS)


@admin.register(MCPAccessLog)
class MCPAccessLogAdmin(admin.ModelAdmin):
    """Reines Leseprotokoll — Einträge entstehen nur durch Aufrufe."""

    list_display = ["created_at", "tool", "user", "token_name", "object_ref", "succeeded"]
    list_filter = ["succeeded", "tool", "created_at"]
    search_fields = ["token_name", "user__username", "object_ref"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "token", "user", "token_name", "tool", "arguments",
                       "object_ref", "succeeded", "error_message"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
