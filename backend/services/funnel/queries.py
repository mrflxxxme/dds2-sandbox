"""
Funnel queries — data aggregation and filtering.

Handles:
- Aggregated funnel data by day
- Detailed per-product data
- Summary totals
- Filter dropdowns
- Cost overrides
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily, WbCostOverride, Nomenclature
from backend.models.wb_finance import WbFinanceRow

logger = logging.getLogger("dds.funnel")


def _compute_metrics(orders_sum: float, buyout: float, adv: float,
                     tax_rate: float, views: int, clicks: int,
                     orders_count: int, cost_total: float = 0,
                     logistics: float = 0, commission: float = 0,
                     storage: float = 0) -> dict:
    """Compute derived funnel metrics from raw aggregates.

    Profit formula (matches BDR logic):
        revenue  = orders_sum × buyout%
        profit   = revenue − adv − commission − logistics − storage − cost − tax
    """
    revenue = orders_sum * buyout / 100 if buyout else orders_sum
    tax = revenue * tax_rate / 100
    profit = revenue - adv - commission - logistics - storage - cost_total - tax
    margin = (profit / revenue * 100) if revenue else 0
    ctr = (clicks / views * 100) if views else 0
    cpc = (adv / clicks) if clicks else 0
    cpm = (adv / views * 1000) if views else 0
    cr = (orders_count / clicks * 100) if clicks else 0
    drr = (adv / orders_sum * 100) if orders_sum else 0
    return {
        "revenue": round(revenue, 2),
        "tax": round(tax, 2),
        "profit": round(profit, 2),
        "margin": round(margin, 2),
        "ctr": round(ctr, 2),
        "cpc": round(cpc, 2),
        "cpm": round(cpm, 2),
        "cr": round(cr, 2),
        "drr": round(drr, 2),
        "logistics": round(logistics, 2),
        "commission": round(commission, 2),
        "storage": round(storage, 2),
        "cost_total": round(cost_total, 2),
    }


async def _get_finance_by_date(db: AsyncSession, pid: int,
                                date_from: Optional[str],
                                date_to: Optional[str]) -> dict:
    """Aggregate WB Finance data by rr_dt (date) for the given period.

    Returns dict[date_iso_str] -> {logistics, commission, storage, penalty}.
    Commission = SUM(retail_amount - ppvz_for_pay) for Продажа/Возврат only.
    Logistics/storage/penalty from all operations.
    """
    # Commission: only from Продажа and Возврат operations
    # (other operations like Возмещение издержек have retail=0, ppvz=0 but shouldn't be counted)
    q = select(
        WbFinanceRow.rr_dt,
        # Logistics (all operations, negated to get positive)
        func.sum(func.abs(WbFinanceRow.delivery_rub)).label("logistics"),
        # Storage (all operations)
        func.sum(func.abs(WbFinanceRow.storage_fee)).label("storage"),
        # Penalty (all operations)
        func.sum(func.abs(WbFinanceRow.penalty)).label("penalty"),
        # Commission = retail_amount - ppvz_for_pay (Продажа + Возврат only)
        func.sum(
            func.case(
                (WbFinanceRow.supplier_oper_name.in_(["Продажа", "Возврат"]),
                 WbFinanceRow.retail_amount - WbFinanceRow.ppvz_for_pay),
                else_=0,
            )
        ).label("commission"),
    ).where(
        WbFinanceRow.project_id == pid,
        WbFinanceRow.rr_dt.is_not(None),
    )
    if date_from:
        q = q.where(WbFinanceRow.rr_dt >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFinanceRow.rr_dt <= date.fromisoformat(date_to))
    q = q.group_by(WbFinanceRow.rr_dt)
    result = await db.execute(q)
    rows = result.all()

    fin_map: dict = {}
    for r in rows:
        commission_val = float(r.commission or 0)
        # Commission can be negative if ppvz > retail (SPP/promos) — use 0 if negative
        if commission_val < 0:
            commission_val = 0
        fin_map[r.rr_dt.isoformat()] = {
            "logistics": float(r.logistics or 0),
            "storage": float(r.storage or 0),
            "commission": commission_val,
            "penalty": float(r.penalty or 0),
        }
    return fin_map


async def get_funnel_aggregated(db: AsyncSession, pid: int, tax_rate: float,
                                date_from: Optional[str], date_to: Optional[str],
                                brand: Optional[str], subject: Optional[str]) -> list[dict]:
    """Get funnel data aggregated by day (no article filter)."""
    q = select(
        WbFunnelDaily.date,
        func.sum(WbFunnelDaily.open_card).label("open_card"),
        func.sum(WbFunnelDaily.add_to_cart).label("add_to_cart"),
        func.sum(WbFunnelDaily.orders_count).label("orders_count"),
        func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_rub"),
        # Revenue = SUM(orders_sum_rub × buyout% / 100) per product
        # Each product's own buyout_percent (from WB card) is applied individually
        func.sum(
            WbFunnelDaily.orders_sum_rub
            * func.coalesce(WbFunnelDaily.buyout_percent, 100) / 100
        ).label("revenue_weighted"),
        func.avg(WbFunnelDaily.cart_to_order_pct).label("cart_to_order_pct"),
        func.avg(WbFunnelDaily.add_to_cart_pct).label("add_to_cart_pct"),
        func.avg(WbFunnelDaily.avg_price).label("avg_price"),
        func.sum(WbFunnelDaily.adv_views).label("adv_views"),
        func.sum(WbFunnelDaily.adv_clicks).label("adv_clicks"),
        func.sum(WbFunnelDaily.adv_sum).label("adv_sum"),
        # Cost: sum(cost_price * orders_count) per day
        func.sum(
            func.coalesce(WbFunnelDaily.cost_price, 0) * WbFunnelDaily.orders_count
        ).label("cost_total"),
    ).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)

    q = q.group_by(WbFunnelDaily.date).order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    rows = result.all()

    # Fetch WB Finance data (logistics, commission, storage) by date
    fin_map = await _get_finance_by_date(db, pid, date_from, date_to)

    data = []
    for r in rows:
        orders_sum = float(r.orders_sum_rub or 0)
        revenue = float(r.revenue_weighted or 0)
        adv = float(r.adv_sum or 0)
        views = int(r.adv_views or 0)
        clicks = int(r.adv_clicks or 0)
        orders_count = int(r.orders_count or 0)
        cost_total = float(r.cost_total or 0)

        # Weighted average buyout %
        buyout_pct = (revenue / orders_sum * 100) if orders_sum else 0

        # Get finance data for this date
        fin = fin_map.get(r.date.isoformat(), {})
        logistics = fin.get("logistics", 0)
        commission = fin.get("commission", 0)
        storage = fin.get("storage", 0)

        # Compute profit using pre-calculated revenue (not orders_sum × avg buyout)
        tax = revenue * tax_rate / 100
        profit = revenue - adv - commission - logistics - storage - cost_total - tax
        margin = (profit / revenue * 100) if revenue else 0
        ctr = (clicks / views * 100) if views else 0
        cpc = (adv / clicks) if clicks else 0
        cpm = (adv / views * 1000) if views else 0
        cr = (orders_count / clicks * 100) if clicks else 0
        drr = (adv / orders_sum * 100) if orders_sum else 0

        data.append({
            "date": r.date.isoformat(),
            "open_card": int(r.open_card or 0),
            "add_to_cart": int(r.add_to_cart or 0),
            "orders_count": orders_count,
            "orders_sum_rub": orders_sum,
            "buyout_percent": round(buyout_pct, 2),
            "revenue": round(revenue, 2),
            "adv_sum": adv,
            "adv_views": views,
            "adv_clicks": clicks,
            "avg_price": round(float(r.avg_price or 0), 2),
            "add_to_cart_pct": round(float(r.add_to_cart_pct or 0), 2),
            "cart_to_order_pct": round(float(r.cart_to_order_pct or 0), 2),
            "tax": round(tax, 2),
            "profit": round(profit, 2),
            "margin": round(margin, 2),
            "ctr": round(ctr, 2),
            "cpc": round(cpc, 2),
            "cpm": round(cpm, 2),
            "cr": round(cr, 2),
            "drr": round(drr, 2),
            "logistics": round(logistics, 2),
            "commission": round(commission, 2),
            "storage": round(storage, 2),
            "cost_total": round(cost_total, 2),
        })

    return data


async def get_funnel_detailed(db: AsyncSession, pid: int, tax_rate: float,
                              date_from: Optional[str], date_to: Optional[str],
                              brand: Optional[str], vendor_code: Optional[str],
                              subject: Optional[str]) -> list[dict]:
    """Get detailed funnel data per product."""
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if vendor_code:
        _vc = vendor_code.replace("%", r"\%").replace("_", r"\_")
        vc_filter = WbFunnelDaily.vendor_code.ilike(f"%{_vc}%")
        if vendor_code.isdigit():
            vc_filter = or_(vc_filter, WbFunnelDaily.nm_id == int(vendor_code))
        q = q.where(vc_filter)
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)

    q = q.order_by(WbFunnelDaily.date.desc(), WbFunnelDaily.orders_sum_rub.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    data = []
    for r in rows:
        orders_sum = float(r.orders_sum_rub or 0)
        buyout = float(r.buyout_percent or 0)
        adv = float(r.adv_sum or 0)
        cost_per_unit = float(r.cost_price or 0)
        cost_total = cost_per_unit * (r.orders_count or 0)
        views = r.adv_views or 0
        clicks = r.adv_clicks or 0
        m = _compute_metrics(orders_sum, buyout, adv, tax_rate, views, clicks,
                             r.orders_count or 0, cost_total)

        data.append({
            "id": r.id,
            "date": r.date.isoformat(),
            "nm_id": r.nm_id,
            "vendor_code": r.vendor_code,
            "subject": r.subject,
            "brand": r.brand,
            "open_card": r.open_card,
            "add_to_cart": r.add_to_cart,
            "orders_count": r.orders_count,
            "orders_sum_rub": orders_sum,
            "buyout_percent": buyout,
            "cost_price": cost_per_unit,
            "cost_total": round(cost_total, 2),
            "adv_sum": adv,
            "adv_views": views,
            "adv_clicks": clicks,
            "add_to_cart_pct": float(r.add_to_cart_pct or 0),
            "cart_to_order_pct": float(r.cart_to_order_pct or 0),
            "avg_price": float(r.avg_price or 0),
            "stocks_wb": r.stocks_wb,
            "stocks_mp": r.stocks_mp,
            **m,
        })

    return data


async def get_summary(db: AsyncSession, pid: int,
                      date_from: Optional[str], date_to: Optional[str],
                      brand: Optional[str], subject: Optional[str]) -> dict:
    """Get summary totals for the period header."""
    q = select(
        func.sum(WbFunnelDaily.open_card).label("open_card"),
        func.sum(WbFunnelDaily.add_to_cart).label("add_to_cart"),
        func.sum(WbFunnelDaily.orders_count).label("orders_count"),
        func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_rub"),
        func.sum(WbFunnelDaily.adv_sum).label("adv_sum"),
        func.sum(WbFunnelDaily.adv_views).label("adv_views"),
        func.sum(WbFunnelDaily.adv_clicks).label("adv_clicks"),
    ).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)

    result = await db.execute(q)
    row = result.one()

    return {
        "open_card": int(row.open_card or 0),
        "add_to_cart": int(row.add_to_cart or 0),
        "orders_count": int(row.orders_count or 0),
        "orders_sum_rub": float(row.orders_sum_rub or 0),
        "adv_sum": float(row.adv_sum or 0),
        "adv_views": int(row.adv_views or 0),
        "adv_clicks": int(row.adv_clicks or 0),
        "drr": round(
            float(row.adv_sum or 0) / float(row.orders_sum_rub or 1) * 100, 2
        ) if float(row.orders_sum_rub or 0) > 0 else 0,
    }


async def get_filters(db: AsyncSession, pid: int) -> dict:
    """Get unique brands, subjects, date range for filter dropdowns."""
    brands = await db.execute(
        select(WbFunnelDaily.brand).where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.brand.isnot(None),
        ).distinct()
    )
    subjects = await db.execute(
        select(WbFunnelDaily.subject).where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.subject.isnot(None),
        ).distinct()
    )
    dates = await db.execute(
        select(
            func.min(WbFunnelDaily.date).label("min_date"),
            func.max(WbFunnelDaily.date).label("max_date"),
        ).where(WbFunnelDaily.project_id == pid)
    )
    d = dates.one()

    # Cap max_date at yesterday — today's data is incomplete until day ends
    from datetime import date as date_type
    max_dt = d.max_date
    yesterday = date_type.today() - timedelta(days=1)
    if max_dt and max_dt >= date_type.today():
        max_dt = yesterday

    return {
        "brands": sorted([r[0] for r in brands if r[0]]),
        "subjects": sorted([r[0] for r in subjects if r[0]]),
        "min_date": d.min_date.isoformat() if d.min_date else None,
        "max_date": max_dt.isoformat() if max_dt else None,
    }


# ─── Cost overrides ─────────────────────────────────────────────────────────

async def get_cost_overrides(db: AsyncSession, pid: int) -> dict:
    """Get all manual cost overrides + items truly without cost anywhere."""
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides

    overrides = await db.execute(
        select(WbCostOverride).where(WbCostOverride.project_id == pid)
    )
    override_list = [
        {"nm_id": o.nm_id, "cost_price": float(o.cost_price)}
        for o in overrides.scalars()
    ]

    # Load existing cost sources
    avg_costs = await load_avg_costs(db, pid)       # article_seller → avg cost
    cost_ovr = await load_cost_overrides(db, pid)    # nm_id → cost

    # Items in funnel with no cost_price set
    no_cost = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
        ).where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.cost_price.is_(None),
        ).distinct()
    )

    # Filter: only truly missing = no override AND no avg cost from orders
    missing = []
    for r in no_cost:
        # Already has manual override?
        if r.nm_id in cost_ovr and cost_ovr[r.nm_id] > 0:
            continue
        # Has avg cost from cost_order_items?
        if r.vendor_code and r.vendor_code in avg_costs and avg_costs[r.vendor_code] > 0:
            continue
        missing.append({
            "nm_id": r.nm_id, "vendor_code": r.vendor_code,
            "subject": r.subject, "brand": r.brand,
        })

    return {"overrides": override_list, "missing": missing}


async def set_cost_override(db: AsyncSession, pid: int, nm_id: int,
                            cost_price: float) -> dict:
    """Set or update manual cost price for an nmId."""
    stmt = pg_insert(WbCostOverride).values(
        project_id=pid, nm_id=nm_id, cost_price=cost_price,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_cost_override_nm",
        set_={"cost_price": cost_price},
    )
    await db.execute(stmt)

    # Also update existing funnel rows
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

    Returns summary: saved count, not_found barcodes.
    """
    if not items:
        return {"saved": 0, "not_found": [], "errors": []}

    # Collect all barcodes from request
    barcodes = [item.barcode.strip() for item in items]

    # 1. Load Nomenclature: barcode → article_wb (nm_id)
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

    # 2. Check if numeric barcodes are direct nm_ids (existing in WbFunnelDaily)
    numeric_codes = [b for b in barcodes if b.isdigit() and b not in barcode_to_nm]
    if numeric_codes:
        nm_ids = [int(b) for b in numeric_codes]
        existing = await db.execute(
            select(WbFunnelDaily.nm_id).where(
                WbFunnelDaily.project_id == pid,
                WbFunnelDaily.nm_id.in_(nm_ids),
            ).distinct()
        )
        for row in existing:
            barcode_to_nm[str(row.nm_id)] = row.nm_id

    # 3. Process each item
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
            # Все WB-транзакции в RUB, конвертация валют не требуется

            stmt = pg_insert(WbCostOverride).values(
                project_id=pid, nm_id=nm_id, cost_price=cost,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_cost_override_nm",
                set_={"cost_price": cost},
            )
            await db.execute(stmt)

            # Update existing funnel rows
            await db.execute(
                WbFunnelDaily.__table__.update()
                .where(WbFunnelDaily.project_id == pid, WbFunnelDaily.nm_id == nm_id)
                .values(cost_price=cost)
            )

            saved += 1
        except Exception as e:
            errors.append(f"{barcode}: {str(e)}")
            logger.error(f"Bulk cost override error for barcode={barcode}: {e}")

    await db.commit()

    return {
        "saved": saved,
        "not_found": not_found,
        "errors": errors,
    }
