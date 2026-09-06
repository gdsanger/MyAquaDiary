from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from services.models import Device, DeviceReading


class DashboardWarningTests(TestCase):
    """Fehlercode 1 oder 2 gehört auf das Dashboard — sofort und im Klartext."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            owner=self.user,
            name="Filter Becken 1",
            kind=Device.Kind.EHEIM_CLASSICVARIO,
            mac_address="AA:BB:CC:DD:EE:FF",
            host="192.168.1.50",
        )

    def add_reading(self, error_code, minutes_ago=0):
        DeviceReading.objects.create(
            device=self.device,
            read_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
            payload={},
            error_code=error_code,
        )

    def test_error_code_is_shown_in_plain_language(self):
        self.add_reading(1)
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, "Rotor blockiert")
        self.assertContains(response, reverse("services:device_detail", args=[self.device.pk]))

    def test_air_in_filter_is_shown(self):
        self.add_reading(2)
        self.assertContains(self.client.get(reverse("dashboard:index")), "Luft im Filter")

    def test_no_warning_when_the_last_reading_is_clean(self):
        self.add_reading(1, minutes_ago=30)
        self.add_reading(0)
        self.assertNotContains(self.client.get(reverse("dashboard:index")), "Störung")

    def test_dashboard_without_devices_stays_empty(self):
        self.assertNotContains(self.client.get(reverse("dashboard:index")), "Störung")

    def test_dashboard_does_not_query_devices_over_the_network(self):
        # Der Status kommt aus dem letzten Messwert; ein stummes Gerät darf das
        # Dashboard nicht aufhalten. Vier Abfragen: Session, Benutzer, Geräte
        # mit Störung und die KI-Konfiguration für die Navigation.
        self.add_reading(1)
        with self.assertNumQueries(4):
            self.client.get(reverse("dashboard:index"))
