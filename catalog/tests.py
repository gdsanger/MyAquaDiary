from django.test import TestCase
from django.urls import reverse

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
