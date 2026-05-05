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
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import WbFunnelDaily
from backend.services.localization_tariff import get_krp, get_ktr, status_label

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


@cached(prefix="reports:localization", ttl=300)
async def get_summary(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
) -> dict:
    """Top-block отчёта локализации за период.

    Кэш 300 сек. После мутаций (sync wb_funnel_daily) надо вызвать
    invalidate_cache("reports:localization").
    """
    rows = await _load_rows(db, project_id, date_from, date_to)
    agg = _aggregate_by_sku(rows)
    sku_rows = _build_sku_rows(agg)
    return _build_summary(sku_rows)


@cached(prefix="reports:localization_skus", ttl=300)
async def get_by_sku(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
) -> list[dict]:
    """Таблица отчёта локализации (per-SKU) за период.

    Кэш 300 сек.
    """
    rows = await _load_rows(db, project_id, date_from, date_to)
    agg = _aggregate_by_sku(rows)
    return _build_sku_rows(agg)
