# ruff: noqa: RUF002, RUF003
"""Сервис отчёта «Индекс локализации» (ИЛ + ИРП).

Источник данных: `wb_funnel_daily.localization_percent` (с 23.03.2026,
сохраняется как точное значение из WB Analytics v3 sales-funnel/products,
поле `localizationPercent`).

Расчёты (см. backend/DOMAIN_LOCALIZATION.md):
- per-SKU loc_pct      = средневзвеш. localization_percent по orders_count
- per-SKU ktr / krp    = таблицы из backend/services/localization_tariff.py
- top.localization_idx = средневзвеш. КТР по orders (Σ orders×ktr / Σ orders)
- top.irp_percent      = средневзвеш. КРП по orders (Σ orders×krp / Σ orders)

ВАЖНО: каждый запрос фильтрует `WbFunnelDaily.project_id == project_id`
(multi-tenancy). Кэш `@cached(prefix="reports:localization", ttl=300)`.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import WbFunnelDaily, WbOrder
from backend.services.localization_tariff import get_krp, get_ktr, status_label
from backend.services.warehouse_district import (
    DISTRICT_LABELS,
    DISTRICT_ORDER,
    okrug_to_district,
    warehouse_to_district,
)

logger = logging.getLogger("dds.localization")


_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


def _q2(value: Decimal) -> Decimal:
    """Округление до 2 знаков после запятой (HALF_UP)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _load_rows(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
) -> list[WbFunnelDaily]:
    """Поднять строки funnel за период с непустым localization_percent.

    Multi-tenancy: фильтр по project_id обязателен.
    """
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    q = (
        select(WbFunnelDaily)
        .where(WbFunnelDaily.project_id == project_id)
        .where(WbFunnelDaily.date >= d_from)
        .where(WbFunnelDaily.date <= d_to)
        .where(WbFunnelDaily.localization_percent.isnot(None))
        .where(WbFunnelDaily.orders_count > 0)
    )
    res = await db.execute(q)
    return list(res.scalars())


def _aggregate_by_sku(rows: list[WbFunnelDaily]) -> dict[int, dict]:
    """Свернуть строки funnel в агрегаты per-SKU.

    Возвращает: {nm_id: {orders, weighted_loc_sum, vendor_code, subject, brand}}
    weighted_loc_sum = Σ(orders × loc_pct) — потом делим на orders для среднего.
    """
    acc: dict[int, dict] = defaultdict(
        lambda: {
            "orders": 0,
            "weighted_loc_sum": _ZERO,  # Σ orders×loc
            "vendor_code": None,
            "subject": None,
            "brand": None,
        }
    )
    for r in rows:
        if r.nm_id is None or r.orders_count is None or r.localization_percent is None:
            continue
        orders = int(r.orders_count or 0)
        if orders <= 0:
            continue
        loc = Decimal(str(r.localization_percent))
        item = acc[r.nm_id]
        item["orders"] += orders
        item["weighted_loc_sum"] += loc * Decimal(orders)
        # Берём первое непустое значение (vendor_code/subject/brand редко меняется)
        if not item["vendor_code"] and r.vendor_code:
            item["vendor_code"] = r.vendor_code
        if not item["subject"] and r.subject:
            item["subject"] = r.subject
        if not item["brand"] and r.brand:
            item["brand"] = r.brand
    return acc


def _build_sku_rows(agg: dict[int, dict]) -> list[dict]:
    """Из агрегатов построить per-SKU строки (raw dict, не Pydantic)."""
    rows: list[dict] = []
    for nm_id, item in agg.items():
        orders = int(item["orders"])
        if orders <= 0:
            continue
        loc_pct = (item["weighted_loc_sum"] / Decimal(orders)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ktr = get_ktr(loc_pct)
        krp = get_krp(loc_pct)
        local = int((Decimal(orders) * loc_pct / _HUNDRED).quantize(_ONE, rounding=ROUND_HALF_UP))
        local = max(0, min(local, orders))
        non_local = orders - local
        contribution = (Decimal(orders) * ktr).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        title = item["vendor_code"] or item["subject"] or str(nm_id)
        rows.append(
            {
                "nm_id": int(nm_id),
                "vendor_code": item["vendor_code"],
                "title": title,
                "subject": item["subject"],
                "brand": item["brand"],
                "total": orders,
                "local": local,
                "non_local": non_local,
                "loc_pct": loc_pct,
                "ktr": ktr,
                "krp": krp,
                "contribution": contribution,
                "status": status_label(loc_pct),
            }
        )
    rows.sort(key=lambda r: (-r["total"], r["nm_id"]))
    return rows


def _build_summary(sku_rows: list[dict]) -> dict:
    """Из per-SKU строк собрать top-block (взвешенные средние)."""
    total_orders = 0
    weighted_ktr = _ZERO
    weighted_krp = _ZERO
    weighted_loc = _ZERO
    local_orders = 0
    articles_local = 0
    articles_critical = 0

    for r in sku_rows:
        orders = int(r["total"])
        total_orders += orders
        weighted_ktr += Decimal(orders) * Decimal(str(r["ktr"]))
        weighted_krp += Decimal(orders) * Decimal(str(r["krp"]))
        weighted_loc += Decimal(orders) * Decimal(str(r["loc_pct"]))
        local_orders += int(r["local"])
        if Decimal(str(r["krp"])) == _ZERO:
            articles_local += 1
        else:
            articles_critical += 1

    if total_orders > 0:
        denom = Decimal(total_orders)
        loc_idx = (weighted_ktr / denom).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        irp_pct = (weighted_krp / denom).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        loc_pct_overall = (weighted_loc / denom).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        loc_idx = _ONE
        irp_pct = _ZERO
        loc_pct_overall = _ZERO

    return {
        "localization_index": loc_idx,
        "irp_percent": irp_pct,
        "local_orders": local_orders,
        "non_local_orders": max(0, total_orders - local_orders),
        "total_orders": total_orders,
        "loc_pct_overall": loc_pct_overall,
        "articles_count": len(sku_rows),
        "articles_local_count": articles_local,
        "articles_critical_count": articles_critical,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Per-district breakdown (из таблицы wb_orders)
# ═══════════════════════════════════════════════════════════════════════════════


def _empty_district_map() -> dict[str, dict[str, int]]:
    """Канонический словарь {district_key: {local: 0, non_local: 0}} для всех ФО.

    Используется как «нулевая» заготовка чтобы UI всегда видел все 7 ключей.
    """
    return {key: {"local": 0, "non_local": 0} for key in DISTRICT_ORDER}


async def _load_district_breakdown(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
) -> dict[int, dict[str, dict[str, int]]]:
    """Поднять per-(nm_id, district) счётчики local/non_local из wb_orders.

    Фильтры:
    - project_id == project_id (multi-tenancy)
    - order_date BETWEEN date_from и date_to (включительно)
    - is_cancel == False
    - country_name == 'Россия' (зарубежные склады обрабатываются отдельно
      через district=abroad — но обычные WB-РФ заказы идут только если
      countryName='Россия')
    - warehouse_type == 'Склад WB' (FBO; FBS / маркетплейс не считаем)

    Логика:
    - district = okrug_to_district(oblast_okrug_name, country_name)
    - source_district = warehouse_to_district(warehouse_name)
    - local если district == source_district, иначе non_local

    Возвращает {nm_id: {district: {"local": N, "non_local": M}}}.
    """
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    q = (
        select(
            WbOrder.nm_id,
            WbOrder.warehouse_name,
            WbOrder.oblast_okrug_name,
            WbOrder.country_name,
        )
        .where(WbOrder.project_id == project_id)
        .where(WbOrder.order_date >= d_from)
        .where(WbOrder.order_date < d_to + timedelta(days=1))
        .where(WbOrder.is_cancel == False)  # noqa: E712 — SQLAlchemy expression
        .where(WbOrder.country_name == "Россия")
        .where(WbOrder.warehouse_type == "Склад WB")
    )
    res = await db.execute(q)

    by_nm: dict[int, dict[str, dict[str, int]]] = defaultdict(_empty_district_map)
    for nm_id, warehouse_name, okrug, country in res.all():
        if nm_id is None:
            continue
        delivery_district = okrug_to_district(okrug, country)
        source_district = warehouse_to_district(warehouse_name)
        # «Местным» считаем заказ доставки = округ склада-источника.
        # При unknown с любой стороны — non_local (консервативно).
        is_local = (
            delivery_district == source_district
            and delivery_district in DISTRICT_ORDER
            and source_district in DISTRICT_ORDER
        )
        # Кладём счётчик в district получателя (так пользователь видит «куда»).
        bucket_key = delivery_district if delivery_district in DISTRICT_ORDER else "abroad"
        bucket = by_nm[int(nm_id)][bucket_key]
        if is_local:
            bucket["local"] += 1
        else:
            bucket["non_local"] += 1
    return dict(by_nm)


def _district_list_for_nm(
    nm_breakdown: dict[str, dict[str, int]] | None,
) -> list[dict]:
    """Преобразовать {district: {local, non_local}} → list[DistrictBreakdown]
    в порядке DISTRICT_ORDER. Все 7 ключей всегда присутствуют (нулями).

    Если breakdown == None (нет wb_orders за период) — возвращает пустой список,
    чтобы UI отрисовал «нет данных».
    """
    if nm_breakdown is None:
        return []
    out: list[dict] = []
    for key in DISTRICT_ORDER:
        cnt = nm_breakdown.get(key) or {"local": 0, "non_local": 0}
        local = int(cnt.get("local", 0))
        non_local = int(cnt.get("non_local", 0))
        total = local + non_local
        if total > 0:
            local_pct = (Decimal(local) * _HUNDRED / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            local_pct = _ZERO
        out.append(
            {
                "district": key,
                "label": DISTRICT_LABELS.get(key, key),
                "local": local,
                "non_local": non_local,
                "total": total,
                "local_pct": local_pct,
            }
        )
    return out


def _aggregate_district_totals(
    by_nm: dict[int, dict[str, dict[str, int]]],
) -> list[dict]:
    """Сложить per-nm районные счётчики в общий total per district."""
    if not by_nm:
        return []
    totals: dict[str, dict[str, int]] = _empty_district_map()
    for nm_breakdown in by_nm.values():
        for district, cnt in nm_breakdown.items():
            if district in totals:
                totals[district]["local"] += int(cnt.get("local", 0))
                totals[district]["non_local"] += int(cnt.get("non_local", 0))
    return _district_list_for_nm(totals)


@cached(prefix="reports:localization_districts", ttl=300)
async def get_district_breakdown(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
) -> dict:
    """Per-(nm_id, district) breakdown + агрегаты по всем артикулам.

    Кэш 300 сек, ключ включает project_id + dates. После sync wb_orders —
    invalidate_cache("reports:localization_districts").

    Returns:
        {
            "by_nm": {nm_id: {district_key: {"local": N, "non_local": M}}},
            "totals": list[DistrictBreakdown-dict],  # упорядочено DISTRICT_ORDER
            "has_data": bool,  # True если хоть один заказ в wb_orders за период
        }
    """
    by_nm = await _load_district_breakdown(db, project_id, date_from, date_to)
    return {
        "by_nm": by_nm,
        "totals": _aggregate_district_totals(by_nm),
        "has_data": bool(by_nm),
    }


@cached(prefix="reports:localization", ttl=300)
async def get_summary(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
) -> dict:
    """Top-block отчёта локализации за период.

    Кэш 300 сек. После мутаций (sync wb_funnel_daily / sync wb_orders) надо
    вызвать invalidate_cache("reports:localization") и
    invalidate_cache("reports:localization_districts").
    """
    rows = await _load_rows(db, project_id, date_from, date_to)
    agg = _aggregate_by_sku(rows)
    sku_rows = _build_sku_rows(agg)
    summary = _build_summary(sku_rows)

    # Per-district агрегаты (из wb_orders)
    districts = await get_district_breakdown(db, project_id, date_from, date_to)
    summary["district_totals"] = districts.get("totals", []) if districts.get("has_data") else []
    return summary


@cached(prefix="reports:localization_skus", ttl=300)
async def get_by_sku(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
) -> list[dict]:
    """Таблица отчёта локализации (per-SKU) за период.

    Кэш 300 сек. Каждой строке добавляется поле `districts` —
    list[DistrictBreakdown] упорядоченный как DISTRICT_ORDER (или [] если
    wb_orders пуст за период).
    """
    rows = await _load_rows(db, project_id, date_from, date_to)
    agg = _aggregate_by_sku(rows)
    sku_rows = _build_sku_rows(agg)

    districts = await get_district_breakdown(db, project_id, date_from, date_to)
    by_nm = districts.get("by_nm", {}) if districts.get("has_data") else None

    for r in sku_rows:
        if by_nm is None:
            r["districts"] = []
        else:
            r["districts"] = _district_list_for_nm(by_nm.get(int(r["nm_id"])))
    return sku_rows
