"""
Integrations service — API key management and WB sync logic.

Extracted from routers/integrations.py to enable reuse and testing.
"""

import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    IntegrationKey, SyncLog, WbPayout, Nomenclature, Project,
)

logger = logging.getLogger("dds.integrations")


# ─── Encryption helpers ──────────────────────────────────────────────────────


def get_fernet() -> Fernet:
    """Get Fernet cipher using SECRET_KEY (first 32 bytes, base64-padded)."""
    return Fernet(settings.SECRET_KEY[:43].encode() + b"=")


def encrypt(value: str) -> str:
    """Encrypt a string value."""
    return get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt an encrypted string value."""
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return "***INVALID***"


# ─── Integration Keys CRUD ───────────────────────────────────────────────────


async def list_keys(db: AsyncSession, project_id: int) -> list[dict]:
    """List all integration keys for the current project (masked)."""
    result = await db.execute(
        select(IntegrationKey).where(
            or_(
                IntegrationKey.project_id == project_id,
                IntegrationKey.project_id.is_(None),
            )
        ).order_by(IntegrationKey.id)
    )
    keys = result.scalars().all()

    masked = []
    for k in keys:
        try:
            raw = decrypt(k.encrypted_key)
            mask = raw[:6] + "..." + raw[-4:] if len(raw) > 10 else "***"
        except Exception:
            mask = "***ERROR***"
        masked.append({
            "id": k.id,
            "service": k.service,
            "label": k.label,
            "masked_key": mask,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "is_global": k.project_id is None,
        })
    return masked


async def add_key(
    db: AsyncSession,
    project_id: int,
    service: str,
    api_key: str,
    label: Optional[str] = None,
) -> dict:
    """Add a new integration API key (encrypted) for the current project."""
    # Check for duplicate service+project
    existing = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == service,
        )
    )
    dup = existing.scalar_one_or_none()
    if dup:
        # Update existing key
        dup.encrypted_key = encrypt(api_key)
        dup.label = label or dup.label
        await db.commit()
        return {"ok": True, "id": dup.id, "action": "updated"}

    key = IntegrationKey(
        project_id=project_id,
        service=service,
        encrypted_key=encrypt(api_key),
        label=label,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return {"ok": True, "id": key.id, "action": "created"}


async def delete_key(
    db: AsyncSession,
    project_id: int,
    key_id: int,
) -> bool:
    """Delete an integration key (only within the current project)."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.id == key_id,
            IntegrationKey.project_id == project_id,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        return False
    await db.delete(key)
    await db.commit()
    return True


async def get_sync_log(
    db: AsyncSession,
    project_id: int,
    service: Optional[str] = None,
    limit: int = 20,
) -> list:
    """Get sync history for the current project."""
    q = select(SyncLog).where(SyncLog.project_id == project_id)
    if service:
        q = q.where(SyncLog.service == service)
    q = q.order_by(SyncLog.id.desc()).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()
