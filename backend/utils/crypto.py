"""
Encryption utilities for API keys.

Centralised Fernet encrypt/decrypt using SECRET_KEY.
Used by integrations, funnel, scheduler.

Key derivation uses SHA-256 for a deterministic 32-byte key
from SECRET_KEY of any length.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings

logger = logging.getLogger("dds.crypto")


def get_fernet() -> Fernet:
    """Get Fernet cipher using SHA-256 digest of SECRET_KEY (32 bytes)."""
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _get_fernet_legacy() -> Fernet:
    """Legacy key derivation (pre-fix). Used only for migration."""
    key = settings.SECRET_KEY.encode()[:32].ljust(32, b"=")
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    """Encrypt a plaintext string (e.g. API key)."""
    return get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a Fernet-encrypted string.

    Tries new SHA-256 key first, falls back to legacy for migration.
    Raises ValueError if neither works.
    """
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        pass

    # Fallback: try legacy key derivation (migration period)
    try:
        plaintext = _get_fernet_legacy().decrypt(value.encode()).decode()
        logger.warning(
            "Decrypted with legacy key. Value will be re-encrypted on next save."
        )
        return plaintext
    except InvalidToken:
        raise ValueError("Не удалось расшифровать значение. Проверьте SECRET_KEY.")
