"""Die Datenmigration, die aus zwei Gerätemodellen eines macht.

Sie läuft auf jeder Installation genau einmal und ist danach nicht mehr
korrigierbar — deshalb steht sie hier mit echten Migrationen auf einer echten
Datenbank, nicht als Aufruf ihrer Hilfsfunktionen.

Geprüft wird beides: was sie zuordnet, und was sie ausdrücklich **nicht**
zuordnet. ``tank_label`` war Freitext; ein geratenes Becken wäre schlimmer als
ein liegen gebliebenes, weil es niemandem mehr auffällt.
"""

import io
from datetime import date
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from tanks.models import Tank

#: Die Wasserparameter aus der Datenmigration — hier gebraucht, um sie nach dem
#: Leeren der Tabellen wiederherzustellen. Der Modulname beginnt mit einer
#: Ziffer und lässt sich deshalb nicht regulär importieren.
create_parameters = import_module("tanks.migrations.0002_default_parameters").create_parameters

BEFORE = [("services", "0005_mcp_token_and_access_log"), ("tanks", "0002_default_parameters")]
MERGED = [("services", "0007_merge_devices")]
REQUIRED = [("services", "0008_device_tank_required")]


class DeviceMigrationTestCase(TransactionTestCase):
    """Fährt die Datenbank auf den Stand vor dem Zusammenführen zurück."""

    def setUp(self):
        self.migrate(BEFORE)
        self.addCleanup(self.migrate_to_latest)
        self.old_apps = MigrationExecutor(connection).loader.project_state(BEFORE).apps
        self.Device = self.old_apps.get_model("services", "Device")
        self.LegacyDevice = self.old_apps.get_model("tanks", "Device")
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.tank = self.tank_named("Becken 1")

    def _post_teardown(self):
        """Stellt nach dem Leeren der Tabellen die Grunddaten wieder her.

        ``TransactionTestCase`` leert am Ende jede Tabelle — auch die
        Wasserparameter aus der Datenmigration, auf die die übrigen Tests
        bauen. ``serialized_rollback`` scheidet aus: es serialisiert beim
        Anlegen der Testdatenbank die ganze Datenbank, und die Ersatzmodelle
        der MCP-Tests haben zu diesem Zeitpunkt noch keine Tabellen.
        """
        super()._post_teardown()
        create_parameters(apps, None)

    # -- Migrieren ------------------------------------------------------------

    @staticmethod
    def migrate(targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor

    def migrate_to_latest(self):
        """Fährt die Datenbank wieder hoch — für die folgenden Tests.

        Vorher fällt weg, was ein Test absichtlich liegen lässt: ein Gerät ohne
        Becken bringt ``0008`` zu Recht zum Abbruch, hier ist es nur Rest.
        """
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM services_device WHERE tank_id IS NULL")
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def merge(self):
        """Führt die Zusammenführung aus und gibt ihre Ausgabe zurück."""
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.migrate(MERGED)
        return output.getvalue()

    # -- Daten ----------------------------------------------------------------

    def tank_named(self, name, slug=None):
        return Tank.objects.create(
            owner=self.user,
            name=name,
            slug=slug or name.lower().replace(" ", "-"),
            volume_liters="240.0",
            setup_date=date(2024, 1, 1),
        )

    def plug(self, name="Licht", label="Becken 1", owner=None):
        return self.Device.objects.create(
            owner_id=(owner or self.user).pk, name=name, kind="shelly_plug",
            host="192.168.1.60", tank_label=label,
        )

    def legacy(self, tank=None, **kwargs):
        fields = {
            "name": "Außenfilter",
            "kind": "filter",
            "manufacturer": "Eheim",
            "model_name": "2075",
            "installed_on": date(2024, 3, 1),
            "maintenance_interval_days": 90,
            "last_maintenance_on": date(2025, 1, 5),
            "status": "warn",
            "status_message": "Läuft laut",
        }
        fields.update(kwargs)
        return self.LegacyDevice.objects.create(tank_id=(tank or self.tank).pk, **fields)

    def merged(self, **lookup):
        """Ein Gerät nach der Migration, gelesen mit dem neuen Modell."""
        from services.models import Device

        return Device.objects.get(**lookup)


class LabelMatchingTests(DeviceMigrationTestCase):
    def test_a_unique_name_match_assigns_the_tank(self):
        self.plug(label="  becken 1 ")

        self.merge()

        self.assertEqual(self.merged(name="Licht").tank, self.tank)

    def test_an_unknown_label_stays_unassigned_and_is_reported(self):
        self.plug(label="Keller")

        output = self.merge()

        self.assertIsNone(self.merged(name="Licht").tank_id)
        self.assertIn("Licht", output)
        self.assertIn("Keller", output)

    def test_an_empty_label_is_reported_too(self):
        self.plug(label="")

        output = self.merge()

        self.assertIsNone(self.merged(name="Licht").tank_id)
        self.assertIn("ohne Angabe", output)

    def test_an_ambiguous_label_is_not_guessed(self):
        # Zwei Becken desselben Benutzers mit demselben Namen: welches gemeint
        # war, weiß der Freitext nicht — und die Migration rät es nicht.
        self.tank_named("Becken 1", slug="becken-1-zwei")
        self.plug()

        self.merge()

        self.assertIsNone(self.merged(name="Licht").tank_id)

    def test_the_tank_of_another_owner_is_no_match(self):
        stranger = get_user_model().objects.create_user(
            username="eva", email="eva@example.com", password="geheim-123"
        )
        self.plug(label="Becken 1", owner=stranger)

        self.merge()

        self.assertIsNone(self.merged(name="Licht").tank_id)

    def test_nothing_is_printed_when_everything_matches(self):
        self.plug()

        self.assertEqual(self.merge(), "")


class CarryOverTests(DeviceMigrationTestCase):
    def test_a_manual_device_moves_over_with_its_fields(self):
        self.legacy()

        self.merge()

        device = self.merged(name="Außenfilter")
        self.assertEqual(device.owner, self.user)
        self.assertEqual(device.tank, self.tank)
        self.assertEqual(device.kind, "filter")
        self.assertEqual(device.manufacturer, "Eheim")
        self.assertEqual(device.model_name, "2075")
        self.assertEqual(device.installed_on, date(2024, 3, 1))
        self.assertEqual(device.maintenance_interval_days, 90)
        self.assertEqual(device.last_maintenance_on, date(2025, 1, 5))
        self.assertEqual(device.status, "warn")
        self.assertEqual(device.status_message, "Läuft laut")

    def test_the_same_name_in_the_same_tank_is_merged_not_duplicated(self):
        self.plug(name="Außenfilter")
        self.legacy(name="außenfilter")

        self.merge()

        from services.models import Device

        device = Device.objects.get()
        # Die Anbindung bleibt, die Pflegeangaben kommen dazu.
        self.assertEqual(device.kind, "shelly_plug")
        self.assertEqual(device.manufacturer, "Eheim")
        self.assertEqual(device.maintenance_interval_days, 90)

    def test_the_same_name_in_another_tank_stays_a_second_device(self):
        other = self.tank_named("Becken 2")
        self.plug(name="Außenfilter")
        self.legacy(tank=other, name="Außenfilter")

        self.merge()

        from services.models import Device

        self.assertEqual(Device.objects.count(), 2)
        self.assertEqual(
            {device.tank_id for device in Device.objects.all()}, {self.tank.pk, other.pk}
        )


class RequiredTankTests(DeviceMigrationTestCase):
    def test_the_follow_up_migration_refuses_unassigned_devices(self):
        self.plug(label="Keller")
        self.merge()

        with self.assertRaises(RuntimeError) as caught:
            self.migrate(REQUIRED)

        self.assertIn("Licht", str(caught.exception))
        self.assertIn("Admin", str(caught.exception))

    def test_after_the_hand_over_the_column_becomes_mandatory(self):
        self.plug()
        self.merge()

        self.migrate(REQUIRED)

        from services.models import Device

        self.assertEqual(Device.objects.get().tank, self.tank)
        self.assertFalse(hasattr(Device.objects.get(), "tank_label"))
