"""Geräte ohne Beckenzuordnung — „auf Halde".

Ein ersetztes Gerät ist nicht weg: es liegt im Schrank und kommt vielleicht
wieder. ``tank IS NULL`` heißt „nicht im Einsatz". Hier steht, was dieser
Zustand überall auslöst — keine Abfrage, keine Warnung, kein Verbrauch — und wie
das Ein- und Auslagern in der Beckenhistorie landet.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.enums import Status
from core.testing import create_tank, create_user, tank_named
from services import devices as device_service
from services import energy
from services.models import Device, DeviceEvent, DeviceReading
from services.tests.test_devices import fake_service, make_device
from services.tests.test_eheim_client import CLASSICVARIO_PAYLOAD, FakeResponse
from services.tests.test_shelly_devices import make_plug
from tanks.models import Event


class StorageServiceTests(TestCase):
    """Ein- und Auslagern in der Service-Schicht."""

    def setUp(self):
        self.user = create_user("max")
        self.tank = tank_named(self.user, "Becken 1")
        self.device = make_device(self.user, tank=self.tank, name="Außenfilter")

    def test_store_detaches_the_tank_and_sets_the_date(self):
        device_service.store(self.device, user=self.user)

        self.device.refresh_from_db()
        self.assertIsNone(self.device.tank_id)
        self.assertTrue(self.device.is_stored)
        self.assertEqual(self.device.stored_since, timezone.localdate())

    def test_store_records_an_event_at_the_previous_tank(self):
        device_service.store(self.device, user=self.user)

        event = Event.objects.get()
        self.assertEqual(event.tank, self.tank)
        self.assertEqual(event.category, Event.Category.EQUIPMENT)
        self.assertIn("Außenfilter", event.title)
        self.assertEqual(DeviceEvent.objects.get().action, "store")

    def test_install_attaches_a_tank_and_clears_the_date(self):
        device_service.store(self.device, user=self.user)
        target = tank_named(self.user, "Becken 2")

        device_service.install(self.device, target, user=self.user)

        self.device.refresh_from_db()
        self.assertEqual(self.device.tank, target)
        self.assertIsNone(self.device.stored_since)
        self.assertFalse(self.device.is_stored)

    def test_install_records_an_event_at_the_new_tank(self):
        device_service.store(self.device, user=self.user)
        target = tank_named(self.user, "Becken 2")

        device_service.install(self.device, target, user=self.user)

        install_event = Event.objects.filter(title__icontains="eingebaut").get()
        self.assertEqual(install_event.tank, target)
        self.assertEqual(install_event.category, Event.Category.EQUIPMENT)


class StoredDeviceIsQuietTests(TestCase):
    """Ein eingelagertes Gerät wird nicht abgefragt und meldet nichts."""

    def setUp(self):
        self.user = create_user("max")
        self.tank = tank_named(self.user, "Becken 1")

    def test_probe_skips_a_stored_device(self):
        device = make_device(self.user, tank=self.tank)
        device_service.store(device)

        with patch("services.devices.service_for") as service_for:
            result = device_service.probe(device)

        self.assertFalse(result)
        service_for.assert_not_called()

    def test_execute_is_refused_for_a_stored_device(self):
        device = make_device(self.user, tank=self.tank)
        device_service.store(device)

        with patch("services.devices.service_for") as service_for:
            result = device_service.execute(device, "on", user=self.user)

        self.assertFalse(result)
        service_for.assert_not_called()

    def test_a_stored_device_raises_no_dashboard_warning(self):
        device = make_device(self.user, tank=self.tank, name="Außenfilter")
        service, _ = fake_service(device, FakeResponse(200, {**CLASSICVARIO_PAYLOAD, "errorCode": 1}))
        with patch("services.devices.service_for", return_value=service):
            device_service.probe(device)
        self.assertTrue(device_service.warnings_for(self.user))

        device_service.store(device)

        self.assertEqual(device_service.warnings_for(self.user), [])

    def test_poll_skips_stored_devices(self):
        device = make_device(self.user, tank=self.tank)
        device_service.store(device)

        queryset = Device.objects.filter(
            is_active=True, kind__in=Device.POLLED_KINDS, tank__isnull=False
        )

        self.assertNotIn(device, list(queryset))


class StoredDeviceHasNoConsumptionTests(TestCase):
    """Eingelagerte Geräte tauchen in der Verbrauchsauswertung nicht auf."""

    def setUp(self):
        self.user = create_user("max")
        self.tank = tank_named(self.user, "Becken 1")

    def test_a_stored_metered_device_leaves_the_accounting(self):
        plug = make_plug(self.user, tank=self.tank, name="Licht")
        for wh, hours_ago in ((1000, 2), (1500, 1)):
            DeviceReading.objects.create(
                device=plug,
                read_at=timezone.now() - timedelta(hours=hours_ago),
                payload={},
                energy_total_wh=Decimal(str(wh)),
            )
        self.assertTrue(energy.usage_by_tank(self.user, energy.PERIOD_MONTH))

        device_service.store(plug)

        self.assertEqual(energy.usage_by_tank(self.user, energy.PERIOD_MONTH), [])
        self.assertFalse(energy.accounted_devices(self.user).filter(pk=plug.pk).exists())

    def test_a_stored_estimated_device_leaves_the_accounting(self):
        heater = Device.objects.create(
            owner=self.user, tank=self.tank, name="Heizstab", kind=Device.Kind.HEATER,
            power_watts=Decimal("100"),
        )
        self.assertTrue(energy.estimated_devices(self.user).filter(pk=heater.pk).exists())

        device_service.store(heater)

        self.assertFalse(energy.estimated_devices(self.user).filter(pk=heater.pk).exists())


class StorageViewTests(TestCase):
    """Einlagern und Einbauen von der Detailseite aus."""

    def setUp(self):
        self.user = create_user("max")
        self.client.force_login(self.user)
        self.tank = tank_named(self.user, "Becken 1")
        self.device = make_device(self.user, tank=self.tank, name="Außenfilter")

    def test_store_button_detaches_the_device(self):
        response = self.client.post(
            reverse("services:device_store", args=[self.device.pk]), follow=True
        )

        self.device.refresh_from_db()
        self.assertTrue(self.device.is_stored)
        self.assertContains(response, "eingelagert")

    def test_install_view_reattaches_the_device(self):
        device_service.store(self.device)
        target = tank_named(self.user, "Becken 2")

        response = self.client.post(
            reverse("services:device_install", args=[self.device.pk]),
            {"tank": target.pk},
            follow=True,
        )

        self.device.refresh_from_db()
        self.assertEqual(self.device.tank, target)
        self.assertFalse(self.device.is_stored)
        self.assertContains(response, "eingebaut")

    def test_install_refuses_a_foreign_tank(self):
        device_service.store(self.device)
        stranger = create_user("eva")
        foreign = create_tank(stranger, name="Fremd", slug="fremd")

        self.client.post(
            reverse("services:device_install", args=[self.device.pk]),
            {"tank": foreign.pk},
        )

        self.device.refresh_from_db()
        self.assertTrue(self.device.is_stored)

    def test_the_list_shows_a_section_for_stored_devices(self):
        device_service.store(self.device)

        response = self.client.get(reverse("services:device_list"))

        self.assertContains(response, "Nicht im Einsatz")
        self.assertContains(response, "Außenfilter")

    def test_the_detail_page_offers_store_for_a_device_in_use(self):
        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))

        self.assertContains(response, reverse("services:device_store", args=[self.device.pk]))

    def test_the_detail_page_offers_install_for_a_stored_device(self):
        device_service.store(self.device)

        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))

        self.assertContains(response, reverse("services:device_install", args=[self.device.pk]))
        self.assertNotContains(response, reverse("services:device_status", args=[self.device.pk]))
