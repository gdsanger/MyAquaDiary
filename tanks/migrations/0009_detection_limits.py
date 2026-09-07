"""Belegt die Nachweisgrenzen der Parameter vor, die eine haben.

Betroffen sind die Schadstoffe und Nährstoffe, die ein Tröpfchentest als
„nicht nachweisbar" melden kann: Nitrit, Ammonium, Nitrat und Phosphat. Die
Werte sind die üblichen Auflösungsgrenzen gängiger Tests und lassen sich am
Parameter überschreiben, wer einen anderen Testkoffer nutzt.

**Vorhandene Nullwerte werden nicht umgedeutet.** Bei einem als 0 erfassten
Nitritwert ist nicht entscheidbar, ob n.n. gemeint war — diese Migration rührt
die Messwerte deshalb nicht an. ``below_detection`` bleibt für Bestandsdaten
auf ``False`` (der Feldstandard); die Umdeutung eines alten Werts bleibt dem
Nutzer überlassen.

Eisen und Kupfer nennt die Anforderung ebenfalls, es gibt sie aber (noch) nicht
als Parameter. Sobald sie angelegt werden, gehört ihre Nachweisgrenze dazu.
"""

from decimal import Decimal

from django.db import migrations

#: key -> Nachweisgrenze. Auflösung gängiger Tröpfchentests.
DETECTION_LIMITS = {
    "no2": Decimal("0.01"),
    "nh4": Decimal("0.05"),
    "no3": Decimal("1.0"),
    "po4": Decimal("0.02"),
}


def set_detection_limits(apps, schema_editor):
    Parameter = apps.get_model("tanks", "Parameter")
    for key, limit in DETECTION_LIMITS.items():
        Parameter.objects.filter(key=key).update(detection_limit=limit)


def clear_detection_limits(apps, schema_editor):
    Parameter = apps.get_model("tanks", "Parameter")
    Parameter.objects.filter(key__in=DETECTION_LIMITS).update(detection_limit=None)


class Migration(migrations.Migration):
    dependencies = [("tanks", "0008_detection_limit_and_below_detection")]

    operations = [migrations.RunPython(set_detection_limits, clear_detection_limits)]
