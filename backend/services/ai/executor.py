"""
Tool executor — bridges Claude tool calls to DDS service functions.

Each tool call is executed with project_id + brand filtering (multi-tenant safety).
"""

import json
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("dds.ai")


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal and date types."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


def _json(data) -> str:
    """Serialize to JSON string, truncating if too large."""
    result = json.dumps(data, cls=DecimalEncoder, ensure_ascii=False)
    if len(result) > 60000:
        result = result[:60000] + "... (truncated)"
    return result


async def execute_tool(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    tax_rate: float,
    tool_name: str,
    tool_input: dict,
) -> str:
    """Execute a tool call and return the result as a JSON string."""
    # Normalize brand: "все бренды" or empty → None (no filter)
    if brand and brand.lower().strip() in ("все бренды", "all brands", "all", ""):
        brand = None
    try:
        if tool_name == "get_funnel_data":
            return await _get_funnel_data(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_top_products":
            return await _get_top_products(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_cost_data":
            return await _get_cost_data(db, project_id, tool_input)
        elif tool_name == "get_dds_report":
            return await _get_dds_report(db, project_id, tool_input)
        elif tool_name == "get_opiu_report":
            return await _get_opiu_report(db, project_id, brand, tool_input)
        elif tool_name == "compare_periods":
            return await _compare_periods(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_product_info":
            return await _get_product_info(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_stock_info":
            return await _get_stock_info(db, project_id, brand, tool_input)
        elif tool_name == "get_order_geography":
            return await _get_order_geography(db, project_id, brand, tool_input)
        elif tool_name == "get_warehouse_need":
            return await _get_warehouse_need(db, project_id, tool_input)
        elif tool_name == "get_day_analysis":
            return await _get_day_analysis(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_warehouse_stocks":
            return await _get_warehouse_stocks(db, project_id, tool_input)
        elif tool_name == "get_bdr_data":
            return await _get_bdr_data(db, project_id, brand, tool_input)
        elif tool_name == "get_anomalies":
            return await _get_anomalies(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_capital_analysis":
            return await _get_capital_analysis(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_plan_fact":
            return await _get_plan_fact(db, project_id, brand, tool_input)
        elif tool_name == "get_ad_campaigns":
            return await _get_ad_campaigns(db, project_id, brand, tool_input)
        elif tool_name == "get_logistics_history":
            return await _get_logistics_history(db, project_id, tool_input)
        elif tool_name == "get_daily_health":
            return await _get_daily_health(db, project_id, tax_rate, brand, tool_input)
        else:
            return json.dumps({"error": "Unknown tool: " + tool_name})
    except Exception as e:
        logger.exception("Tool execution error: %s", tool_name)
        return json.dumps({"error": str(e)})


# ─── Tool implementations ────────────────────────────────────────────────────


async def _get_funnel_data(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.queries import get_funnel_aggregated

    tax_info = {"tax_regime": "usn_income", "usn_rate": tax_rate, "nds_rate": 0, "cost_as_expense": False}
    data = await get_funnel_aggregated(
        db,
        project_id,
        tax_info,
        date_from=inp.get("date_from"),
        date_to=inp.get("date_to"),
        brand=brand,
        subject=None,
    )
    # Summarize for LLM: aggregate totals + daily breakdown
    if not data:
        return json.dumps({"message": "No funnel data for this period", "data": []})

    total = {
        "days": len(data),
        "orders_count": sum(d.get("orders_count", 0) for d in data),
        "orders_sum_rub": sum(d.get("orders_sum_rub", 0) for d in data),
        "revenue": sum(d.get("revenue", 0) for d in data),
        "profit": sum(d.get("profit", 0) for d in data),
        "adv_sum": sum(d.get("adv_sum", 0) for d in data),
    }
    total_revenue = total["revenue"]
    total["margin"] = round(total["profit"] / total_revenue * 100, 1) if total_revenue else 0
    total["drr"] = round(total["adv_sum"] / total["orders_sum_rub"] * 100, 1) if total["orders_sum_rub"] else 0
    return _json({"summary": total, "daily": data[:14]})


async def _get_top_products(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.product_trends import get_product_trends

    tax_info = {"tax_regime": "usn_income", "usn_rate": tax_rate, "nds_rate": 0, "cost_as_expense": False}
    result = await get_product_trends(
        db,
        project_id,
        tax_info,
        trend_days=inp.get("trend_days", 7),
        brand=brand,
        search=inp.get("search"),
    )
    products = result.get("products", [])
    top = sorted(products, key=lambda p: p.get("revenue", 0), reverse=True)
    return _json(
        {
            "total_products": result.get("total_products", len(products)),
            "trend_days": result.get("trend_days", 7),
            "top_by_revenue": top,
        }
    )


async def _get_cost_data(db, project_id, inp):
    from backend.services.funnel.queries import get_cost_overrides, get_missing_costs

    result = await get_cost_overrides(db, project_id)
    # get_cost_overrides returns {"overrides": [...], "missing": [...]}
    overrides_list = result.get("overrides", []) if isinstance(result, dict) else []
    missing = await get_missing_costs(db, project_id)
    return _json(
        {
            "cost_overrides_count": len(overrides_list),
            "cost_overrides": overrides_list,
            "missing_costs_count": len(missing),
            "missing_costs": missing,
        }
    )


async def _get_dds_report(db, project_id, inp):
    from backend.services.reports.dds import get_dds_month

    data = await get_dds_month(db, project_id, inp["year"], inp["month"])
    if not data:
        return json.dumps({"message": "No DDS data for this month", "rows": []})

    total_income = sum(float(r.get("income", 0) or 0) for r in data)
    total_expense = sum(float(r.get("expense", 0) or 0) for r in data)
    return _json(
        {
            "year": inp["year"],
            "month": inp["month"],
            "total_income": round(total_income, 2),
            "total_expense": round(total_expense, 2),
            "net": round(total_income + total_expense, 2),
            "rows": data,
        }
    )


async def _get_opiu_report(db, project_id, brand, inp):
    from backend.services.opiu_service import get_opiu

    date_from = date.fromisoformat(inp["date_from"])
    date_to = date.fromisoformat(inp["date_to"])
    result = await get_opiu(db, project_id, date_from, date_to, brand=brand)
    if not result:
        return json.dumps({"message": "No OPIU data for this period"})

    # Extract key rows for LLM (skip empty ones)
    rows = result.get("rows", [])
    key_rows = [r for r in rows if r.get("total") and abs(float(r["total"])) > 0]
    return _json(
        {
            "period": result.get("period"),
            "rows": key_rows,
        }
    )


async def _compare_periods(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.queries import get_summary

    s1 = await get_summary(db, project_id, inp["period1_from"], inp["period1_to"], brand, None)
    s2 = await get_summary(db, project_id, inp["period2_from"], inp["period2_to"], brand, None)

    def _delta(a, b):
        if not a or not b:
            return None
        return round((b - a) / a * 100, 1) if a else None

    comparison = {}
    for key in ["orders_count", "orders_sum_rub", "adv_sum", "open_card", "add_to_cart"]:
        v1 = float(s1.get(key, 0) or 0)
        v2 = float(s2.get(key, 0) or 0)
        comparison[key] = {"period1": v1, "period2": v2, "delta_pct": _delta(v1, v2)}

    return _json(
        {
            "period1": {"from": inp["period1_from"], "to": inp["period1_to"]},
            "period2": {"from": inp["period2_from"], "to": inp["period2_to"]},
            "comparison": comparison,
        }
    )


async def _get_product_info(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.product_trends import get_product_trends

    tax_info = {"tax_regime": "usn_income", "usn_rate": tax_rate, "nds_rate": 0, "cost_as_expense": False}
    result = await get_product_trends(
        db,
        project_id,
        tax_info,
        trend_days=7,
        brand=brand,
        search=inp.get("search"),
    )
    products = result.get("products", [])
    if not products:
        return json.dumps({"message": "Product not found: " + str(inp.get("search"))})
    return _json({"products": products})


async def _get_stock_info(db, project_id, brand, inp):
    from backend.services.stock_forecast_service import get_stock_analytics

    mode = "wb_rf_transit" if inp.get("include_rf_stocks", True) else "wb"
    result = await get_stock_analytics(
        db,
        project_id,
        brand_filter=brand,
        article_filter=inp.get("search"),
        mode=mode,
    )
    articles = result.get("articles", [])
    traffic = result.get("traffic_light_counts", {})

    # Top 20 by urgency (red first, then orange)
    priority = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
    sorted_articles = sorted(
        articles, key=lambda a: (priority.get(a.get("traffic_light", "green"), 3), -a.get("orders_30d", 0))
    )

    return _json(
        {
            "data_date": result.get("data_date"),
            "total_articles": len(articles),
            "traffic_light_counts": traffic,
            "articles": sorted_articles,
        }
    )


async def _get_order_geography(db, project_id, brand, inp):
    from backend.services.order_geography_service import get_order_geography

    result = await get_order_geography(
        db,
        project_id,
        date_from=inp["date_from"],
        date_to=inp["date_to"],
        brand=brand,
    )
    cities = result.get("cities", [])
    top_cities = sorted(cities, key=lambda c: c.get("order_count", 0), reverse=True)
    return _json(
        {
            "total_orders": result.get("total_orders", 0),
            "unique_cities": result.get("unique_cities", 0),
            "top_cities": top_cities,
        }
    )


async def _get_warehouse_need(db, project_id, inp):
    from backend.services.warehouse_stock_service import get_warehouse_need

    result = await get_warehouse_need(
        db,
        project_id,
        supply_days=inp.get("supply_days", 14),
        analysis_days=14,
        mode="actual",
    )
    warehouses = result.get("warehouses", [])
    # Summarize: warehouse name, total need, top articles
    summary = []
    for wh in warehouses:
        articles = wh.get("articles", [])
        need_articles = [a for a in articles if a.get("need", 0) > 0]
        if need_articles:
            summary.append(
                {
                    "warehouse": wh.get("name", ""),
                    "total_need_units": sum(a.get("need", 0) for a in need_articles),
                    "articles_count": len(need_articles),
                    "top_articles": sorted(need_articles, key=lambda a: a.get("need", 0), reverse=True)[:5],
                }
            )
    return _json({"supply_days": inp.get("supply_days", 14), "warehouses": summary})


async def _get_day_analysis(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.analysis import get_day_analysis

    target_date = inp.get("date")
    if not target_date:
        from backend.utils.time import utcnow

        target_date = utcnow().strftime("%Y-%m-%d")

    tax_info = {"tax_regime": "usn_income", "usn_rate": tax_rate, "nds_rate": 0, "cost_as_expense": False}
    result = await get_day_analysis(
        db,
        project_id,
        tax_info,
        target_date=target_date,
        brand=brand,
    )
    if not result:
        return json.dumps({"message": "No data for this date"})

    # Return summary without full product list (too large)
    return _json(
        {
            "date": target_date,
            "today": result.get("today"),
            "yesterday": result.get("yesterday"),
            "comparison": result.get("comparison"),
            "anomalies": result.get("anomalies", []),
            "top_products_count": len(result.get("products", [])),
        }
    )


async def _get_warehouse_stocks(db, project_id, inp):
    from backend.services.warehouse_stock_service import get_warehouse_stocks

    result = await get_warehouse_stocks(db, project_id)
    warehouses = result.get("warehouses", [])
    # Sort by total quantity descending
    sorted_wh = sorted(warehouses, key=lambda w: w.get("total_qty", 0), reverse=True)
    return _json(
        {
            "total_warehouses": len(sorted_wh),
            "total_qty": sum(w.get("total_qty", 0) for w in sorted_wh),
            "warehouses": sorted_wh,
        }
    )


async def _get_bdr_data(db, project_id, brand, inp):
    from backend.services.wb_bdr_service import get_wb_bdr

    date_from = date.fromisoformat(inp["date_from"])
    date_to = date.fromisoformat(inp["date_to"])
    article_filter = inp.get("article")

    result = await get_wb_bdr(
        db,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        article=article_filter,
    )
    if not result:
        return json.dumps({"message": "No BDR data for this period"})

    summary = result.get("summary", {})
    articles = result.get("articles", [])

    top_articles = sorted(articles, key=lambda a: float(a.get("realization", 0)), reverse=True)
    # Simplify article data for LLM
    simplified = []
    for a in top_articles:
        simplified.append(
            {
                "article": a.get("sa_name", ""),
                "brand": a.get("brand", ""),
                "realization": a.get("realization", 0),
                "commission": a.get("commission", 0),
                "logistics": a.get("logistics", 0),
                "storage": a.get("storage", 0),
                "penalties": a.get("penalties", 0),
                "adv_sum": a.get("adv_sum", 0),
                "cost_total": a.get("cost_total", 0),
                "to_pay": a.get("to_pay", 0),
                "net_profit": a.get("net_profit", 0),
                "margin_pct": a.get("margin_pct", 0),
                "sale_qty": a.get("sale_qty", 0),
                "ret_qty": a.get("ret_qty", 0),
                "buyout_pct": a.get("buyout_pct", 0),
            }
        )

    return _json(
        {
            "period": result.get("period"),
            "summary": {
                "realization": summary.get("realization", 0),
                "commission": summary.get("commission", 0),
                "logistics": summary.get("logistics", 0),
                "storage": summary.get("storage", 0),
                "penalties": summary.get("penalties", 0),
                "adv_sum": summary.get("adv_sum", 0),
                "cost_total": summary.get("cost_total", 0),
                "to_pay": summary.get("to_pay", 0),
                "net_profit": summary.get("net_profit", 0),
                "margin_pct": summary.get("margin_pct", 0),
                "tax_sum": summary.get("tax_sum", 0),
            },
            "total_articles": len(articles),
            "top_articles": simplified,
        }
    )


async def _get_anomalies(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.anomalies import get_anomalies

    tax_info = {"tax_regime": "usn_income", "usn_rate": tax_rate, "nds_rate": 0, "cost_as_expense": False}
    result = await get_anomalies(
        db,
        project_id,
        tax_info,
        period_days=inp.get("period_days", 7),
        brand=brand,
        include_rf_stocks=True,
        min_orders=inp.get("min_orders", 5),
    )
    anomalies = result.get("anomalies", [])
    summary = result.get("summary", {})
    # Return top 15 anomalies by severity + loss
    priority = {"critical": 0, "warning": 1}
    sorted_anomalies = sorted(
        anomalies,
        key=lambda a: (priority.get(a.get("severity", "warning"), 1), -(a.get("loss_amount") or 0)),
    )
    return _json({
        "summary": summary,
        "anomalies": sorted_anomalies,
        "total_products": result.get("total_products", 0),
        "period_days": result.get("period_days", 7),
    })


async def _get_capital_analysis(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.capital import get_capital_analysis

    tax_info = {"tax_regime": "usn_income", "usn_rate": tax_rate, "nds_rate": 0, "cost_as_expense": False}
    result = await get_capital_analysis(
        db,
        project_id,
        tax_info,
        period_days=inp.get("period_days", 7),
        brand=brand,
        group_by=inp.get("group_by", "brand"),
        include_rf_stocks=True,
        elasticity=inp.get("elasticity", 1.8),
    )
    summary = result.get("summary", {})
    groups = result.get("groups", [])
    top_groups = sorted(groups, key=lambda g: g.get("capital", 0), reverse=True)
    simplified_groups = []
    for g in top_groups:
        simplified_groups.append({
            "name": g.get("group_key", ""),
            "capital": g.get("capital", 0),
            "liquid_pct": g.get("liquid_pct", 0),
            "illiquid_pct": g.get("illiquid_pct", 0),
            "frozen_amount": g.get("frozen_amount", 0),
            "roi_monthly": g.get("roi_monthly", 0),
            "turnover_days": g.get("turnover_days", 0),
            "recommendation": g.get("recommendation", {}),
        })
    return _json({
        "summary": summary,
        "groups": simplified_groups,
        "total_products": result.get("total_products", 0),
    })


async def _get_plan_fact(db, project_id, brand, inp):
    from backend.services.planning.brand_plan import get_plan_fact_brands

    from backend.utils.time import utcnow
    now = utcnow()
    year = inp.get("year", now.year)
    month = inp.get("month", now.month)

    brands_data = await get_plan_fact_brands(db, project_id, year, month)
    # Filter by brand if specified
    if brand:
        brands_data = [b for b in brands_data if b.get("brand") == brand]

    return _json({
        "year": year,
        "month": month,
        "brands": brands_data,
    })


async def _get_ad_campaigns(db, project_id, brand, inp):
    from backend.services.funnel.ad_campaigns_service import get_ad_tab_data

    date_from = inp.get("date_from", "")
    date_to = inp.get("date_to", "")
    if not date_from or not date_to:
        from backend.utils.time import utcnow
        now = utcnow()
        date_to = now.strftime("%Y-%m-%d")
        from datetime import timedelta
        date_from = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    data = await get_ad_tab_data(
        db,
        project_id,
        date_from=date_from,
        date_to=date_to,
        brand=brand or "",
    )
    # Enrich with summary
    total_spend = sum(float(p.get("adv_sum", 0) or 0) for p in data)
    total_orders = sum(float(p.get("orders_sum_rub", 0) or 0) for p in data)
    avg_drr = round(total_spend / total_orders * 100, 1) if total_orders else 0

    # Per-product: include campaigns info
    products = []
    for p in data:
        item = {
            "nm_id": p.get("nm_id"),
            "vendor_code": p.get("vendor_code", ""),
            "subject": p.get("subject", ""),
            "adv_sum": p.get("adv_sum", 0),
            "adv_views": p.get("adv_views", 0),
            "adv_clicks": p.get("adv_clicks", 0),
            "orders_sum_rub": p.get("orders_sum_rub", 0),
            "orders_count": p.get("orders_count", 0),
            "drr": p.get("drr", 0),
            "cpc": p.get("cpc", 0),
            "ctr": p.get("ctr", 0),
            "profit": p.get("profit", 0),
            "margin": p.get("margin", 0),
            "abc_revenue": p.get("abc_revenue", ""),
            "campaigns": p.get("campaigns", []),
        }
        products.append(item)

    # Sort by spend descending
    products.sort(key=lambda x: float(x.get("adv_sum", 0) or 0), reverse=True)

    return _json({
        "period": {"date_from": date_from, "date_to": date_to},
        "summary": {
            "total_spend": total_spend,
            "total_orders_rub": total_orders,
            "avg_drr": avg_drr,
            "products_with_ads": len([p for p in products if float(p.get("adv_sum", 0) or 0) > 0]),
        },
        "products": products,
    })


async def _get_daily_health(db, project_id, tax_rate, brand, inp):
    from backend.services.health_check_service import get_daily_health

    tax_info = {"tax_regime": "usn_income", "usn_rate": tax_rate, "nds_rate": 0, "cost_as_expense": False}
    result = await get_daily_health(
        db,
        project_id,
        tax_info,
        brand=brand,
    )
    return _json(result)


async def _get_logistics_history(db, project_id, inp):
    """Get shipping history: assembly requests with costs, pallets, dates, warehouses."""
    from backend.models.assembly import AssemblyRequest, AssemblyStatus
    from backend.models.warehouse import Warehouse

    statuses = inp.get("statuses", None)
    if statuses:
        valid = []
        for s in statuses:
            try:
                valid.append(AssemblyStatus(s.upper() if isinstance(s, str) else s))
            except (ValueError, AttributeError):
                pass
        status_filter = valid if valid else None
    else:
        status_filter = None
    if not status_filter:
        status_filter = [
            AssemblyStatus.SHIPPED,
            AssemblyStatus.DELIVERED,
            AssemblyStatus.VEHICLE_ASSIGNED,
            AssemblyStatus.READY,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.PENDING,
        ]

    from sqlalchemy import select, func
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(AssemblyRequest)
        .options(selectinload(AssemblyRequest.items), selectinload(AssemblyRequest.warehouse))
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(status_filter),
        )
        .order_by(AssemblyRequest.created_at.desc())
        .limit(100)
    )
    requests = result.scalars().all()

    # Also get avg pickup_cost per destination
    cost_stats_result = await db.execute(
        select(
            Warehouse.name.label("warehouse_name"),
            func.count(AssemblyRequest.id).label("shipments"),
            func.avg(AssemblyRequest.pickup_cost).label("avg_cost"),
            func.sum(AssemblyRequest.pallets_count).label("total_pallets"),
        )
        .join(Warehouse, Warehouse.id == AssemblyRequest.warehouse_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            Warehouse.is_deleted.is_(False),
            AssemblyRequest.pickup_cost.isnot(None),
            AssemblyRequest.pickup_cost > 0,
        )
        .group_by(Warehouse.name)
    )
    cost_stats = [
        {
            "warehouse": row.warehouse_name,
            "shipments": row.shipments,
            "avg_pickup_cost": round(float(row.avg_cost or 0), 0),
            "total_pallets": int(row.total_pallets or 0),
        }
        for row in cost_stats_result
    ]

    shipments = []
    for req in requests:
        total_qty = sum(item.quantity for item in req.items) if req.items else 0
        wh_name = req.warehouse.name if req.warehouse else ""
        # Get destination from linked FBO supply
        destination = ""
        if hasattr(req, 'wb_fbo_supply') and req.wb_fbo_supply:
            destination = req.wb_fbo_supply.warehouse_name or ""

        shipments.append({
            "id": req.id,
            "number": req.number,
            "status": req.status.value if hasattr(req.status, 'value') else str(req.status),
            "warehouse": wh_name,
            "destination": destination,
            "pallets_count": req.pallets_count or 0,
            "pallet_weight_kg": float(req.pallet_weight_kg or 0),
            "pickup_cost": float(req.pickup_cost or 0),
            "delivery_date": req.delivery_date.isoformat() if req.delivery_date else None,
            "shipped_at": req.shipped_at.isoformat() if hasattr(req, 'shipped_at') and req.shipped_at else None,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "items_count": len(req.items) if req.items else 0,
            "total_qty": total_qty,
        })

    return _json({
        "total_shipments": len(shipments),
        "shipments": shipments,
        "cost_stats_by_warehouse": cost_stats,
    })
