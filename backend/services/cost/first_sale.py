"""
Service: cost.first_sale — backfill of Nomenclature.first_sale_date from local
WbFunnelDaily aggregates.

Why local DB instead of WB /sales API:
- /sales is rate-limited (~1 req/min) and can return up to 80k records.
- WbFunnelDaily already syncs daily (3-5x/day cron) and tracks orders_count per nm_id.
- One SQL aggregation `SELECT MIN(date) GROUP BY nm_id WHERE orders_count > 0` is
  faster, deterministic, and free of external dependencies.

Trade-off: history depth depends on how long the project has been syncing the funnel.
For SKUs first sold *before* the funnel sync started, MIN(date) equals the earliest
funnel record, not the true first sale. For our purpose (new ≤40 days, active >40 days)
this is safe — any over-40-day proxy still yields the correct "active" badge.

Called from sync_wb_nomenclature (best-effort, errors not fatal).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.integrations import WbFunnelDaily

logger = logging.getLogger("dds.cost.first_sale")


async def backfill_first_sale_dates(
    db: AsyncSession,
    project_id: int,
    *,
    only_missing: bool = True,
) -> dict[str, int]:
    """Compute Nomenclature.first_sale_date from local WbFunnelDaily MIN(date).

    Args:
        db: AsyncSession (caller controls transaction lifecycle; we commit at end).
        project_id: tenant filter — applied on EVERY query (iron rule).
        only_missing: True → only fill rows where first_sale_date IS NULL (default;
                      first_sale_date is immutable for any nm_id once known).
                      False → recompute every nm_id (admin reset).

    Returns:
        {"candidates": <nm_ids in nomenclature considered>,
         "matched":    <nm_ids found in wb_funnel_daily with orders>,
         "updated":    <nomenclature rows actually updated>}

    Behavior:
        - Single SELECT to wb_funnel_daily; never per-SKU.
        - No WB API calls — purely local.
        - Multi-tenancy enforced on both SELECT and UPDATE.
    """
    # Step 1: candidate nm_ids from nomenclature.
    nm_stmt = select(Nomenclature.article_wb).where(
        Nomenclature.project_id == project_id,
        Nomenclature.article_wb.is_not(None),
    )
    if only_missing:
        nm_stmt = nm_stmt.where(Nomenclature.first_sale_date.is_(None))

    nm_result = await db.execute(nm_stmt)
    candidate_nm_ids: set[int] = {int(r) for r in nm_result.scalars().all() if r is not None}

    if not candidate_nm_ids:
        return {"candidates": 0, "matched": 0, "updated": 0}

    # Step 2: MIN(date) per nm_id from wb_funnel_daily where orders_count > 0.
    funnel_stmt = (
        select(WbFunnelDaily.nm_id, func.min(WbFunnelDaily.date).label("first_sale"))
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.nm_id.in_(candidate_nm_ids),
            WbFunnelDaily.orders_count > 0,
        )
        .group_by(WbFunnelDaily.nm_id)
    )
    funnel_result = await db.execute(funnel_stmt)
    min_by_nm = {int(nm): dt for nm, dt in funnel_result.all()}

    # Step 3: bulk update — only nm_ids with a confirmed first sale.
    updated = 0
    for nm_id, dt in min_by_nm.items():
        upd_stmt = (
            update(Nomenclature)
            .where(
                Nomenclature.project_id == project_id,
                Nomenclature.article_wb == nm_id,
            )
            .values(first_sale_date=dt)
        )
        res = await db.execute(upd_stmt)
        updated += res.rowcount or 0  # type: ignore[attr-defined]

    await db.commit()

    logger.info(
        "first_sale_date.backfill",
        extra={
            "project_id": project_id,
            "candidates": len(candidate_nm_ids),
            "matched": len(min_by_nm),
            "updated": updated,
            "only_missing": only_missing,
        },
    )

    return {"candidates": len(candidate_nm_ids), "matched": len(min_by_nm), "updated": updated}
