"""Entfernt das zweite Gerätemodell und ergänzt die Ereigniskategorie Technik.

Die Daten sind zu diesem Zeitpunkt bereits übernommen (siehe
``services.0007_merge_devices``); die Abhängigkeit unten stellt genau das sicher.
Die neue Kategorie *Technik* ist das Ziel jeder Schaltaktion — geschrieben in
:func:`services.devices.record_event`.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tanks", "0002_default_parameters"),
        # Erst übernehmen, dann löschen.
        ("services", "0007_merge_devices"),
    ]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="category",
            field=models.CharField(
                choices=[
                    ("water_change", "Wasserwechsel"),
                    ("maintenance", "Wartung"),
                    ("treatment", "Behandlung"),
                    ("stocking", "Besatzänderung"),
                    ("equipment", "Technik"),
                    ("incident", "Vorfall"),
                    ("other", "Sonstiges"),
                ],
                default="other",
                max_length=15,
                verbose_name="Kategorie",
            ),
        ),
        migrations.DeleteModel(name="Device"),
    ]
