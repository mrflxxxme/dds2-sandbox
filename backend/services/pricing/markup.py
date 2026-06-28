# ruff: noqa: RUF001, RUF002, RUF003
"""Аналитика наценки по артикулам.

Объединяет текущую цену витрины ВБ (WbPrice) + себестоимость единицы
(override → avg по закупкам → склад) + расходы ВБ/СПП/прибыль из воронки
(get_funnel_by_sku) → коэффициент наценки, доля себестоимости, чистая маржа.
Группировка по категории (CategoryOverride → subject), фильтры.

Кэшируется тяжёлая загрузка (по project_id + период); фильтры/группировка —
в памяти поверх кэша (дёшево, переиспользует один прогон на все фильтры).
"""

import logging
from collections import defaultdict
from datetime import date as _date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import Nomenclature, Project, WarehouseStock, WbPrice
from backend.schemas.pricing import PricingGroup, PricingResponse, PricingRow, PricingSummary
from backend.services import funnel as funnel_service
from backend.services import refs_service
from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides, load_tax_settings
from backend.services.funnel.bdr_rates import get_bdr_rates

logger = logging.getLogger("dds.pricing")

UNCATEGORIZED = "Без категории"
_MAX_ROWS = 50000


async def _load_tax_info(db: AsyncSession, pid: int) -> dict:
    """Налоговые настройки проекта (как в воронке: TaxRate → project.tax_rate=6%)."""
    today = _date.today()
    tax_info = await load_tax_settings(db, pid, today, today)
    if tax_info.get("usn_rate", 0) == 0 and tax_info.get("nds_rate", 0) == 0:
        proj = await db.get(Project, pid)
        legacy = float((proj.tax_rate if proj else None) or 6)
        tax_info = {"tax_regime": "usn_income", "usn_rate": legacy, "nds_rate": 0, "cost_as_expense": False}
    return tax_info


async def _load_meta_map(db: AsyncSession, pid: int) -> dict[int, dict]:
    """nm_id → {brand, subject, article_seller, wh_cost} одним сканом Nomenclature (+склад).

    wh_cost = func.max(WarehouseStock.cost_price) по складам/баркодам карточки —
    грубая верхняя оценка, используется ТОЛЬКО как 3-й приоритет себестоимости
    (после avg по закупкам и ручного override). Один товар (article_wb) = много
    баркодов × складов; max под group_by(article_wb) даёт ровно один ряд на nm_id.
    """
    rows = await db.execute(
        select(
            Nomenclature.article_wb.label("nm_id"),
            func.max(Nomenclature.brand).label("brand"),
            func.max(Nomenclature.subject).label("subject"),
            func.max(Nomenclature.article_seller).label("article_seller"),
            func.max(WarehouseStock.cost_price).label("wh_cost"),
        )
        .outerjoin(
            WarehouseStock,
            (WarehouseStock.nomenclature_id == Nomenclature.id) & (WarehouseStock.project_id == pid),
        )
        .where(Nomenclature.project_id == pid, Nomenclature.article_wb.isnot(None))
        .group_by(Nomenclature.article_wb)
        .limit(_MAX_ROWS)
    )
    return {
        r.nm_id: {
            "brand": r.brand,
            "subject": r.subject,
            "article_seller": r.article_seller,
            "wh_cost": float(r.wh_cost) if r.wh_cost else None,
        }
        for r in rows
    }


def _resolve_cost(nm_id: int, meta: dict, avg_costs: dict[str, float], overrides: dict[int, float]) -> float | None:
    """Себестоимость единицы: avg по закупкам (article_seller) → override(nm) → склад."""
    article = (meta.get("article_seller") or "").lower()
    if article and article in avg_costs:
        return avg_costs[article]
    ov = overrides.get(nm_id, 0)
    if ov > 0:
        return ov
    wh = meta.get("wh_cost")
    return wh if wh and wh > 0 else None


def _build_row(
    nm_id: int,
    price: WbPrice | None,
    funnel: dict | None,
    cost: float | None,
    cat_overrides: dict[int, str],
    meta: dict | None,
) -> PricingRow:
    f = funnel or {}
    m = meta or {}
    vendor_code = (price.vendor_code if price and price.vendor_code else None) or f.get("vendor_code") or m.get(
        "article_seller"
    )
    brand = f.get("brand") or m.get("brand")
    subject = f.get("subject") or m.get("subject")
    category = cat_overrides.get(nm_id) or subject or UNCATEGORIZED

    current_price = float(price.price) if price and price.price is not None else None
    base_price = float(price.base_price) if price and price.base_price is not None else None
    discount = float(price.discount) if price and price.discount is not None else None
    cost_price = float(cost) if cost else None

    has_price = current_price is not None and current_price > 0
    has_cost = cost_price is not None and cost_price > 0

    markup_coef = markup_pct = cost_share_pct = None
    if has_price and has_cost and current_price is not None and cost_price is not None:
        markup_coef = round(current_price / cost_price, 3)
        markup_pct = round((current_price - cost_price) / cost_price * 100, 2)
        cost_share_pct = round(cost_price / current_price * 100, 2)

    spp_rate = float(f.get("spp_rate") or 0)
    buyer_price = round(current_price * (1 - spp_rate / 100), 2) if has_price and current_price is not None else None

    revenue = float(f.get("revenue") or 0)
    # commission из воронки = выручка − к перечислению = ВСЕ удержания ВБ
    # (комиссия+логистика+хранение, по финотчёту в BDR-пути; только комиссия в легаси).
    wb_expenses = round(float(f.get("commission") or 0), 2)
    cost_total = float(f.get("cost_total") or 0)
    profit = float(f.get("profit") or 0)
    net_markup_pct = round(profit / cost_total * 100, 2) if cost_total > 0 else None

    return PricingRow(
        nm_id=nm_id,
        vendor_code=vendor_code,
        brand=brand,
        subject=subject,
        category=category,
        current_price=current_price,
        base_price=base_price,
        discount=discount,
        cost_price=cost_price,
        has_cost=has_cost,
        has_price=has_price,
        markup_coef=markup_coef,
        markup_pct=markup_pct,
        cost_share_pct=cost_share_pct,
        spp_rate=round(spp_rate, 2),
        buyer_price=buyer_price,
        orders_count=int(f.get("orders_count") or 0),
        revenue=round(revenue, 2),
        wb_expenses=wb_expenses,
        adv_sum=round(float(f.get("adv_sum") or 0), 2),
        tax=round(float(f.get("tax") or 0), 2),
        cost_total=round(cost_total, 2),
        profit=round(profit, 2),
        margin_pct=round(float(f.get("margin") or 0), 2),
        net_markup_pct=net_markup_pct,
    )


def _apply_filters(
    rows: list[PricingRow], brand: str | None, category: str | None, search: str | None, min_orders: int
) -> list[PricingRow]:
    out = rows
    if brand:
        out = [r for r in out if (r.brand or "") == brand]
    if category:
        out = [r for r in out if r.category == category]
    if search:
        s = search.lower()
        out = [r for r in out if s in (r.vendor_code or "").lower() or s in str(r.nm_id)]
    if min_orders > 0:
        out = [r for r in out if r.orders_count >= min_orders]
    return out


def _portfolio_markup(items: list[PricingRow]) -> tuple[float | None, float | None, float | None]:
    """Портфельная наценка группы: Σ цена / Σ себест по строкам с обоими."""
    sum_price = sum(r.current_price for r in items if r.has_price and r.has_cost and r.current_price)
    sum_cost = sum(r.cost_price for r in items if r.has_price and r.has_cost and r.cost_price)
    if sum_cost <= 0:
        return None, None, None
    coef = round(sum_price / sum_cost, 3)
    pct = round((sum_price - sum_cost) / sum_cost * 100, 2)
    share = round(sum_cost / sum_price * 100, 2) if sum_price > 0 else None
    return coef, pct, share


def _sort_by_markup(items: list[PricingRow]) -> None:
    items.sort(key=lambda r: r.markup_pct if r.markup_pct is not None else -1e18, reverse=True)


def _group_by_category(rows: list[PricingRow]) -> list[PricingGroup]:
    buckets: dict[str, list[PricingRow]] = defaultdict(list)
    for r in rows:
        buckets[r.category].append(r)

    groups: list[PricingGroup] = []
    for cat, items in buckets.items():
        coef, pct, share = _portfolio_markup(items)
        revenue = sum(r.revenue for r in items)
        profit = sum(r.profit for r in items)
        _sort_by_markup(items)
        groups.append(
            PricingGroup(
                category=cat,
                articles=len(items),
                priced_articles=sum(1 for r in items if r.has_price),
                markup_coef=coef,
                markup_pct=pct,
                cost_share_pct=share,
                revenue=round(revenue, 2),
                profit=round(profit, 2),
                cost_total=round(sum(r.cost_total for r in items), 2),
                wb_expenses=round(sum(r.wb_expenses for r in items), 2),
                margin_pct=round(profit / revenue * 100, 2) if revenue > 0 else 0,
                children=items,
            )
        )
    groups.sort(key=lambda g: g.revenue, reverse=True)
    return groups


def _build_summary(rows: list[PricingRow]) -> PricingSummary:
    coef, pct, share = _portfolio_markup(rows)
    revenue = sum(r.revenue for r in rows)
    profit = sum(r.profit for r in rows)
    return PricingSummary(
        total_articles=len(rows),
        priced_articles=sum(1 for r in rows if r.has_price),
        costed_articles=sum(1 for r in rows if r.has_cost),
        revenue=round(revenue, 2),
        profit=round(profit, 2),
        cost_total=round(sum(r.cost_total for r in rows), 2),
        wb_expenses=round(sum(r.wb_expenses for r in rows), 2),
        markup_pct=pct,
        cost_share_pct=share,
        margin_pct=round(profit / revenue * 100, 2) if revenue > 0 else 0,
    )


@cached(prefix="reports:pricing_markup", ttl=300)
async def _compute_rows(
    db: AsyncSession, project_id: int, date_from: str | None = None, date_to: str | None = None
) -> dict:
    """Тяжёлая загрузка + построение ВСЕХ строк (без фильтров/группировки).

    Кэшируется по (project_id, date_from, date_to) — фильтры применяются поверх
    в памяти, поэтому смена бренда/категории/поиска не плодит прогоны.
    """
    tax_info = await _load_tax_info(db, project_id)
    bdr_rates_map = await get_bdr_rates(db, project_id)

    funnel_rows = await funnel_service.get_funnel_by_sku(
        db, project_id, tax_info, date_from, date_to, None, None,
        bdr_rates_map=bdr_rates_map, limit=_MAX_ROWS,
    )
    funnel_by_nm = {r["nm_id"]: r for r in funnel_rows}

    price_rows = (
        await db.execute(select(WbPrice).where(WbPrice.project_id == project_id).limit(_MAX_ROWS))
    ).scalars().all()
    price_by_nm = {p.nm_id: p for p in price_rows}
    price_synced_at = max((p.synced_at for p in price_rows if p.synced_at), default=None)

    avg_costs = await load_avg_costs(db, project_id)
    overrides = await load_cost_overrides(db, project_id)
    meta_map = await _load_meta_map(db, project_id)
    cat_overrides = await refs_service.get_category_overrides(db, project_id)

    nm_ids = set(price_by_nm) | set(funnel_by_nm)
    rows = []
    for nm_id in nm_ids:
        meta = meta_map.get(nm_id) or {}
        cost = _resolve_cost(nm_id, meta, avg_costs, overrides)
        rows.append(
            _build_row(nm_id, price_by_nm.get(nm_id), funnel_by_nm.get(nm_id), cost, cat_overrides, meta).model_dump()
        )

    return {
        "rows": rows,
        "price_synced_at": price_synced_at.isoformat() if price_synced_at else None,
        "has_bdr": bool(bdr_rates_map),
    }


async def get_markup_analytics(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    brand: str | None = None,
    category: str | None = None,
    search: str | None = None,
    min_orders: int = 0,
    group_by: str = "category",
) -> dict:
    """Наценка по артикулам: текущая цена ВБ + себестоимость + расходы ВБ.

    Универсум строк = все артикулы с ценой (WbPrice) ∪ продававшиеся за период
    (воронка). Метрики наценки = «—» для строк без цены/себестоимости.
    """
    raw = await _compute_rows(db, project_id, date_from=date_from, date_to=date_to)
    rows = [PricingRow(**r) for r in raw["rows"]]
    rows = _apply_filters(rows, brand, category, search, min_orders)
    summary = _build_summary(rows)
    synced = raw["price_synced_at"]
    has_bdr = raw["has_bdr"]

    if group_by == "sku":
        _sort_by_markup(rows)
        resp = PricingResponse(
            group_by="sku", data_rows=rows, data_groups=[], summary=summary,
            price_synced_at=synced, has_bdr=has_bdr,
        )
    else:
        resp = PricingResponse(
            group_by="category", data_rows=[], data_groups=_group_by_category(rows),
            summary=summary, price_synced_at=synced, has_bdr=has_bdr,
        )
    return resp.model_dump(mode="json")
