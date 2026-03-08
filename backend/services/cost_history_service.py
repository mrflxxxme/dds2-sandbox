"""
Service: cost_history — cost price history per article across orders.

Queries cost_order_items joined with cost_orders to build a pivot:
rows = articles, columns = orders, cells = cost per unit (total_rub).
"""

import logging
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from backend.models import CostOrder, CostOrderItem

logger = logging.getLogger("dds.cost_history")


async def get_cost_history(
    db: AsyncSession,
    project_id: int,
    article_search: Optional[str] = None,
) -> dict:
    """
    Returns cost history pivot:
    {
      orders: [{order_no, ship_date}, ...],
      articles: [
        {article_seller, barcode, costs: {order_no: total_rub, ...}, avg_cost, latest_cost},
        ...
      ]
    }
    """
    # 1. Get all active orders, sorted by ship_date
    orders_q = (
        select(CostOrder)
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
        .order_by(CostOrder.ship_date.desc(), CostOrder.order_no.desc())
    )
    orders_result = await db.execute(orders_q)
    orders = orders_result.scalars().all()

    order_list = [
        {"order_no": o.order_no, "ship_date": str(o.ship_date) if o.ship_date else None}
        for o in orders
    ]
    order_nos = [o.order_no for o in orders]

    if not order_nos:
        return {"orders": [], "articles": []}

    # 2. Get all cost_order_items for these orders
    items_q = (
        select(CostOrderItem)
        .where(CostOrderItem.order_no.in_(order_nos))
        .order_by(CostOrderItem.article_seller, CostOrderItem.id)
    )
    items_result = await db.execute(items_q)
    items = items_result.scalars().all()

    # 3. Build pivot: article -> {order_no: cost_per_unit}
    articles_map: dict[str, dict] = {}

    for item in items:
        art = item.article_seller or item.barcode or "—"

        if art not in articles_map:
            articles_map[art] = {
                "article_seller": art,
                "barcode": item.barcode or "",
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

    # 4. Compute avg and latest cost
    result_articles = []
    for art_key, art_data in articles_map.items():
        # Filter by search
        if article_search and article_search.lower() not in art_key.lower():
            continue

        avg_cost = (
            round(art_data["total_cost_sum"] / art_data["cost_count"], 2)
            if art_data["cost_count"] > 0
            else 0.0
        )

        # Latest cost = first order in order_nos that has this article
        latest_cost = 0.0
        for ono in order_nos:
            if ono in art_data["costs"]:
                latest_cost = art_data["costs"][ono]["cost"]
                break

        result_articles.append({
            "article_seller": art_data["article_seller"],
            "barcode": art_data["barcode"],
            "subject": art_data["subject"],
            "costs": art_data["costs"],
            "avg_cost": avg_cost,
            "latest_cost": latest_cost,
        })

    # Sort by article name
    result_articles.sort(key=lambda x: x["article_seller"])

    return {
        "orders": order_list,
        "articles": result_articles,
    }
