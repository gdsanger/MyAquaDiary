"""Verbrauchsauswertung: aus Zählerständen wird Verbrauch je Zeitraum."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from services import energy
from services.models import DeviceReading
from services.tests.test_shelly_devices import make_plug


class EnergyTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.device = make_plug(self.user, name="Licht", tank_name="Becken 1")

    def add_reading(self, watt_hours, *, days_ago=0, hours_ago=0, device=None):
        return DeviceReading.objects.create(
            device=device or self.device,
            read_at=timezone.now() - timezone.timedelta(days=days_ago, hours=hours_ago),
            payload={},
            energy_total_wh=Decimal(str(watt_hours)),
        )


class BucketTests(EnergyTestCase):
    def test_consumption_is_the_difference_between_meter_readings(self):
        self.add_reading(1000, hours_ago=3)
        self.add_reading(1500, hours_ago=2)
        self.add_reading(2500, hours_ago=1)

        buckets = energy.device_buckets(self.device, energy.PERIOD_DAY)

        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].kwh, Decimal("1.500"))

    def test_the_first_reading_alone_is_no_consumption(self):
        self.add_reading(1000)
        self.assertEqual(energy.device_buckets(self.device, energy.PERIOD_DAY), [])

    def test_a_meter_reset_is_counted_as_consumption_since_the_reset(self):
        # Gen1 fängt nach einem Stromausfall wieder bei null an.
        self.add_reading(5000, hours_ago=3)
        self.add_reading(200, hours_ago=2)
        self.add_reading(300, hours_ago=1)

        buckets = energy.device_buckets(self.device, energy.PERIOD_DAY)

        self.assertEqual(buckets[0].kwh, Decimal("0.300"))

    def test_readings_without_a_meter_are_ignored(self):
        DeviceReading.objects.create(device=self.device, read_at=timezone.now(), payload={})
        self.add_reading(1000, hours_ago=2)
        self.add_reading(1200, hours_ago=1)

        self.assertEqual(
            energy.device_buckets(self.device, energy.PERIOD_DAY)[0].kwh, Decimal("0.200")
        )

    def test_days_are_kept_apart(self):
        self.add_reading(1000, days_ago=2)
        self.add_reading(1500, days_ago=1)
        self.add_reading(1800)

        buckets = energy.device_buckets(self.device, energy.PERIOD_DAY)

        self.assertEqual(len(buckets), 2)
        self.assertEqual([bucket.kwh for bucket in buckets], [Decimal("0.500"), Decimal("0.300")])

    def test_a_year_collects_everything(self):
        self.add_reading(1000, days_ago=2)
        self.add_reading(1500, days_ago=1)
        self.add_reading(1800)

        buckets = energy.device_buckets(self.device, energy.PERIOD_YEAR)

        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].kwh, Decimal("0.800"))

    def test_unknown_period_falls_back_to_the_month(self):
        self.assertEqual(energy.normalize_period("quartal"), energy.PERIOD_MONTH)
        self.assertEqual(energy.normalize_period(None), energy.PERIOD_MONTH)
        self.assertEqual(energy.normalize_period("Tag"), energy.PERIOD_DAY)


class ComparisonTests(EnergyTestCase):
    def setUp(self):
        super().setUp()
        self.heater = make_plug(
            self.user, name="Heizung", tank_name="Becken 2", host="192.168.1.61"
        )

    def test_consumption_is_compared_per_tank(self):
        self.add_reading(1000, hours_ago=2)
        self.add_reading(1400, hours_ago=1)
        self.add_reading(2000, hours_ago=2, device=self.heater)
        self.add_reading(3000, hours_ago=1, device=self.heater)

        usages = energy.usage_by_tank(self.user, energy.PERIOD_MONTH)

        self.assertEqual([usage.label for usage in usages], ["Becken 2", "Becken 1"])
        self.assertEqual(usages[0].kwh, Decimal("1.000"))
        self.assertEqual(usages[1].kwh, Decimal("0.400"))

    def test_devices_of_one_tank_are_added_up(self):
        second = make_plug(self.user, name="Filter", tank_name="Becken 1", host="192.168.1.62")
        self.add_reading(1000, hours_ago=2)
        self.add_reading(1400, hours_ago=1)
        self.add_reading(500, hours_ago=2, device=second)
        self.add_reading(600, hours_ago=1, device=second)

        usages = {usage.label: usage for usage in energy.usage_by_tank(self.user, energy.PERIOD_MONTH)}

        self.assertEqual(usages["Becken 1"].kwh, Decimal("0.500"))
        self.assertIn("Filter", usages["Becken 1"].device_names)
        self.assertIn("Licht", usages["Becken 1"].device_names)

    def test_the_reading_before_the_period_is_the_reference_point(self):
        # Ohne den letzten Messwert des Vormonats fehlte der Verbrauch
        # zwischen ihm und dem ersten Messwert des laufenden Monats.
        start = energy.bucket_start(timezone.now(), energy.PERIOD_DAY)
        self.add_reading(1000, days_ago=1)
        self.add_reading(1200)

        usages = energy.usage_by_device(self.user, energy.PERIOD_DAY, start=start)

        self.assertEqual(usages[0].kwh, Decimal("0.200"))

    def test_foreign_devices_are_not_counted(self):
        eva = get_user_model().objects.create_user(
            username="eva", email="eva@example.com", password="geheim-123"
        )
        self.add_reading(1000, hours_ago=2)
        self.add_reading(1400, hours_ago=1)

        self.assertEqual(energy.usage_by_tank(eva, energy.PERIOD_MONTH), [])

    @override_settings(ENERGY_PRICE_PER_KWH="0.40")
    def test_cost_follows_the_configured_price(self):
        self.add_reading(1000, hours_ago=2)
        self.add_reading(3000, hours_ago=1)

        usages = energy.usage_by_tank(self.user, energy.PERIOD_MONTH)

        self.assertEqual(usages[0].kwh, Decimal("2.000"))
        self.assertEqual(usages[0].cost, Decimal("0.80"))
        self.assertEqual(energy.total_cost(usages), Decimal("0.80"))

    @override_settings(ENERGY_PRICE_PER_KWH="keine Zahl")
    def test_an_unusable_price_falls_back_instead_of_breaking_the_page(self):
        self.assertEqual(energy.price_per_kwh(), energy.DEFAULT_PRICE_PER_KWH)

    def test_a_plug_without_readings_shows_up_with_zero(self):
        usages = energy.usage_by_device(self.user, energy.PERIOD_MONTH)

        self.assertEqual(len(usages), 2)
        self.assertEqual(usages[0].kwh, Decimal("0.000"))
