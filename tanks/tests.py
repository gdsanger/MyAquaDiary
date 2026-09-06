import datetime

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Parameter, Tank, TankParameterTarget, WaterType

User = get_user_model()


def make_tank(owner, **kwargs):
    defaults = dict(
        name="Südamerika",
        water_type=WaterType.SUESSWASSER,
        started_on=datetime.date(2024, 1, 1),
    )
    defaults.update(kwargs)
    return Tank.objects.create(owner=owner, **defaults)


class TankSlugTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )

    def test_slug_is_generated_from_name(self):
        tank = make_tank(self.user_a, name="Südamerika")
        self.assertEqual(tank.slug, "sudamerika")

    def test_slug_collision_for_same_owner_gets_a_numeric_suffix(self):
        first = make_tank(self.user_a, name="Südamerika")
        second = make_tank(self.user_a, name="Südamerika")
        self.assertEqual(first.slug, "sudamerika")
        self.assertEqual(second.slug, "sudamerika-2")

    def test_two_owners_may_share_the_same_slug(self):
        tank_a = make_tank(self.user_a, name="Südamerika")
        tank_b = make_tank(self.user_b, name="Südamerika")
        self.assertEqual(tank_a.slug, tank_b.slug)


class TankIsolationTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank_a = make_tank(self.user_a, name="Beckens A")
        self.tank_b = make_tank(self.user_b, name="Beckens B")

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
            reverse("tanks:detail", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 404)

    def test_detail_view_allows_access_to_own_tank(self):
        self.client.force_login(self.user_a)
        response = self.client.get(
            reverse("tanks:detail", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 200)

    def test_update_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            reverse("tanks:update", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_view_denies_access_to_other_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.post(
            reverse("tanks:delete", kwargs={"slug": self.tank_a.slug})
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Tank.objects.filter(pk=self.tank_a.pk).exists())

    def test_owner_can_delete_their_own_tank(self):
        self.client.force_login(self.user_a)
        response = self.client.post(
            reverse("tanks:delete", kwargs={"slug": self.tank_a.slug})
        )
        self.assertRedirects(response, reverse("tanks:list"))
        self.assertFalse(Tank.objects.filter(pk=self.tank_a.pk).exists())

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("tanks:list"))
        self.assertEqual(response.status_code, 302)


def tank_form_data(**overrides):
    data = dict(
        name="Neues Becken",
        model_name="",
        length_cm="",
        height_cm="",
        depth_cm="",
        volume_gross_l="",
        volume_net_l="",
        water_type=WaterType.SUESSWASSER,
        biotope="",
        started_on="2024-01-01",
        shut_down_on="",
        description="",
        substrate="",
        hardscape="",
        filtration="",
        lighting="",
        co2="",
        fertilization="",
        **{
            "photos-TOTAL_FORMS": "0",
            "photos-INITIAL_FORMS": "0",
            "photos-MIN_NUM_FORMS": "0",
            "photos-MAX_NUM_FORMS": "1000",
        },
    )
    data.update(overrides)
    return data


class TankCreateViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.client.force_login(self.user)

    def test_create_assigns_current_user_as_owner(self):
        response = self.client.post(reverse("tanks:create"), tank_form_data(), follow=True)
        self.assertEqual(response.status_code, 200)
        tank = Tank.objects.get(name="Neues Becken")
        self.assertEqual(tank.owner, self.user)


class TankOverviewSplitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.client.force_login(self.user)
        self.active_tank = make_tank(self.user, name="Aktiv")
        self.dissolved_tank = make_tank(
            self.user, name="Aufgelöst", shut_down_on=datetime.date(2024, 6, 1)
        )

    def test_is_dissolved_property(self):
        self.assertFalse(self.active_tank.is_dissolved)
        self.assertTrue(self.dissolved_tank.is_dissolved)

    def test_list_view_separates_active_and_dissolved_tanks(self):
        response = self.client.get(reverse("tanks:list"))
        self.assertEqual(list(response.context["active_tanks"]), [self.active_tank])
        self.assertEqual(list(response.context["dissolved_tanks"]), [self.dissolved_tank])

    def test_dissolved_tank_stays_readable(self):
        response = self.client.get(
            reverse("tanks:detail", kwargs={"slug": self.dissolved_tank.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aufgelöst")


class TankParameterTargetInlineTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alice", email="alice@example.com", password="s3cret-pw"
        )
        self.user_b = User.objects.create_user(
            username="bob", email="bob@example.com", password="s3cret-pw"
        )
        self.tank = make_tank(self.user_a)
        self.parameter = Parameter.objects.create(key="ph", name="pH-Wert", decimals=1)
        self.client.force_login(self.user_a)

    def test_edit_form_is_rendered_for_a_parameter_without_a_target_yet(self):
        response = self.client.get(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pH-Wert")

    def test_post_creates_a_target(self):
        response = self.client.post(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            ),
            {"minimum": "6.5", "target": "7.0", "maximum": "7.5"},
        )
        self.assertEqual(response.status_code, 200)
        target = TankParameterTarget.objects.get(tank=self.tank, parameter=self.parameter)
        self.assertEqual(str(target.minimum), "6.50")
        self.assertEqual(str(target.target), "7.00")
        self.assertEqual(str(target.maximum), "7.50")

    def test_post_updates_an_existing_target(self):
        TankParameterTarget.objects.create(
            tank=self.tank, parameter=self.parameter, minimum=6, target=7, maximum=7.5
        )
        self.client.post(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            ),
            {"minimum": "6.2", "target": "7.0", "maximum": "7.8"},
        )
        self.assertEqual(
            TankParameterTarget.objects.filter(tank=self.tank, parameter=self.parameter).count(),
            1,
        )
        target = TankParameterTarget.objects.get(tank=self.tank, parameter=self.parameter)
        self.assertEqual(str(target.minimum), "6.20")

    def test_cannot_edit_targets_of_another_users_tank(self):
        self.client.force_login(self.user_b)
        response = self.client.get(
            reverse(
                "tanks:target-edit",
                kwargs={"slug": self.tank.slug, "parameter_id": self.parameter.pk},
            )
        )
        self.assertEqual(response.status_code, 404)


class SeedParametersCommandTests(TestCase):
    def test_seeds_the_full_default_catalog(self):
        call_command("seed_parameters")
        expected_keys = {
            "ph", "kh", "gh", "temp", "lf", "nh4", "no2", "no3", "po4",
            "fe", "k", "mg", "cu", "o2",
        }
        self.assertEqual(set(Parameter.objects.values_list("key", flat=True)), expected_keys)

    def test_command_is_idempotent(self):
        call_command("seed_parameters")
        call_command("seed_parameters")
        self.assertEqual(Parameter.objects.count(), 14)
