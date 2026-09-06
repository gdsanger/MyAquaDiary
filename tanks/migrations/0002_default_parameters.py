"""Legt die üblichen Wasserparameter samt Standard-Zielbereich an.

Ohne diese Grunddaten hätten Messwerte keinen Zielbereich und damit keinen
Status — Dashboard und Beckendetail wären farblich leer. Die Zielbereiche
gelten für ein durchschnittliches Süßwasser-Gesellschaftsbecken; pro Becken
lassen sie sich über ``TankParameterTarget`` überschreiben.
"""

from decimal import Decimal

from django.db import migrations

PARAMETERS = [
    # key, Name, Einheit, Nachkommastellen, min, max, Leitparameter, Reihenfolge
    ("temperatur", "Temperatur", "°C", 1, "23.0", "27.0", True, 10),
    ("ph", "pH-Wert", "", 1, "6.5", "7.5", True, 20),
    ("no2", "Nitrit", "mg/l", 2, None, "0.10", True, 30),
    ("no3", "Nitrat", "mg/l", 0, None, "25", False, 40),
    ("nh4", "Ammonium", "mg/l", 2, None, "0.10", False, 50),
    ("kh", "Karbonathärte", "°dH", 0, "4", "10", False, 60),
    ("gh", "Gesamthärte", "°dH", 0, "6", "16", False, 70),
    ("po4", "Phosphat", "mg/l", 2, "0.05", "1.00", False, 80),
    ("leitwert", "Leitfähigkeit", "µS/cm", 0, "200", "700", False, 90),
]


def _decimal(value):
    return None if value is None else Decimal(value)


def create_parameters(apps, schema_editor):
    Parameter = apps.get_model("tanks", "Parameter")
    for key, name, unit, decimals, minimum, maximum, is_key, order in PARAMETERS:
        Parameter.objects.update_or_create(
            key=key,
            defaults={
                "name": name,
                "unit": unit,
                "decimals": decimals,
                "default_min": _decimal(minimum),
                "default_max": _decimal(maximum),
                "is_key_parameter": is_key,
                "sort_order": order,
            },
        )


def remove_parameters(apps, schema_editor):
    Parameter = apps.get_model("tanks", "Parameter")
    Parameter.objects.filter(key__in=[row[0] for row in PARAMETERS]).delete()


class Migration(migrations.Migration):
    dependencies = [("tanks", "0001_initial")]

    operations = [migrations.RunPython(create_parameters, remove_parameters)]
