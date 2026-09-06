"""Führt die beiden Gerätebestände zusammen.

Zwei Schritte:

1. Jedes angebundene Gerät bekommt sein Becken über ``tank_label``. Verglichen
   wird getrimmt und ohne Rücksicht auf Groß- und Kleinschreibung, und zwar
   ausschließlich gegen die Becken **des Besitzers**. Zugeordnet wird nur bei
   genau einem Treffer.
2. Die manuell gepflegten ``tanks.Device``-Einträge wandern herüber. Trägt im
   selben Becken schon ein Gerät denselben Namen, werden beide zusammengeführt
   statt ein zweites anzulegen.

Was sich nicht eindeutig zuordnen lässt, bleibt stehen und wird beim Deployment
ausgegeben. Geraten wird nicht: ``tank_label`` ist Freitext, und ein falsch
zugeordnetes Gerät fällt später niemandem mehr auf. Pflicht wird der
Fremdschlüssel erst in ``0008_device_tank_required`` — nach der Nachpflege von
Hand.
"""

import sys

from django.db import migrations


def _tanks_by_name(Tank, owner_id, cache):
    """``{Beckenname: [Becken]}`` eines Besitzers, getrimmt und kleingeschrieben."""
    if owner_id not in cache:
        mapping = {}
        for tank in Tank.objects.filter(owner_id=owner_id):
            mapping.setdefault(tank.name.strip().casefold(), []).append(tank)
        cache[owner_id] = mapping
    return cache[owner_id]


def resolve_tank(Tank, device, cache):
    """Das Becken zu einem ``tank_label`` — nur bei einem eindeutigen Treffer."""
    label = (device.tank_label or "").strip().casefold()
    if not label:
        return None
    matches = _tanks_by_name(Tank, device.owner_id, cache).get(label, [])
    return matches[0] if len(matches) == 1 else None


#: Felder, die ein ``tanks.Device`` mitbringt und die es hier vorher nicht gab.
CARRIED_OVER = [
    "manufacturer",
    "model_name",
    "installed_on",
    "maintenance_interval_days",
    "last_maintenance_on",
]


def merge_devices(apps, schema_editor):
    Device = apps.get_model("services", "Device")
    Tank = apps.get_model("tanks", "Tank")
    LegacyDevice = apps.get_model("tanks", "Device")

    cache = {}
    unresolved = []
    for device in Device.objects.filter(tank__isnull=True):
        tank = resolve_tank(Tank, device, cache)
        if tank is None:
            unresolved.append(device)
            continue
        device.tank = tank
        device.save(update_fields=["tank"])

    for legacy in LegacyDevice.objects.select_related("tank"):
        _carry_over(Device, legacy)

    _report(unresolved)


def _carry_over(Device, legacy):
    """Übernimmt ein manuell gepflegtes Gerät — oder ergänzt das vorhandene."""
    existing = Device.objects.filter(
        tank_id=legacy.tank_id, name__iexact=legacy.name.strip()
    ).first()
    if existing is None:
        Device.objects.create(
            owner_id=legacy.tank.owner_id,
            tank_id=legacy.tank_id,
            name=legacy.name,
            kind=legacy.kind,
            status=legacy.status,
            status_message=legacy.status_message,
            last_seen=legacy.last_seen_at,
            **{field: getattr(legacy, field) for field in CARRIED_OVER},
        )
        return

    # Zusammenführen heißt ergänzen: was am angebundenen Gerät schon steht,
    # ist aktueller als der Handeintrag und bleibt stehen.
    changed = [field for field in CARRIED_OVER if not getattr(existing, field)
               and getattr(legacy, field)]
    for field in changed:
        setattr(existing, field, getattr(legacy, field))
    if changed:
        existing.save(update_fields=changed)


def _report(unresolved):
    """Meldet, was von Hand nachgezogen werden muss — sichtbar im Deployment."""
    if not unresolved:
        return
    sys.stdout.write(
        f"\n  {len(unresolved)} Gerät(e) ohne eindeutiges Becken. Bitte im Admin "
        "zuordnen, bevor die nächste Migration läuft:\n"
    )
    for device in unresolved:
        label = (device.tank_label or "").strip() or "ohne Angabe"
        sys.stdout.write(f"    - {device.name} (Becken laut Freitext: {label})\n")


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0006_device_tank"),
    ]

    operations = [
        migrations.RunPython(merge_devices, migrations.RunPython.noop),
    ]
