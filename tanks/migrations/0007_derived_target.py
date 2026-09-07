"""Beckeneigener Zielbereich für abgeleitete Größen (CO₂).

Nur der Zielbereich wird gespeichert, nie der gerechnete Wert selbst — siehe
``tanks/derived.py``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tanks", "0006_substrate_and_hardscape")]

    operations = [
        migrations.CreateModel(
            name="TankDerivedTarget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(choices=[("co2", "CO₂")], max_length=30, verbose_name="Größe")),
                ("minimum", models.DecimalField(blank=True, decimal_places=3, max_digits=8, null=True, verbose_name="min")),
                ("maximum", models.DecimalField(blank=True, decimal_places=3, max_digits=8, null=True, verbose_name="max")),
                (
                    "tank",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="derived_targets",
                        to="tanks.tank",
                    ),
                ),
            ],
            options={
                "verbose_name": "Zielbereich (berechnet)",
                "verbose_name_plural": "Zielbereiche (berechnet)",
            },
        ),
        migrations.AddConstraint(
            model_name="tankderivedtarget",
            constraint=models.UniqueConstraint(
                fields=("tank", "key"), name="unique_derived_target_per_tank"
            ),
        ),
    ]
