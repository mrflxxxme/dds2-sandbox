"""
Service: wb_finance_sync — download and store WB reportDetailByPeriod locally.

Handles:
- Initial sync (last 2 months)
- Weekly auto-sync (previous week, Monday)
- Upsert rows by rrd_id
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_finance import WbFinanceRow, WbFinanceSyncLog
from backend.services.integrations_service import _get_wb_key

logger = logging.getLogger("dds.wb_finance_sync")

# Numeric fields to extract from WB API rows
_NUMERIC_FIELDS = [
    "retail_price_withdisc_rub", "retail_amount", "ppvz_for_pay",
    "ppvz_sales_commission", "delivery_rub", "penalty", "additional_payment",
    "storage_fee", "acceptance", "deduction", "ppvz_reward",
    "rebill_logistic_cost", "ppvz_vw", "ppvz_vw_nds",
]


async def sync_wb_finance(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
) -> dict:
    """Download WB finance report and upsert into wb_finance_rows.

    Paginates using rrdid cursor (WB returns max 100k per request).
    Batch upserts in chunks of 1000 for performance.

    Returns {status, rows_synced, errors}.
    """
    from backend.integrations.wb_api import WBApiClient

    _key, api_key = await _get_wb_key(db, project_id)
    client = WBApiClient(api_key)

    logger.info("wb_finance_sync: fetching %s — %s for project %s", date_from, date_to, project_id)

    # ── Paginated fetch using rrdid cursor ──
    all_rows: list[dict] = []
    rrdid_cursor = 0
    page_limit = 100000
    page_num = 0

    try:
        while True:
            page_num += 1
            page = await client.get_finance_report(
                date_from, date_to, limit=page_limit, rrdid=rrdid_cursor,
            )
            if not page:
                break

            all_rows.extend(page)
            logger.info(
                "wb_finance_sync: page %d — got %d rows (cursor rrdid=%d, total so far: %d)",
                page_num, len(page), rrdid_cursor, len(all_rows),
            )

            # If we got less than limit, we've fetched everything
            if len(page) < page_limit:
                break

            # Move cursor to last rrd_id
            last_rrdid = max(r.get("rrd_id", 0) for r in page)
            if last_rrdid <= rrdid_cursor:
                break  # Safety: no progress
            rrdid_cursor = last_rrdid

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error("wb_finance_sync: API error on page %d: %s", page_num, error_msg)
        db.add(WbFinanceSyncLog(
            project_id=project_id, date_from=date_from, date_to=date_to,
            rows_synced=0, status="ERROR", error_msg=error_msg,
        ))
        await db.commit()
        return {"status": "error", "rows_synced": 0, "errors": [error_msg]}

    logger.info("wb_finance_sync: total %d rows from API (%d pages)", len(all_rows), page_num)

    if not all_rows:
        db.add(WbFinanceSyncLog(
            project_id=project_id, date_from=date_from, date_to=date_to,
            rows_synced=0, status="OK",
        ))
        await db.commit()
        return {"status": "ok", "rows_synced": 0, "errors": []}

    # ── Batch upsert in chunks of 1000 ──
    rows_synced = 0
    batch: list[dict] = []
    BATCH_SIZE = 1000

    for row in all_rows:
        rrd_id = row.get("rrd_id")
        if not rrd_id:
            continue

        values = _row_to_values(row, project_id)
        batch.append(values)

        if len(batch) >= BATCH_SIZE:
            await _upsert_batch(db, batch)
            rows_synced += len(batch)
            batch = []

    # Flush remaining
    if batch:
        await _upsert_batch(db, batch)
        rows_synced += len(batch)

    # Log sync success
    db.add(WbFinanceSyncLog(
        project_id=project_id, date_from=date_from, date_to=date_to,
        rows_synced=rows_synced, status="OK",
    ))
    await db.commit()

    logger.info("wb_finance_sync: upserted %d rows for project %s", rows_synced, project_id)
    return {"status": "ok", "rows_synced": rows_synced, "errors": []}


def _row_to_values(row: dict, project_id: int) -> dict:
    """Convert a WB API row dict to DB column values."""
    values = {
        "project_id": project_id,
        "rrd_id": row.get("rrd_id"),
        "realizationreport_id": row.get("realizationreport_id", 0) or 0,
        "date_from": _parse_date(row.get("date_from")),
        "date_to": _parse_date(row.get("date_to")),
        "sa_name": (row.get("sa_name") or "")[:200],
        "nm_id": row.get("nm_id", 0) or 0,
        "brand_name": (row.get("brand_name") or "")[:200],
        "subject_name": (row.get("subject_name") or "")[:200],
        "doc_type_name": (row.get("doc_type_name") or "")[:100],
        "supplier_oper_name": (row.get("supplier_oper_name") or "")[:200],
        "quantity": row.get("quantity", 0) or 0,
        "synced_at": datetime.utcnow(),
    }
    for field in _NUMERIC_FIELDS:
        val = row.get(field, 0) or 0
        try:
            values[field] = Decimal(str(val))
        except Exception:
            values[field] = Decimal("0")
    return values


async def _upsert_batch(db: AsyncSession, batch: list[dict]):
    """Bulk upsert a batch of rows using ON CONFLICT DO UPDATE."""
    stmt = pg_insert(WbFinanceRow).values(batch)
    update_cols = {
        k: stmt.excluded[k]
        for k in batch[0].keys()
        if k not in ("project_id", "rrd_id")
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_wb_finance_rrd",
        set_=update_cols,
    )
    await db.execute(stmt)


async def get_sync_status(db: AsyncSession, project_id: int) -> dict:
    """Get sync status for frontend badge display."""
    # Last sync
    last = await db.execute(
        select(WbFinanceSyncLog)
        .where(WbFinanceSyncLog.project_id == project_id)
        .order_by(WbFinanceSyncLog.synced_at.desc())
        .limit(1)
    )
    last_row = last.scalar_one_or_none()

    # Date range coverage
    coverage = await db.execute(
        select(
            func.min(WbFinanceRow.date_from).label("min_date"),
            func.max(WbFinanceRow.date_to).label("max_date"),
            func.count(WbFinanceRow.id).label("total_rows"),
        ).where(WbFinanceRow.project_id == project_id)
    )
    cov = coverage.one()

    return {
        "last_sync": last_row.synced_at.isoformat() if last_row else None,
        "last_status": last_row.status if last_row else None,
        "last_rows": last_row.rows_synced if last_row else 0,
        "last_error": last_row.error_msg if last_row else None,
        "coverage_from": cov.min_date.isoformat() if cov.min_date else None,
        "coverage_to": cov.max_date.isoformat() if cov.max_date else None,
        "total_rows": cov.total_rows or 0,
    }


async def ensure_initial_sync(db: AsyncSession, project_id: int) -> dict | None:
    """If no data exists, trigger initial sync for last 2 months.

    Returns sync result or None if data already exists.
    """
    count = await db.execute(
        select(func.count(WbFinanceRow.id))
        .where(WbFinanceRow.project_id == project_id)
    )
    total = count.scalar() or 0

    if total > 0:
        return None  # Already has data

    logger.info("wb_finance_sync: initial sync for project %s (last 2 months)", project_id)
    today = date.today()
    date_from = today - timedelta(days=60)
    return await sync_wb_finance(db, project_id, date_from, today)


def _parse_date(val) -> date:
    """Parse date from WB API (can be string or None)."""
    if not val:
        return date.today()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return date.today()
