# ruff: noqa: RUF001
"""
Funnel grouping queries — by tag and by imt_id (склейка).

Uses the same profit calculation as brand/subject grouping.
"""

import logging
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.services.funnel.bdr_rates import BdrRatesLookup, compute_profit_bdr
from backend.services.funnel.queries import _traffic_metrics
from backend.services.tariff_service import get_avg_buyout_map, get_tariff_map

logger = logging.getLogger("dds.funnel")


def _new_group_agg(has_bdr: bool) -> dict:
    """Create a fresh aggregation dict for a group."""
    return {
        "label": "",
        "open_card": 0,
        "add_to_cart": 0,
        "orders_count": 0,
        "orders_sum_rub": 0.0,
        "revenue": 0.0,
        "adv_sum": 0.0,
        "adv_views": 0,
        "adv_clicks": 0,
        "cost_total": 0.0,
        "commission": 0.0,
        "tax": 0.0,
        "profit": 0.0,
        "has_tariff_gaps": False,
        "has_bdr": has_bdr,
        "cart_to_order_pcts": [],
        "add_to_cart_pcts": [],
        "avg_prices": [],
        "bdr_revenue": 0.0,
        "bdr_profit": 0.0,
        "bdr_tax": 0.0,
        "bdr_commission": 0.0,
        "bdr_cost_total": 0.0,
        "w_spp_sum": 0.0,
        "w_topay_sum": 0.0,
        "w_total": 0.0,
        "leg_revenue": 0.0,
        "leg_commission": 0.0,
    }


def _accumulate_row(agg: dict, r: WbFunnelDaily, nm_id: int, bdr_rates_map, tariff_map, buyout_map, tax_info):
    """Accumulate one funnel row into an aggregation dict."""
    orders_sum = float(r.orders_sum_rub or 0)
    cost_per_unit = float(r.cost_price or 0)
    orders_count = int(r.orders_count or 0)
    adv = float(r.adv_sum or 0)

    agg["open_card"] += int(r.open_card or 0)
    agg["add_to_cart"] += int(r.add_to_cart or 0)
    agg["orders_count"] += orders_count
    agg["orders_sum_rub"] += orders_sum
    agg["adv_sum"] += adv
    agg["adv_views"] += int(r.adv_views or 0)
    agg["adv_clicks"] += int(r.adv_clicks or 0)

    if r.cart_to_order_pct:
        agg["cart_to_order_pcts"].append(float(r.cart_to_order_pct))
    if r.add_to_cart_pct:
        agg["add_to_cart_pcts"].append(float(r.add_to_cart_pct))
    if r.avg_price:
        agg["avg_prices"].append(float(r.avg_price))

    bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
    if bdr:
        m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
        agg["bdr_revenue"] += m["revenue"]
        agg["bdr_profit"] += m["profit"]
        agg["bdr_tax"] += m["tax"]
        agg["bdr_commission"] += m["commission"]
        agg["bdr_cost_total"] += m["cost_total"]
        agg["w_spp_sum"] += bdr.spp_rate * orders_sum
        agg["w_topay_sum"] += bdr.to_pay_rate * orders_sum
        agg["w_total"] += orders_sum
    else:
        buyout_pct = buyout_map.get(nm_id, 100)
        revenue = orders_sum * buyout_pct / 100
        subj = r.subject or ""
        rate = tariff_map.get(subj, 0)
        if rate == 0 and revenue > 0:
            agg["has_tariff_gaps"] = True
        agg["leg_revenue"] += revenue
        agg["leg_commission"] += revenue * rate / 100
        agg["cost_total"] += cost_per_unit * orders_count * buyout_pct / 100


def _finalize_groups(grp_agg: dict, tax_rate: float, label_key: str, limit: int) -> list[dict]:
    """Convert aggregation dicts to final output rows."""
    data = []
    for _key, agg in grp_agg.items():
        orders_sum = agg["orders_sum_rub"]
        adv = agg["adv_sum"]
        views = agg["adv_views"]
        clicks = agg["adv_clicks"]
        orders_count = agg["orders_count"]

        revenue = agg["bdr_revenue"] + agg["leg_revenue"]
        commission = agg["bdr_commission"] + agg["leg_commission"]
        cost_total = agg["bdr_cost_total"] + agg["cost_total"]

        leg_tax = agg["leg_revenue"] * tax_rate / 100
        tax = agg["bdr_tax"] + leg_tax

        profit = (
            agg["bdr_profit"]
            + (
                agg["leg_revenue"]
                - adv * (agg["leg_revenue"] / revenue if revenue else 0)
                - agg["leg_commission"]
                - agg["cost_total"]
                - leg_tax
            )
            if revenue
            else 0
        )

        margin = (profit / revenue * 100) if revenue else 0
        buyout_pct = (revenue / orders_sum * 100) if orders_sum else 0
        traffic = _traffic_metrics(orders_sum, adv, views, clicks, orders_count)

        avg_cart = sum(agg["cart_to_order_pcts"]) / len(agg["cart_to_order_pcts"]) if agg["cart_to_order_pcts"] else 0
        avg_atc = sum(agg["add_to_cart_pcts"]) / len(agg["add_to_cart_pcts"]) if agg["add_to_cart_pcts"] else 0
        avg_price = sum(agg["avg_prices"]) / len(agg["avg_prices"]) if agg["avg_prices"] else 0

        data.append(
            {
                label_key: agg["label"],
                "open_card": agg["open_card"],
                "add_to_cart": agg["add_to_cart"],
                "orders_count": orders_count,
                "orders_sum_rub": round(orders_sum, 2),
                "buyout_percent": round(buyout_pct, 2),
                "revenue": round(revenue, 2),
                "adv_sum": round(adv, 2),
                "adv_views": views,
                "adv_clicks": clicks,
                "avg_price": round(avg_price, 2),
                "add_to_cart_pct": round(avg_atc, 2),
                "cart_to_order_pct": round(avg_cart, 2),
                "tax": round(tax, 2),
                "profit": round(profit, 2),
                "margin": round(margin, 2),
                "commission": round(commission, 2),
                "commission_rate": round((commission / revenue * 100) if revenue else 0, 2),
                "cost_total": round(cost_total, 2),
                "spp_rate": round(agg["w_spp_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "to_pay_rate": round(agg["w_topay_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "has_tariff_gaps": agg["has_tariff_gaps"],
                "has_bdr": agg["has_bdr"],
                **traffic,
            }
        )

    data = [d for d in data if d["orders_sum_rub"] > 0 or d["adv_sum"] > 0]
    data.sort(key=lambda x: x["orders_sum_rub"], reverse=True)
    return data[:limit]


async def _load_funnel_rows(db: AsyncSession, pid: int, date_from, date_to, brand, subject):
    """Load filtered funnel rows."""
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)
    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    q = q.order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    return result.scalars().all()


# ─── Group by Tag ────────────────────────────────────────────────────────────


async def get_funnel_by_tag(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    limit: int = 500,
) -> list[dict]:
    """Get funnel data aggregated by product tag.

    One product with N tags appears in N group rows (multi-tag pivot).
    Products without tags appear in "Без ярлыка" group.
    """
    from backend.models.refs import ProductTag, ProductTagMap

    rows = await _load_funnel_rows(db, pid, date_from, date_to, brand, subject)

    # Load tag mapping: nm_id → [(tag_id, tag_name)]
    tag_result = await db.execute(
        select(ProductTagMap.nm_id, ProductTag.id, ProductTag.name)
        .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
        .where(ProductTagMap.project_id == pid, ProductTag.is_deleted.is_(False))
    )
    nm_tags: dict[int, list[str]] = defaultdict(list)
    for nm_id, _tag_id, tag_name in tag_result:
        nm_tags[nm_id].append(tag_name)

    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)
    has_bdr = bool(bdr_rates_map)

    grp_agg: dict = defaultdict(lambda: _new_group_agg(has_bdr))

    for r in rows:
        nm_id = r.nm_id
        tag_names = nm_tags.get(nm_id, ["Без ярлыка"])

        for tag_name in tag_names:
            agg = grp_agg[tag_name]
            if not agg["label"]:
                agg["label"] = tag_name
            _accumulate_row(agg, r, nm_id, bdr_rates_map, tariff_map, buyout_map, tax_info)

    return _finalize_groups(grp_agg, tax_rate, "tag", limit)


# ─── Group by IMT (склейка) ─────────────────────────────────────────────────


async def get_funnel_by_imt(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    limit: int = 500,
) -> list[dict]:
    """Get funnel data aggregated by imt_id (WB card group / склейка).

    Uses imt_id from nomenclature table. Products without imt_id appear in "Без склейки".
    Shows alias name if configured, otherwise #imt_id.
    """
    from backend.models.cost import Nomenclature
    from backend.models.refs import ImtAlias

    rows = await _load_funnel_rows(db, pid, date_from, date_to, brand, subject)

    # Load nm_id → imt_id mapping from nomenclature
    nom_result = await db.execute(
        select(Nomenclature.article_wb, Nomenclature.imt_id).where(
            Nomenclature.project_id == pid,
            Nomenclature.imt_id.isnot(None),
        )
    )
    nm_to_imt: dict[int, int] = {}
    for article_wb, imt_id in nom_result:
        if article_wb and imt_id:
            nm_to_imt[article_wb] = imt_id

    # Load imt_id → alias mapping
    alias_result = await db.execute(select(ImtAlias.imt_id, ImtAlias.name).where(ImtAlias.project_id == pid))
    imt_aliases: dict[int, str] = {r.imt_id: r.name for r in alias_result}

    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)
    has_bdr = bool(bdr_rates_map)

    grp_agg: dict = defaultdict(lambda: _new_group_agg(has_bdr))

    for r in rows:
        nm_id = r.nm_id
        imt_id = nm_to_imt.get(nm_id)

        grp_key = imt_aliases.get(imt_id, f"#{imt_id}") if imt_id else "Без склейки"

        agg = grp_agg[grp_key]
        if not agg["label"]:
            agg["label"] = grp_key
        _accumulate_row(agg, r, nm_id, bdr_rates_map, tariff_map, buyout_map, tax_info)

    return _finalize_groups(grp_agg, tax_rate, "imt_group", limit)
