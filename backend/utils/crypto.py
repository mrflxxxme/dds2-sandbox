"""
Encryption utilities for API keys.

Centralised Fernet encrypt/decrypt using SECRET_KEY.
Used by integrations, funnel, scheduler.
"""

import base64
import logging

from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings

logger = logging.getLogger("dds.crypto")


def get_fernet() -> Fernet:
    """Get Fernet cipher using SECRET_KEY (first 32 bytes, base64-padded)."""
    key = settings.SECRET_KEY.encode()[:32].ljust(32, b"=")
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    """Encrypt a plaintext string (e.g. API key)."""
    return get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a Fernet-encrypted string. Raises ValueError on failure."""
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError("Не удалось расшифровать значение. Проверьте SECRET_KEY.")
