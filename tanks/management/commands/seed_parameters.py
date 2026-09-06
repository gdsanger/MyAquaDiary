from django.core.management.base import BaseCommand

from tanks.models import Parameter

PARAMETERS = [
    {"key": "ph", "name": "pH-Wert", "unit": "", "decimals": 1},
    {"key": "kh", "name": "Karbonathärte (KH)", "unit": "°dKH", "decimals": 1},
    {"key": "gh", "name": "Gesamthärte (GH)", "unit": "°dGH", "decimals": 1},
    {"key": "temp", "name": "Temperatur", "unit": "°C", "decimals": 1},
    {"key": "lf", "name": "Leitfähigkeit", "unit": "µS/cm", "decimals": 0},
    {
        "key": "nh4",
        "name": "Ammonium (NH4)",
        "unit": "mg/l",
        "decimals": 2,
        "supports_below_detection": True,
    },
    {
        "key": "no2",
        "name": "Nitrit (NO2)",
        "unit": "mg/l",
        "decimals": 2,
        "supports_below_detection": True,
    },
    {
        "key": "no3",
        "name": "Nitrat (NO3)",
        "unit": "mg/l",
        "decimals": 1,
        "supports_below_detection": True,
    },
    {
        "key": "po4",
        "name": "Phosphat (PO4)",
        "unit": "mg/l",
        "decimals": 2,
        "supports_below_detection": True,
    },
    {
        "key": "fe",
        "name": "Eisen (Fe)",
        "unit": "mg/l",
        "decimals": 2,
        "supports_below_detection": True,
    },
    {"key": "k", "name": "Kalium (K)", "unit": "mg/l", "decimals": 1},
    {"key": "mg", "name": "Magnesium (Mg)", "unit": "mg/l", "decimals": 1},
    {
        "key": "cu",
        "name": "Kupfer (Cu)",
        "unit": "mg/l",
        "decimals": 3,
        "supports_below_detection": True,
    },
    {"key": "o2", "name": "Sauerstoff (O2)", "unit": "mg/l", "decimals": 1},
]


class Command(BaseCommand):
    help = "Befüllt den globalen Parameter-Katalog mit den Standard-Messgrößen."

    def handle(self, *args, **options):
        for position, entry in enumerate(PARAMETERS, start=1):
            defaults = {**entry, "position": position * 10}
            key = defaults.pop("key")
            parameter, created = Parameter.objects.update_or_create(
                key=key, defaults=defaults
            )
            verb = "angelegt" if created else "aktualisiert"
            self.stdout.write(f"{parameter.name} ({parameter.key}) {verb}.")
