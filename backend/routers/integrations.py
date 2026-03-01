"""
Router: /integrations — manage external API keys and sync data.
"""

import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import IntegrationKey, SyncLog, WbPayout
from backend.schemas import (
    MessageResponse, StatusResponse, DeleteResponse,
    IntegrationKeySchema, SyncLogSchema, WbPayoutSchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations")


# ─── Encryption helpers ──────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    """Get Fernet cipher using SECRET_KEY (first 32 bytes, base64-padded)."""
    import base64
    key = settings.SECRET_KEY.encode()[:32].ljust(32, b"=")
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError("Не удалось расшифровать API-ключ. Проверьте SECRET_KEY.")


# ─── Integration Keys CRUD ───────────────────────────────────────────────────

@router.get("/keys", response_model=list[IntegrationKeySchema])
async def list_keys(db: AsyncSession = Depends(get_db)):
    """List all integration keys (masked)."""
    result = await db.execute(
        select(IntegrationKey).order_by(IntegrationKey.created_at.desc())
    )
    keys = result.scalars().all()
    # Mask the encrypted key for security
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


@router.post("/keys", response_model=IntegrationKeySchema)
async def add_key(
    service: str,
    api_key: str,
    label: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Add a new integration API key (encrypted)."""
    if service not in ("wb", "ozon"):
        raise HTTPException(400, f"Unsupported service: {service}. Use 'wb' or 'ozon'.")

    # Test connection before saving
    if service == "wb":
        from backend.integrations.wb_api import WBApiClient
        client = WBApiClient(api_key)
        valid = await client.test_connection()
        if not valid:
            raise HTTPException(400, "WB API ключ невалидный. Проверьте ключ.")

    encrypted = _encrypt(api_key)
    key = IntegrationKey(
        service=service,
        label=label or service.upper(),
        encrypted_key=encrypted,
        is_active=True,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    return {
        "id": key.id,
        "service": key.service,
        "label": key.label,
        "is_active": key.is_active,
        "created_at": key.created_at,
        "last_sync_at": key.last_sync_at,
        "key_preview": "***" + api_key[-4:],
    }


@router.delete("/keys/{key_id}", response_model=DeleteResponse)
async def delete_key(key_id: int, db: AsyncSession = Depends(get_db)):
    """Delete an integration key."""
    result = await db.execute(select(IntegrationKey).where(IntegrationKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(404, "Ключ не найден")
    await db.delete(key)
    await db.commit()
    return {"deleted": True, "id": key_id}


# ─── WB Sync ─────────────────────────────────────────────────────────────────

@router.post("/wb/sync", response_model=SyncLogSchema)
async def sync_wb_sales(
    sync_type: str = Query("sales", enum=["sales", "orders", "finance"]),
    date_from: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Sync data from Wildberries API.
    - sales: загрузить продажи → wb_payouts
    - orders: загрузить заказы (для будущего модуля)
    - finance: загрузить финансовый отчёт (детализация выплат)
    """
    # Get active WB key
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.service == "wb",
            IntegrationKey.is_active == True,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(400, "WB API ключ не найден. Добавьте ключ через /integrations/keys")

    api_key = _decrypt(key.encrypted_key)

    # Default: last 7 days
    if date_from is None:
        date_from = date.today() - timedelta(days=7)

    # Create sync log
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

            # Upsert into wb_payouts
            existing_ids = set()
            if payouts:
                req_ids = [p["request_id"] for p in payouts]
                result = await db.execute(
                    select(WbPayout.request_id).where(WbPayout.request_id.in_(req_ids))
                )
                existing_ids = {r[0] for r in result}

            inserted = 0
            for p in payouts:
                if p["request_id"] in existing_ids:
                    continue
                payout = WbPayout(
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
            # Orders processing can be extended later

        elif sync_type == "finance":
            date_to = date.today()
            raw_data = await client.get_finance_report(date_from, date_to)
            sync_log.rows_fetched = len(raw_data)
            sync_log.rows_inserted = 0
            # Finance report processing can be extended later

        sync_log.status = "OK"
        sync_log.finished_at = datetime.utcnow()

        # Update last_sync_at on the key
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


@router.post("/wb/sync_nomenclature", response_model=SyncLogSchema)
async def sync_wb_nomenclature(
    db: AsyncSession = Depends(get_db),
):
    """
    Sync nomenclature from Wildberries Content API (cards/list).
    Fetches all product cards and upserts into nomenclature table.
    Maps: nmID→article_wb, subjectName→subject, vendorCode→article_seller,
          sizes.skus→barcode, dimensions→volume_l.
    """
    from backend.models import Nomenclature

    # Get active WB key
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.service == "wb",
            IntegrationKey.is_active == True,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(400, "WB API ключ не найден. Добавьте ключ через /integrations/keys")

    api_key = _decrypt(key.encrypted_key)

    # Create sync log
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
                select(Nomenclature).where(Nomenclature.barcode == bc)
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


@router.get("/sync_log", response_model=list[SyncLogSchema])

async def get_sync_log(
    service: Optional[str] = Query(None),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
):
    """Get sync history."""
    q = select(SyncLog).order_by(SyncLog.started_at.desc()).limit(limit)
    if service:
        q = q.where(SyncLog.service == service)
    result = await db.execute(q)
    return result.scalars().all()
