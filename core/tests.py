from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class RegistrationTests(TestCase):
    def test_registration_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "newuser",
                "email": "newuser@example.com",
                "password1": "a-strong-passw0rd",
                "password2": "a-strong-passw0rd",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="newuser").exists())
        self.assertIn("_auth_user_id", self.client.session)


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)

    def test_user_can_update_display_name_and_notification_settings(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("profile"),
            {
                "display_name": "Alice A.",
                "timezone": "Europe/Berlin",
                "notify_email": "on",
                "notify_lead_days": 5,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "Alice A.")
        self.assertEqual(self.user.notify_lead_days, 5)
