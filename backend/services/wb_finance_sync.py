"""
Service: wb_finance_sync — download and store WB reportDetailByPeriod locally.

Handles:
- Initial sync (last 2 months)
- Weekly auto-sync (previous week, Monday)
- Upsert rows by rrd_id
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_finance import WbFinanceRow, WbFinanceSyncLog
from backend.services.integrations_service import _get_wb_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.wb_finance_sync")

# Numeric fields to extract from WB API rows
_NUMERIC_FIELDS = [
    "retail_price_withdisc_rub",
    "retail_amount",
    "ppvz_for_pay",
    "ppvz_sales_commission",
    "delivery_rub",
    "penalty",
    "additional_payment",
    "storage_fee",
    "acceptance",
    "deduction",
    "ppvz_reward",
    "rebill_logistic_cost",
    "ppvz_vw",
    "ppvz_vw_nds",
]


async def sync_wb_finance(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    period: str | None = None,
) -> dict:
    """Download WB finance report and upsert into wb_finance_rows.

    Streams page-by-page: download → batch upsert → commit → next page.
    Data is persisted incrementally so nothing is lost on timeout.

    period: "daily" for daily reports, None for weekly (default).

    Returns {status, rows_synced, pages, errors}.
    """
    from backend.integrations.wb_api import WBApiClient

    _key, api_key = await _get_wb_key(db, project_id)
    client = WBApiClient(api_key, project_id=project_id)

    logger.info("wb_finance_sync: fetching %s — %s (period=%s) for project %s", date_from, date_to, period, project_id)

    rrdid_cursor = 0
    page_limit = 20000
    page_num = 0
    total_synced = 0
    errors: list[str] = []

    try:
        while True:
            page_num += 1
            page = await client.get_finance_report(
                date_from,
                date_to,
                limit=page_limit,
                rrdid=rrdid_cursor,
                period=period,
            )
            if not page:
                break

            logger.info(
                "wb_finance_sync: page %d — got %d rows (cursor rrdid=%d)",
                page_num,
                len(page),
                rrdid_cursor,
            )

            # ── Batch upsert THIS page immediately ──
            batch: list[dict] = []
            BATCH_SIZE = 1000

            for row in page:
                rrd_id = row.get("rrd_id")
                if not rrd_id:
                    continue
                batch.append(_row_to_values(row, project_id))

                if len(batch) >= BATCH_SIZE:
                    await _upsert_batch(db, batch)
                    total_synced += len(batch)
                    batch = []

            if batch:
                await _upsert_batch(db, batch)
                total_synced += len(batch)

            # Commit after each page — data is safe
            await db.commit()
            logger.info(
                "wb_finance_sync: page %d committed (%d rows total so far)",
                page_num,
                total_synced,
            )

            # If we got less than limit, we've fetched everything
            if len(page) < page_limit:
                break

            # Move cursor to last rrd_id
            last_rrdid = max(r.get("rrd_id", 0) for r in page)
            del page  # Free API response memory before next page
            if last_rrdid <= rrdid_cursor:
                break  # Safety: no progress
            rrdid_cursor = last_rrdid

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error("wb_finance_sync: API error on page %d: %s", page_num, error_msg)
        errors.append(error_msg)
        # Even on error, log what we synced so far
        db.add(
            WbFinanceSyncLog(
                project_id=project_id,
                date_from=date_from,
                date_to=date_to,
                rows_synced=total_synced,
                status="ERROR",
                error_msg=error_msg,
            )
        )
        await db.commit()
        return {"status": "error", "rows_synced": total_synced, "pages": page_num, "errors": errors}

    # Log sync success
    db.add(
        WbFinanceSyncLog(
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            rows_synced=total_synced,
            status="OK",
        )
    )
    await db.commit()

    # Invalidate WB-dependent report caches
    from backend.cache import invalidate_project_reports

    await invalidate_project_reports(project_id)

    # Background prewarm: re-compute reports so cache is hot
    import asyncio

    from backend.scheduler import prewarm_project

    task = asyncio.create_task(prewarm_project(project_id))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    logger.info("wb_finance_sync: done — %d rows in %d pages for project %s", total_synced, page_num, project_id)
    return {"status": "ok", "rows_synced": total_synced, "pages": page_num, "errors": []}


def _row_to_values(row: dict, project_id: int) -> dict:
    """Convert a WB API row dict to DB column values."""
    values = {
        "project_id": project_id,
        "rrd_id": row.get("rrd_id"),
        "realizationreport_id": row.get("realizationreport_id", 0) or 0,
        "date_from": _parse_date(row.get("date_from")),
        "date_to": _parse_date(row.get("date_to")),
        "rr_dt": _parse_date(row.get("rr_dt")),
        "sale_dt": _parse_date(row.get("sale_dt")),
        "order_dt": _parse_date(row.get("order_dt")),
        "sa_name": (row.get("sa_name") or "")[:200],
        "nm_id": row.get("nm_id", 0) or 0,
        "brand_name": (row.get("brand_name") or "")[:200],
        "subject_name": (row.get("subject_name") or "")[:200],
        "doc_type_name": (row.get("doc_type_name") or "")[:100],
        "supplier_oper_name": (row.get("supplier_oper_name") or "")[:200],
        "bonus_type_name": (row.get("bonus_type_name") or "")[:500] or None,
        "quantity": row.get("quantity", 0) or 0,
        "synced_at": utcnow(),
    }
    for field in _NUMERIC_FIELDS:
        val = row.get(field, 0) or 0
        try:
            values[field] = Decimal(str(val))
        except Exception:
            values[field] = Decimal("0")
    return values


async def _upsert_batch(db: AsyncSession, batch: list[dict]) -> None:
    """Bulk upsert a batch of rows using ON CONFLICT DO UPDATE."""
    stmt = pg_insert(WbFinanceRow).values(batch)
    update_cols = {k: stmt.excluded[k] for k in batch[0].keys() if k not in ("project_id", "rrd_id")}
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
    count = await db.execute(select(func.count(WbFinanceRow.id)).where(WbFinanceRow.project_id == project_id))
    total = count.scalar() or 0

    if total > 0:
        return None  # Already has data

    logger.info("wb_finance_sync: initial sync for project %s (last 2 months)", project_id)
    today = date.today()
    date_from = today - timedelta(days=60)
    return await sync_wb_finance(db, project_id, date_from, today)


def _parse_date(val: object) -> date | None:
    """Parse date from WB API (can be string or None).

    Returns None for missing/invalid values instead of date.today()
    to avoid polluting max(rr_dt) queries with fake dates.
    """
    if not val:
        return None
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None
