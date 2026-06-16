# ruff: noqa: RUF002, RUF003
"""
Cost — auto-recalculation of себестоимость (landed cost) for affected orders.

When a user changes an input that feeds the per-item cost calc — per-barcode
weight/area, a category duty rule, or an article-level duty exception — the
stored cost on `cost_order_items` (cost_rub/duty_rub/vat_rub/util_rub/total_rub)
goes stale. These helpers find the orders that reference the changed input and
re-run the authoritative `recalculate_order_items` on each, then invalidate the
cost-derived report caches. Synchronous and idempotent.
"""

import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_project_reports
from backend.models import CostOrder, CostOrderItem, Project
from backend.services.cost.items import recalculate_order_items

logger = logging.getLogger("dds")


async def _affected_orders(db: AsyncSession, project_id: int, key_col: Any, values: Iterable[str]) -> list[str]:
    """DISTINCT order_no of active items (in active orders) matching key_col IN values."""
    vals = [v for v in dict.fromkeys(values) if v]  # dedup, drop None/empty
    if not vals:
        return []
    result = await db.execute(
        select(CostOrderItem.order_no)
        .join(
            CostOrder,
            (CostOrder.order_no == CostOrderItem.order_no)
            & (CostOrder.project_id == CostOrderItem.project_id)
            & (CostOrder.is_deleted == False),  # noqa: E712
        )
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,  # noqa: E712
            key_col.in_(vals),
        )
        .distinct()
    )
    return [r for (r,) in result.all()]


async def find_orders_by_barcodes(db: AsyncSession, project_id: int, barcodes: Iterable[str]) -> list[str]:
    """Orders whose active items reference any of these barcodes (weight/area triggers)."""
    return await _affected_orders(db, project_id, CostOrderItem.barcode, barcodes)


async def find_orders_by_subjects(db: AsyncSession, project_id: int, subjects: Iterable[str]) -> list[str]:
    """Orders whose active items belong to any of these categories (duty-rule trigger)."""
    return await _affected_orders(db, project_id, CostOrderItem.subject, subjects)


async def find_orders_by_articles(db: AsyncSession, project_id: int, articles: Iterable[str]) -> list[str]:
    """Orders whose active items have any of these seller articles (duty-exception trigger)."""
    return await _affected_orders(db, project_id, CostOrderItem.article_seller, articles)


async def recalc_orders(db: AsyncSession, project_id: int, order_nos: Iterable[str]) -> int:
    """Recalculate stored cost for the given orders using the project's current VAT,
    then invalidate cost-derived report caches. Returns the number of items updated.

    Always invalidates (even with no affected orders) so duty/VAT settings reads
    stay fresh. Idempotent: re-running yields the same stored values.
    """
    order_nos = [o for o in dict.fromkeys(order_nos) if o]
    total = 0
    if order_nos:
        vat = (await db.execute(select(Project.vat_rate).where(Project.id == project_id))).scalar_one_or_none()
        vat_rate = Decimal(str(vat)) if vat else None
        for order_no in order_nos:
            # Best-effort per order: the trigger mutation already committed, so a single
            # bad order must not 500 the save or block the rest. Recalc is idempotent —
            # the order self-heals on the next trigger / manual recalc.
            try:
                updated, _err = await recalculate_order_items(db, project_id, order_no, vat_rate)
                total += updated or 0
            except Exception:
                await db.rollback()
                logger.warning("auto-recalc failed for order %s (project %s)", order_no, project_id, exc_info=True)
    await invalidate_project_reports(project_id)
    return total
