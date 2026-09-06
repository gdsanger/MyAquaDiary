from collections import defaultdict

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from tanks.models import MaintenanceSchedule


class Command(BaseCommand):
    """Für den täglichen Cron gedacht. Der eigentliche Versand läuft über
    den konfigurierten E-Mail-Backend (perspektivisch der Graph-API-Service,
    eigenes Ticket) — hier wird nur ausgewählt, wer wann erinnert wird."""

    help = "Versendet Erinnerungen für fällige und anstehende Termine per E-Mail."

    def handle(self, *args, **options):
        today = timezone.localdate()
        schedules = (
            MaintenanceSchedule.objects.filter(is_active=True, notify_email=True)
            .exclude(last_reminded_on=today)
            .select_related("tank", "tank__owner")
            .order_by("tank__owner_id", "next_due_on")
        )

        due_by_user = defaultdict(list)
        for schedule in schedules:
            if schedule.is_due or schedule.is_upcoming:
                due_by_user[schedule.tank.owner].append(schedule)

        sent_count = 0
        for user, user_schedules in due_by_user.items():
            if not user.email:
                continue
            self._send_reminder(user, user_schedules, today)
            for schedule in user_schedules:
                schedule.last_reminded_on = today
            MaintenanceSchedule.objects.bulk_update(user_schedules, ["last_reminded_on"])
            sent_count += 1

        self.stdout.write(f"{sent_count} Erinnerungs-Mail(s) versendet.")

    def _send_reminder(self, user, schedules, today):
        lines = [
            f"- {schedule.tank.name}: {schedule.title} "
            f"({'fällig' if schedule.next_due_on <= today else 'anstehend'}, {schedule.next_due_on})"
            for schedule in schedules
        ]
        send_mail(
            subject="MyAquaDiary – fällige Termine",
            message="Diese Termine warten auf dich:\n\n" + "\n".join(lines),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
        )
