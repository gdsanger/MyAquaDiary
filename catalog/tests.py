import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import AnimalSpecies, PlantSpecies
from core.enums import Difficulty, WaterType
from core.testing import (
    create_animal,
    create_plant,
    create_tank,
    create_user,
    image_upload,
    stock,
)


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


def grant_catalog_edit(user):
    """Gibt dem Benutzer das Pflegerecht aus catalog.CatalogPermission."""
    user.user_permissions.add(Permission.objects.get(codename="can_edit_catalog"))
    return get_user_model().objects.get(pk=user.pk)


class CatalogPermissionTests(TestCase):
    """Der Katalog ist userübergreifend — hier entscheidet das Recht."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.plant = create_plant()
        self.animal = create_animal()

    def write_urls(self):
        return [
            reverse("catalog:plant-create"),
            reverse("catalog:plant-update", args=[self.plant.slug]),
            reverse("catalog:plant-delete", args=[self.plant.slug]),
            reverse("catalog:plant-image-upload", args=[self.plant.slug]),
            reverse("catalog:animal-create"),
            reverse("catalog:animal-update", args=[self.animal.slug]),
            reverse("catalog:animal-delete", args=[self.animal.slug]),
            reverse("catalog:animal-image-upload", args=[self.animal.slug]),
        ]

    def test_without_the_permission_every_write_path_is_forbidden(self):
        for url in self.write_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_without_the_permission_no_button_is_shown(self):
        response = self.client.get(reverse("catalog:plant-list"))
        self.assertFalse(response.context["can_edit_catalog"])
        self.assertNotContains(response, reverse("catalog:plant-create"))

        response = self.client.get(self.plant.get_absolute_url())
        self.assertNotContains(response, reverse("catalog:plant-update", args=[self.plant.slug]))
        self.assertNotContains(response, "Bilder hochladen")

    def test_with_the_permission_the_buttons_appear(self):
        self.client.force_login(grant_catalog_edit(self.user))
        response = self.client.get(reverse("catalog:plant-list"))
        self.assertTrue(response.context["can_edit_catalog"])
        self.assertContains(response, reverse("catalog:plant-create"))

        response = self.client.get(self.plant.get_absolute_url())
        self.assertContains(response, reverse("catalog:plant-update", args=[self.plant.slug]))

    def test_staff_may_edit_without_an_explicit_permission(self):
        self.client.force_login(create_user("chefin", is_staff=True))
        self.assertEqual(self.client.get(reverse("catalog:plant-create")).status_code, 200)

    def test_anonymous_visitors_are_sent_to_the_login(self):
        self.client.logout()
        response = self.client.get(reverse("catalog:plant-create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class SpeciesWriteTests(TestCase):
    def setUp(self):
        self.user = grant_catalog_edit(create_user())
        self.client.force_login(self.user)

    def plant_payload(self, **overrides):
        data = {
            "scientific_name": "Anubias barteri",
            "common_name": "Speerblatt",
            "summary": "",
            "description": "",
            "water_type": WaterType.FRESHWATER,
            "difficulty": Difficulty.EASY,
            "placement": PlantSpecies.Placement.MIDGROUND,
            "growth_rate": PlantSpecies.GrowthRate.SLOW,
            "light_demand": PlantSpecies.LightDemand.LOW,
        }
        data.update(overrides)
        return data

    def test_a_plant_species_is_created_with_a_derived_address(self):
        response = self.client.post(reverse("catalog:plant-create"), self.plant_payload())
        species = PlantSpecies.objects.get(scientific_name="Anubias barteri")
        self.assertEqual(species.slug, "anubias-barteri")
        self.assertRedirects(response, species.get_absolute_url())

    def test_a_reversed_temperature_range_is_refused(self):
        response = self.client.post(
            reverse("catalog:plant-create"),
            self.plant_payload(temperature_min="28", temperature_max="22"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlantSpecies.objects.filter(scientific_name="Anubias barteri").exists())
        self.assertContains(response, "Obergrenze liegt unter der Untergrenze")

    def test_editing_keeps_the_address(self):
        species = create_plant()
        self.client.post(
            reverse("catalog:plant-update", args=[species.slug]),
            self.plant_payload(scientific_name="Cryptocoryne beckettii", common_name="Neu"),
        )
        species.refresh_from_db()
        self.assertEqual(species.common_name, "Neu")
        self.assertEqual(species.slug, "cryptocoryne-wendtii")

    def test_an_animal_species_is_created(self):
        self.client.post(
            reverse("catalog:animal-create"),
            {
                "scientific_name": "Otocinclus affinis",
                "common_name": "Ohrgitterharnischwels",
                "summary": "",
                "description": "",
                "water_type": WaterType.FRESHWATER,
                "difficulty": Difficulty.MEDIUM,
                "category": AnimalSpecies.Category.FISH,
                "temperament": AnimalSpecies.Temperament.PEACEFUL,
                "min_group_size": "6",
            },
        )
        self.assertTrue(AnimalSpecies.objects.filter(slug="otocinclus-affinis").exists())

    def test_a_species_kept_in_a_tank_is_not_deleted(self):
        animal = create_animal()
        stock(create_tank(self.user), animal)
        response = self.client.post(reverse("catalog:animal-delete", args=[animal.slug]))
        self.assertRedirects(response, animal.get_absolute_url())
        self.assertTrue(AnimalSpecies.objects.filter(pk=animal.pk).exists())

    def test_the_confirmation_names_the_number_of_uses(self):
        animal = create_animal()
        stock(create_tank(self.user), animal)
        response = self.client.get(reverse("catalog:animal-delete", args=[animal.slug]))
        self.assertEqual(response.context["usage_count"], 1)

    def test_an_unused_species_is_deleted(self):
        animal = create_animal()
        response = self.client.post(reverse("catalog:animal-delete", args=[animal.slug]))
        self.assertRedirects(response, reverse("catalog:animal-list"))
        self.assertFalse(AnimalSpecies.objects.exists())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SpeciesImageTests(TestCase):
    def setUp(self):
        self.user = grant_catalog_edit(create_user())
        self.client.force_login(self.user)
        self.species = create_plant()

    def test_several_images_are_uploaded_at_once(self):
        self.client.post(
            reverse("catalog:plant-image-upload", args=[self.species.slug]),
            {"images": [image_upload("a.png"), image_upload("b.png")], "caption": "Aufsitzend"},
        )
        self.assertEqual(self.species.images.count(), 2)
        # Ohne Primärbild wird das erste hochgeladene zum Primärbild.
        self.assertEqual(self.species.images.filter(is_primary=True).count(), 1)

    def test_the_primary_image_can_be_chosen_and_is_unique(self):
        self.client.post(
            reverse("catalog:plant-image-upload", args=[self.species.slug]),
            {"images": [image_upload("a.png"), image_upload("b.png")], "caption": ""},
        )
        second = self.species.images.filter(is_primary=False).get()
        self.client.post(
            reverse("catalog:plant-image-primary", args=[self.species.slug, second.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual([i.pk for i in self.species.images.filter(is_primary=True)], [second.pk])
        self.assertEqual(self.species.primary_image.pk, second.pk)

    def test_deleting_asks_first_and_promotes_a_successor(self):
        self.client.post(
            reverse("catalog:plant-image-upload", args=[self.species.slug]),
            {"images": [image_upload("a.png"), image_upload("b.png")], "caption": ""},
        )
        primary = self.species.images.get(is_primary=True)
        url = reverse("catalog:plant-image-delete", args=[self.species.slug, primary.pk])

        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Bild löschen?")
        self.assertEqual(self.species.images.count(), 2)

        self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(self.species.images.count(), 1)
        self.assertTrue(self.species.images.get().is_primary)

    def test_the_gallery_comes_back_as_a_fragment(self):
        response = self.client.post(
            reverse("catalog:plant-image-upload", args=[self.species.slug]),
            {"images": [image_upload("a.png")], "caption": ""},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        self.assertNotIn("<html", content)
        self.assertIn('id="katalog-galerie"', content)
