# ruff: noqa: RUF002, RUF003
"""Sync supplier orders из WB Statistics API → таблица `wb_orders`.

Идемпотентный UPSERT по `(project_id, srid)`. Используется отчётом «Индекс
локализации» (per-district breakdown) и любыми будущими отчётами, которым
нужна гранулярная история заказов с складом-источником и регионом доставки.

Особенности:
- WB API rate-limit ~1 req/min на ключ → один запрос на проект,
  фильтрация по `date_to` происходит в Python (не позже «вчера 23:59 MSK»).
- Дедуп по `srid` ДО UPSERT (защита от CardinalityViolation если WB
  вернул дубликат — прецедент wb_stocks).
- Иммутабельные поля (nm_id, srid, order_date, total_price,
  discount_percent) НЕ обновляются на повторных синхронизациях.
- После sync — invalidate `reports:localization`, `reports:localization_skus`,
  `reports:localization_districts`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytz
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models import WbOrder
from backend.services.funnel.wb_api_client import fetch_supplier_orders, get_wb_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.wb_orders")


_MSK = pytz.timezone("Europe/Moscow")
_BATCH_SIZE = 500


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _yesterday_msk_eod() -> datetime:
    """Конец вчерашнего дня по МСК (23:59:59) — верхняя граница sync.

    Сегодняшние данные ВБ ещё неполные (отчёт строится в течение суток),
    поэтому отсекаем заказы с order_date позже yesterday 23:59 MSK.
    """
    today_msk = datetime.now(_MSK).date()
    yesterday = today_msk - timedelta(days=1)
    eod = datetime.combine(yesterday, time(23, 59, 59))
    return _MSK.localize(eod)


def _parse_dt(value: Any) -> datetime | None:
    """WB отдаёт ISO-строки вида '2026-04-30T18:23:11'. Привязываем к МСК.

    WB sentinel `'0001-01-01T00:00:00'` (не отменён / не задано) → None,
    т.к. pytz переполняется на годах < 1900.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        if value.year < 1900:
            return None
        return value if value.tzinfo else _MSK.localize(value)
    if isinstance(value, str):
        try:
            cleaned = value.split("+")[0].split("Z")[0]
            dt = datetime.fromisoformat(cleaned)
            if dt.year < 1900:
                return None
            return _MSK.localize(dt) if dt.tzinfo is None else dt
        except (ValueError, TypeError, OverflowError):
            logger.debug("wb_orders: cannot parse datetime '%s'", value)
            return None
    return None


def _to_decimal(value: Any) -> Decimal | None:
    """Безопасное приведение к Decimal (не используем float для денег)."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _to_str(value: Any, max_len: int | None = None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if max_len and len(s) > max_len:
        return s[:max_len]
    return s


def _row_from_wb(project_id: int, raw: dict, sync_ts: datetime) -> dict | None:
    """Преобразовать WB API dict → row для UPSERT.

    Возвращает None если запись невалидна (нет srid / nm_id / order_date).
    """
    srid = _to_str(raw.get("srid"), 120)
    nm_id = _to_int(raw.get("nmId"))
    order_dt = _parse_dt(raw.get("date"))
    if not srid or nm_id is None or order_dt is None:
        return None

    return {
        "project_id": project_id,
        "srid": srid,
        "g_number": _to_str(raw.get("gNumber"), 40),
        "sticker": _to_str(raw.get("sticker"), 40),
        "income_id": _to_int(raw.get("incomeID")),
        "order_date": order_dt,
        "last_change_date": _parse_dt(raw.get("lastChangeDate")),
        "nm_id": nm_id,
        "barcode": _to_str(raw.get("barcode"), 40),
        "vendor_code": _to_str(raw.get("supplierArticle"), 120),
        "subject": _to_str(raw.get("subject"), 200),
        "brand": _to_str(raw.get("brand"), 200),
        "category": _to_str(raw.get("category"), 200),
        "tech_size": _to_str(raw.get("techSize"), 40),
        "warehouse_name": _to_str(raw.get("warehouseName"), 120),
        "warehouse_type": _to_str(raw.get("warehouseType"), 40),
        "country_name": _to_str(raw.get("countryName"), 80),
        "oblast_okrug_name": _to_str(raw.get("oblastOkrugName"), 120),
        "region_name": _to_str(raw.get("regionName"), 200),
        "total_price": _to_decimal(raw.get("totalPrice")),
        "discount_percent": _to_int(raw.get("discountPercent")),
        "spp": _to_int(raw.get("spp")),
        "finished_price": _to_decimal(raw.get("finishedPrice")),
        "price_with_disc": _to_decimal(raw.get("priceWithDisc")),
        "is_cancel": bool(raw.get("isCancel", False)),
        "cancel_date": _parse_dt(raw.get("cancelDate")),
        "is_supply": bool(raw.get("isSupply", False)),
        "is_realization": bool(raw.get("isRealization", True)),
        "synced_at": sync_ts,
    }


async def _upsert_batch(db: AsyncSession, rows: list[dict]) -> tuple[int, int]:
    """UPSERT batch строк через ON CONFLICT DO UPDATE.

    Иммутабельные поля (nm_id, srid, order_date, total_price, discount_percent)
    НЕ обновляются. Возвращает (inserted, updated) — приближённо: PG не
    различает в CTE без RETURNING, поэтому cчитаем total_affected как
    `inserted + updated` и вычисляем split через предварительный SELECT.
    """
    if not rows:
        return 0, 0

    # Сначала узнаём какие srid уже существуют → split insert/update
    srids = [r["srid"] for r in rows]
    pid = rows[0]["project_id"]
    from sqlalchemy import select

    existing = await db.execute(select(WbOrder.srid).where(WbOrder.project_id == pid).where(WbOrder.srid.in_(srids)))
    existing_srids = {row[0] for row in existing.all()}

    inserted = sum(1 for r in rows if r["srid"] not in existing_srids)
    updated = len(rows) - inserted

    stmt = pg_insert(WbOrder).values(rows)
    update_set = {
        "is_cancel": stmt.excluded.is_cancel,
        "cancel_date": stmt.excluded.cancel_date,
        "last_change_date": stmt.excluded.last_change_date,
        "warehouse_name": stmt.excluded.warehouse_name,
        "warehouse_type": stmt.excluded.warehouse_type,
        "country_name": stmt.excluded.country_name,
        "oblast_okrug_name": stmt.excluded.oblast_okrug_name,
        "region_name": stmt.excluded.region_name,
        "finished_price": stmt.excluded.finished_price,
        "price_with_disc": stmt.excluded.price_with_disc,
        "synced_at": stmt.excluded.synced_at,
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "srid"],
        set_=update_set,
    )
    await db.execute(stmt)
    return inserted, updated


# ─── Public API ───────────────────────────────────────────────────────────────


async def sync_wb_orders(
    db: AsyncSession,
    project_id: int,
    days_back: int = 30,
) -> dict:
    """Sync supplier orders за последние `days_back` дней для одного проекта.

    Идемпотентно: повторный вызов обновит is_cancel / cancel_date /
    last_change_date / warehouse_*, но не тронет иммутабельные поля.

    Args:
        db: AsyncSession
        project_id: целевой проект
        days_back: глубина истории (default 30 дней). WB Statistics API
            ограничен ~90 днями назад.

    Returns:
        {
            "inserted": int,    # новые srid
            "updated": int,     # уже существовавшие
            "total_fetched": int,  # всего получено от WB (до фильтра)
            "skipped_future": int, # отброшено (order_date > yesterday EOD)
            "skipped_invalid": int, # отброшено (нет srid/nm_id/date)
            "errors": list[str],
        }
    """
    started = utcnow()
    api_key = await get_wb_key(db, project_id, "wb")
    if not api_key:
        # Backward-compatible: try wb_analytics service tag
        api_key = await get_wb_key(db, project_id, "wb_analytics")
    if not api_key:
        msg = f"wb_orders sync project={project_id}: no WB API key — skipping"
        logger.info(msg)
        return {
            "inserted": 0,
            "updated": 0,
            "total_fetched": 0,
            "skipped_future": 0,
            "skipped_invalid": 0,
            "errors": ["no WB API key"],
        }

    date_from = (date.today() - timedelta(days=days_back)).isoformat()
    raw_orders = await fetch_supplier_orders(api_key, date_from)
    total_fetched = len(raw_orders)
    if total_fetched == 0:
        logger.info("wb_orders sync project=%d: WB returned 0 orders", project_id)
        return {
            "inserted": 0,
            "updated": 0,
            "total_fetched": 0,
            "skipped_future": 0,
            "skipped_invalid": 0,
            "errors": [],
        }

    # Фильтр по date_to (вчера 23:59 MSK) — сегодняшние данные неполные
    cutoff = _yesterday_msk_eod()
    sync_ts = utcnow()

    rows: list[dict] = []
    skipped_future = 0
    skipped_invalid = 0
    for raw in raw_orders:
        row = _row_from_wb(project_id, raw, sync_ts)
        if row is None:
            skipped_invalid += 1
            continue
        # Приводим обе даты к UTC для сравнения (cutoff in MSK, order_date with tz)
        if row["order_date"] > cutoff:
            skipped_future += 1
            continue
        rows.append(row)

    # Дедуп по srid (защита от дублей в одном batch — CardinalityViolation)
    by_srid: dict[str, dict] = {}
    for row in rows:
        existing = by_srid.get(row["srid"])
        if existing is None:
            by_srid[row["srid"]] = row
        else:
            # Берём более «свежую» запись по lastChangeDate (или просто перезаписываем)
            ex_lcd = existing.get("last_change_date")
            new_lcd = row.get("last_change_date")
            if new_lcd is not None and (ex_lcd is None or new_lcd > ex_lcd):
                by_srid[row["srid"]] = row
    deduped = list(by_srid.values())

    # Batch UPSERT по 500 строк
    total_inserted = 0
    total_updated = 0
    errors: list[str] = []
    for i in range(0, len(deduped), _BATCH_SIZE):
        batch = deduped[i : i + _BATCH_SIZE]
        try:
            inserted, updated = await _upsert_batch(db, batch)
            total_inserted += inserted
            total_updated += updated
        except Exception as e:
            errors.append(f"batch {i // _BATCH_SIZE} error: {str(e)[:200]}")
            logger.error(
                "wb_orders sync project=%d batch %d failed: %s",
                project_id,
                i // _BATCH_SIZE,
                e,
                exc_info=True,
            )

    await db.commit()

    # Инвалидация связанных кэшей
    for prefix in (
        "reports:localization",
        "reports:localization_skus",
        "reports:localization_districts",
        "reports:localization_daily",
    ):
        try:
            await invalidate_cache(prefix)
        except Exception as e:
            logger.warning("wb_orders sync: invalidate %s failed: %s", prefix, e)

    duration = (utcnow() - started).total_seconds()
    logger.info(
        "wb_orders sync project=%d: fetched=%d, inserted=%d, updated=%d, "
        "skipped_future=%d, skipped_invalid=%d, dedup_dropped=%d, took=%.1fs",
        project_id,
        total_fetched,
        total_inserted,
        total_updated,
        skipped_future,
        skipped_invalid,
        len(rows) - len(deduped),
        duration,
    )

    return {
        "inserted": total_inserted,
        "updated": total_updated,
        "total_fetched": total_fetched,
        "skipped_future": skipped_future,
        "skipped_invalid": skipped_invalid,
        "errors": errors,
    }
