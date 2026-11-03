import logging
import os
import re
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)

_cipher_instance: Optional[Fernet | MultiFernet] = None


def get_pin_cipher() -> Fernet | MultiFernet:
    """Return the active Fernet cipher for PIN encryption at rest.

    Fails fast if PIN_ENCRYPTION_KEY is not set or cannot complete a canary self-test.
    Never silently falls back to SECRET_KEY or unencrypted plaintext.
    """
    global _cipher_instance
    if _cipher_instance is not None:
        return _cipher_instance

    key_raw = os.getenv("PIN_ENCRYPTION_KEY")
    if not key_raw or not key_raw.strip():
        raise RuntimeError(
            "FATAL: PIN_ENCRYPTION_KEY environment variable is missing. "
            "Backend startup halted to prevent plaintext PIN storage or credential corruption."
        )

    try:
        primary_key = key_raw.strip().encode("utf-8")
        primary_fernet = Fernet(primary_key)

        fallback_raw = os.getenv("PIN_ENCRYPTION_KEY_FALLBACK")
        if fallback_raw and fallback_raw.strip():
            fallback_fernet = Fernet(fallback_raw.strip().encode("utf-8"))
            cipher = MultiFernet([primary_fernet, fallback_fernet])
        else:
            cipher = primary_fernet

        # Canary self-test to verify encryption and decryption functionality
        canary = b"mr_signage_canary_test"
        token = cipher.encrypt(canary)
        if cipher.decrypt(token) != canary:
            raise RuntimeError("Canary decryption verification failed")

        _cipher_instance = cipher
        return _cipher_instance
    except Exception as exc:
        raise RuntimeError(f"FATAL: PIN_ENCRYPTION_KEY is invalid or malformed: {exc}") from exc


def reset_pin_cipher() -> None:
    """Reset cached cipher instance (used primarily in tests)."""
    global _cipher_instance
    _cipher_instance = None


def encrypt_pin(plain_pin: Optional[str]) -> Optional[str]:
    """Encrypt a 4-digit PIN string to Fernet ciphertext."""
    if not plain_pin or not str(plain_pin).strip():
        return None
    val = str(plain_pin).strip()
    cipher = get_pin_cipher()
    return cipher.encrypt(val.encode("utf-8")).decode("utf-8")


def decrypt_pin(stored_value: Optional[str]) -> Optional[str]:
    """Decrypt a stored Fernet token to a plaintext PIN string.

    Strict runtime policy: only valid Fernet tokens are decrypted.
    If decryption fails or token is malformed, returns None.
    """
    if not stored_value or not str(stored_value).strip():
        return None
    val = str(stored_value).strip()
    cipher = get_pin_cipher()
    try:
        decrypted = cipher.decrypt(val.encode("utf-8")).decode("utf-8")
        return decrypted if re.match(r"^\d{4}$", decrypted) else None
    except (InvalidToken, Exception) as exc:
        logger.warning("Failed to decrypt PIN value: %s", exc)
        return None
