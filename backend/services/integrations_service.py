"""
Service: integrations — business logic for integration keys and WB sync.
Extracted from routers/integrations.py to keep router as thin HTTP layer.
"""

import logging
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import IntegrationKey, SyncLog, WbPayout, Nomenclature
from backend.utils.crypto import encrypt as _encrypt, decrypt as _decrypt

logger = logging.getLogger("dds.integrations")


# ─── Integration Keys ────────────────────────────────────────────────────────


async def list_keys(db: AsyncSession, project_id: int) -> list[dict]:
    """List all integration keys for a project (with masked previews)."""
    result = await db.execute(
        select(IntegrationKey)
        .where(IntegrationKey.project_id == project_id)
        .order_by(IntegrationKey.created_at.desc())
    )
    keys = result.scalars().all()
    output = []
    for k in keys:
        output.append({
            "id": k.id,
            "service": k.service,
            "label": k.label,
            "is_active": k.is_active,
            "created_at": k.created_at,
            "last_sync_at": k.last_sync_at,
            "key_preview": "***" + _decrypt(k.encrypted_key)[-4:],
        })
    return output


async def add_key(
    db: AsyncSession,
    project_id: int,
    service: str,
    api_key: str,
    label: Optional[str] = None,
) -> dict:
    """
    Add a new integration API key (encrypted).
    Validates the key with the external service before saving.
    Returns dict with key info.
    Raises ValueError on validation failure.
    """
    if service not in ("wb", "ozon"):
        raise ValueError(f"Unsupported service: {service}. Use 'wb' or 'ozon'.")

    # Test connection before saving
    if service == "wb":
        from backend.integrations.wb_api import WBApiClient
        client = WBApiClient(api_key)
        valid = await client.test_connection()
        if not valid:
            raise ValueError("WB API ключ невалидный. Проверьте ключ.")

    encrypted = _encrypt(api_key)
    key = IntegrationKey(
        project_id=project_id,
        service=service,
        label=label or service.upper(),
        encrypted_key=encrypted,
        is_active=True,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    # Auto-restart scheduler jobs
    if service == "wb":
        try:
            from backend.scheduler import restart_backfill_jobs
            restart_backfill_jobs()
            logger.info("Scheduler jobs restarted for project %s after WB key added", project_id)
        except Exception as e:
            logger.warning("Could not restart scheduler jobs: %s", e)

    return {
        "id": key.id,
        "service": key.service,
        "label": key.label,
        "is_active": key.is_active,
        "created_at": key.created_at,
        "last_sync_at": key.last_sync_at,
        "key_preview": "***" + api_key[-4:],
    }


async def delete_key(db: AsyncSession, project_id: int, key_id: int) -> bool:
    """Delete an integration key. Returns True if deleted, False if not found."""
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


# ─── WB Sync ─────────────────────────────────────────────────────────────────


async def _get_wb_key(db: AsyncSession, project_id: int) -> tuple:
    """Get active WB key and decrypted API key. Raises ValueError if not found."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == "wb",
            IntegrationKey.is_active == True,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("WB API ключ не найден. Добавьте ключ через /integrations/keys")
    return key, _decrypt(key.encrypted_key)


async def sync_wb_sales(
    db: AsyncSession,
    project_id: int,
    sync_type: str = "sales",
    date_from: Optional[date] = None,
) -> SyncLog:
    """
    Sync data from Wildberries API.
    - sales: fetch sales → wb_payouts
    - orders: fetch orders (future)
    - finance: fetch finance report (future)
    """
    key, api_key = await _get_wb_key(db, project_id)

    if date_from is None:
        date_from = date.today() - timedelta(days=7)

    sync_log = SyncLog(
        integration_id=key.id,
        service="wb",
        sync_type=sync_type,
        started_at=datetime.utcnow(),
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    try:
        from backend.integrations.wb_api import WBApiClient, parse_wb_sales_to_payouts

        client = WBApiClient(api_key)

        if sync_type == "sales":
            raw_data = await client.get_sales(date_from)
            payouts = parse_wb_sales_to_payouts(raw_data)

            existing_ids: set = set()
            if payouts:
                req_ids = [p["request_id"] for p in payouts]
                result = await db.execute(
                    select(WbPayout.request_id).where(
                        WbPayout.project_id == project_id,
                        WbPayout.request_id.in_(req_ids),
                    )
                )
                existing_ids = {r[0] for r in result}

            inserted = 0
            for p in payouts:
                if p["request_id"] in existing_ids:
                    continue
                payout = WbPayout(
                    project_id=project_id,
                    request_id=p["request_id"],
                    amount_rub=Decimal(str(p["amount_rub"])),
                    currency=p["currency"],
                    created_at=p["created_at"],
                    wb_status_raw=p.get("wb_status_raw"),
                    status=p.get("status", "TRANSIT"),
                    imported_at=datetime.utcnow(),
                )
                db.add(payout)
                inserted += 1

            sync_log.rows_fetched = len(raw_data)
            sync_log.rows_inserted = inserted

        elif sync_type == "orders":
            raw_data = await client.get_orders(date_from)
            sync_log.rows_fetched = len(raw_data)
            sync_log.rows_inserted = 0

        elif sync_type == "finance":
            date_to = date.today()
            raw_data = await client.get_finance_report(date_from, date_to)
            sync_log.rows_fetched = len(raw_data)
            sync_log.rows_inserted = 0

        sync_log.status = "OK"
        sync_log.finished_at = datetime.utcnow()
        key.last_sync_at = datetime.utcnow()

        await db.commit()
        await db.refresh(sync_log)

    except Exception as e:
        sync_log.status = "ERROR"
        sync_log.error_msg = str(e)[:1000]
        sync_log.finished_at = datetime.utcnow()
        await db.commit()
        await db.refresh(sync_log)
        logger.error("WB sync error: %s", e)

    return sync_log


async def sync_wb_nomenclature(db: AsyncSession, project_id: int) -> SyncLog:
    """
    Sync nomenclature from Wildberries Content API (cards/list).
    Fetches all product cards and upserts into nomenclature table.
    """
    key, api_key = await _get_wb_key(db, project_id)

    sync_log = SyncLog(
        integration_id=key.id,
        service="wb",
        sync_type="nomenclature",
        started_at=datetime.utcnow(),
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    try:
        from backend.integrations.wb_api import WBApiClient, parse_wb_cards_to_nomenclature

        client = WBApiClient(api_key)
        raw_cards = await client.get_cards_list(limit=100)
        nom_items = parse_wb_cards_to_nomenclature(raw_cards)

        inserted = 0
        updated = 0

        for item in nom_items:
            bc = item["barcode"]
            result = await db.execute(
                select(Nomenclature).where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.barcode == bc,
                )
            )
            nom = result.scalar_one_or_none()
            if nom:
                nom.brand = item.get("brand") or nom.brand
                nom.subject = item.get("subject") or nom.subject
                nom.article_seller = item.get("article_seller") or nom.article_seller
                nom.article_wb = item.get("article_wb") or nom.article_wb
                if item.get("volume_l"):
                    nom.volume_l = Decimal(str(item["volume_l"]))
                nom.updated_at = datetime.utcnow()
                updated += 1
            else:
                nom = Nomenclature(
                    project_id=project_id,
                    barcode=bc,
                    brand=item.get("brand"),
                    subject=item.get("subject"),
                    article_seller=item.get("article_seller"),
                    article_wb=item.get("article_wb"),
                    volume_l=Decimal(str(item.get("volume_l", 0))),
                )
                db.add(nom)
                inserted += 1

        sync_log.rows_fetched = len(raw_cards)
        sync_log.rows_inserted = inserted
        sync_log.status = "OK"
        sync_log.finished_at = datetime.utcnow()
        sync_log.error_msg = f"cards={len(raw_cards)}, barcodes={len(nom_items)}, inserted={inserted}, updated={updated}"

        key.last_sync_at = datetime.utcnow()
        await db.commit()
        await db.refresh(sync_log)

    except Exception as e:
        sync_log.status = "ERROR"
        sync_log.error_msg = str(e)[:1000]
        sync_log.finished_at = datetime.utcnow()
        await db.commit()
        await db.refresh(sync_log)
        logger.error("WB nomenclature sync error: %s", e)

    return sync_log


# ─── Sync Log ─────────────────────────────────────────────────────────────────


async def get_sync_log(
    db: AsyncSession,
    project_id: int,
    service: Optional[str] = None,
    limit: int = 20,
) -> list:
    """Get sync history for a project."""
    key_ids_q = select(IntegrationKey.id).where(
        IntegrationKey.project_id == project_id
    )
    q = (
        select(SyncLog)
        .where(SyncLog.integration_id.in_(key_ids_q))
        .order_by(SyncLog.started_at.desc())
        .limit(limit)
    )
    if service:
        q = q.where(SyncLog.service == service)
    result = await db.execute(q)
    return result.scalars().all()
