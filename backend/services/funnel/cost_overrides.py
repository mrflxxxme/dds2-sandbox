"""Funnel cost overrides — manual cost price management.

Extracted from queries.py for maintainability.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Nomenclature, WbCostOverride, WbFunnelDaily

logger = logging.getLogger("dds.funnel")


async def get_missing_costs(db: AsyncSession, pid: int) -> list[dict]:
    """Return products without cost_price from BOTH funnel and finance data.

    Merges two sources:
    1. wb_funnel_daily — products with orders but no cost_price
    2. wb_finance_rows — products with sales in BDR but no cost anywhere

    Excludes products that have cost from:
    - WbCostOverride (manual overrides by nm_id)
    - Cost order history (avg cost by vendor_code/sa_name)
    """
    from backend.models.wb_finance import WbFinanceRow
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides

    # Load existing cost sources
    cost_ovr = await load_cost_overrides(db, pid)
    avg_costs = await load_avg_costs(db, pid)

    # ── Source 1: Funnel (wb_funnel_daily) ──
    q = (
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
            func.sum(WbFunnelDaily.orders_sum_rub).label("total_orders"),
            func.sum(WbFunnelDaily.orders_count).label("total_qty"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.orders_sum_rub > 0,
        )
        .where((WbFunnelDaily.cost_price.is_(None)) | (WbFunnelDaily.cost_price == 0))
        .group_by(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
        )
        .order_by(func.sum(WbFunnelDaily.orders_sum_rub).desc())
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
        if r.nm_id in cost_ovr and cost_ovr[r.nm_id] > 0:
            continue
        if r.vendor_code and r.vendor_code in avg_costs and avg_costs[r.vendor_code] > 0:
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
        # Skip if has cost from any source
        if nm_id and nm_id in cost_ovr and cost_ovr[nm_id] > 0:
            continue
        if sa_name and sa_name in avg_costs and avg_costs[sa_name] > 0:
            continue

        seen_nm_ids.add(nm_id)
        result.append(
            {
                "nm_id": nm_id,
                "barcode": barcode_map.get(nm_id, ""),
                "vendor_code": sa_name,
                "subject": r.subject or "",
                "brand": r.brand or "",
                "total_orders": round(float(r.total_orders or 0), 2),
                "total_qty": int(r.total_qty or 0),
                "days_count": 0,
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
