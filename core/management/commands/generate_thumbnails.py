"""Bildvarianten für Bestandsdaten nachziehen.

Neue Bilder bekommen ihre Varianten beim Speichern. Was vorher hochgeladen
wurde, hat nur das Original — dieser Befehl holt es nach. Bis er gelaufen ist,
zeigen die Ansichten weiter das Original; sichtbar ist also alles, nur noch
nicht klein.
"""

from django.apps import apps
from django.core.management.base import BaseCommand

from core.images import VariantError, build_variants, drop_location_in_storage


class Command(BaseCommand):
    help = "Erzeugt fehlende Bildvarianten (Kachel, Vorschau) für vorhandene Bilder."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Vorhandene Varianten verwerfen und neu erzeugen.",
        )
        parser.add_argument(
            "--model",
            action="append",
            metavar="app.Modell",
            help="Nur dieses Modell bearbeiten; mehrfach angebbar.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        models = self.image_models(options.get("model"))
        if not models:
            self.stdout.write(self.style.WARNING("Kein Modell mit Bildvarianten gefunden."))
            return

        for model in models:
            done, skipped, failed = self.process(model, force=force)
            label = f"{model._meta.app_label}.{model.__name__}"
            self.stdout.write(
                f"{label}: {done} bearbeitet, {skipped} bereits vollständig, {failed} fehlerhaft"
            )

    def image_models(self, wanted):
        """Alle Modelle mit ``IMAGE_VARIANTS`` — auf Wunsch gefiltert.

        Kein Register, das jemand pflegen müsste: ein neues Bildmodell wird
        hier allein dadurch bekannt, dass es den Mixin verwendet.
        """
        found = [
            model for model in apps.get_models() if getattr(model, "IMAGE_VARIANTS", None) is not None
        ]
        if not wanted:
            return found
        names = {name.lower() for name in wanted}
        return [
            model
            for model in found
            if f"{model._meta.app_label}.{model.__name__}".lower() in names
            or model._meta.label_lower in names
        ]

    def process(self, model, *, force):
        fields = model.IMAGE_VARIANTS
        done = skipped = failed = 0
        # ``iterator`` statt einer Liste: die Bestandsdaten können viele sein,
        # und je Objekt wird ohnehin eine Bilddatei gelesen.
        for obj in model.objects.exclude(**{fields.source: ""}).iterator():
            try:
                changed = list(build_variants(obj, fields, force=force))
                if drop_location_in_storage(obj, fields):
                    changed.append(fields.source)
            except VariantError as exc:
                failed += 1
                self.stderr.write(self.style.WARNING(f"{model.__name__} {obj.pk}: {exc}"))
                continue
            if not changed:
                skipped += 1
                continue
            # Am Modell vorbei speichern: ``save()`` würde die Varianten ein
            # zweites Mal prüfen, und mehr als diese Felder ändert sich nicht.
            model.objects.filter(pk=obj.pk).update(
                **{name: getattr(obj, name) for name in changed}
            )
            done += 1
        return done, skipped, failed
