"""Titelbild eines Geräts.

Die Bildverarbeitung selbst — verkleinern, EXIF-Orientierung anwenden, GPS
entfernen — steht in :mod:`core.images` und wird von Becken und Gerät geteilt.
Hier wird geprüft, dass das Gerät sie tatsächlich bekommt und dass die Pflege
auf der Detailseite vollständig ist: hochladen, ersetzen, entfernen.
"""

import shutil
import tempfile
from io import BytesIO, StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.images import GPS_IFD
from core.testing import create_user, photo_upload, tank_named
from services.models import Device

MEDIA_ROOT = tempfile.mkdtemp()


def opened(field):
    """Das Bild hinter einem Feld — geöffnet, gelesen, wieder geschlossen."""
    field.open("rb")
    try:
        return Image.open(BytesIO(field.read()))
    finally:
        field.close()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DeviceCoverTestCase(TestCase):
    """Gemeinsame Grundlage: ein Gerät ohne Anbindung, angemeldeter Besitzer."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            owner=self.user,
            tank=tank_named(self.user),
            name="Außenfilter links",
            kind=Device.Kind.FILTER,
        )

    def with_cover(self, **kwargs):
        self.device.set_cover(photo_upload(size=(1200, 800), **kwargs))
        return self.device


class CoverVariantTests(DeviceCoverTestCase):
    """Was beim Hochladen entsteht — dasselbe wie am Becken."""

    def test_both_variants_and_the_dimensions_are_created(self):
        device = self.with_cover()

        self.assertTrue(device.cover_thumbnail)
        self.assertTrue(device.cover_preview)
        self.assertEqual((device.cover_width, device.cover_height), (1200, 800))
        self.assertEqual(opened(device.cover_thumbnail).size, (400, 267))

    def test_the_files_lie_under_the_device(self):
        """Ein Ordner je Gerät — wie bei den Dokumenten, nur öffentlich."""
        device = self.with_cover()

        self.assertTrue(device.cover_image.name.startswith(f"devices/{device.pk}/cover/"))
        self.assertIn("/cover/thumbs/", device.cover_thumbnail.name)
        self.assertIn("/cover/preview/", device.cover_preview.name)

    def test_a_portrait_stands_upright(self):
        device = self.with_cover(orientation=6)

        self.assertEqual((device.cover_width, device.cover_height), (800, 1200))

    def test_the_location_is_removed_everywhere(self):
        device = self.with_cover(located=True)

        for field in (device.cover_image, device.cover_thumbnail, device.cover_preview):
            with self.subTest(field=field.name):
                self.assertNotIn(GPS_IFD, opened(field).getexif())

    def test_the_variant_carries_the_scaled_dimensions(self):
        device = self.with_cover()

        self.assertEqual(device.thumb.url, device.cover_thumbnail.url)
        self.assertEqual((device.thumb.width, device.thumb.height), (400, 267))

    def test_a_replacement_leaves_nothing_behind(self):
        """Es gibt genau ein Titelbild; das alte ist danach Ballast."""
        device = self.with_cover()
        before = [
            Path(field.path)
            for field in (device.cover_image, device.cover_thumbnail, device.cover_preview)
        ]

        device.set_cover(photo_upload("anders.jpg", size=(800, 1200)))

        self.assertEqual((device.cover_width, device.cover_height), (800, 1200))
        self.assertFalse(any(path.exists() for path in before))
        self.assertTrue(Path(device.cover_image.path).exists())

    def test_clearing_removes_files_and_fields(self):
        device = self.with_cover()
        paths = [
            Path(field.path)
            for field in (device.cover_image, device.cover_thumbnail, device.cover_preview)
        ]

        device.clear_cover()
        device.refresh_from_db()

        self.assertFalse(any(path.exists() for path in paths))
        self.assertFalse(device.has_cover)
        self.assertFalse(device.cover_thumbnail)
        self.assertFalse(device.cover_preview)
        self.assertIsNone(device.cover_width)
        self.assertIsNone(device.cover_height)

    def test_deleting_the_device_removes_every_file(self):
        device = self.with_cover()
        paths = [
            Path(field.path)
            for field in (device.cover_image, device.cover_thumbnail, device.cover_preview)
        ]

        device.delete()

        self.assertFalse(any(path.exists() for path in paths))

    def test_saving_the_device_does_not_touch_the_credentials(self):
        """Das Bild ist ein Feld; der Rest des Geräts bleibt, wie er war."""
        self.device.set_credentials("api", "geheim")
        self.device.save()

        self.with_cover()
        self.device.refresh_from_db()

        self.assertEqual(self.device.api_password, "geheim")

    def test_generate_thumbnails_covers_devices(self):
        device = self.with_cover()
        Device.objects.filter(pk=device.pk).update(
            cover_thumbnail="", cover_preview="", cover_width=None, cover_height=None
        )

        call_command("generate_thumbnails", stdout=StringIO(), stderr=StringIO())
        device.refresh_from_db()

        self.assertTrue(device.cover_thumbnail)
        self.assertTrue(device.cover_preview)
        self.assertEqual((device.cover_width, device.cover_height), (1200, 800))


class CoverSymbolTests(TestCase):
    """Ohne Bild steht das Zeichen der Geräteart auf der Fläche."""

    def test_every_kind_has_a_symbol(self):
        for kind in Device.Kind:
            with self.subTest(kind=kind):
                self.assertTrue(Device(kind=kind).cover_symbol)

    def test_a_connected_device_borrows_the_symbol_of_its_function(self):
        self.assertEqual(Device(kind=Device.Kind.EHEIM_CLASSICVARIO).cover_symbol, "filter")
        self.assertEqual(Device(kind=Device.Kind.SHELLY_PLUG).cover_symbol, "socket")

    def test_an_unknown_kind_falls_back(self):
        self.assertEqual(Device(kind="was-auch-immer").cover_symbol, "other")


class CoverViewTests(DeviceCoverTestCase):
    """Hochladen, Ersetzen und Entfernen auf der Detailseite."""

    def url(self, name):
        return reverse(name, args=[self.device.pk])

    def post(self, name, data=None, htmx=True):
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(self.url(name), data or {}, **headers)

    def test_the_placeholder_names_the_kind(self):
        response = self.client.get(self.url("services:device_detail"))

        self.assertContains(response, "mad-cover-placeholder")
        self.assertContains(response, "Kein Titelbild hinterlegt – Filter")

    def test_uploading_stores_the_cover(self):
        response = self.post(
            "services:device_cover_edit", {"cover_image": photo_upload(size=(1200, 800))}
        )
        self.device.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.device.has_cover)
        self.assertContains(response, self.device.cover_preview.url)

    def test_the_answer_to_htmx_is_only_the_section(self):
        response = self.post(
            "services:device_cover_edit", {"cover_image": photo_upload(size=(1200, 800))}
        )

        self.assertContains(response, 'id="geraet-cover"')
        self.assertNotContains(response, "<h1")

    def test_without_htmx_the_whole_page_comes_back(self):
        response = self.post(
            "services:device_cover_edit",
            {"cover_image": photo_upload(size=(1200, 800))},
            htmx=False,
        )

        self.assertContains(response, "<h1")
        self.assertContains(response, 'id="geraet-cover"')

    def test_a_file_that_is_no_image_is_rejected(self):
        response = self.post("services:device_cover_edit", {"cover_image": StringIO("kein Bild")})
        self.device.refresh_from_db()

        self.assertFalse(self.device.has_cover)
        self.assertContains(response, "geraet-cover")

    def test_replacing_over_the_page_keeps_one_image(self):
        self.with_cover()
        before = Path(self.device.cover_image.path)

        self.post("services:device_cover_edit", {"cover_image": photo_upload("neu.jpg", size=(600, 400))})
        self.device.refresh_from_db()

        self.assertTrue(self.device.has_cover)
        self.assertNotEqual(Path(self.device.cover_image.path), before)
        self.assertFalse(before.exists())

    def test_deleting_asks_before_it_acts(self):
        self.with_cover()

        response = self.client.get(self.url("services:device_cover_delete"), HTTP_HX_REQUEST="true")
        self.device.refresh_from_db()

        self.assertContains(response, "Soll das Titelbild")
        self.assertTrue(self.device.has_cover)

    def test_deleting_removes_the_cover(self):
        self.with_cover()

        self.post("services:device_cover_delete")
        self.device.refresh_from_db()

        self.assertFalse(self.device.has_cover)

    def test_the_section_can_be_fetched_again(self):
        """Das Ziel jedes „Abbrechen"."""
        response = self.client.get(self.url("services:device_cover"), HTTP_HX_REQUEST="true")

        self.assertContains(response, 'id="geraet-cover"')

    def test_a_foreign_device_stays_out_of_reach(self):
        other = create_user("fremde")
        self.client.force_login(other)

        for name in (
            "services:device_cover",
            "services:device_cover_edit",
            "services:device_cover_delete",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.url(name)).status_code, 404)


class CoverListTests(DeviceCoverTestCase):
    """Die Kachel in der Geräteliste."""

    def test_the_list_shows_the_thumbnail(self):
        self.with_cover()

        response = self.client.get(reverse("services:device_list"))

        self.assertContains(response, self.device.cover_thumbnail.url)

    def test_without_a_cover_the_placeholder_holds_the_space(self):
        response = self.client.get(reverse("services:device_list"))

        self.assertContains(response, "mad-cover-placeholder")
        self.assertContains(response, "mad-cover-symbol")
