from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import AnimalGroup, CatalogAnimal, CatalogAnimalImage, Difficulty

User = get_user_model()


def make_animal(**kwargs):
    defaults = dict(
        scientific_name="Mikrogeophagus ramirezi",
        group=AnimalGroup.FISCH,
        difficulty=Difficulty.MEDIUM,
    )
    defaults.update(kwargs)
    return CatalogAnimal.objects.create(**defaults)


def make_image(animal, **kwargs):
    defaults = dict(
        image=SimpleUploadedFile("animal.jpg", b"fake-image-bytes", content_type="image/jpeg"),
    )
    defaults.update(kwargs)
    return CatalogAnimalImage.objects.create(animal=animal, **defaults)


class CatalogAnimalTests(TestCase):
    def test_slug_is_generated_from_name_and_variety(self):
        animal = make_animal(scientific_name="Mikrogeophagus ramirezi", variety="Electric Blue")
        self.assertEqual(animal.slug, "mikrogeophagus-ramirezi-electric-blue")

    def test_same_species_with_different_variety_is_a_separate_entry(self):
        wild = make_animal(scientific_name="Mikrogeophagus ramirezi")
        variant = make_animal(
            scientific_name="Mikrogeophagus ramirezi",
            variety="Electric Blue",
            is_line_bred_variant=True,
        )
        self.assertEqual(CatalogAnimal.objects.count(), 2)
        self.assertNotEqual(variant.slug, wild.slug)
        self.assertFalse(wild.is_line_bred_variant)
        self.assertTrue(variant.is_line_bred_variant)

    def test_same_species_and_variety_combination_is_rejected(self):
        make_animal(scientific_name="Mikrogeophagus ramirezi", variety="Electric Blue")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_animal(scientific_name="Mikrogeophagus ramirezi", variety="Electric Blue")

    def test_slug_collision_gets_a_numeric_suffix(self):
        first = make_animal(scientific_name="Mikrogeophagus ramirezi", variety="Electric Blue")
        second = make_animal(scientific_name="Mikrogeophagus ramirezi'", variety="Electric Blue")
        self.assertEqual(first.slug, "mikrogeophagus-ramirezi-electric-blue")
        self.assertEqual(second.slug, "mikrogeophagus-ramirezi-electric-blue-2")

    def test_str_includes_variety_when_present(self):
        animal = make_animal(scientific_name="Neocaridina davidi", variety="Red Fire")
        self.assertEqual(str(animal), "Neocaridina davidi 'Red Fire'")

    def test_deleting_creator_keeps_animal(self):
        user = User.objects.create_user(username="keeper", email="keeper@example.com", password="pw")
        animal = make_animal(created_by=user)
        user.delete()
        animal.refresh_from_db()
        self.assertIsNone(animal.created_by)


class CatalogAnimalImageTests(TestCase):
    def test_first_image_becomes_primary_automatically(self):
        animal = make_animal()
        image = make_image(animal)
        self.assertTrue(image.is_primary)

    def test_marking_a_new_image_primary_demotes_the_previous_one(self):
        animal = make_animal()
        first = make_image(animal)
        second = make_image(animal, is_primary=True)

        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)
        self.assertEqual(
            CatalogAnimalImage.objects.filter(animal=animal, is_primary=True).count(), 1
        )

    def test_two_primary_images_violate_the_db_constraint(self):
        animal = make_animal()
        make_image(animal)
        second = make_image(animal)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CatalogAnimalImage.objects.filter(pk=second.pk).update(is_primary=True)

    def test_animal_exposes_its_primary_image(self):
        animal = make_animal()
        make_image(animal, position=1)
        second = make_image(animal, position=2, is_primary=True)
        self.assertEqual(animal.primary_image, second)

    def test_deleting_the_primary_image_promotes_another_one(self):
        animal = make_animal()
        first = make_image(animal, position=1)
        second = make_image(animal, position=2)
        self.assertTrue(first.is_primary)

        first.delete()

        second.refresh_from_db()
        self.assertTrue(second.is_primary)


def animal_form_data(**overrides):
    data = dict(
        scientific_name="Caridina multidentata",
        variety="",
        common_name="",
        family="",
        origin="",
        group=AnimalGroup.GARNELE,
        difficulty=Difficulty.EASY,
        social_behavior="",
        zone="",
        diet="",
        breeding_type="",
        breeding_notes="",
        description="",
        care_notes="",
        compatibility_notes="",
        warning="",
        source_url="",
        **{
            "images-TOTAL_FORMS": "0",
            "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0",
            "images-MAX_NUM_FORMS": "1000",
        },
    )
    data.update(overrides)
    return data


class CatalogAnimalListViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="viewer", email="viewer@example.com", password="pw")
        self.client.force_login(self.user)
        self.easy = make_animal(
            scientific_name="Caridina multidentata",
            group=AnimalGroup.GARNELE,
            difficulty=Difficulty.EASY,
            min_tank_liters=20,
        )
        self.hard = make_animal(
            scientific_name="Tanichthys albonubes",
            group=AnimalGroup.FISCH,
            difficulty=Difficulty.DEMANDING,
            min_tank_liters=100,
        )

    def test_lists_all_animals_by_default(self):
        response = self.client.get(reverse("catalog:animal-list"))
        self.assertContains(response, "Caridina multidentata")
        self.assertContains(response, "Tanichthys albonubes")

    def test_search_filters_by_name(self):
        response = self.client.get(reverse("catalog:animal-list"), {"q": "Caridina"})
        self.assertContains(response, "Caridina multidentata")
        self.assertNotContains(response, "Tanichthys albonubes")

    def test_filter_by_group(self):
        response = self.client.get(reverse("catalog:animal-list"), {"group": AnimalGroup.GARNELE})
        self.assertContains(response, "Caridina multidentata")
        self.assertNotContains(response, "Tanichthys albonubes")

    def test_filter_by_difficulty(self):
        response = self.client.get(reverse("catalog:animal-list"), {"difficulty": Difficulty.DEMANDING})
        self.assertNotContains(response, "Caridina multidentata")
        self.assertContains(response, "Tanichthys albonubes")

    def test_filter_by_max_tank_liters(self):
        response = self.client.get(reverse("catalog:animal-list"), {"max_liters": "50"})
        self.assertContains(response, "Caridina multidentata")
        self.assertNotContains(response, "Tanichthys albonubes")

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("catalog:animal-list"))
        self.assertEqual(response.status_code, 302)

    def test_detail_view_renders_steckbrief_and_gallery(self):
        make_image(self.easy, caption="Weibchen mit Eiern")
        response = self.client.get(reverse("catalog:animal-detail", kwargs={"slug": self.easy.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Steckbrief")
        self.assertContains(response, "Weibchen mit Eiern")


class CatalogAnimalEditPermissionTests(TestCase):
    def setUp(self):
        self.plain_user = User.objects.create_user(
            username="plain", email="plain@example.com", password="pw"
        )
        self.editor = User.objects.create_user(
            username="editor", email="editor@example.com", password="pw", can_edit_catalog=True
        )
        self.staff = User.objects.create_user(
            username="staffer", email="staffer@example.com", password="pw", is_staff=True
        )

    def test_plain_user_cannot_access_create_view(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("catalog:animal-create"))
        self.assertEqual(response.status_code, 403)

    def test_editor_can_render_the_create_form(self):
        self.client.force_login(self.editor)
        response = self.client.get(reverse("catalog:animal-create"))
        self.assertEqual(response.status_code, 200)

    def test_user_with_can_edit_catalog_can_create_an_animal(self):
        self.client.force_login(self.editor)
        response = self.client.post(reverse("catalog:animal-create"), animal_form_data(), follow=True)
        self.assertEqual(response.status_code, 200)
        animal = CatalogAnimal.objects.get(scientific_name="Caridina multidentata")
        self.assertEqual(animal.created_by, self.editor)

    def test_staff_can_create_an_animal_without_the_flag(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("catalog:animal-create"), animal_form_data())
        self.assertEqual(
            CatalogAnimal.objects.filter(scientific_name="Caridina multidentata").count(), 1
        )

    def test_plain_user_cannot_access_update_view(self):
        animal = make_animal()
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("catalog:animal-update", kwargs={"slug": animal.slug}))
        self.assertEqual(response.status_code, 403)

    def test_editor_can_update_an_animal(self):
        animal = make_animal()
        self.client.force_login(self.editor)
        data = animal_form_data(scientific_name=animal.scientific_name, care_notes="Bevorzugt Höhlen.")
        response = self.client.post(
            reverse("catalog:animal-update", kwargs={"slug": animal.slug}), data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        animal.refresh_from_db()
        self.assertEqual(animal.care_notes, "Bevorzugt Höhlen.")
from catalog.models import AnimalSpecies, PlantSpecies
from core.enums import Difficulty, WaterType
from core.testing import create_animal, create_plant, create_tank, create_user, stock


class PlantCatalogTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.crypto = create_plant(
            "Cryptocoryne wendtii",
            common_name="Wendts Wasserkelch",
            placement=PlantSpecies.Placement.MIDGROUND,
            difficulty=Difficulty.EASY,
        )
        self.riccia = create_plant(
            "Riccia fluitans",
            slug="riccia-fluitans",
            common_name="Teichlebermoos",
            placement=PlantSpecies.Placement.FLOATING,
            difficulty=Difficulty.MEDIUM,
        )

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("catalog:plant-list"))
        self.assertEqual(response.status_code, 302)

    def test_list_shows_all_species(self):
        response = self.client.get(reverse("catalog:plant-list"))
        self.assertEqual(response.context["result_count"], 2)
        self.assertContains(response, "Wendts Wasserkelch")

    def test_search_matches_common_and_scientific_name(self):
        for term in ("Riccia", "Teichleber"):
            with self.subTest(term=term):
                response = self.client.get(reverse("catalog:plant-list"), {"q": term})
                self.assertEqual(
                    [species.pk for species in response.context["species_list"]], [self.riccia.pk]
                )

    def test_filters_combine(self):
        response = self.client.get(
            reverse("catalog:plant-list"),
            {"filter": PlantSpecies.Placement.FLOATING, "anspruch": Difficulty.MEDIUM},
        )
        self.assertEqual(response.context["result_count"], 1)

    def test_unknown_filter_values_are_ignored(self):
        response = self.client.get(reverse("catalog:plant-list"), {"anspruch": "unsinn"})
        self.assertEqual(response.context["result_count"], 2)

    def test_grid_fragment_contains_only_the_grid(self):
        response = self.client.get(reverse("catalog:plant-grid"), {"q": "Riccia"})
        content = response.content.decode()
        self.assertNotIn("<html", content)
        self.assertIn('id="katalog-raster"', content)
        self.assertIn("Teichlebermoos", content)

    def test_empty_result_is_explained(self):
        response = self.client.get(reverse("catalog:plant-grid"), {"q": "gibtsnicht"})
        self.assertContains(response, "Keine Art gefunden")

    def test_detail_lists_own_tanks_containing_the_species(self):
        from tanks.models import Planting

        tank = create_tank(self.user)
        Planting.objects.create(
            tank=tank, species=self.crypto, quantity=5, planted_on=tank.setup_date
        )
        response = self.client.get(self.crypto.get_absolute_url())
        self.assertEqual(list(response.context["own_tanks"]), [tank])
        self.assertContains(response, tank.name)

    def test_detail_says_so_when_the_species_is_not_kept(self):
        response = self.client.get(self.riccia.get_absolute_url())
        self.assertContains(response, "kommt in keinem deiner Becken vor")

    def test_detail_ignores_other_users_tanks(self):
        from tanks.models import Planting

        stranger_tank = create_tank(create_user("fremd"), name="Fremdbecken", slug="fremd")
        Planting.objects.create(
            tank=stranger_tank, species=self.crypto, quantity=5, planted_on=stranger_tank.setup_date
        )
        response = self.client.get(self.crypto.get_absolute_url())
        self.assertEqual(list(response.context["own_tanks"]), [])


class AnimalCatalogTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.neon = create_animal()
        self.shrimp = create_animal(
            "Neocaridina davidi",
            slug="neocaridina-davidi",
            common_name="Zwerggarnele",
            category=AnimalSpecies.Category.SHRIMP,
            water_type=WaterType.FRESHWATER,
            min_group_size=10,
        )

    def test_category_filter(self):
        response = self.client.get(
            reverse("catalog:animal-list"), {"filter": AnimalSpecies.Category.SHRIMP}
        )
        self.assertEqual(
            [species.pk for species in response.context["species_list"]], [self.shrimp.pk]
        )

    def test_water_type_filter(self):
        response = self.client.get(reverse("catalog:animal-list"), {"wassertyp": WaterType.MARINE})
        self.assertEqual(response.context["result_count"], 0)

    def test_detail_shows_minimum_group_size(self):
        response = self.client.get(self.neon.get_absolute_url())
        self.assertContains(response, "mindestens 10 Tiere")

    def test_detail_lists_own_tanks(self):
        tank = create_tank(self.user)
        stock(tank, self.neon, quantity=12)
        response = self.client.get(self.neon.get_absolute_url())
        self.assertEqual(list(response.context["own_tanks"]), [tank])

    def test_removed_stock_no_longer_counts_as_present(self):
        tank = create_tank(self.user)
        stocking = stock(tank, self.neon, quantity=12)
        stocking.removed_on = tank.setup_date
        stocking.save()
        response = self.client.get(self.neon.get_absolute_url())
        self.assertEqual(list(response.context["own_tanks"]), [])
