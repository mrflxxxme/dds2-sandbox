"""
Service: cost_history — cost price history per article across orders.

Queries cost_order_items joined with cost_orders to build a pivot:
rows = articles, columns = orders, cells = cost per unit (total_rub).
Joins nomenclature for WB article (article_wb) and brand.
"""

import logging

from sqlalchemy import or_, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CostOrder, CostOrderItem
from backend.models.enums import VehicleStatus

logger = logging.getLogger("dds.cost_history")


async def get_cost_history(
    db: AsyncSession,
    project_id: int,
    article_search: str | None = None,
    brand_filter: str | None = None,
) -> dict:
    """
    Returns cost history pivot with brand filter, article search, WB article.
    {
      orders: [{order_no, ship_date}, ...],
      brands: ["Brand1", "Brand2", ...],
      articles: [
        {article_seller, barcode, article_wb, brand, subject,
         costs: {order_no: {cost, qty, price_cny}, ...}, avg_cost, latest_cost},
        ...
      ]
    }
    """
    # 1. Get all active orders, sorted by ship_date
    #    Exclude FORMING — data not finalised yet (prices/qty may change)
    orders_q = (
        select(CostOrder)
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
            or_(
                CostOrder.status.is_(None),
                CostOrder.status != VehicleStatus.FORMING,
            ),
        )
        .order_by(CostOrder.ship_date.desc(), CostOrder.order_no.desc())
    )
    orders_result = await db.execute(orders_q)
    orders = orders_result.scalars().all()

    order_list = [{"order_no": o.order_no, "ship_date": str(o.ship_date) if o.ship_date else None} for o in orders]
    order_nos = [o.order_no for o in orders]

    if not order_nos:
        return {"orders": [], "articles": [], "brands": []}

    # 2. Load nomenclature lookup: article_seller -> {article_wb, brand}
    nomen_q = await db.execute(
        sa_text(
            "SELECT article_seller, article_wb, brand FROM nomenclature "
            "WHERE project_id = :pid AND article_seller IS NOT NULL"
        ),
        {"pid": project_id},
    )
    nomen_map: dict[str, dict] = {}
    all_brands: set[str] = set()
    for row in nomen_q.fetchall():
        nomen_map[row[0]] = {"article_wb": str(row[1]) if row[1] else "", "brand": row[2] or ""}
        if row[2]:
            all_brands.add(row[2])

    # 3. Get all cost_order_items for these orders
    items_q = (
        select(CostOrderItem)
        .where(
            CostOrderItem.order_no.in_(order_nos),
            CostOrderItem.is_deleted == False,
        )
        .order_by(CostOrderItem.article_seller, CostOrderItem.id)
    )
    items_result = await db.execute(items_q)
    items = items_result.scalars().all()

    # 4. Build pivot: article -> {order_no: cost_per_unit}
    articles_map: dict[str, dict] = {}

    for item in items:
        art = item.article_seller or item.barcode or "—"
        nomen = nomen_map.get(art, {})

        if art not in articles_map:
            articles_map[art] = {
                "article_seller": art,
                "barcode": item.barcode or "",
                "article_wb": nomen.get("article_wb", ""),
                "brand": nomen.get("brand", ""),
                "subject": item.subject or "",
                "costs": {},
                "total_cost_sum": 0.0,
                "cost_count": 0,
            }

        cost_per_unit = float(item.total_rub) if item.total_rub else 0.0
        qty = item.qty or 1

        articles_map[art]["costs"][item.order_no] = {
            "cost": round(cost_per_unit, 2),
            "qty": qty,
            "price_cny": float(item.price_cny) if item.price_cny else 0.0,
        }

        if cost_per_unit > 0:
            articles_map[art]["total_cost_sum"] += cost_per_unit
            articles_map[art]["cost_count"] += 1

    # 5. Compute avg and latest cost, apply filters
    result_articles = []
    for art_key, art_data in articles_map.items():
        # Filter by search
        if article_search:
            s = article_search.lower()
            if (
                s not in art_key.lower()
                and s not in art_data.get("article_wb", "").lower()
                and s not in art_data.get("barcode", "").lower()
            ):
                continue

        # Filter by brand
        if brand_filter and art_data.get("brand", "").lower() != brand_filter.lower():
            continue

        avg_cost = round(art_data["total_cost_sum"] / art_data["cost_count"], 2) if art_data["cost_count"] > 0 else 0.0

        # Latest cost = first order in order_nos that has this article
        latest_cost = 0.0
        for ono in order_nos:
            if ono in art_data["costs"]:
                latest_cost = art_data["costs"][ono]["cost"]
                break

        result_articles.append(
            {
                "article_seller": art_data["article_seller"],
                "barcode": art_data["barcode"],
                "article_wb": art_data["article_wb"],
                "brand": art_data["brand"],
                "subject": art_data["subject"],
                "costs": art_data["costs"],
                "avg_cost": avg_cost,
                "latest_cost": latest_cost,
            }
        )

    # Sort by article name
    result_articles.sort(key=lambda x: x["article_seller"])

    return {
        "orders": order_list,
        "brands": sorted(all_brands),
        "articles": result_articles,
    }
