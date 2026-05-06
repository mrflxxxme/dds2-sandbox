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

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
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
    - INNER JOIN с wb_funnel_daily по (project_id, nm_id, date_msk):
      берём только те заказы, для которых есть `localization_percent`
      в funnel — иначе расходится с топ-блоком (top-block считается
      по wb_funnel_daily, который покрывает не все дни).

    Логика:
    - district = okrug_to_district(oblast_okrug_name, country_name)
    - source_district = warehouse_to_district(warehouse_name)
    - local если district == source_district, иначе non_local

    Возвращает {nm_id: {district: {"local": N, "non_local": M}}}.
    """
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    # order_date хранится как DateTime(timezone=True); funnel.date — календарная
    # дата по MSK (как в WB-кабинете). Приводим order_date к MSK-дате для JOIN.
    order_date_msk = func.date(func.timezone("Europe/Moscow", WbOrder.order_date))
    q = (
        select(
            WbOrder.nm_id,
            WbOrder.warehouse_name,
            WbOrder.oblast_okrug_name,
            WbOrder.country_name,
        )
        .join(
            WbFunnelDaily,
            (WbFunnelDaily.project_id == WbOrder.project_id)
            & (WbFunnelDaily.nm_id == WbOrder.nm_id)
            & (WbFunnelDaily.date == order_date_msk)
            & (WbFunnelDaily.localization_percent.isnot(None))
            & (WbFunnelDaily.orders_count > 0),
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


async def sync_localization_only(
    project_id: int,
    date_from: str,
    date_to: str,
) -> dict:
    """Облегчённый синк ТОЛЬКО `localization_percent` за период.

    Зачем: полный `run_funnel_sync` тянет рекламную статистику (минуты из-за
    WB rate-limit 429). Для отчёта локализации нужно только поле
    `localizationPercent` от sales-funnel — значит делаем минимум:
    history-batch (5 дней) или per-day fallback, UPSERT только этого поля.

    Запускается background-task'ом из endpoint /localization/sync.
    """
    import asyncio

    import httpx

    from backend.database import AsyncSessionLocal
    from backend.services.funnel.wb_api_client import get_wb_key
    from backend.services.funnel.wb_funnel_api import (
        fetch_funnel,
        fetch_funnel_history,
    )

    started_at = date.today().isoformat()
    logger.info(
        "localization-only sync: project=%d period=%s..%s started=%s",
        project_id,
        date_from,
        date_to,
        started_at,
    )

    async with AsyncSessionLocal() as db:
        api_key = await get_wb_key(db, project_id, "wb_analytics")
        if not api_key:
            api_key = await get_wb_key(db, project_id, "wb")
        if not api_key:
            logger.warning("localization-only sync: no WB key for project=%d", project_id)
            return {"status": "error", "error": "no WB key"}

        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
        all_dates: list[str] = []
        d = d_from
        while d <= d_to:
            all_dates.append(d.isoformat())
            d += timedelta(days=1)
        if not all_dates:
            return {"status": "ok", "days": 0, "rows_updated": 0}

        # Список nm_ids проекта — обязателен для /history (без них 400).
        nm_ids_rows = (
            await db.execute(
                select(WbFunnelDaily.nm_id)
                .where(WbFunnelDaily.project_id == project_id)
                .where(WbFunnelDaily.nm_id.isnot(None))
                .distinct()
            )
        ).all()
        nm_ids = [r[0] for r in nm_ids_rows]
        logger.info("localization-only sync: %d nm_ids loaded", len(nm_ids))

        async def _upsert_day(ds: str, day_data: dict) -> int:
            """Upsert one day's funnel data — точечно, только нужные поля."""
            rows = []
            for nm_id, fd in day_data.items():
                rows.append(
                    {
                        "project_id": project_id,
                        "date": date.fromisoformat(ds),
                        "nm_id": nm_id,
                        "vendor_code": fd.get("vendor_code") or "",
                        "subject": fd.get("subject") or "",
                        "brand": fd.get("brand") or "",
                        "open_card": fd.get("open_card", 0),
                        "add_to_cart": fd.get("add_to_cart", 0),
                        "orders_count": fd.get("orders_count", 0),
                        "orders_sum_rub": fd.get("orders_sum_rub", 0),
                        "buyout_percent": fd.get("buyout_percent", 0),
                        "cart_to_order_pct": fd.get("cart_to_order_pct", 0),
                        "add_to_cart_pct": fd.get("add_to_cart_pct", 0),
                        "localization_percent": fd.get("localization_percent"),
                        "time_to_ready_minutes": fd.get("time_to_ready_minutes"),
                    }
                )
            if not rows:
                return 0
            stmt = pg_insert(WbFunnelDaily).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_funnel_daily",
                set_={
                    "vendor_code": stmt.excluded.vendor_code,
                    "subject": stmt.excluded.subject,
                    "brand": stmt.excluded.brand,
                    "orders_count": stmt.excluded.orders_count,
                    "localization_percent": stmt.excluded.localization_percent,
                    "time_to_ready_minutes": stmt.excluded.time_to_ready_minutes,
                },
            )
            await db.execute(stmt)
            await db.commit()
            return len(rows)

        # 1) /history endpoint — один большой батч (с nm_ids WB позволяет
        #    запросить весь период сразу, обычно до ~30 дней). Если WB вернёт
        #    400/402 за длинное окно — fallback per-day.
        total = 0
        days_done = 0
        use_history = True
        WINDOW = 30
        for i in range(0, len(all_dates), WINDOW):
            window = all_dates[i : i + WINDOW]
            w_from, w_to = window[0], window[-1]
            if use_history:
                try:
                    data = await fetch_funnel_history(api_key, w_from, w_to, nm_ids=nm_ids)
                except (TimeoutError, httpx.HTTPError) as e:
                    logger.warning("history fetch error %s..%s: %s", w_from, w_to, e)
                    data = None
                if data is None:
                    use_history = False
                    logger.info("history unavailable, falling back to per-day")
                else:
                    for ds, dd in data.items():
                        total += await _upsert_day(ds, dd)
                        days_done += 1
                    logger.info("history batch %s..%s: %d days saved", w_from, w_to, len(data))
                    await asyncio.sleep(1)
                    continue
            # Per-day fallback с инкрементальным save после каждого дня
            for ds in window:
                try:
                    day_data = await fetch_funnel(api_key, ds)
                    if day_data:
                        total += await _upsert_day(ds, day_data)
                        days_done += 1
                        logger.info("per-day saved %s: %d nm_ids", ds, len(day_data))
                except (TimeoutError, httpx.HTTPError) as e:
                    logger.warning("per-day fetch error %s: %s", ds, e)
                await asyncio.sleep(1)

    # Инвалидация кэша
    for prefix in (
        "reports:localization",
        "reports:localization_skus",
        "reports:localization_districts",
        "reports:localization_dates",
        "reports:localization_daily",
    ):
        try:
            await invalidate_cache(prefix)
        except Exception as e:
            logger.warning("invalidate %s failed: %s", prefix, e)

    logger.info(
        "localization-only sync done: project=%d days=%d rows=%d",
        project_id,
        days_done,
        total,
    )
    return {"status": "ok", "days": days_done, "rows_updated": total}


@cached(prefix="reports:localization_dates", ttl=300)
async def get_available_dates(
    db: AsyncSession,
    project_id: int,
) -> list[str]:
    """Список ISO-дат, за которые в funnel есть `localization_percent`.

    UI использует для блокировки недоступных дат в date-picker'е.
    Multi-tenancy: фильтр project_id обязателен.
    """
    q = (
        select(WbFunnelDaily.date)
        .where(WbFunnelDaily.project_id == project_id)
        .where(WbFunnelDaily.localization_percent.isnot(None))
        .where(WbFunnelDaily.orders_count > 0)
        .group_by(WbFunnelDaily.date)
        .order_by(WbFunnelDaily.date)
    )
    res = await db.execute(q)
    return [d.isoformat() for d in res.scalars()]


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


@cached(prefix="reports:localization_daily", ttl=300)
async def get_daily(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
    subject: str | None = None,
    brand: str | None = None,
) -> list[dict]:
    """Динамика ИЛ/ИРП по дням за период.

    Логика — та же что в `_build_summary`, но per-day: для каждой даты в
    `wb_funnel_daily` с непустым `localization_percent` считается взвешенное
    среднее КТР/КРП по `orders_count`.

    Фильтры:
    - subject (предмет) — `WbFunnelDaily.subject == subject` если задан
    - brand   — аналогично

    Кэш 300 сек, ключ включает project_id + dates + subject + brand. После
    sync funnel/wb_orders инвалидируется через `reports:localization_daily`.

    Returns:
        list[{date, localization_index, irp_percent, total_orders, articles_count}]
        упорядочено по date ASC. Дни без orders с localization_percent
        в результате не появляются (UI показывает «нет данных» как пропуск).
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
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    res = await db.execute(q)
    all_rows = list(res.scalars())

    by_date: dict[date, list[WbFunnelDaily]] = defaultdict(list)
    for r in all_rows:
        if r.date is None:
            continue
        by_date[r.date].append(r)

    points: list[dict] = []
    for d in sorted(by_date.keys()):
        agg = _aggregate_by_sku(by_date[d])
        sku_rows = _build_sku_rows(agg)
        summary = _build_summary(sku_rows)
        if summary["total_orders"] <= 0:
            continue
        points.append(
            {
                "date": d.isoformat(),
                "localization_index": summary["localization_index"],
                "irp_percent": summary["irp_percent"],
                "total_orders": summary["total_orders"],
                "articles_count": summary["articles_count"],
            }
        )
    return points


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
            # Cache JSON-сериализация переводит int ключи в str → пробуем оба
            nm_key = r["nm_id"]
            entry = by_nm.get(nm_key) or by_nm.get(str(nm_key)) or by_nm.get(int(nm_key))
            r["districts"] = _district_list_for_nm(entry)
    return sku_rows
