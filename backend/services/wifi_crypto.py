"""Wi-Fi credential encryption service using the system Fernet cipher."""

from typing import Optional
from ..pin_crypto import get_pin_cipher


def encrypt_wifi_password(raw_password: Optional[str]) -> Optional[str]:
    """Encrypt a Wi-Fi password for secure storage."""
    if not raw_password:
        return None
    try:
        cipher = get_pin_cipher()
        return cipher.encrypt(raw_password.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def decrypt_wifi_password(encrypted_token: Optional[str]) -> Optional[str]:
    """Decrypt an encrypted Wi-Fi password."""
    if not encrypted_token:
        return None
    try:
        cipher = get_pin_cipher()
        return cipher.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except Exception:
        return None
