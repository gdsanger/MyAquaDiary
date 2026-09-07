"""Ereigniskategorie *Beobachtung* und Fotos am Ereignis.

Eine Beobachtung ist ein Ereignis — Zeitpunkt, Titel, Text, Becken — und
bekommt deshalb eine Kategorie statt eines eigenen Modells. Was dem Ereignis
bisher fehlte, sind die Bilder: ``TankPhoto.event`` ist optional und wird beim
Löschen des Ereignisses auf ``NULL`` gesetzt, nicht mitgelöscht. Ein Foto
gehört dem Becken; die Zuordnung zum Ereignis ist eine Angabe darüber und
nicht seine Existenzgrundlage.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tanks", "0003_remove_device_event_equipment"),
    ]

    operations = [
        migrations.AddField(
            model_name="tankphoto",
            name="event",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="photos",
                to="tanks.event",
                verbose_name="Ereignis",
            ),
        ),
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
                    ("observation", "Beobachtung"),
                    ("other", "Sonstiges"),
                ],
                default="other",
                max_length=15,
                verbose_name="Kategorie",
            ),
        ),
    ]
