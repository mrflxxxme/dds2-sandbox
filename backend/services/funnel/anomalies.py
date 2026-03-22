# ruff: noqa: RUF001
"""Anomalies report — detect problematic products across 5 patterns.

Detectors:
1. hit_loss     — top-revenue product with margin < 10%
2. hit_oos      — top-orders product running out of stock
3. toxic_ads    — high ad spend with DRR > 30%
4. dead_stock   — frozen capital in slow-moving inventory
5. low_buyout   — cheap items with low buyout eating logistics
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Warehouse, WarehouseStock
from backend.models.cost import Nomenclature
from backend.services.funnel.bdr_rates import BdrRatesLookup
from backend.services.funnel.product_trends import get_product_trends

logger = logging.getLogger("dds.funnel.anomalies")

# Deduplication priority: higher number = keep
_PRIORITY = {
    "hit_loss": 50,
    "hit_oos": 40,
    "toxic_ads": 30,
    "dead_stock": 20,
    "low_buyout": 10,
}


# ─── RF stocks loader ────────────────────────────────────────────────────────


async def _load_rf_stocks(db: AsyncSession, project_id: int) -> dict[int, int]:
    """Load non-WB warehouse stocks grouped by nm_id (article_wb).

    Returns {nm_id: total_quantity} for all RF/external warehouses.
    """
    q = (
        select(
            Nomenclature.article_wb,
            func.sum(WarehouseStock.quantity).label("qty"),
        )
        .join(Nomenclature, Nomenclature.id == WarehouseStock.nomenclature_id)
        .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712
            Warehouse.warehouse_type != "wb",
            WarehouseStock.project_id == project_id,
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb)
    )
    result = await db.execute(q)
    return {row.article_wb: int(row.qty or 0) for row in result}


# ─── Detectors ────────────────────────────────────────────────────────────────


def _detect_hit_loss(products: list[dict], top20_threshold: float) -> list[dict]:
    """Top-revenue products with dangerously low margin."""
    anomalies = []
    for p in products:
        revenue = p["orders_sum_rub"]
        margin = p["margin"]
        if revenue < top20_threshold or margin >= 10:
            continue

        if margin < 0:
            title = "Хит в убытке"
            desc = "Маржа рухнула в ноль. Рост стоимости логистики и рекламы."
            action = "Поднять цену или снизить расходы на рекламу"
        else:
            title = "Хит с низкой маржой"
            desc = "Маржа упала ниже 10%. Пересмотрите цену или расходы."
            action = "Проверить себестоимость, логистику и рекламу"

        loss = abs(revenue * (10 - margin) / 100)
        anomalies.append(
            _make_item(
                p,
                "critical",
                "hit_loss",
                title,
                desc,
                loss,
                action,
                margin=margin,
                orders_sum=revenue,
            )
        )
    return anomalies


def _detect_hit_oos(
    products: list[dict],
    top30_threshold: int,
    period_days: int,
) -> list[dict]:
    """Top-orders products about to go out of stock."""
    anomalies = []
    for p in products:
        orders = p["orders_count"]
        turnover = p["turnover_days"]
        if orders < top30_threshold or turnover <= 0 or turnover >= 10:
            continue

        daily_sales = orders / period_days if period_days else 0
        restock_qty = max(0, int(daily_sales * 14 - p["stocks_wb"]))
        loss = daily_sales * p["avg_price"] * max(0, 7 - turnover)
        desc = f"Закончится через {turnover:.0f} дн. Отгрузите {restock_qty} шт."
        anomalies.append(
            _make_item(
                p,
                "critical",
                "hit_oos",
                "Хит без запаса",
                desc,
                loss,
                "Срочно отгрузить товар на склад WB",
                turnover_days=turnover,
                stocks_wb=p["stocks_wb"],
                days_left=turnover,
                restock_qty=restock_qty,
            )
        )
    return anomalies


def _detect_toxic_ads(products: list[dict], period_days: int) -> list[dict]:
    """Products with excessively high ad spend relative to revenue."""
    anomalies = []
    for p in products:
        adv = p["adv_sum"]
        drr = p["drr"]
        revenue = p["orders_sum_rub"]
        if adv <= 5000 or drr <= 30:
            continue

        appetite = adv / period_days * 7 if period_days else 0
        loss = max(0, adv - revenue * 0.10)
        desc = "Аномально высокий ДРР, бюджет уходит без отдачи."
        anomalies.append(
            _make_item(
                p,
                "critical",
                "toxic_ads",
                "Токсичная реклама",
                desc,
                loss,
                "Снизить ставки или отключить неэффективные кампании",
                drr=drr,
                adv_sum=adv,
                appetite_weekly=round(appetite, 2),
                orders_sum=revenue,
            )
        )
    return anomalies


def _detect_dead_stock(
    products: list[dict],
    rf_stocks_map: dict[int, int],
    include_rf: bool,
) -> list[dict]:
    """Products with significant capital frozen in slow-moving stock."""
    anomalies = []
    for p in products:
        cost = p["cost_price"]
        if cost <= 0:
            continue

        wb_value = p["stocks_wb"] * cost
        rf_qty = rf_stocks_map.get(p["nm_id"], 0) if include_rf else 0
        rf_value = rf_qty * cost
        frozen = wb_value + rf_value
        turnover = p["turnover_days"]

        if frozen <= 30000 or turnover <= 30:
            continue

        if turnover > 60:
            severity = "critical"
            title = "Мёртвый сток"
            desc = "Омертвление капитала (WB+РФ). Движений нет."
            action = "Запустить распродажу или вывезти со склада"
        else:
            severity = "warning"
            title = "Вползаем в неликвид"
            desc = "Товар замедляется. Стимулируйте спрос."
            action = "Увеличить рекламу или снизить цену"

        total_stock = p["stocks_wb"] + rf_qty
        anomalies.append(
            _make_item(
                p,
                severity,
                "dead_stock",
                title,
                desc,
                frozen,
                action,
                turnover_days=turnover,
                stocks_wb=total_stock,
                frozen_value=round(frozen, 2),
            )
        )
    return anomalies


def _detect_low_buyout(products: list[dict]) -> list[dict]:
    """Cheap products with low buyout percentage eating into margins."""
    anomalies = []
    for p in products:
        price = p["avg_price"]
        buyout = p.get("buyout_pct", 100)
        if price <= 0:
            continue

        logistics_ratio = 110 / price * 100 if price > 0 else 0
        is_low_buyout = price < 1500 and buyout < 70
        is_high_logistics = logistics_ratio > 15

        if not (is_low_buyout or is_high_logistics):
            continue

        desc = "Аномально низкий выкуп съедает профит дешёвого товара."
        anomalies.append(
            _make_item(
                p,
                "warning",
                "low_buyout",
                "Покатушки",
                desc,
                None,
                "Улучшить описание/фото для повышения выкупа",
                buyout_pct=buyout,
                avg_price=price,
            )
        )
    return anomalies


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_item(
    product: dict,
    severity: str,
    anomaly_type: str,
    title: str,
    description: str,
    loss_amount: float | None,
    action: str,
    **extra_metrics,
) -> dict:
    """Build an anomaly item dict from product data and extra metrics."""
    metrics = {
        "margin": product.get("margin"),
        "drr": product.get("drr"),
        "adv_sum": product.get("adv_sum"),
        "turnover_days": product.get("turnover_days"),
        "stocks_wb": product.get("stocks_wb"),
        "avg_price": product.get("avg_price"),
        "orders_sum": product.get("orders_sum_rub"),
    }
    metrics.update(extra_metrics)

    return {
        "nm_id": product["nm_id"],
        "vendor_code": product["vendor_code"],
        "brand": product["brand"],
        "subject": product["subject"],
        "severity": severity,
        "anomaly_type": anomaly_type,
        "title": title,
        "description": description,
        "loss_amount": round(loss_amount, 2) if loss_amount is not None else None,
        "metrics": metrics,
        "action": action,
    }


def _deduplicate(anomalies: list[dict]) -> list[dict]:
    """Keep only the highest-priority anomaly per nm_id."""
    best: dict[int, dict] = {}
    for a in anomalies:
        nm = a["nm_id"]
        prio = _PRIORITY.get(a["anomaly_type"], 0)
        existing = best.get(nm)
        if existing is None or prio > _PRIORITY.get(existing["anomaly_type"], 0):
            best[nm] = a
    return list(best.values())


# ─── Main entry point ─────────────────────────────────────────────────────────


async def get_anomalies(
    db: AsyncSession,
    project_id: int,
    tax_info: dict,
    period_days: int = 7,
    brand: str | None = None,
    include_rf_stocks: bool = False,
    min_orders: int = 5,
    bdr_rates_map: BdrRatesLookup | None = None,
) -> dict:
    """Run all anomaly detectors and return structured report.

    Reuses get_product_trends() for per-product aggregated data,
    then applies 5 pattern detectors.
    """
    # 1. Get per-product data
    trends = await get_product_trends(
        db,
        project_id,
        tax_info,
        period_days,
        brand,
        bdr_rates_map=bdr_rates_map,
    )
    all_products = trends["products"]

    # 2. Filter by min_orders
    products = [p for p in all_products if p["orders_count"] >= min_orders]

    # 3. Enrich with buyout_pct from bdr_rates if available
    if bdr_rates_map:
        for p in products:
            bdr = bdr_rates_map.get(p["nm_id"])
            p["buyout_pct"] = round(bdr.buyout_pct * 100, 1) if bdr else 100
    else:
        for p in products:
            p["buyout_pct"] = 100

    # 4. Load RF stocks if requested
    rf_stocks_map: dict[int, int] = {}
    if include_rf_stocks:
        try:
            rf_stocks_map = await _load_rf_stocks(db, project_id)
        except Exception:
            logger.warning("Failed to load RF stocks, continuing without", exc_info=True)

    # 5. Calculate thresholds
    revenues = sorted((p["orders_sum_rub"] for p in products), reverse=True)
    top20_idx = max(1, len(revenues) * 20 // 100)
    top20_threshold = revenues[top20_idx - 1] if revenues else 0

    order_counts = sorted((p["orders_count"] for p in products), reverse=True)
    top30_idx = max(1, len(order_counts) * 30 // 100)
    top30_threshold = order_counts[top30_idx - 1] if order_counts else 0

    # 6. Run detectors
    all_anomalies: list[dict] = []
    all_anomalies.extend(_detect_hit_loss(products, top20_threshold))
    all_anomalies.extend(_detect_hit_oos(products, top30_threshold, period_days))
    all_anomalies.extend(_detect_toxic_ads(products, period_days))
    all_anomalies.extend(_detect_dead_stock(products, rf_stocks_map, include_rf_stocks))
    all_anomalies.extend(_detect_low_buyout(products))

    # 7. Deduplicate
    anomalies = _deduplicate(all_anomalies)

    # 8. Sort by loss_amount DESC (None → end)
    anomalies.sort(key=lambda a: a["loss_amount"] or 0, reverse=True)

    # 9. Calculate summary
    anomaly_nm_ids = {a["nm_id"] for a in anomalies}
    total_loss = sum(a["loss_amount"] or 0 for a in anomalies)
    oos_count = sum(1 for a in anomalies if a["anomaly_type"] == "hit_oos")
    frozen = sum(a["loss_amount"] or 0 for a in anomalies if a["anomaly_type"] == "dead_stock")
    healthy = len(products) - len(anomaly_nm_ids)

    return {
        "summary": {
            "total_loss": round(total_loss, 2),
            "oos_risk_count": oos_count,
            "frozen_capital": round(frozen, 2),
            "healthy_count": max(0, healthy),
        },
        "anomalies": anomalies,
        "total_products": len(products),
        "period_days": period_days,
    }
