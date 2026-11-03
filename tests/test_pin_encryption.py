import os
import unittest
from cryptography.fernet import Fernet

from backend.pin_crypto import (
    decrypt_pin,
    encrypt_pin,
    get_pin_cipher,
    reset_pin_cipher,
)


class TestPinEncryption(unittest.TestCase):
    def setUp(self):
        self.original_key = os.environ.get("PIN_ENCRYPTION_KEY")
        self.test_key = Fernet.generate_key().decode("utf-8")
        os.environ["PIN_ENCRYPTION_KEY"] = self.test_key
        reset_pin_cipher()

    def tearDown(self):
        if self.original_key is not None:
            os.environ["PIN_ENCRYPTION_KEY"] = self.original_key
        else:
            os.environ.pop("PIN_ENCRYPTION_KEY", None)
        reset_pin_cipher()

    def test_roundtrip_encryption_and_decryption(self):
        pin = "4921"
        ciphertext = encrypt_pin(pin)
        self.assertIsNotNone(ciphertext)
        self.assertTrue(ciphertext.startswith("gAAAAA"))
        self.assertNotEqual(ciphertext, pin)

        decrypted = decrypt_pin(ciphertext)
        self.assertEqual(decrypted, pin)

    def test_empty_or_none_handling(self):
        self.assertIsNone(encrypt_pin(None))
        self.assertIsNone(encrypt_pin(""))
        self.assertIsNone(encrypt_pin("   "))
        self.assertIsNone(decrypt_pin(None))
        self.assertIsNone(decrypt_pin(""))
        self.assertIsNone(decrypt_pin("   "))

    def test_missing_encryption_key_raises_runtime_error(self):
        os.environ.pop("PIN_ENCRYPTION_KEY", None)
        reset_pin_cipher()
        with self.assertRaises(RuntimeError) as ctx:
            get_pin_cipher()
        self.assertIn("PIN_ENCRYPTION_KEY environment variable is missing", str(ctx.exception))

    def test_invalid_key_raises_runtime_error(self):
        os.environ["PIN_ENCRYPTION_KEY"] = "not-a-valid-base64-fernet-key"
        reset_pin_cipher()
        with self.assertRaises(RuntimeError) as ctx:
            get_pin_cipher()
        self.assertIn("invalid or malformed", str(ctx.exception))

    def test_strict_rejection_of_plaintext_runtime(self):
        # Once migrated, raw plaintext passed to decrypt_pin must return None (not decrypted)
        raw_plaintext = "1234"
        self.assertIsNone(decrypt_pin(raw_plaintext))

    def test_tampered_ciphertext_returns_none(self):
        ciphertext = encrypt_pin("1234")
        tampered = ciphertext[:-4] + "AAAA"
        self.assertIsNone(decrypt_pin(tampered))


if __name__ == "__main__":
    unittest.main()
