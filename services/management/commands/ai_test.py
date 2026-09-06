"""Verbindungstest für die KI-Assistenz.

Prüft von der Kommandozeile aus, ob der hinterlegte API-Key trägt und welches
Modell antwortet — ohne dafür ein Foto hochzuladen. Der Aufruf kostet ein paar
Dutzend Token und taucht wie jeder andere im Verbrauchsprotokoll auf.
"""

from django.core.management.base import BaseCommand, CommandError

from services.ai import AIService
from services.models import AIUsageLog


class Command(BaseCommand):
    help = "Stellt eine Testanfrage an Anthropic Claude."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prompt",
            default="Antworte mit einem einzigen Satz: Wozu dient ein Wasserwechsel?",
            help="Frage, die gestellt wird.",
        )

    def handle(self, *args, **options):
        service = AIService()
        if not service.is_configured:
            raise CommandError(
                "Die KI-Assistenz ist nicht eingerichtet. Der API-Key wird im Admin "
                "unter Services → KI-Konfiguration hinterlegt (oder über "
                "ANTHROPIC_API_KEY vorgegeben)."
            )

        self.stdout.write(f"Modell: {service.model_name}")
        result = service.ask(
            AIUsageLog.Action.TEST,
            system="Du antwortest knapp und auf Deutsch.",
            prompt=options["prompt"],
            effort="low",
            max_tokens=1000,
        )
        if not result:
            raise CommandError(result.error)

        self.stdout.write(result.text)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.total_tokens} Token ({result.prompt_tokens} ein, "
                f"{result.completion_tokens} aus) · {result.cost_usd} USD · "
                f"{result.duration_ms} ms"
            )
        )
