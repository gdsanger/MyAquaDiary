"""Das Becken kommt als Fremdschlüssel an das Gerät — zunächst optional.

Nur Schema, keine Daten: die Zuordnung steht in ``0007_merge_devices``. Beides
in einer Migration ginge nicht — PostgreSQL legt den Index einer neuen Spalte
erst am Ende der Transaktion an und verweigert das, sobald in derselben
Transaktion Zeilen derselben Tabelle geschrieben wurden.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tanks", "0002_default_parameters"),
        ("services", "0005_mcp_token_and_access_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="tank",
            field=models.ForeignKey(
                null=True,
                help_text="Becken, an dem das Gerät hängt — Grundlage der Verbrauchsauswertung "
                "und der Warnungen.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="devices",
                to="tanks.tank",
                verbose_name="Becken",
            ),
        ),
        migrations.AddField(
            model_name="device",
            name="manufacturer",
            field=models.CharField(blank=True, max_length=80, verbose_name="Hersteller"),
        ),
        migrations.AddField(
            model_name="device",
            name="model_name",
            field=models.CharField(blank=True, max_length=80, verbose_name="Modell"),
        ),
        migrations.AddField(
            model_name="device",
            name="installed_on",
            field=models.DateField(blank=True, null=True, verbose_name="In Betrieb seit"),
        ),
        migrations.AddField(
            model_name="device",
            name="maintenance_interval_days",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Leer lassen, wenn das Gerät keine wiederkehrende Wartung braucht.",
                null=True,
                verbose_name="Wartungsintervall (Tage)",
            ),
        ),
        migrations.AddField(
            model_name="device",
            name="last_maintenance_on",
            field=models.DateField(blank=True, null=True, verbose_name="Letzte Wartung"),
        ),
        migrations.AddField(
            model_name="device",
            name="status",
            field=models.CharField(
                choices=[
                    ("ok", "In Ordnung"),
                    ("warn", "Abweichung"),
                    ("critical", "Kritisch"),
                    ("unknown", "Unbekannt"),
                ],
                default="ok",
                help_text="Bei angebundenen Geräten aus dem letzten Messwert fortgeschrieben, "
                "sonst eine Handeingabe.",
                max_length=10,
                verbose_name="Status",
            ),
        ),
        migrations.AddField(
            model_name="device",
            name="status_message",
            field=models.CharField(blank=True, max_length=200, verbose_name="Statusmeldung"),
        ),
        migrations.AlterField(
            model_name="device",
            name="kind",
            field=models.CharField(
                choices=[
                    ("eheim_classicvario", "Eheim classicVARIO+e"),
                    ("eheim_other", "Eheim (sonstiges)"),
                    ("shelly_plug", "Shelly Plug"),
                    ("filter", "Filter"),
                    ("heater", "Heizer"),
                    ("light", "Beleuchtung"),
                    ("co2", "CO₂-Anlage"),
                    ("pump", "Pumpe"),
                    ("doser", "Dosierpumpe"),
                    ("sensor", "Sensor"),
                    ("socket", "Steckdose"),
                    ("other", "Sonstiges"),
                ],
                max_length=30,
                verbose_name="Art",
            ),
        ),
    ]
