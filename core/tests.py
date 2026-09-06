from django.test import SimpleTestCase, override_settings

from core.crypto import DecryptionError, decrypt, encrypt


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
