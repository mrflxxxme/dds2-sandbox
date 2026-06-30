# ruff: noqa: RUF001, RUF002, RUF003
"""Синк текущих цен витрины ВБ (API «Цены и скидки») в таблицу wb_prices.

Один UPSERT-ряд на (project_id, nm_id). Зеркало паттерна sync_wb_nomenclature:
SyncLog + батч-UPSERT + аккуратный rollback на ошибке.
"""

import json
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import SyncLog, WbPrice
from backend.services.integrations_service import _get_wb_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

# Redis-ключ с реальными ценами покупателя (с СПП) из публичного card-API.
# Это внешние/«живые» данные, не доменные → кэш, а не таблица (без миграции).
SPP_CACHE_PREFIX = "pricing_spp"
_SPP_TTL = 172800  # 2 суток


def spp_cache_key(project_id: int) -> str:
    return f"{SPP_CACHE_PREFIX}:project_id={project_id}"


def _to_decimal(value: object) -> Decimal | None:
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


async def sync_card_spp(db: AsyncSession, project_id: int) -> dict:
    """Синк реальной цены покупателя (с СПП) из публичного card-API в Redis.

    Берёт nm_id из wb_prices (что уже синканы) → card-API → {nm_id: цена_с_СПП}
    в Redis (`pricing_spp:project_id=…`). Card-API ключа не требует. Best-effort:
    флак отдельных батчей не валит синк. Возвращает статистику.
    """
    from backend.cache import get_redis
    from backend.integrations.wb_card_api import fetch_card_buyer_prices

    nm_ids = list(
        (await db.execute(select(WbPrice.nm_id).where(WbPrice.project_id == project_id))).scalars().all()
    )
    if not nm_ids:
        return {"status": "OK", "requested": 0, "fetched": 0, "synced_at": None}

    prices = await fetch_card_buyer_prices(nm_ids)
    synced_at = utcnow().isoformat()

    r = await get_redis()
    if r is not None and prices:
        payload = json.dumps(
            {"map": {str(nm): p for nm, p in prices.items()}, "synced_at": synced_at, "count": len(prices)}
        )
        await r.set(spp_cache_key(project_id), payload, ex=_SPP_TTL)

    logger.info("card-СПП sync: project %d — %d/%d nm", project_id, len(prices), len(nm_ids))
    return {"status": "OK", "requested": len(nm_ids), "fetched": len(prices), "synced_at": synced_at}


async def load_spp_map(project_id: int) -> dict[int, float]:
    """Прочитать карту {nm_id: цена_с_СПП} из Redis (пусто, если синка не было)."""
    from backend.cache import get_redis

    r = await get_redis()
    if r is None:
        return {}
    raw = await r.get(spp_cache_key(project_id))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {int(k): float(v) for k, v in (data.get("map") or {}).items()}
    except (ValueError, TypeError):
        return {}
