"""Macht das Becken zur Pflicht und entfernt den Freitext.

Bewusst eine eigene Migration: ``0007_merge_devices`` ordnet zu, was eindeutig
zuordenbar ist, und meldet den Rest. Erst wenn der von Hand nachgezogen ist, kann die
Spalte ``NOT NULL`` werden. Läuft das Deployment vorher, bricht es hier mit
einer Liste der betroffenen Geräte ab — das ist der Sinn der Trennung. Ein
Gerät stillschweigend zu löschen oder einem geratenen Becken zuzuordnen wäre
das schlechtere Ende.
"""

import django.db.models.deletion
from django.db import migrations, models


def require_tank(apps, schema_editor):
    Device = apps.get_model("services", "Device")
    orphans = list(Device.objects.filter(tank__isnull=True).values_list("name", flat=True))
    if not orphans:
        return
    raise RuntimeError(
        "Diese Geräte hängen an keinem Becken: "
        + ", ".join(orphans)
        + ". Bitte im Django-Admin unter „Geräte“ jeweils ein Becken eintragen "
        "(oder das Gerät löschen) und die Migration danach erneut starten."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0007_merge_devices"),
    ]

    operations = [
        migrations.RunPython(require_tank, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="device",
            name="tank",
            field=models.ForeignKey(
                help_text="Becken, an dem das Gerät hängt — Grundlage der Verbrauchsauswertung "
                "und der Warnungen.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="devices",
                to="tanks.tank",
                verbose_name="Becken",
            ),
        ),
        migrations.RemoveField(
            model_name="device",
            name="tank_label",
        ),
    ]
