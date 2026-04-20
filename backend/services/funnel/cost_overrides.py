"""Funnel cost overrides — manual cost price management.

Extracted from queries.py for maintainability.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Nomenclature, WbCostOverride, WbFunnelDaily

logger = logging.getLogger("dds.funnel")

# Cost price below this ratio of average retail price is treated as missing.
# Catches garbage like 1.01 on a 2000 RUB SKU (user pasted thousands and
# dropped the multiplier) — product would otherwise silently skew reports.
MIN_COST_RATIO = 0.05


async def get_missing_costs(db: AsyncSession, pid: int) -> list[dict]:
    """Return products without cost_price from BOTH funnel and finance data.

    Merges two sources:
    1. wb_funnel_daily — products with orders but no (or suspiciously low) cost_price
    2. wb_finance_rows — products with sales in BDR but no cost anywhere

    "Suspiciously low" = cost < MIN_COST_RATIO * avg_retail_price.

    Excludes products that have meaningful cost from:
    - WbCostOverride (manual overrides by nm_id)
    - Cost order history (avg cost by vendor_code/sa_name)
    """
    from backend.models.wb_finance import WbFinanceRow
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides

    # Load existing cost sources
    cost_ovr = await load_cost_overrides(db, pid)
    avg_costs = await load_avg_costs(db, pid)

    # ── Source 1: Funnel (wb_funnel_daily) ──
    # avg_retail_price = SUM(orders_sum_rub) / SUM(orders_count)
    total_orders_sum = func.sum(WbFunnelDaily.orders_sum_rub)
    total_qty_sum = func.sum(WbFunnelDaily.orders_count)
    avg_retail_expr = total_orders_sum / func.nullif(total_qty_sum, 0)
    current_cost_expr = func.max(WbFunnelDaily.cost_price)

    q = (
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
            total_orders_sum.label("total_orders"),
            total_qty_sum.label("total_qty"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
            current_cost_expr.label("current_cost"),
            avg_retail_expr.label("avg_price"),
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.orders_sum_rub > 0,
        )
        .group_by(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
        )
        .having(
            current_cost_expr.is_(None)
            | (current_cost_expr == 0)
            | (current_cost_expr < MIN_COST_RATIO * avg_retail_expr)
        )
        .order_by(total_orders_sum.desc())
    )
    rows = (await db.execute(q)).all()

    # Load barcodes from nomenclature
    nm_ids = [r.nm_id for r in rows]
    barcode_map: dict[int, str] = {}
    if nm_ids:
        nom_q = select(Nomenclature.article_wb, Nomenclature.barcode).where(
            Nomenclature.project_id == pid,
            Nomenclature.article_wb.in_(nm_ids),
        )
        for n in (await db.execute(nom_q)).all():
            if n.article_wb and n.barcode:
                barcode_map[n.article_wb] = n.barcode

    seen_nm_ids: set[int] = set()
    result = []
    for r in rows:
        avg_price = float(r.avg_price or 0)
        min_cost = MIN_COST_RATIO * avg_price
        current_cost = float(r.current_cost or 0)

        if r.nm_id in cost_ovr and cost_ovr[r.nm_id] >= min_cost and cost_ovr[r.nm_id] > 0:
            continue
        if (
            r.vendor_code
            and r.vendor_code in avg_costs
            and avg_costs[r.vendor_code] >= min_cost
            and avg_costs[r.vendor_code] > 0
        ):
            continue
        seen_nm_ids.add(r.nm_id)
        result.append(
            {
                "nm_id": r.nm_id,
                "barcode": barcode_map.get(r.nm_id, ""),
                "vendor_code": r.vendor_code or "",
                "subject": r.subject or "",
                "brand": r.brand or "",
                "total_orders": round(float(r.total_orders or 0), 2),
                "total_qty": int(r.total_qty or 0),
                "days_count": int(r.days_count or 0),
                "current_cost": round(current_cost, 2),
                "avg_price": round(avg_price, 2),
                "is_suspicious": current_cost > 0,
            }
        )

    # ── Source 2: Finance (wb_finance_rows) — products in BDR without cost ──
    finance_q = (
        select(
            func.max(WbFinanceRow.nm_id).label("nm_id"),
            WbFinanceRow.sa_name,
            func.max(WbFinanceRow.brand_name).label("brand"),
            func.max(WbFinanceRow.subject_name).label("subject"),
            func.sum(WbFinanceRow.retail_price_withdisc_rub).label("total_orders"),
            func.count().label("total_qty"),
        )
        .where(
            WbFinanceRow.project_id == pid,
            WbFinanceRow.sa_name.isnot(None),
            func.lower(func.coalesce(WbFinanceRow.sa_name, "")) != "неопознанный товар",
        )
        .group_by(WbFinanceRow.sa_name)
    )
    finance_rows = (await db.execute(finance_q)).all()

    for r in finance_rows:
        nm_id = int(r.nm_id or 0)
        sa_name = r.sa_name or ""

        # Skip if already found in funnel source
        if nm_id and nm_id in seen_nm_ids:
            continue

        total_orders = float(r.total_orders or 0)
        total_qty = int(r.total_qty or 0)
        avg_price = total_orders / total_qty if total_qty else 0
        min_cost = MIN_COST_RATIO * avg_price

        # Skip if has meaningful cost from any source (>= 5% of retail)
        current_cost = 0.0
        if nm_id and nm_id in cost_ovr and cost_ovr[nm_id] > 0:
            current_cost = cost_ovr[nm_id]
            if current_cost >= min_cost:
                continue
        if sa_name and sa_name in avg_costs and avg_costs[sa_name] > 0:
            if not current_cost:
                current_cost = avg_costs[sa_name]
            if avg_costs[sa_name] >= min_cost:
                continue

        seen_nm_ids.add(nm_id)
        result.append(
            {
                "nm_id": nm_id,
                "barcode": barcode_map.get(nm_id, ""),
                "vendor_code": sa_name,
                "subject": r.subject or "",
                "brand": r.brand or "",
                "total_orders": round(total_orders, 2),
                "total_qty": total_qty,
                "days_count": 0,
                "current_cost": round(current_cost, 2),
                "avg_price": round(avg_price, 2),
                "is_suspicious": current_cost > 0,
            }
        )

    # Sort by total_orders descending
    result.sort(key=lambda x: x["total_orders"], reverse=True)
    return result


async def get_cost_overrides(db: AsyncSession, pid: int) -> dict:
    """Get all manual cost overrides + items truly without cost anywhere."""
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides

    overrides = await db.execute(select(WbCostOverride).where(WbCostOverride.project_id == pid))
    override_list = [{"nm_id": o.nm_id, "cost_price": float(o.cost_price)} for o in overrides.scalars()]

    avg_costs = await load_avg_costs(db, pid)
    cost_ovr = await load_cost_overrides(db, pid)

    no_cost = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.cost_price.is_(None),
        )
        .distinct()
    )

    missing = []
    for r in no_cost:
        if r.nm_id in cost_ovr and cost_ovr[r.nm_id] > 0:
            continue
        if r.vendor_code and r.vendor_code in avg_costs and avg_costs[r.vendor_code] > 0:
            continue
        missing.append(
            {
                "nm_id": r.nm_id,
                "vendor_code": r.vendor_code,
                "subject": r.subject,
                "brand": r.brand,
            }
        )

    return {"overrides": override_list, "missing": missing}


async def set_cost_override(db: AsyncSession, pid: int, nm_id: int, cost_price: float) -> dict:
    """Set or update manual cost price for an nmId."""
    stmt = pg_insert(WbCostOverride).values(
        project_id=pid,
        nm_id=nm_id,
        cost_price=cost_price,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_cost_override_nm",
        set_={"cost_price": cost_price},
    )
    await db.execute(stmt)

    await db.execute(
        WbFunnelDaily.__table__.update()
        .where(WbFunnelDaily.project_id == pid, WbFunnelDaily.nm_id == nm_id)
        .values(cost_price=cost_price)
    )

    await db.commit()
    return {"status": "ok"}


async def bulk_set_cost_overrides(db: AsyncSession, pid: int, items: list) -> dict:
    """Bulk set cost prices by barcode or nm_id.

    Resolution order for each barcode value:
    1. Direct nm_id match (if barcode is numeric and matches WbFunnelDaily.nm_id)
    2. Nomenclature barcode → article_wb (nm_id)
    """
    if not items:
        return {"saved": 0, "not_found": [], "errors": []}

    barcodes = [item.barcode.strip() for item in items]

    nom_result = await db.execute(
        select(Nomenclature.barcode, Nomenclature.article_wb).where(
            Nomenclature.project_id == pid,
            Nomenclature.barcode.in_(barcodes),
        )
    )
    barcode_to_nm: dict[str, int] = {}
    for row in nom_result:
        if row.article_wb:
            barcode_to_nm[row.barcode] = row.article_wb

    numeric_codes = [b for b in barcodes if b.isdigit() and b not in barcode_to_nm]
    if numeric_codes:
        nm_ids = [int(b) for b in numeric_codes]
        existing = await db.execute(
            select(WbFunnelDaily.nm_id)
            .where(
                WbFunnelDaily.project_id == pid,
                WbFunnelDaily.nm_id.in_(nm_ids),
            )
            .distinct()
        )
        for row in existing:
            barcode_to_nm[str(row.nm_id)] = row.nm_id

    saved = 0
    not_found = []
    errors = []

    for item in items:
        barcode = item.barcode.strip()
        nm_id = barcode_to_nm.get(barcode)

        if not nm_id:
            not_found.append(barcode)
            continue

        try:
            cost = item.cost_price

            stmt = pg_insert(WbCostOverride).values(
                project_id=pid,
                nm_id=nm_id,
                cost_price=cost,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_cost_override_nm",
                set_={"cost_price": cost},
            )
            await db.execute(stmt)

            await db.execute(
                WbFunnelDaily.__table__.update()
                .where(WbFunnelDaily.project_id == pid, WbFunnelDaily.nm_id == nm_id)
                .values(cost_price=cost)
            )

            saved += 1
        except Exception as e:
            errors.append(f"{barcode}: {e!s}")
            logger.error(f"Bulk cost override error for barcode={barcode}: {e}")

    await db.commit()

    return {
        "saved": saved,
        "not_found": not_found,
        "errors": errors,
    }
