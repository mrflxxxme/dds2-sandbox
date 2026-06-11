# ruff: noqa: RUF001, RUF002, RUF003
"""
Расширенные столбцы воронки — себестоимость остатков и прогноз исчерпания.

- WB: Σ WbWarehouseStock.quantity_full × цена единицы
- Свои склады: Σ WarehouseStock.quantity (резерв включён — товар физически на складе),
  брак (defect_quantity) исключён
- Цена единицы (канон get_unified_stock_summary):
  CostOrderItem weighted avg → WbCostOverride → WarehouseStock.cost_price
- Прогноз: ср. заказы/день за последние 7 дней (якорь на вчера, неполный день
  не размывает тренд) + тренд против предыдущих 7 дней
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Nomenclature, WarehouseStock, WbFunnelDaily, WbWarehouseStock
from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides
from backend.services.stock_forecast_service import compute_days_left, compute_trend_pct
from backend.utils.time import utcnow

logger = logging.getLogger("dds.funnel")

_MAX_ROWS = 50000

# Поля, добавляемые в строки воронки при extended=true
_ROW_FIELDS = (
    "wb_stock_qty",
    "wb_stock_cost",
    "own_stock_qty",
    "own_stock_cost",
    "stock_days_left",
    "stock_out_date",
    "stock_trend_pct",
)


async def get_stock_cost_map(db: AsyncSession, pid: int) -> dict[int, dict]:
    """Карта nm_id → остатки/себестоимость/прогноз для расширенного режима воронки."""
    today = utcnow().date()
    cur_start = today - timedelta(days=7)  # [today-7 .. today-1] — последние 7 полных дней
    prev_start = today - timedelta(days=14)  # [today-14 .. today-8]
    yesterday = today - timedelta(days=1)

    # 1. WB остатки: quantity_full включает товар в пути (как в get_unified_stock_summary)
    wb_result = await db.execute(
        select(
            WbWarehouseStock.nm_id,
            func.sum(WbWarehouseStock.quantity_full).label("qty"),
            func.max(WbWarehouseStock.brand).label("brand"),
            func.max(WbWarehouseStock.subject).label("subject"),
        )
        .where(WbWarehouseStock.project_id == pid)
        .group_by(WbWarehouseStock.nm_id)
        .limit(_MAX_ROWS)
    )
    wb_rows = wb_result.all()

    # 2. Свои склады: годный сток (включая резерв), брак — отдельное поле, не входит
    own_result = await db.execute(
        select(
            Nomenclature.article_wb.label("nm_id"),
            func.sum(WarehouseStock.quantity).label("qty"),
            func.max(Nomenclature.article_seller).label("article_seller"),
            func.max(Nomenclature.brand).label("brand"),
            func.max(Nomenclature.subject).label("subject"),
            func.max(WarehouseStock.cost_price).label("stock_cost_price"),
        )
        .join(Nomenclature, WarehouseStock.nomenclature_id == Nomenclature.id)
        .where(
            WarehouseStock.project_id == pid,
            Nomenclature.project_id == pid,
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb)
        .limit(_MAX_ROWS)
    )
    own_rows = own_result.all()

    # 2b. article_seller для nm_id, у которых есть только WB-сток (для avg-cost lookup)
    nom_result = await db.execute(
        select(
            Nomenclature.article_wb.label("nm_id"),
            func.max(Nomenclature.article_seller).label("article_seller"),
        )
        .where(Nomenclature.project_id == pid, Nomenclature.article_wb.isnot(None))
        .group_by(Nomenclature.article_wb)
        .limit(_MAX_ROWS)
    )
    article_by_nm: dict[int, str | None] = {r.nm_id: r.article_seller for r in nom_result.all()}

    # 3. Цена единицы
    avg_costs = await load_avg_costs(db, pid)  # article_seller_lower → cost
    overrides = await load_cost_overrides(db, pid)  # nm_id → cost

    # 4. Скорость продаж: текущие и предыдущие 7 дней одним запросом
    rate_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.coalesce(func.sum(WbFunnelDaily.orders_count).filter(WbFunnelDaily.date >= cur_start), 0).label(
                "cur_orders"
            ),
            func.coalesce(func.sum(WbFunnelDaily.orders_count).filter(WbFunnelDaily.date < cur_start), 0).label(
                "prev_orders"
            ),
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.date >= prev_start,
            WbFunnelDaily.date <= yesterday,
        )
        .group_by(WbFunnelDaily.nm_id)
        .limit(_MAX_ROWS)
    )
    rates = {r.nm_id: (int(r.cur_orders), int(r.prev_orders)) for r in rate_result.all()}

    stock_cost_price: dict[int, float] = {}
    stock_map: dict[int, dict] = {}

    def _entry(nm_id: int) -> dict:
        if nm_id not in stock_map:
            stock_map[nm_id] = {
                "wb_qty": 0,
                "wb_cost_rub": 0.0,
                "own_qty": 0,
                "own_cost_rub": 0.0,
                "avg_daily": 0.0,
                "avg_daily_prev": 0.0,
                "days_left": 0,
                "out_date": None,
                "trend_pct": 0.0,
                "brand": "",
                "subject": "",
            }
        return stock_map[nm_id]

    for r in wb_rows:
        e = _entry(r.nm_id)
        e["wb_qty"] = int(r.qty or 0)
        e["brand"] = r.brand or e["brand"]
        e["subject"] = r.subject or e["subject"]

    for r in own_rows:
        e = _entry(r.nm_id)
        e["own_qty"] = int(r.qty or 0)
        e["brand"] = e["brand"] or r.brand or ""
        e["subject"] = e["subject"] or r.subject or ""
        if r.article_seller:
            article_by_nm.setdefault(r.nm_id, r.article_seller)
        if r.stock_cost_price and float(r.stock_cost_price) > 0:
            stock_cost_price[r.nm_id] = float(r.stock_cost_price)

    for nm_id, e in stock_map.items():
        # Приоритет цены: avg по заказам → override → cost_price складской строки
        unit_cost = 0.0
        article = article_by_nm.get(nm_id)
        if article and article.lower() in avg_costs:
            unit_cost = avg_costs[article.lower()]
        elif overrides.get(nm_id, 0) > 0:
            unit_cost = overrides[nm_id]
        elif nm_id in stock_cost_price:
            unit_cost = stock_cost_price[nm_id]

        e["wb_cost_rub"] = round(e["wb_qty"] * unit_cost, 2)
        e["own_cost_rub"] = round(e["own_qty"] * unit_cost, 2)

        cur_orders, prev_orders = rates.get(nm_id, (0, 0))
        e["avg_daily"] = round(cur_orders / 7, 4)
        e["avg_daily_prev"] = round(prev_orders / 7, 4)
        e["trend_pct"] = compute_trend_pct(e["avg_daily"], e["avg_daily_prev"])

        total_stock = e["wb_qty"] + e["own_qty"]
        e["days_left"] = compute_days_left(total_stock, e["avg_daily"])
        e["out_date"] = (today + timedelta(days=e["days_left"])).isoformat() if 0 < e["days_left"] < 999 else None

    return stock_map


def apply_min_orders(rows: list[dict], min_orders: int) -> list[dict]:
    """Серверный порог: скрыть строки с числом заказов за период ниже min_orders."""
    if min_orders <= 0:
        return rows
    return [r for r in rows if int(r.get("orders_count") or 0) >= min_orders]


def _zero_fields(row: dict) -> None:
    for f in _ROW_FIELDS:
        row[f] = None if f == "stock_out_date" else 0


def _apply_totals(
    row: dict, wb_qty: int, wb_cost: float, own_qty: int, own_cost: float, avg_daily: float, avg_prev: float
) -> None:
    today = utcnow().date()
    row["wb_stock_qty"] = wb_qty
    row["wb_stock_cost"] = round(wb_cost, 2)
    row["own_stock_qty"] = own_qty
    row["own_stock_cost"] = round(own_cost, 2)
    days_left = compute_days_left(wb_qty + own_qty, avg_daily)
    row["stock_days_left"] = days_left
    row["stock_out_date"] = (today + timedelta(days=days_left)).isoformat() if 0 < days_left < 999 else None
    row["stock_trend_pct"] = compute_trend_pct(avg_daily, avg_prev)


def _merge_nm_ids(row: dict, nm_ids: list[int], stock_map: dict[int, dict]) -> None:
    infos = [stock_map[nm] for nm in nm_ids if nm in stock_map]
    if not infos:
        _zero_fields(row)
        return
    _apply_totals(
        row,
        sum(i["wb_qty"] for i in infos),
        sum(i["wb_cost_rub"] for i in infos),
        sum(i["own_qty"] for i in infos),
        sum(i["own_cost_rub"] for i in infos),
        sum(i["avg_daily"] for i in infos),
        sum(i["avg_daily_prev"] for i in infos),
    )


def merge_stock_costs(rows: list[dict], stock_map: dict[int, dict], group_by: str) -> None:
    """Мёрж складских полей в строки воронки (in-place).

    sku/abc — по nm_id строки; tag/imt — сумма по children;
    brand/subject — атрибуция стока по его собственному brand/subject.
    """
    if group_by in ("sku", "abc"):
        for row in rows:
            _merge_nm_ids(row, [row.get("nm_id")], stock_map)
        return

    if group_by in ("tag", "imt"):
        for row in rows:
            children = row.get("children") or []
            for child in children:
                _merge_nm_ids(child, [child.get("nm_id")], stock_map)
            _merge_nm_ids(row, [c.get("nm_id") for c in children], stock_map)
        return

    if group_by in ("brand", "subject"):
        by_key: dict[str, list[int]] = {}
        for nm_id, info in stock_map.items():
            by_key.setdefault(info.get(group_by) or "", []).append(nm_id)
        for row in rows:
            key = row.get(group_by) or ""
            _merge_nm_ids(row, by_key.get(key, []), stock_map)
