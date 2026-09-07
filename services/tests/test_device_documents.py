"""Technische Daten, Dokumente und Links am Gerät.

Der wichtigste Teil steht in :class:`DocumentAccessTests`: eine Rechnung ist
kein Beckenfoto. Sie liegt außerhalb von ``MEDIA_ROOT`` und geht nur über eine
Ansicht raus, die vorher prüft, wem das Gerät gehört — geprüft wird das hier
nicht über die Oberfläche, sondern über den direkten Aufruf der Adresse.
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.testing import tank_named
from services.models import Device, DeviceDocument, DeviceLink, DeviceSpec

PDF = b"%PDF-1.4\nnur ein Beleg\n"


def document_upload(name="rechnung.pdf", payload=PDF):
    return SimpleUploadedFile(name, payload, content_type="application/pdf")


def make_manual_device(owner, name="Heizstab", **kwargs):
    """Ein Gerät ohne Anbindung — für Dokumente ist die Anbindung ohne Belang."""
    return Device.objects.create(
        owner=owner,
        tank=kwargs.pop("tank", None) or tank_named(owner),
        name=name,
        kind=kwargs.pop("kind", Device.Kind.HEATER),
        **kwargs,
    )


class DeviceDataTestCase(TestCase):
    """Jeder Test bekommt eine eigene, leere geschützte Ablage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.mkdtemp()
        cls.override = override_settings(PRIVATE_MEDIA_ROOT=cls.private_root)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        shutil.rmtree(cls.private_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="max", email="max@example.com", password="geheim-123"
        )
        self.client.force_login(self.user)
        self.device = make_manual_device(self.user)

    def add_document(self, title="Rechnung", kind=DeviceDocument.Kind.INVOICE, device=None):
        return DeviceDocument.objects.create(
            device=device or self.device, kind=kind, title=title, file=document_upload()
        )


class SpecTests(DeviceDataTestCase):
    def test_a_spec_is_created_and_shown(self):
        response = self.client.post(
            reverse("services:device_spec_create", args=[self.device.pk]),
            {"label": "Farbtemperatur", "value": "6500", "unit": "K", "position": 0},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Farbtemperatur")
        self.assertContains(response, "6500 K")

    def test_specs_are_ordered_by_position(self):
        DeviceSpec.objects.create(device=self.device, label="Zweitens", value="2", position=2)
        DeviceSpec.objects.create(device=self.device, label="Erstens", value="1", position=1)
        labels = [spec.label for spec in self.device.specs.all()]
        self.assertEqual(labels, ["Erstens", "Zweitens"])

    def test_htmx_gets_only_the_section_back(self):
        response = self.client.get(
            reverse("services:device_spec_create", args=[self.device.pk]), HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, 'id="geraet-specs"')
        self.assertNotContains(response, "<html")

    def test_without_htmx_the_whole_page_comes_back_with_the_form(self):
        response = self.client.get(reverse("services:device_spec_create", args=[self.device.pk]))
        self.assertContains(response, "Technische Angabe")
        self.assertContains(response, "<html")

    def test_deleting_asks_first(self):
        spec = DeviceSpec.objects.create(device=self.device, label="Lumen", value="1200")
        self.client.get(reverse("services:device_spec_delete", args=[self.device.pk, spec.pk]))
        self.assertTrue(DeviceSpec.objects.filter(pk=spec.pk).exists())

        self.client.post(reverse("services:device_spec_delete", args=[self.device.pk, spec.pk]))
        self.assertFalse(DeviceSpec.objects.filter(pk=spec.pk).exists())

    def test_a_spec_of_a_foreign_device_is_not_reachable(self):
        eva = get_user_model().objects.create_user(
            username="eva", email="eva@example.com", password="geheim-123"
        )
        foreign = DeviceSpec.objects.create(
            device=make_manual_device(eva, name="Fremdgerät"), label="Lumen", value="900"
        )
        url = reverse("services:device_spec_update", args=[self.device.pk, foreign.pk])
        self.assertEqual(self.client.get(url).status_code, 404)


class LinkTests(DeviceDataTestCase):
    def test_a_link_is_created(self):
        response = self.client.post(
            reverse("services:device_link_create", args=[self.device.pk]),
            {"title": "Herstellerseite", "url": "https://example.com/geraet", "position": 0},
            follow=True,
        )
        self.assertContains(response, "Herstellerseite")
        self.assertEqual(self.device.links.count(), 1)

    def test_links_are_ordered_by_position(self):
        DeviceLink.objects.create(
            device=self.device, title="Ersatzteile", url="https://example.com/b", position=2
        )
        DeviceLink.objects.create(
            device=self.device, title="Handbuch", url="https://example.com/a", position=1
        )
        self.assertEqual(
            [link.title for link in self.device.links.all()], ["Handbuch", "Ersatzteile"]
        )

    def test_something_that_is_not_an_address_is_refused(self):
        response = self.client.post(
            reverse("services:device_link_create", args=[self.device.pk]),
            {"title": "Kaputt", "url": "kein-link", "position": 0},
        )
        self.assertEqual(self.device.links.count(), 0)
        self.assertContains(response, "gültige")


class DocumentUploadTests(DeviceDataTestCase):
    def test_a_document_is_uploaded_and_listed(self):
        response = self.client.post(
            reverse("services:device_document_create", args=[self.device.pk]),
            {
                "title": "Rechnung 2026",
                "kind": DeviceDocument.Kind.INVOICE,
                "file": document_upload(),
            },
            follow=True,
        )
        self.assertContains(response, "Rechnung 2026")
        document = self.device.documents.get()
        self.assertTrue(document.file.name.startswith(f"devices/{self.device.pk}/docs/"))

    def test_the_file_lands_outside_the_public_media_folder(self):
        document = self.add_document()
        path = document.file.storage.path(document.file.name)
        self.assertTrue(path.startswith(self.private_root))

    def test_a_document_file_has_no_public_address(self):
        """``url()`` gibt es nicht — wer eine baut, hat den Schutz umgangen."""
        document = self.add_document()
        with self.assertRaises(ValueError):
            document.file.url  # noqa: B018 — der Zugriff selbst ist der Test

    def test_an_executable_file_type_is_refused(self):
        response = self.client.post(
            reverse("services:device_document_create", args=[self.device.pk]),
            {
                "title": "Kein Dokument",
                "kind": DeviceDocument.Kind.OTHER,
                "file": SimpleUploadedFile("skript.html", b"<script>", content_type="text/html"),
            },
        )
        self.assertEqual(self.device.documents.count(), 0)
        self.assertContains(response, "Dateiendung")

    @override_settings(DEVICE_DOCUMENT_MAX_BYTES=64)
    def test_a_file_beyond_the_size_limit_is_refused(self):
        response = self.client.post(
            reverse("services:device_document_create", args=[self.device.pk]),
            {
                "title": "Zu groß",
                "kind": DeviceDocument.Kind.MANUAL,
                "file": document_upload(payload=b"x" * 200),
            },
        )
        self.assertEqual(self.device.documents.count(), 0)
        self.assertContains(response, "größer als")


class DocumentAccessTests(DeviceDataTestCase):
    def test_the_owner_gets_the_file(self):
        document = self.add_document()
        response = self.client.get(document.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_somebody_else_gets_a_404_even_with_the_exact_address(self):
        document = self.add_document()
        address = document.get_absolute_url()
        eva = get_user_model().objects.create_user(
            username="eva", email="eva@example.com", password="geheim-123"
        )
        self.client.force_login(eva)
        self.assertEqual(self.client.get(address).status_code, 404)

    def test_without_a_login_there_is_no_file(self):
        document = self.add_document()
        address = document.get_absolute_url()
        self.client.logout()
        response = self.client.get(address)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    @override_settings(PRIVATE_MEDIA_ACCEL_LOCATION="/geschuetzt/")
    def test_nginx_delivers_the_file_when_it_is_configured_to(self):
        document = self.add_document()
        response = self.client.get(document.get_absolute_url())
        self.assertEqual(response["X-Accel-Redirect"], f"/geschuetzt/{document.file.name}")
        self.assertEqual(response.content, b"")


class DocumentDeletionTests(DeviceDataTestCase):
    def test_deleting_a_document_removes_the_file(self):
        document = self.add_document()
        storage, name = document.file.storage, document.file.name
        self.assertTrue(storage.exists(name))

        self.client.post(
            reverse("services:device_document_delete", args=[self.device.pk, document.pk])
        )

        self.assertFalse(DeviceDocument.objects.filter(pk=document.pk).exists())
        self.assertFalse(storage.exists(name))

    def test_deleting_the_device_takes_its_documents_with_it(self):
        document = self.add_document()
        storage, name = document.file.storage, document.file.name

        self.device.delete()

        self.assertEqual(DeviceDocument.objects.count(), 0)
        self.assertFalse(storage.exists(name))


class MasterDataTests(DeviceDataTestCase):
    """Seriennummer, Kauf, Garantie und Nennleistung am Gerät selbst."""

    def test_the_master_data_are_saved_from_the_device_form(self):
        response = self.client.post(
            reverse("services:device_edit", args=[self.device.pk]),
            {
                "name": self.device.name,
                "kind": self.device.kind,
                "tank": self.device.tank_id,
                "status": self.device.status,
                "status_message": "",
                "is_active": "on",
                "serial_number": "SN-4711",
                "supplier": "Zoohandlung Müller",
                "purchased_on": "2026-01-15",
                "purchase_price": "89.90",
                "warranty_until": "2028-01-15",
                "power_watts": "150.0",
                "flow_rate_lph": "600",
                "daily_runtime_hours": "8.0",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.device.refresh_from_db()
        self.assertEqual(self.device.serial_number, "SN-4711")
        self.assertEqual(str(self.device.purchase_price), "89.90")
        self.assertEqual(str(self.device.warranty_until), "2028-01-15")
        self.assertEqual(str(self.device.power_watts), "150.0")
        self.assertEqual(self.device.flow_rate_lph, 600)

    def test_the_detail_page_shows_them(self):
        Device.objects.filter(pk=self.device.pk).update(
            serial_number="SN-4711", supplier="Zoohandlung Müller", power_watts="150.0"
        )
        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))
        self.assertContains(response, "SN-4711")
        self.assertContains(response, "Zoohandlung Müller")
        # Deutsche Lokalisierung: das Komma ist hier kein Tippfehler.
        self.assertContains(response, "150,0 W")

    def test_a_device_without_a_meter_shows_an_estimate_marked_as_one(self):
        Device.objects.filter(pk=self.device.pk).update(
            power_watts="100.0", daily_runtime_hours="10.0"
        )
        response = self.client.get(reverse("services:device_detail", args=[self.device.pk]))
        self.assertContains(response, "geschätzt")
        self.assertContains(response, "Hochgerechnet aus Nennleistung")
