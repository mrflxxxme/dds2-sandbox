"""
One-time migration script: re-encrypt all API keys with new SHA-256 key derivation.

Run inside Docker:
    docker compose exec backend python -m scripts.migrate_crypto_keys

What it does:
1. Reads all encrypted values (wb_api_keys / integration_keys)
2. Decrypts with legacy key (old padding method)
3. Re-encrypts with new SHA-256 key
4. Updates the row in DB

Safe to run multiple times (idempotent — skips already-migrated keys).
"""

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def migrate():
    from backend.database import SyncSessionLocal
    from backend.models import IntegrationKey
    from backend.utils.crypto import get_fernet, _get_fernet_legacy

    fernet_new = get_fernet()
    fernet_old = _get_fernet_legacy()

    with SyncSessionLocal() as db:
        keys = db.query(IntegrationKey).all()
        if not keys:
            logger.info("No integration keys found. Nothing to migrate.")
            return

        migrated = 0
        skipped = 0

        for key in keys:
            if not key.encrypted_key:
                skipped += 1
                continue

            # Try decrypting with NEW key first (already migrated)
            try:
                fernet_new.decrypt(key.encrypted_key.encode())
                skipped += 1  # Already using new encryption
                continue
            except Exception:
                pass

            # Decrypt with OLD key, re-encrypt with NEW
            try:
                plaintext = fernet_old.decrypt(key.encrypted_key.encode()).decode()
                key.encrypted_key = fernet_new.encrypt(plaintext.encode()).decode()
                migrated += 1
                logger.info(f"  ✅ Migrated key id={key.id} type={key.key_type}")
            except Exception as e:
                logger.error(f"  ❌ Failed to migrate key id={key.id}: {e}")

        db.commit()
        logger.info(f"\nDone: {migrated} migrated, {skipped} skipped (already OK)")


if __name__ == "__main__":
    migrate()
