"""Periodisches Abfragen der angebundenen Geräte (für Cron)."""

from django.core.management.base import BaseCommand

from services import devices as device_service
from services.models import Device


class Command(BaseCommand):
    help = (
        "Fragt alle aktiven Geräte ab und legt je Gerät einen Messwert an. "
        "Ein nicht erreichbares Gerät wird gemeldet, bricht den Lauf aber nicht ab."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--device",
            type=int,
            dest="device_id",
            help="Nur dieses Gerät abfragen (ID).",
        )
        parser.add_argument(
            "--owner",
            dest="owner",
            help="Nur Geräte dieses Benutzernamens abfragen.",
        )

    def handle(self, *args, **options):
        queryset = Device.objects.filter(is_active=True, kind__in=Device.EHEIM_KINDS)
        if options.get("device_id"):
            queryset = queryset.filter(pk=options["device_id"])
        if options.get("owner"):
            queryset = queryset.filter(owner__username=options["owner"])

        polled = failed = 0
        for device in queryset:
            result = device_service.probe(device)
            if result:
                polled += 1
                reading = result.reading
                details = []
                if reading.rpm_percent is not None:
                    details.append(f"{reading.rpm_percent} %")
                if reading.mode_label:
                    details.append(reading.mode_label)
                self.stdout.write(f"{device}: {', '.join(details) or 'gelesen'}")
                if reading.has_error:
                    self.stdout.write(self.style.WARNING(f"{device}: {reading.error_text}"))
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"{device}: {result.error}"))

        summary = f"{polled} Gerät(e) abgefragt, {failed} nicht erreichbar."
        self.stdout.write(self.style.SUCCESS(summary) if not failed else summary)
