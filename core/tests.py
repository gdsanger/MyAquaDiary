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
