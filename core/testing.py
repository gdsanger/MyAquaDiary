"""Hilfsfunktionen, die sich mehrere Testmodule teilen."""

from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from catalog.models import AnimalSpecies, PlantSpecies
from tanks.models import CareTask, Measurement, Parameter, Stocking, Tank

PASSWORD = "test-passwort-1234"


def create_user(username="aquarianer", **kwargs):
    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", password=PASSWORD, **kwargs
    )


def create_tank(owner, name="Gesellschaftsbecken", **kwargs):
    defaults = {
        "slug": kwargs.pop("slug", name.lower().replace(" ", "-")),
        "volume_liters": Decimal("240.0"),
        "setup_date": timezone.localdate() - timedelta(days=400),
        "accent": 3,
    }
    defaults.update(kwargs)
    return Tank.objects.create(owner=owner, name=name, **defaults)


def create_measurement(tank, parameter_key="ph", value="7.0", days_ago=0):
    parameter = Parameter.objects.get(key=parameter_key)
    return Measurement.objects.create(
        tank=tank,
        parameter=parameter,
        value=Decimal(value),
        measured_at=timezone.now() - timedelta(days=days_ago),
    )


def create_task(tank, title="Wasserwechsel", days_until_due=0, interval_days=7):
    return CareTask.objects.create(
        tank=tank,
        title=title,
        category=CareTask.Category.WATER_CHANGE,
        interval_days=interval_days,
        due_on=timezone.localdate() + timedelta(days=days_until_due),
    )


def create_animal(scientific_name="Paracheirodon innesi", **kwargs):
    defaults = {
        "slug": kwargs.pop("slug", scientific_name.lower().replace(" ", "-")),
        "common_name": kwargs.pop("common_name", "Neonsalmler"),
        "min_group_size": 10,
    }
    defaults.update(kwargs)
    return AnimalSpecies.objects.create(scientific_name=scientific_name, **defaults)


def create_plant(scientific_name="Cryptocoryne wendtii", **kwargs):
    defaults = {
        "slug": kwargs.pop("slug", scientific_name.lower().replace(" ", "-")),
        "common_name": kwargs.pop("common_name", "Wendts Wasserkelch"),
    }
    defaults.update(kwargs)
    return PlantSpecies.objects.create(scientific_name=scientific_name, **defaults)


def image_upload(name="foto.png"):
    """Winziges, echtes PNG als Upload.

    Ein ``ImageField`` lässt Pillow prüfen, ob die Datei wirklich ein Bild ist;
    ein paar zufällige Bytes reichen dafür nicht.
    """
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def stock(tank, species, quantity=12, days_ago=30):
    return Stocking.objects.create(
        tank=tank,
        species=species,
        quantity=quantity,
        added_on=timezone.localdate() - timedelta(days=days_ago),
    )
