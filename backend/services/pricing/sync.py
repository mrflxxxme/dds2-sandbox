# ruff: noqa: RUF001, RUF002, RUF003
"""Синк текущих цен витрины ВБ (API «Цены и скидки») в таблицу wb_prices.

Один UPSERT-ряд на (project_id, nm_id). Зеркало паттерна sync_wb_nomenclature:
SyncLog + батч-UPSERT + аккуратный rollback на ошибке.
"""

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import SyncLog, WbPrice
from backend.services.integrations_service import _get_wb_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None


async def sync_wb_prices(db: AsyncSession, project_id: int) -> SyncLog:
    """Синхронизировать текущие цены витрины ВБ для проекта."""
    key, api_key = await _get_wb_key(db, project_id)
    key_id = key.id  # capture before any rollback expires the ORM object
    started_at = utcnow()

    sync_log = SyncLog(
        integration_id=key_id,
        service="wb",
        sync_type="prices",
        started_at=started_at,
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    try:
        from backend.integrations.wb_api import WBApiClient, parse_wb_prices

        client = WBApiClient(api_key, project_id=project_id)
        raw_goods = await client.get_prices(limit=1000)
        items = parse_wb_prices(raw_goods)

        # Existing rows for this project → upsert in-place, иначе insert
        existing = await db.execute(select(WbPrice).where(WbPrice.project_id == project_id))
        by_nm: dict[int, WbPrice] = {row.nm_id: row for row in existing.scalars().all()}

        now = utcnow()
        inserted = 0
        updated = 0
        for item in items:
            nm_id = item["nm_id"]
            row = by_nm.get(nm_id)
            if row:
                row.vendor_code = item.get("vendor_code") or row.vendor_code
                row.base_price = _to_decimal(item.get("base_price"))
                row.price = _to_decimal(item.get("price"))
                row.discount = _to_decimal(item.get("discount"))
                row.currency = item.get("currency") or "RUB"
                row.synced_at = now
                updated += 1
            else:
                db.add(
                    WbPrice(
                        project_id=project_id,
                        nm_id=nm_id,
                        vendor_code=item.get("vendor_code"),
                        base_price=_to_decimal(item.get("base_price")),
                        price=_to_decimal(item.get("price")),
                        discount=_to_decimal(item.get("discount")),
                        currency=item.get("currency") or "RUB",
                        synced_at=now,
                    )
                )
                inserted += 1

        sync_log.rows_fetched = len(raw_goods)
        sync_log.rows_inserted = inserted
        sync_log.status = "OK"
        sync_log.finished_at = utcnow()
        sync_log.error_msg = f"goods={len(raw_goods)}, parsed={len(items)}, inserted={inserted}, updated={updated}"
        key.last_sync_at = utcnow()

        await db.commit()
        await db.refresh(sync_log)

    except Exception as e:
        # Failed flush poisons the session — rollback before writing ERROR log.
        await db.rollback()
        logger.error("WB prices sync error: %s", e)

        err_log = SyncLog(
            integration_id=key_id,
            service="wb",
            sync_type="prices",
            started_at=started_at,
            finished_at=utcnow(),
            status="ERROR",
            error_msg=str(e)[:1000],
        )
        db.add(err_log)
        await db.commit()
        await db.refresh(err_log)
        return err_log

    return sync_log
