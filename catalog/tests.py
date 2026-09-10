import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog import sources
from catalog.models import AnimalSpecies, PlantSpecies, SpeciesLink
from core.enums import Difficulty, WaterType
from core.testing import (
    create_animal,
    create_plant,
    create_tank,
    create_user,
    image_upload,
    stock,
    unbalanced_tags,
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


class SpeciesVariantTests(TestCase):
    """Stammform und Zuchtform sind zwei Steckbriefe, nicht einer.

    *Mikrogeophagus ramirezi* ist das Musterbeispiel: die Wildform betreibt
    Brutpflege und wird mehrere Jahre alt, 'Electric Blue' ist hochgezüchtet,
    kurzlebig und infektanfällig. Ein gemeinsamer Eintrag müsste beides
    behaupten.
    """

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.wild = create_animal(
            "Mikrogeophagus ramirezi",
            slug="mikrogeophagus-ramirezi",
            common_name="Schmetterlingsbuntbarsch",
        )
        self.blue = create_animal(
            "Mikrogeophagus ramirezi",
            slug="mikrogeophagus-ramirezi-electric-blue",
            common_name="Schmetterlingsbuntbarsch",
            variant="Electric Blue",
            is_cultivated_form=True,
        )

    def test_one_scientific_name_carries_several_forms(self):
        self.assertEqual(
            AnimalSpecies.objects.filter(scientific_name="Mikrogeophagus ramirezi").count(), 2
        )

    def test_the_same_form_twice_is_refused_regardless_of_case(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_animal(
                "mikrogeophagus ramirezi",
                slug="noch-einer",
                variant="electric blue",
                is_cultivated_form=True,
            )

    def test_the_stem_form_is_the_entry_without_a_variant(self):
        # Zweimal „ohne Sorte“ ist derselbe Eintrag — das bleibt ausgeschlossen.
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_animal("Mikrogeophagus ramirezi", slug="dublette")

    def test_the_display_name_carries_the_variant(self):
        self.assertEqual(str(self.wild), "Schmetterlingsbuntbarsch")
        self.assertEqual(str(self.blue), "Schmetterlingsbuntbarsch 'Electric Blue'")

    def test_a_species_without_a_common_name_falls_back_to_the_scientific_one(self):
        species = create_plant(
            "Alternanthera reineckii",
            slug="alternanthera-reineckii-red-ruby",
            common_name="",
            variant="Red Ruby",
        )
        self.assertEqual(str(species), "Alternanthera reineckii 'Red Ruby'")

    def test_search_finds_a_species_by_its_variant(self):
        response = self.client.get(reverse("catalog:animal-list"), {"q": "Electric"})
        self.assertEqual(
            [species.pk for species in response.context["species_list"]], [self.blue.pk]
        )

    def test_only_stem_forms_can_be_asked_for(self):
        response = self.client.get(reverse("catalog:animal-list"), {"nur_stammformen": "1"})
        self.assertEqual(
            [species.pk for species in response.context["species_list"]], [self.wild.pk]
        )
        self.assertTrue(response.context["filters"]["wild_only"])

    def test_without_the_filter_both_forms_are_listed(self):
        response = self.client.get(reverse("catalog:animal-list"))
        self.assertEqual(response.context["result_count"], 2)
        self.assertFalse(response.context["filters"]["wild_only"])

    def test_the_list_marks_a_cultivated_form(self):
        response = self.client.get(reverse("catalog:animal-grid"), {"q": "Electric"})
        self.assertContains(response, "Zuchtform")
        self.assertContains(response, "&#x27;Electric Blue&#x27;")

    def test_the_list_does_not_mark_the_stem_form(self):
        response = self.client.get(reverse("catalog:animal-grid"), {"nur_stammformen": "1"})
        self.assertNotContains(response, "Zuchtform")

    def test_the_detail_page_names_the_form(self):
        response = self.client.get(self.blue.get_absolute_url())
        self.assertContains(response, "Sorte 'Electric Blue'")
        self.assertContains(response, "Zuchtform")


def grant_catalog_edit(user):
    """Gibt dem Benutzer das Pflegerecht aus catalog.CatalogPermission."""
    user.user_permissions.add(Permission.objects.get(codename="can_edit_catalog"))
    return get_user_model().objects.get(pk=user.pk)


class OriginTests(TestCase):
    """Natürliche Verbreitung: Auswahlfeld zum Filtern, Freitext zum Lesen."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.tetra = create_animal(
            "Paracheirodon innesi",
            origin_region=AnimalSpecies.Region.SOUTH_AMERICA,
            origin_detail="Oberer Rio Negro, Brasilien und Kolumbien",
        )
        self.shrimp = create_animal(
            "Neocaridina davidi",
            slug="neocaridina-davidi",
            common_name="Zwerggarnele",
            origin_region=AnimalSpecies.Region.ASIA,
        )

    def test_the_profile_names_region_and_detail(self):
        response = self.client.get(self.tetra.get_absolute_url())
        self.assertContains(response, "Südamerika")
        self.assertContains(response, "Oberer Rio Negro")

    def test_a_species_without_an_origin_shows_no_row(self):
        species = create_animal("Danio rerio", slug="danio-rerio", common_name="Zebrabärbling")
        response = self.client.get(species.get_absolute_url())
        self.assertNotContains(response, "<dt>Herkunft</dt>")

    def test_the_catalog_can_be_narrowed_to_one_region(self):
        response = self.client.get(
            reverse("catalog:animal-list"), {"verbreitung": AnimalSpecies.Region.SOUTH_AMERICA}
        )
        self.assertEqual(
            [species.pk for species in response.context["species_list"]], [self.tetra.pk]
        )

    def test_an_unknown_region_is_ignored(self):
        response = self.client.get(reverse("catalog:animal-list"), {"verbreitung": "atlantis"})
        self.assertEqual(response.context["result_count"], 2)

    def test_plants_carry_the_same_field(self):
        crypto = create_plant(origin_region=PlantSpecies.Region.ASIA, origin_detail="Sri Lanka")
        response = self.client.get(
            reverse("catalog:plant-list"), {"verbreitung": PlantSpecies.Region.ASIA}
        )
        self.assertEqual([species.pk for species in response.context["species_list"]], [crypto.pk])

    def test_a_cultivar_without_a_wild_population_says_so(self):
        # „Zuchtform ohne Wildvorkommen" ist eine Aussage und keine Lücke.
        blue = create_animal(
            "Mikrogeophagus ramirezi",
            slug="mikrogeophagus-ramirezi-electric-blue",
            variant="Electric Blue",
            is_cultivated_form=True,
            origin_region=AnimalSpecies.Region.CULTIVAR,
        )
        response = self.client.get(blue.get_absolute_url())
        self.assertContains(response, "Zuchtform ohne Wildvorkommen")


class AnimalTraitTests(TestCase):
    """Aufenthaltsbereich, Ernährung und Sozialstruktur — Steckbrief und Filter."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.catfish = create_animal(
            "Corydoras paleatus",
            slug="corydoras-paleatus",
            common_name="Marmorierter Panzerwels",
            zone=AnimalSpecies.Zone.BOTTOM,
            diet=AnimalSpecies.Diet.OMNIVORE,
            social_structure=AnimalSpecies.Social.SHOAL,
        )
        self.cichlid = create_animal(
            "Apistogramma cacatuoides",
            slug="apistogramma-cacatuoides",
            common_name="Kakadu-Zwergbuntbarsch",
            zone=AnimalSpecies.Zone.LOWER,
            diet=AnimalSpecies.Diet.CARNIVORE,
            social_structure=AnimalSpecies.Social.HAREM,
        )

    def test_the_profile_names_all_three(self):
        response = self.client.get(self.catfish.get_absolute_url())
        self.assertContains(response, "Aufenthaltsbereich")
        self.assertContains(response, "Boden")
        self.assertContains(response, "Beides")
        self.assertContains(response, "Schwarm")

    def test_an_unset_trait_shows_no_row(self):
        plain = create_animal("Danio rerio", slug="danio-rerio")
        response = self.client.get(plain.get_absolute_url())
        self.assertNotContains(response, "Aufenthaltsbereich")

    def test_each_trait_is_a_filter(self):
        cases = [
            ({"bereich": AnimalSpecies.Zone.BOTTOM}, self.catfish),
            ({"ernaehrung": AnimalSpecies.Diet.CARNIVORE}, self.cichlid),
            ({"sozialstruktur": AnimalSpecies.Social.HAREM}, self.cichlid),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                response = self.client.get(reverse("catalog:animal-list"), query)
                self.assertEqual(
                    [species.pk for species in response.context["species_list"]], [expected.pk]
                )

    def test_filters_combine_with_the_search(self):
        response = self.client.get(
            reverse("catalog:animal-list"),
            {"q": "Corydoras", "ernaehrung": AnimalSpecies.Diet.CARNIVORE},
        )
        self.assertEqual(response.context["result_count"], 0)

    def test_the_filter_bar_offers_them(self):
        response = self.client.get(reverse("catalog:animal-list"))
        params = [row["param"] for row in response.context["filter_rows"]]
        self.assertEqual(
            params, ["filter", "verbreitung", "bereich", "ernaehrung", "sozialstruktur"]
        )

    def test_the_plant_catalog_keeps_its_own_filters(self):
        response = self.client.get(reverse("catalog:plant-list"))
        params = [row["param"] for row in response.context["filter_rows"]]
        self.assertEqual(params, ["filter", "verbreitung", "kultivierbarkeit"])


class PlantCultivationTests(TestCase):
    """Kultivierbarkeit emers / submers / beides — Steckbrief und Filter."""

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.anubias = create_plant(
            "Anubias barteri",
            slug="anubias-barteri",
            common_name="Speerblatt",
            growth_form_water=PlantSpecies.Growth.BOTH,
            emersed_notes="Blüht emers regelmäßig; der Übergang braucht hohe Luftfeuchte.",
        )
        self.eleocharis = create_plant(
            "Eleocharis acicularis",
            slug="eleocharis-acicularis",
            common_name="Nadelsimse",
            growth_form_water=PlantSpecies.Growth.SUBMERSED,
        )
        self.fittonia = create_plant(
            "Fittonia albivenis",
            slug="fittonia-albivenis",
            common_name="Silbernetzblatt",
            growth_form_water=PlantSpecies.Growth.EMERSED,
        )

    def test_the_profile_names_the_cultivation_form(self):
        response = self.client.get(self.anubias.get_absolute_url())
        self.assertContains(response, "Kultivierbarkeit")
        self.assertContains(response, "Beides")

    def test_the_profile_carries_the_notes_the_choice_is_too_coarse_for(self):
        response = self.client.get(self.anubias.get_absolute_url())
        self.assertContains(response, "Emerse Kultur")
        self.assertContains(response, "Blüht emers regelmäßig")

    def test_an_unset_cultivation_form_shows_no_row(self):
        plain = create_plant("Vesicularia dubyana", slug="vesicularia-dubyana")
        response = self.client.get(plain.get_absolute_url())
        self.assertNotContains(response, "Kultivierbarkeit")

    def test_only_submersed_species_can_be_asked_for(self):
        """Die Abfrage, die beim Kauf zählt: was hält untergetaucht wirklich?"""
        response = self.client.get(
            reverse("catalog:plant-list"),
            {"kultivierbarkeit": PlantSpecies.Growth.SUBMERSED},
        )
        self.assertEqual(
            [species.pk for species in response.context["species_list"]], [self.eleocharis.pk]
        )

    def test_the_filter_separates_the_three_forms(self):
        cases = [
            (PlantSpecies.Growth.EMERSED, self.fittonia),
            (PlantSpecies.Growth.BOTH, self.anubias),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                response = self.client.get(
                    reverse("catalog:plant-list"), {"kultivierbarkeit": value}
                )
                self.assertEqual(
                    [species.pk for species in response.context["species_list"]], [expected.pk]
                )

    def test_an_unknown_value_is_ignored(self):
        response = self.client.get(
            reverse("catalog:plant-list"), {"kultivierbarkeit": "amphibisch"}
        )
        self.assertEqual(response.context["result_count"], 3)

    def test_the_filter_bar_offers_it(self):
        response = self.client.get(reverse("catalog:plant-list"))
        self.assertContains(response, 'name="kultivierbarkeit"')
        self.assertContains(response, "Nur submers")

    def test_the_animal_catalog_has_no_such_field(self):
        """Der Name darf nicht mit der Wuchsform aus #1212 kollidieren.

        Sie heißt ``growth_form``; diese Angabe heißt ``growth_form_water`` und
        gibt es nur an der Pflanze. Ein Tier hat weder das eine noch das andere.
        """
        self.assertFalse(hasattr(AnimalSpecies, "growth_form_water"))
        field_names = {field.name for field in PlantSpecies._meta.get_fields()}
        self.assertIn("growth_form_water", field_names)
        self.assertNotIn("growth_form", field_names)


class SpeciesLinkModelTests(TestCase):
    """Ein Link gehört zu genau einer Art — sonst nimmt ihn die Datenbank nicht."""

    def setUp(self):
        self.plant = create_plant()
        self.animal = create_animal()

    def test_a_link_to_a_plant_is_stored(self):
        link = SpeciesLink.objects.create(
            plant=self.plant, title="Flowgrow", url="https://www.flowgrow.de/"
        )
        self.assertEqual(list(self.plant.links.all()), [link])
        self.assertEqual(link.species, self.plant)
        self.assertEqual(link.species_kind, "plant")

    def test_two_species_at_once_are_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpeciesLink.objects.create(
                plant=self.plant, animal=self.animal, title="Beides", url="https://example.org/"
            )

    def test_a_link_without_a_species_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpeciesLink.objects.create(title="Nirgends", url="https://example.org/")

    def test_the_form_level_check_reports_both_cases(self):
        for link in (
            SpeciesLink(plant=self.plant, animal=self.animal, title="x", url="https://e.org/"),
            SpeciesLink(title="x", url="https://e.org/"),
        ):
            with self.subTest(plant=link.plant_id, animal=link.animal_id):
                with self.assertRaises(ValidationError):
                    link.clean()

    def test_links_are_sorted_by_position(self):
        second = SpeciesLink.objects.create(
            animal=self.animal, title="Forum", url="https://example.org/2", position=2
        )
        first = SpeciesLink.objects.create(
            animal=self.animal, title="DRTA-Archiv", url="https://example.org/1", position=1
        )
        self.assertEqual(list(self.animal.links.all()), [first, second])

    def test_deleting_the_species_takes_its_links(self):
        SpeciesLink.objects.create(animal=self.animal, title="x", url="https://example.org/")
        self.animal.delete()
        self.assertFalse(SpeciesLink.objects.exists())


@override_settings(
    CATALOG_SEARCH_SOURCES={
        "animal": [{"key": "drta", "label": "DRTA-Archiv", "url": "https://drta.test/?s={query}"}],
        "plant": [
            {"key": "flowgrow", "label": "Flowgrow", "url": "https://flowgrow.test/?q={query}"}
        ],
    }
)
class SearchLinkTests(TestCase):
    """Suchlink-Hilfe statt Datenabruf."""

    def setUp(self):
        self.user = grant_catalog_edit(create_user())
        self.client.force_login(self.user)
        self.animal = create_animal()

    def test_the_address_carries_the_scientific_name(self):
        links = sources.search_links("animal", self.animal)
        self.assertEqual(
            links, [{"label": "DRTA-Archiv", "url": "https://drta.test/?s=Paracheirodon+innesi"}]
        )

    def test_the_cultivar_is_searched_by_its_species_name(self):
        # Eine Fachdatenbank führt keine Zuchtformen — mit „Electric Blue" im
        # Suchbegriff liefert sie nichts.
        blue = create_animal(
            "Mikrogeophagus ramirezi",
            slug="mikrogeophagus-ramirezi-electric-blue",
            variant="Electric Blue",
            is_cultivated_form=True,
        )
        self.assertIn("Mikrogeophagus+ramirezi", sources.search_links("animal", blue)[0]["url"])
        self.assertNotIn("Electric", sources.search_links("animal", blue)[0]["url"])

    @override_settings(
        CATALOG_SEARCH_SOURCES={"animal": [{"label": "Ohne", "url": "https://x.test/"}]}
    )
    def test_a_pattern_without_a_placeholder_is_skipped(self):
        self.assertEqual(sources.search_links("animal", self.animal), [])

    def test_without_a_configured_source_there_is_nothing_to_offer(self):
        with override_settings(CATALOG_SEARCH_SOURCES={}):
            self.assertEqual(sources.search_links("animal", self.animal), [])

    def test_the_detail_page_offers_the_search(self):
        response = self.client.get(self.animal.get_absolute_url())
        self.assertContains(response, "DRTA-Archiv durchsuchen")
        self.assertContains(response, "https://drta.test/?s=Paracheirodon+innesi")

    def test_the_plant_catalog_points_at_its_own_source(self):
        plant = create_plant()
        response = self.client.get(plant.get_absolute_url())
        self.assertContains(response, "Flowgrow durchsuchen")

    def test_without_the_permission_there_is_no_search_button(self):
        self.client.force_login(create_user("leser"))
        response = self.client.get(self.animal.get_absolute_url())
        self.assertNotContains(response, "DRTA-Archiv durchsuchen")


class SpeciesLinkViewTests(TestCase):
    """Links auf der Detailseite pflegen — mit HTMX als Fragment, ohne als Seite."""

    def setUp(self):
        self.user = grant_catalog_edit(create_user())
        self.client.force_login(self.user)
        self.animal = create_animal()

    def payload(self, **overrides):
        data = {
            "kind": SpeciesLink.Kind.DATABASE,
            "title": "DRTA-Archiv: Neonsalmler",
            "url": "https://www.drta-archiv.de/paracheirodon-innesi/",
            "position": "0",
        }
        data.update(overrides)
        return data

    def create_url(self):
        return reverse("catalog:animal-link-create", args=[self.animal.slug])

    def test_a_link_is_added_and_shown_on_the_detail_page(self):
        self.client.post(self.create_url(), self.payload())
        link = self.animal.links.get()
        self.assertEqual(link.animal, self.animal)
        response = self.client.get(self.animal.get_absolute_url())
        self.assertContains(response, "DRTA-Archiv: Neonsalmler")
        self.assertContains(response, "Artdatenbank")

    def test_the_section_comes_back_as_a_fragment(self):
        response = self.client.post(self.create_url(), self.payload(), HTTP_HX_REQUEST="true")
        content = response.content.decode()
        self.assertNotIn("<html", content)
        self.assertIn('id="katalog-quellen"', content)

    def test_without_htmx_the_form_stays_on_the_page(self):
        # Ohne JavaScript muss „Quelle hinzufügen" eine Seite mit Formular
        # liefern — ein Redirect auf die Detailseite hätte keine Wirkung.
        response = self.client.get(self.create_url())
        self.assertContains(response, "Quelle hinzufügen")
        self.assertContains(response, 'name="url"')

    def test_an_address_without_a_scheme_is_refused(self):
        response = self.client.post(
            self.create_url(), self.payload(url="ftp://example.org/datei"), HTTP_HX_REQUEST="true"
        )
        self.assertFalse(self.animal.links.exists())
        self.assertContains(response, "http://")

    def test_a_link_is_edited(self):
        self.client.post(self.create_url(), self.payload())
        link = self.animal.links.get()
        self.client.post(
            reverse("catalog:animal-link-update", args=[self.animal.slug, link.pk]),
            self.payload(title="Neu benannt", kind=SpeciesLink.Kind.FORUM),
        )
        link.refresh_from_db()
        self.assertEqual(link.title, "Neu benannt")
        self.assertEqual(link.kind, SpeciesLink.Kind.FORUM)

    def test_deleting_asks_first(self):
        self.client.post(self.create_url(), self.payload())
        link = self.animal.links.get()
        url = reverse("catalog:animal-link-delete", args=[self.animal.slug, link.pk])

        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Quelle entfernen?")
        self.assertTrue(self.animal.links.exists())

        self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertFalse(self.animal.links.exists())

    def test_a_link_of_another_species_is_not_reachable(self):
        other = create_animal("Danio rerio", slug="danio-rerio")
        link = SpeciesLink.objects.create(animal=other, title="x", url="https://example.org/")
        response = self.client.get(
            reverse("catalog:animal-link-update", args=[self.animal.slug, link.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_plant_catalog_has_its_own_addresses(self):
        plant = create_plant()
        self.client.post(
            reverse("catalog:plant-link-create", args=[plant.slug]),
            self.payload(title="Flowgrow", url="https://www.flowgrow.de/db/cryptocoryne-wendtii"),
        )
        self.assertEqual(plant.links.get().plant, plant)

    def test_without_the_permission_no_link_can_be_touched(self):
        link = SpeciesLink.objects.create(
            animal=self.animal, title="x", url="https://example.org/"
        )
        self.client.force_login(create_user("leser"))
        urls = [
            self.create_url(),
            reverse("catalog:animal-link-update", args=[self.animal.slug, link.pk]),
            reverse("catalog:animal-link-delete", args=[self.animal.slug, link.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_a_reader_sees_the_links_but_no_buttons(self):
        SpeciesLink.objects.create(animal=self.animal, title="Quelle", url="https://example.org/")
        self.client.force_login(create_user("leser"))
        response = self.client.get(self.animal.get_absolute_url())
        self.assertContains(response, "Quelle")
        self.assertNotContains(response, "Quelle hinzufügen")


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

    def test_the_address_of_a_cultivar_carries_the_variant(self):
        self.client.post(
            reverse("catalog:plant-create"),
            self.plant_payload(scientific_name="Cryptocoryne wendtii"),
        )
        self.client.post(
            reverse("catalog:plant-create"),
            self.plant_payload(
                scientific_name="Cryptocoryne wendtii",
                variant="Flamingo",
                is_cultivated_form="on",
            ),
        )
        self.assertEqual(
            sorted(PlantSpecies.objects.values_list("slug", flat=True)),
            ["cryptocoryne-wendtii", "cryptocoryne-wendtii-flamingo"],
        )

    def test_the_same_species_and_variant_is_refused_by_the_form(self):
        create_plant(
            "Hygrophila polysperma",
            slug="hygrophila-polysperma-sunset",
            variant="Sunset",
            is_cultivated_form=True,
        )
        response = self.client.post(
            reverse("catalog:plant-create"),
            self.plant_payload(scientific_name="hygrophila polysperma", variant="sunset"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PlantSpecies.objects.count(), 1)
        self.assertContains(response, "bereits im Katalog")

    def test_quotes_around_the_variant_are_not_stored(self):
        self.client.post(
            reverse("catalog:plant-create"),
            self.plant_payload(variant="'Flamingo'"),
        )
        self.assertEqual(PlantSpecies.objects.get().variant, "Flamingo")

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
                "origin_region": AnimalSpecies.Region.SOUTH_AMERICA,
                "origin_detail": "Südostbrasilien",
                "zone": AnimalSpecies.Zone.BOTTOM,
                "diet": AnimalSpecies.Diet.HERBIVORE,
                "social_structure": AnimalSpecies.Social.GROUP,
            },
        )
        species = AnimalSpecies.objects.get(slug="otocinclus-affinis")
        self.assertEqual(species.origin_region, AnimalSpecies.Region.SOUTH_AMERICA)
        self.assertEqual(species.origin_detail, "Südostbrasilien")
        self.assertEqual(species.zone, AnimalSpecies.Zone.BOTTOM)
        self.assertEqual(species.diet, AnimalSpecies.Diet.HERBIVORE)
        self.assertEqual(species.social_structure, AnimalSpecies.Social.GROUP)

    def test_the_profile_fields_stay_optional(self):
        """Ein Steckbrief ohne Haltungsmerkmale muss speicherbar bleiben.

        Der Katalog wird nachgepflegt; wer einen Eintrag anlegt, hat selten
        alles zur Hand.
        """
        response = self.client.post(reverse("catalog:plant-create"), self.plant_payload())
        species = PlantSpecies.objects.get(scientific_name="Anubias barteri")
        self.assertRedirects(response, species.get_absolute_url())
        self.assertEqual(species.origin_region, "")
        self.assertEqual(species.origin_display, "")
        self.assertEqual(species.growth_form_water, "")
        self.assertEqual(species.emersed_notes, "")

    def test_the_cultivation_form_is_stored_with_its_notes(self):
        self.client.post(
            reverse("catalog:plant-create"),
            self.plant_payload(
                growth_form_water=PlantSpecies.Growth.BOTH,
                emersed_notes="Emers schneller und blühend; langsam umgewöhnen.",
            ),
        )
        species = PlantSpecies.objects.get(slug="anubias-barteri")
        self.assertEqual(species.growth_form_water, PlantSpecies.Growth.BOTH)
        self.assertIn("langsam umgewöhnen", species.emersed_notes)

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


class DetailLayoutTests(TestCase):
    """Die rechte Spalte der Detailseite trägt zwei Karten untereinander.

    Ohne Stapel gilt für beide `.mad-card { height: 100% }` — jede beansprucht
    dann die volle Spaltenhöhe, und die zweite rutscht unter die erste (#1252).
    """

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.animal = create_animal()
        self.plant = create_plant()

    def detail_pages(self):
        return {"Tier": self.animal, "Pflanze": self.plant}

    def test_both_cards_of_the_right_column_sit_in_one_stack(self):
        for label, species in self.detail_pages().items():
            with self.subTest(label):
                content = self.client.get(species.get_absolute_url()).content.decode()
                stack = content.index('class="mad-card-stack"')
                gallery = content.index('id="katalog-galerie"')
                for heading in ("In eigenen Becken", 'id="katalog-quellen"'):
                    self.assertLess(stack, content.index(heading))
                    self.assertLess(content.index(heading), gallery)

    def test_the_rendered_page_has_no_unbalanced_element(self):
        tank = create_tank(self.user)
        stock(tank, self.animal, quantity=12)
        SpeciesLink.objects.create(
            animal=self.animal, title="DRTA-Archiv", url="https://www.drta-archiv.de/"
        )
        for label, species in self.detail_pages().items():
            with self.subTest(label):
                content = self.client.get(species.get_absolute_url()).content.decode()
                self.assertEqual(unbalanced_tags(content), [])

    def test_an_empty_own_tanks_card_keeps_the_page_intact(self):
        # Die Art in keinem eigenen Becken: die Karte schrumpft auf ihren
        # Hinweistext, die Quellen-Karte muss trotzdem darunter stehen.
        for label, species in self.detail_pages().items():
            with self.subTest(label):
                content = self.client.get(species.get_absolute_url()).content.decode()
                self.assertIn("kommt in keinem deiner Becken vor", content)
                self.assertLess(
                    content.index("In eigenen Becken"), content.index('id="katalog-quellen"')
                )
                self.assertEqual(unbalanced_tags(content), [])
