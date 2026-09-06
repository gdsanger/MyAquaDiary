"""Testmail von der Kommandozeile — u. a. zum Prüfen der Templates in echten Clients."""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services.graph import mail_enabled, send_template_mail
from services.models import MailConfig

#: Beispielkontexte, damit sich jedes Template ohne Echtdaten verschicken lässt.
SAMPLE_CONTEXTS = {
    "test_mail": lambda config: {"sender_address": config.sender_address},
    "appointment_reminder": lambda config: {
        "recipient_name": "Testbenutzer",
        "appointments": [
            {
                "title": "Wasserwechsel 30 %",
                "tank_name": "Gesellschaftsbecken 240 l",
                "due_date": timezone.localdate(),
            },
            {
                "title": "Filterschwämme ausspülen",
                "tank_name": "Nano-Becken 30 l",
                "due_date": timezone.localdate() + timedelta(days=1),
            },
        ],
        "detail_url": "",
    },
    "measurement_alert": lambda config: {
        "recipient_name": "Testbenutzer",
        "tank_name": "Gesellschaftsbecken 240 l",
        "parameter": "Nitrit (NO2)",
        "value": "0,8",
        "unit": "mg/l",
        "target_min": "0,0",
        "target_max": "0,1",
        "measured_at": timezone.now(),
        "detail_url": "",
    },
    "account_activation": lambda config: {
        "recipient_name": "Testbenutzer",
        "activation_url": "https://example.invalid/aktivieren/beispiel-token",
        "valid_hours": 24,
    },
    "password_reset": lambda config: {
        "recipient_name": "Testbenutzer",
        "reset_url": "https://example.invalid/passwort/beispiel-token",
        "valid_hours": 24,
    },
}


class Command(BaseCommand):
    help = (
        "Verschickt eine Testmail über Microsoft Graph. Mit --template lässt sich "
        "jedes Mailtemplate mit Beispieldaten an echte Postfächer schicken."
    )

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Empfängeradresse")
        parser.add_argument(
            "--template",
            default="test_mail",
            choices=sorted(SAMPLE_CONTEXTS),
            help="Zu versendendes Mailtemplate (Standard: test_mail)",
        )

    def handle(self, *args, **options):
        if not mail_enabled():
            raise CommandError(
                "Der Mailversand ist nicht konfiguriert. Zugangsdaten im Admin unter "
                "'Mail-Konfiguration' pflegen oder GRAPH_*-Variablen setzen."
            )

        config = MailConfig.load()
        template = options["template"]
        recipient = options["recipient"]

        result = send_template_mail(recipient, template, SAMPLE_CONTEXTS[template](config))
        if not result:
            raise CommandError(f"Versand fehlgeschlagen: {result.error}")
        self.stdout.write(self.style.SUCCESS(f"Mail '{template}' an {recipient} versendet."))
