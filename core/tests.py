from datetime import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from core.crypto import DecryptionError, decrypt, encrypt
from core.images import scaled_size, taken_at
from core.testing import image_upload, photo_upload


class CryptoTests(SimpleTestCase):
    def test_round_trip(self):
        self.assertEqual(decrypt(encrypt("geheim")), "geheim")

    def test_ciphertext_differs_from_plaintext(self):
        self.assertNotIn("geheim", encrypt("geheim"))

    def test_same_value_encrypts_to_different_tokens(self):
        self.assertNotEqual(encrypt("geheim"), encrypt("geheim"))

    def test_umlauts_survive_round_trip(self):
        self.assertEqual(decrypt(encrypt("Schlüssel-Größe")), "Schlüssel-Größe")

    def test_wrong_key_raises(self):
        token = encrypt("geheim")
        with override_settings(FIELD_ENCRYPTION_KEYS=["ein-ganz-anderer-schluessel"]):
            with self.assertRaises(DecryptionError):
                decrypt(token)

    def test_garbage_raises(self):
        with self.assertRaises(DecryptionError):
            decrypt("kein-fernet-token")

    def test_key_rotation_keeps_old_values_readable(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=["alt"]):
            token = encrypt("geheim")
        # Neuer Primärschlüssel, alter bleibt zum Entschlüsseln hinterlegt.
        with override_settings(FIELD_ENCRYPTION_KEYS=["neu", "alt"]):
            self.assertEqual(decrypt(token), "geheim")
"""Tests zum Farbkonzept: Palette, Kontraste und Light-Mode-Zusage.

Diese Tests halten die Gestaltungsvorgaben maschinell nach, damit sie beim
Weiterbauen nicht unbemerkt verletzt werden.
"""

import re

from django.conf import settings
from django.test import SimpleTestCase

CSS_PATH = settings.BASE_DIR / "static" / "css" / "main.css"
TEMPLATE_DIR = settings.BASE_DIR / "templates"

VARIABLE_PATTERN = re.compile(r"(--mad-[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;")
COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)
HEX_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
COLOR_FUNCTION_PATTERN = re.compile(r"\b(?:rgba?|hsla?)\(")


def relative_luminance(hex_color):
    """Relative Leuchtdichte nach WCAG 2.1."""
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground, background):
    light, dark = sorted(
        (relative_luminance(foreground), relative_luminance(background)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


class PaletteTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        # Kommentare erklaeren unter anderem, warum es keinen Dark Mode gibt —
        # fuer die Regelpruefung zaehlt nur das tatsaechliche Regelwerk.
        cls.rules = COMMENT_PATTERN.sub("", cls.css)
        cls.palette = dict(VARIABLE_PATTERN.findall(cls.rules))

    def test_palette_matches_specification(self):
        """Die im Farbkonzept festgelegten Werte stehen unverändert im CSS."""
        expected = {
            "--mad-primary": "#1b6b8c",
            "--mad-secondary": "#3e8e5a",
            "--mad-accent": "#d98a2b",
            "--mad-danger": "#c4523e",
            "--mad-bg": "#f7f9fa",
            "--mad-surface": "#ffffff",
            "--mad-text": "#1f2933",
            "--mad-text-muted": "#5c6b73",
        }
        for name, value in expected.items():
            self.assertEqual(self.palette.get(name), value, name)

    def test_text_contrast_meets_wcag_aa(self):
        """Jede Text-auf-Fläche-Kombination erreicht mindestens 4.5:1."""
        pairs = [
            ("--mad-text", "--mad-bg"),
            ("--mad-text", "--mad-surface"),
            ("--mad-text", "--mad-surface-sunken"),
            ("--mad-text-muted", "--mad-bg"),
            ("--mad-text-muted", "--mad-surface"),
            ("--mad-text-muted", "--mad-surface-sunken"),
            ("--mad-primary-strong", "--mad-bg"),
            ("--mad-primary-strong", "--mad-surface"),
            ("--mad-primary-strong", "--mad-primary-tint"),
            ("--mad-secondary-strong", "--mad-surface"),
            ("--mad-secondary-strong", "--mad-secondary-tint"),
            ("--mad-accent-strong", "--mad-surface"),
            ("--mad-accent-strong", "--mad-accent-tint"),
            ("--mad-danger-strong", "--mad-surface"),
            ("--mad-danger-strong", "--mad-danger-tint"),
            # Weiße Schrift auf gefüllten Schaltflächen.
            ("--mad-surface", "--mad-primary"),
            ("--mad-surface", "--mad-secondary-strong"),
            # Wassertyp-Kennzeichnung ist Text, keine reine Fläche.
            ("--mad-water-fresh", "--mad-surface"),
            ("--mad-water-brackish", "--mad-surface"),
            ("--mad-water-marine", "--mad-surface"),
        ]
        for foreground, background in pairs:
            with self.subTest(pair=f"{foreground} auf {background}"):
                ratio = contrast_ratio(self.palette[foreground], self.palette[background])
                self.assertGreaterEqual(round(ratio, 2), 4.5, f"{ratio:.2f}:1")

    def test_interactive_borders_meet_wcag_non_text_contrast(self):
        """Begrenzungen bedienbarer Elemente erreichen mindestens 3:1."""
        for background in ("--mad-surface", "--mad-bg"):
            with self.subTest(background=background):
                ratio = contrast_ratio(self.palette["--mad-border-strong"], self.palette[background])
                self.assertGreaterEqual(round(ratio, 2), 3.0, f"{ratio:.2f}:1")

    def test_tank_accents_are_distinguishable(self):
        """Alle acht Beckenfarben sind verschieden und heben sich vom Grund ab."""
        accents = [self.palette[f"--mad-tank-{i}"] for i in range(1, 9)]
        self.assertEqual(len(set(accents)), 8)
        for accent in accents:
            self.assertGreaterEqual(contrast_ratio(accent, self.palette["--mad-surface"]), 3.0)

    def test_status_colours_are_not_reused_for_other_meanings(self):
        """Grün/Orange/Rot bleiben dem Status vorbehalten.

        Becken- und Wassertypfarben dürfen sie deshalb nicht wiederverwenden.
        """
        status_colours = {
            self.palette["--mad-secondary"],
            self.palette["--mad-accent"],
            self.palette["--mad-danger"],
        }
        identity_colours = {self.palette[f"--mad-tank-{i}"] for i in range(1, 9)} | {
            self.palette["--mad-water-fresh"],
            self.palette["--mad-water-brackish"],
            self.palette["--mad-water-marine"],
        }
        self.assertFalse(status_colours & identity_colours)

    def test_no_dark_mode(self):
        """Kein Dark Mode: weder Systemabfrage noch dunkles Bootstrap-Theme."""
        self.assertNotIn("prefers-color-scheme", self.rules)
        self.assertNotIn('data-bs-theme="dark"', self.rules)
        self.assertIn("color-scheme: light", self.rules)
        base_template = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
        self.assertIn('data-bs-theme="light"', base_template)
        self.assertNotIn("dark", base_template.lower())


class TemplateColourTests(SimpleTestCase):
    """Farben gehören ins Stylesheet, nicht in die Templates."""

    def test_templates_contain_no_colour_values(self):
        for path in sorted(TEMPLATE_DIR.rglob("*.html")):
            content = path.read_text(encoding="utf-8")
            with self.subTest(template=path.relative_to(TEMPLATE_DIR).as_posix()):
                self.assertIsNone(HEX_PATTERN.search(content))
                self.assertIsNone(COLOR_FUNCTION_PATTERN.search(content))


class ImageAspectTests(SimpleTestCase):
    """Das Seitenverhältnis gibt der Container vor, nicht das Bild.

    Der Fehler, den diese Tests festhalten, ist im Browser sichtbar und in
    Django unsichtbar: die Vorlage schreibt ``width``/``height`` ans ``<img>``,
    damit die Fläche vor dem Laden feststeht. Beide Attribute sind
    Presentational Hints und wirken wie eine Autorenregel mit Spezifität 0.
    ``width: 100%`` überschreibt den einen — die Pixelhöhe des anderen bleibt
    ohne eigene Regel stehen und macht jedes ``aspect-ratio`` wirkungslos. Die
    Kachel ist dann so hoch, wie das Bild groß ist, und damit von Bild zu Bild
    verschieden.

    Deshalb wird hier auf ``height: auto`` bestanden: es ist die Regel, die man
    beim Aufräumen für überflüssig hält.
    """

    #: Bildflächen mit vorgegebenem Seitenverhältnis und ihr Sollwert.
    CROPPED = {
        ".mad-thumb": "3 / 2",
        ".mad-cover": "3 / 2",
        ".mad-gallery img": "1 / 1",
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        css = COMMENT_PATTERN.sub("", CSS_PATH.read_text(encoding="utf-8"))
        # Nur die Grundregeln: die Ausnahmen für schmale Displays stehen in
        # ``@media``-Blöcken am Dateiende und tragen dieselben Selektoren
        # noch einmal.
        base = css.split("@media", 1)[0]
        cls.blocks = {
            selector.strip(): body
            for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", base)
        }

    def declarations(self, selector):
        body = self.blocks.get(selector)
        self.assertIsNotNone(body, f"Regel {selector} fehlt")
        pairs = (part.split(":", 1) for part in body.split(";") if ":" in part)
        return {name.strip(): value.strip() for name, value in pairs}

    def test_overview_images_carry_a_fixed_ratio(self):
        for selector, ratio in self.CROPPED.items():
            with self.subTest(selector=selector):
                rules = self.declarations(selector)
                self.assertEqual(rules.get("aspect-ratio"), ratio)
                self.assertEqual(rules.get("object-fit"), "cover")

    def test_overview_images_ignore_the_height_attribute(self):
        """Ohne ``height: auto`` bestimmt das Bild die Layouthöhe."""
        for selector in self.CROPPED:
            with self.subTest(selector=selector):
                self.assertEqual(self.declarations(selector).get("height"), "auto")

    def test_the_large_view_is_not_cropped(self):
        """In der Großansicht zählt die vollständige Aufnahme."""
        rules = self.declarations(".mad-photo")
        self.assertEqual(rules.get("object-fit"), "contain")
        self.assertNotIn("aspect-ratio", rules)
        self.assertEqual(rules.get("height"), "auto")

    def test_the_cover_tile_stays_small(self):
        """Das Titelbild soll die Spalte schmücken, nicht die Seite füllen."""
        self.assertEqual(self.declarations(".mad-cover").get("width"), "min(100%, 22rem)")


class ExifTests(SimpleTestCase):
    """Aufnahmezeitpunkt aus dem Bild — und was passiert, wenn keiner drinsteht."""

    def test_the_shot_time_is_read(self):
        moment = datetime(2026, 5, 1, 18, 30, 0)
        self.assertEqual(taken_at(photo_upload(taken_at=moment)), moment)

    def test_an_image_without_exif_has_no_shot_time(self):
        self.assertIsNone(taken_at(image_upload()))

    def test_the_file_stays_readable_afterwards(self):
        """Das Bild wandert danach in den Speicher — der Zeiger muss vorn stehen."""
        upload = photo_upload(taken_at=datetime(2026, 5, 1, 18, 30, 0))
        taken_at(upload)
        self.assertTrue(upload.read())

    def test_something_that_is_not_an_image_is_no_error(self):
        broken = SimpleUploadedFile("kein-bild.jpg", b"nur text", content_type="image/jpeg")
        self.assertIsNone(taken_at(broken))


class ScaledSizeTests(SimpleTestCase):
    """Die Maße am ``<img>`` müssen zu der Datei passen, die geladen wird.

    Sie werden nicht gemessen, sondern aus den Maßen des Originals gerechnet —
    stimmt die Rechnung nicht, reserviert der Browser die falsche Fläche und
    die Seite springt beim Laden genau so, wie es die Angabe verhindern soll.
    """

    def test_the_longest_edge_is_capped(self):
        self.assertEqual(scaled_size(4000, 3000, 400), (400, 300))

    def test_a_portrait_keeps_standing(self):
        self.assertEqual(scaled_size(3000, 4000, 400), (300, 400))

    def test_a_small_image_keeps_its_size(self):
        """Hochskaliert wird nicht — auch nicht in der Rechnung."""
        self.assertEqual(scaled_size(120, 80, 400), (120, 80))

    def test_a_square_stays_square(self):
        self.assertEqual(scaled_size(2000, 2000, 400), (400, 400))

    def test_without_known_dimensions_there_are_none(self):
        self.assertEqual(scaled_size(None, None, 400), (None, None))
