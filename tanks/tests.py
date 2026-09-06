from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Tank

User = get_user_model()


class TankIsolationTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank_a = Tank.objects.create(owner=self.user_a, name="Beckens A")
        self.tank_b = Tank.objects.create(owner=self.user_b, name="Beckens B")

    def test_manager_for_user_only_returns_own_tanks(self):
        self.assertEqual(list(Tank.objects.for_user(self.user_a)), [self.tank_a])
        self.assertEqual(list(Tank.objects.for_user(self.user_b)), [self.tank_b])

    def test_list_view_only_shows_own_tanks(self):
        self.client.force_login(self.user_b)
        response = self.client.get(reverse("tanks:list"))
        self.assertContains(response, "Beckens B")
        self.assertNotContains(response, "Beckens A")

    def test_detail_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            reverse("tanks:detail", kwargs={"pk": self.tank_a.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_detail_view_allows_access_to_own_tank(self):
        self.client.force_login(self.user_a)
        response = self.client.get(
            reverse("tanks:detail", kwargs={"pk": self.tank_a.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("tanks:list"))
        self.assertEqual(response.status_code, 302)
