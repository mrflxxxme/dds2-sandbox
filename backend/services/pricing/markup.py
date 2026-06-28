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
import math
from collections import defaultdict
from datetime import date as _date
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import Nomenclature, Project, WarehouseStock, WbFunnelDaily, WbPrice, WbWarehouseStock
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.schemas.pricing import PricingGroup, PricingResponse, PricingRow, PricingSummary
from backend.services import funnel as funnel_service
from backend.services import refs_service
from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides, load_tax_settings
from backend.services.funnel.bdr_rates import get_bdr_rates
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

UNCATEGORIZED = "Без категории"
_MAX_ROWS = 50000

# Пороги аномалий
_THIN_MARKUP_PCT = 30.0  # наценка ниже — «низкая наценка»
_SUSPICIOUS_COST_SHARE_PCT = 5.0  # себест < 5% цены — вероятно ошибка данных

ANOMALY_PRICE_BELOW_COST = "Цена ниже себестоимости"
ANOMALY_AD_BLEED = "Реклама в минус"
ANOMALY_LOSS = "Убыток после расходов ВБ"
ANOMALY_NO_COST = "Нет себестоимости"
ANOMALY_SUSPICIOUS_COST = "Подозрительная себестоимость"
ANOMALY_THIN_MARKUP = "Низкая наценка"
ANOMALY_DEAD_STOCK = "Залежавшийся остаток"

_DEAD_STOCK_DAYS = 365  # >1 года до распродажи остатка = неликвид
_NEW_PRODUCT_DAYS = 30  # first_sale_date пуст/в пределах N дней = новинка (раскачка, не неликвид)


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
            func.max(Nomenclature.first_sale_date).label("first_sale_date"),
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
            "first_sale_date": r.first_sale_date,
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


async def _load_wb_stock_map(db: AsyncSession, pid: int) -> dict[int, int]:
    """nm_id → остаток на складах ВБ (Σ quantity_full, включая товар в пути)."""
    rows = await db.execute(
        select(WbWarehouseStock.nm_id, func.sum(WbWarehouseStock.quantity_full).label("qty"))
        .where(WbWarehouseStock.project_id == pid)
        .group_by(WbWarehouseStock.nm_id)
        .limit(_MAX_ROWS)
    )
    return {r.nm_id: int(r.qty or 0) for r in rows}


async def _load_pipeline_stock(db: AsyncSession, pid: int) -> dict[int, dict[str, int]]:
    """nm_id → {own, assembly, transit}: наш склад + в сборке + в пути на ВБ.

    own = Σ WarehouseStock.quantity (наш склад); assembly = активные сборки
    (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED, до отгрузки); transit = SHIPPED
    (отгружено, едет на ВБ, ещё не принято).
    """
    out: dict[int, dict[str, int]] = defaultdict(lambda: {"own": 0, "assembly": 0, "transit": 0})

    own = await db.execute(
        select(
            Nomenclature.article_wb.label("nm_id"),
            func.coalesce(func.sum(WarehouseStock.quantity), 0).label("qty"),
        )
        .join(WarehouseStock, WarehouseStock.nomenclature_id == Nomenclature.id)
        .where(
            Nomenclature.project_id == pid,
            WarehouseStock.project_id == pid,
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb)
        .limit(_MAX_ROWS)
    )
    for r in own:
        out[r.nm_id]["own"] = int(r.qty or 0)

    active = [
        AssemblyStatus.PENDING,
        AssemblyStatus.IN_PROGRESS,
        AssemblyStatus.READY,
        AssemblyStatus.VEHICLE_ASSIGNED,
    ]
    asm = await db.execute(
        select(
            Nomenclature.article_wb.label("nm_id"),
            AssemblyRequest.status,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .where(
            AssemblyRequest.project_id == pid,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.status.in_([*active, AssemblyStatus.SHIPPED]),
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb, AssemblyRequest.status)
        .limit(_MAX_ROWS)
    )
    for ar in asm:
        key = "transit" if ar.status == AssemblyStatus.SHIPPED else "assembly"
        out[ar.nm_id][key] += int(ar.qty or 0)

    return dict(out)


_ELASTICITY_DAYS = 90  # окно истории для оценки эластичности
_ELASTICITY_MIN_POINTS = 6  # минимум дней с продажами и ценой
_ELASTICITY_MIN_DISTINCT = 3  # минимум различных уровней цены
_ELASTICITY_MIN_CV = 0.03  # минимальная вариация цены (коэф. вариации)


async def _load_elasticity_map(db: AsyncSession, pid: int) -> dict[int, float]:
    """nm_id → эластичность спроса по цене (оценка лог-лог OLS по дневной истории).

    Берём последние 90 дней (price=avg_price, qty=orders_count). Считаем только
    для артикулов с реальной вариацией цены (≥3 уровня, CV≥3%, ≥6 точек) — иначе
    наклон не определён. E < 0 норма (спрос падает с ростом цены). Оценка грубая —
    короткое окно и шумные данные ВБ; для направления решения, не для точного числа.
    """
    start = utcnow().date() - timedelta(days=_ELASTICITY_DAYS)
    rows = await db.execute(
        select(WbFunnelDaily.nm_id, WbFunnelDaily.avg_price, WbFunnelDaily.orders_count)
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.date >= start,
            WbFunnelDaily.avg_price > 0,
            WbFunnelDaily.orders_count > 0,
        )
        .limit(_MAX_ROWS * 4)
    )
    series: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        series[r.nm_id].append((float(r.avg_price), float(r.orders_count)))

    out: dict[int, float] = {}
    for nm_id, pts in series.items():
        if len(pts) < _ELASTICITY_MIN_POINTS:
            continue
        prices = [p for p, _ in pts]
        if len({round(p) for p in prices}) < _ELASTICITY_MIN_DISTINCT:
            continue
        mean_p = sum(prices) / len(prices)
        if mean_p <= 0:
            continue
        var_p = sum((p - mean_p) ** 2 for p in prices) / len(prices)
        if (var_p**0.5) / mean_p < _ELASTICITY_MIN_CV:
            continue
        xs = [math.log(p) for p, _ in pts]
        ys = [math.log(q) for _, q in pts]
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            continue
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        slope = sxy / sxx
        # эластичность спроса нормально ОТРИЦАТЕЛЬНА; положительный наклон =
        # смешение факторов (сезон/акции) → не используем как ценовой сигнал.
        if slope >= 0:
            continue
        out[nm_id] = round(max(-20.0, slope), 2)  # клампим экстремум (шум короткого окна)
    return out


def _classify_anomaly(r: PricingRow) -> str | None:
    """Метка аномалии (самое серьёзное первым) или None для нормальной строки."""
    if r.has_price and r.has_cost and r.current_price is not None and r.cost_price is not None and r.current_price < r.cost_price:
        return ANOMALY_PRICE_BELOW_COST
    if r.orders_count > 0 and r.profit < 0:
        # реклама — причина убытка? (без рекламы юнит был бы в плюсе)
        if r.profit + r.adv_sum > 0:
            return ANOMALY_AD_BLEED
        return ANOMALY_LOSS
    if r.has_price and not r.has_cost:
        return ANOMALY_NO_COST
    if r.cost_share_pct is not None and r.cost_share_pct < _SUSPICIOUS_COST_SHARE_PCT:
        return ANOMALY_SUSPICIOUS_COST
    if r.markup_pct is not None and 0 <= r.markup_pct < _THIN_MARKUP_PCT:
        return ANOMALY_THIN_MARKUP
    # неликвид: остаток есть, а продаж нет / распродажа дольше года.
    # Новинки исключаем — у них мало продаж из-за раскачки, а не из-за неликвида.
    if (
        not r.is_new
        and r.total_stock > 0
        and (r.orders_count == 0 or (r.days_left is not None and r.days_left > _DEAD_STOCK_DAYS))
    ):
        return ANOMALY_DEAD_STOCK
    return None


def _recommend(r: PricingRow) -> str:
    """Короткая авто-рекомендация по цене/остатку на основе метрик строки."""
    a = r.anomaly
    if a == ANOMALY_PRICE_BELOW_COST:
        return "Срочно поднять цену"
    if a == ANOMALY_AD_BLEED:
        return f"Сократить рекламу — ДРР {r.drr:.0f}%, ест прибыль"
    if a == ANOMALY_LOSS:
        # убыток не из-за рекламы: либо цена низкая, либо неликвид
        if r.total_stock > 0 and (r.orders_count == 0 or (r.days_left is not None and r.days_left > _DEAD_STOCK_DAYS)):
            return "Убыток + не продаётся → распродать скидкой на ВБ"
        if r.markup_pct is not None and r.markup_pct < _THIN_MARKUP_PCT:
            return "Поднять цену / снизить себест."
        return "Пересмотреть юнит-экономику"
    if a == ANOMALY_NO_COST:
        return "Заполнить себестоимость"
    if a == ANOMALY_SUSPICIOUS_COST:
        return "Проверить себестоимость"
    if a == ANOMALY_THIN_MARKUP:
        return "Поднять цену"
    if a == ANOMALY_DEAD_STOCK:
        return "Снизить цену / акция — распродать на ВБ"
    # новинка без аномалий — раскачивать спрос, а не судить как неликвид
    if r.is_new:
        return "Новинка — раскачивать (реклама/контент/конкур. цена)"
    # эластичность спроса → направление цены
    if r.elasticity_label == "неэластичный":
        return "Неэласт. спрос — поднять цену"
    if r.optimal_price is not None and r.current_price:
        if r.optimal_price > r.current_price * 1.05:
            return "Поднять цену (эластичность)"
        if r.optimal_price < r.current_price * 0.95:
            return "Снизить цену (эластичность)"
    # сигналы по обороту/остатку
    if r.days_left is not None and r.days_left < 14 and r.markup_pct is not None and r.markup_pct > 80:
        return "Высокий спрос — поднять цену / пополнить"
    if r.days_left is not None and r.days_left > 120:
        return "Затоварено — снизить цену"
    return "OK"


def _build_row(
    nm_id: int,
    price: WbPrice | None,
    funnel: dict | None,
    cost: float | None,
    cat_overrides: dict[int, str],
    meta: dict | None,
    wb_stock: int = 0,
    period_days: int = 30,
    elasticity: float | None = None,
    today: _date | None = None,
    extra: dict | None = None,
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
    orders_count = int(f.get("orders_count") or 0)

    adv_sum = round(float(f.get("adv_sum") or 0), 2)
    tax = round(float(f.get("tax") or 0), 2)

    # Новинка: first_sale_date пуст или в пределах окна раскачки (не неликвид)
    fsd = m.get("first_sale_date")
    _today = today or _date.today()
    is_new = fsd is None or fsd >= _today - timedelta(days=_NEW_PRODUCT_DAYS)

    # Остатки по локациям: ВБ + наш склад + в сборке + в пути на ВБ = весь товар.
    # Метрики капитала/оборота считаем по ВСЕМ остаткам — для точного анализа.
    e = extra or {}
    own_stock = int(e.get("own", 0))
    assembly_stock = int(e.get("assembly", 0))
    transit_stock = int(e.get("transit", 0))
    total_stock = wb_stock + own_stock + assembly_stock + transit_stock

    stock_value_cost = round(total_stock * cost_price, 2) if has_cost and cost_price is not None else None
    profit_per_unit = (profit / orders_count) if orders_count > 0 else None
    stock_potential_profit = (
        round(total_stock * profit_per_unit, 2) if profit_per_unit is not None and total_stock > 0 else None
    )
    stock_potential_revenue = (
        round(total_stock * current_price, 2) if has_price and current_price is not None and total_stock > 0 else None
    )
    avg_daily = orders_count / period_days if period_days > 0 else 0
    days_left = round(total_stock / avg_daily, 1) if avg_daily > 0 and total_stock > 0 else None
    sales_per_month = round(avg_daily * 30, 1) if avg_daily > 0 else None

    # ДРР и точка безубыточности
    drr = round(adv_sum / revenue * 100, 2) if revenue > 0 else 0
    # цена, при которой прибыль = 0: масштабируем текущую цену так, чтобы
    # ценозависимый нетто-вклад (выручка − удержания ВБ − налог) покрыл fixed-затраты
    # (себестоимость + реклама). Учитывает СПП/комиссию/налог через realized-доли.
    # Безубыток считаем БЕЗ рекламы: реклама — отдельный (дискреционный) рычаг,
    # а не ценовая себестоимость единицы. Иначе на товаре с большим ДРР и парой
    # продаж точка безубытка раздувается до абсурда. Это «ценовой пол»: цена,
    # покрывающая себестоимость + удержания ВБ + налог.
    net_contrib = revenue - wb_expenses - tax  # ≈ к перечислению минус налог
    breakeven_price = None
    if has_price and current_price is not None and revenue > 0 and net_contrib > 0:
        breakeven_price = round(current_price * cost_total / net_contrib, 2)

    # Запас прочности по цене: насколько можно снижать до нулевой прибыли
    safety_margin_pct = (
        round((current_price - breakeven_price) / current_price * 100, 2)
        if breakeven_price is not None and has_price and current_price
        else None
    )
    # GMROI: валовая маржа за период / заморожено в остатке (бенчмарк ≥3)
    gross_profit = revenue - cost_total
    gmroi = round(gross_profit / stock_value_cost, 2) if stock_value_cost and stock_value_cost > 0 else None
    # Sell-through: доля распроданного от (продано + весь остаток)
    sold_plus_stock = orders_count + total_stock
    sell_through_pct = round(orders_count / sold_plus_stock * 100, 1) if sold_plus_stock > 0 else None

    # Эластичность спроса → ярлык + оценка цены под макс. прибыль
    elasticity_label = ""
    optimal_price = None
    if elasticity is not None:
        ae = abs(elasticity)
        if ae >= 1.0:
            elasticity_label = "эластичный"
            if breakeven_price is not None and ae > 1.0:
                raw = breakeven_price * ae / (ae - 1.0)  # Лернер: опт. наценка над «ценой безубытка»
                cap = current_price * 3 if has_price and current_price else raw
                optimal_price = round(min(raw, cap), 2)
        else:
            elasticity_label = "неэластичный"  # спрос слабо реагирует → можно поднимать цену
            if has_price and current_price:
                optimal_price = round(current_price * 1.15, 2)

    row = PricingRow(
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
        orders_count=orders_count,
        revenue=round(revenue, 2),
        wb_expenses=wb_expenses,
        adv_sum=adv_sum,
        tax=tax,
        cost_total=round(cost_total, 2),
        profit=round(profit, 2),
        margin_pct=round(float(f.get("margin") or 0), 2),
        net_markup_pct=net_markup_pct,
        wb_stock=wb_stock,
        own_stock=own_stock,
        assembly_stock=assembly_stock,
        transit_stock=transit_stock,
        total_stock=total_stock,
        is_new=is_new,
        stock_value_cost=stock_value_cost,
        stock_potential_profit=stock_potential_profit,
        stock_potential_revenue=stock_potential_revenue,
        days_left=days_left,
        sales_per_month=sales_per_month,
        drr=drr,
        breakeven_price=breakeven_price,
        safety_margin_pct=safety_margin_pct,
        gmroi=gmroi,
        sell_through_pct=sell_through_pct,
        elasticity=elasticity,
        elasticity_label=elasticity_label,
        optimal_price=optimal_price,
    )
    row.anomaly = _classify_anomaly(row)
    row.recommendation = _recommend(row)
    return row


def _apply_filters(
    rows: list[PricingRow],
    brand: str | None,
    category: str | None,
    search: str | None,
    min_orders: int,
    only_in_stock: bool = False,
    anomaly_only: bool = False,
) -> list[PricingRow]:
    out = rows
    if only_in_stock:
        out = [r for r in out if r.wb_stock > 0]
    if anomaly_only:
        out = [r for r in out if r.anomaly]
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


def _assign_abc(rows: list[PricingRow]) -> None:
    """ABC-класс по доле выручки (Парето): A ≤80%, B ≤95%, остальное C."""
    total = sum(r.revenue for r in rows)
    if total <= 0:
        for r in rows:
            r.abc = None
        return
    cum = 0.0
    for r in sorted(rows, key=lambda x: x.revenue, reverse=True):
        if r.revenue <= 0:
            r.abc = "C"
            continue
        cum += r.revenue
        share = cum / total
        r.abc = "A" if share <= 0.8 else ("B" if share <= 0.95 else "C")


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
                wb_stock=sum(r.wb_stock for r in items),
                stock_value_cost=round(sum(r.stock_value_cost or 0 for r in items), 2),
                children=items,
            )
        )
    groups.sort(key=lambda g: g.revenue, reverse=True)
    return groups


# Порядок аномалий в выдаче (от критичных к информационным)
_ANOMALY_ORDER = [
    ANOMALY_PRICE_BELOW_COST,
    ANOMALY_AD_BLEED,
    ANOMALY_LOSS,
    ANOMALY_NO_COST,
    ANOMALY_SUSPICIOUS_COST,
    ANOMALY_THIN_MARKUP,
    ANOMALY_DEAD_STOCK,
]


def _group_by_anomaly(rows: list[PricingRow]) -> list[PricingGroup]:
    """Только аномальные строки, сгруппированные по типу аномалии."""
    buckets: dict[str, list[PricingRow]] = defaultdict(list)
    for r in rows:
        if r.anomaly:
            buckets[r.anomaly].append(r)

    groups: list[PricingGroup] = []
    for label in _ANOMALY_ORDER:
        items = buckets.get(label)
        if not items:
            continue
        coef, pct, share = _portfolio_markup(items)
        revenue = sum(r.revenue for r in items)
        profit = sum(r.profit for r in items)
        items.sort(key=lambda r: (r.wb_stock, -(r.profit or 0)), reverse=True)
        groups.append(
            PricingGroup(
                category=label,
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
                wb_stock=sum(r.wb_stock for r in items),
                stock_value_cost=round(sum(r.stock_value_cost or 0 for r in items), 2),
                children=items,
            )
        )
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
        wb_stock_units=sum(r.wb_stock for r in rows),
        total_stock_units=sum(r.total_stock for r in rows),
        stock_value_cost=round(sum(r.stock_value_cost or 0 for r in rows), 2),
        anomalies=sum(1 for r in rows if r.anomaly),
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
    wb_stock_map = await _load_wb_stock_map(db, project_id)
    pipeline_map = await _load_pipeline_stock(db, project_id)
    elasticity_map = await _load_elasticity_map(db, project_id)

    # длина периода (для темпа продаж / дней до исчерпания)
    period_days = 30
    if date_from and date_to:
        try:
            period_days = max(1, (_date.fromisoformat(date_to) - _date.fromisoformat(date_from)).days + 1)
        except ValueError:
            period_days = 30

    # универсум: с ценой ∪ продававшиеся ∪ остаток на ВБ ∪ наш склад/сборка/в пути
    today = utcnow().date()
    nm_ids = set(price_by_nm) | set(funnel_by_nm) | set(wb_stock_map) | set(pipeline_map)
    rows = []
    for nm_id in nm_ids:
        meta = meta_map.get(nm_id) or {}
        cost = _resolve_cost(nm_id, meta, avg_costs, overrides)
        rows.append(
            _build_row(
                nm_id,
                price_by_nm.get(nm_id),
                funnel_by_nm.get(nm_id),
                cost,
                cat_overrides,
                meta,
                wb_stock=wb_stock_map.get(nm_id, 0),
                period_days=period_days,
                elasticity=elasticity_map.get(nm_id),
                today=today,
                extra=pipeline_map.get(nm_id),
            ).model_dump()
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
    only_in_stock: bool = False,
    anomaly_only: bool = False,
    group_by: str = "category",
) -> dict:
    """Наценка по артикулам: текущая цена ВБ + себестоимость + расходы ВБ.

    Универсум строк = артикулы с ценой (WbPrice) ∪ продававшиеся за период
    (воронка) ∪ с остатком на ВБ. only_in_stock=True оставляет только остаток>0.
    group_by: category | sku | anomaly (только аномальные, по типу).
    """
    raw = await _compute_rows(db, project_id, date_from=date_from, date_to=date_to)
    rows = [PricingRow(**r) for r in raw["rows"]]
    rows = _apply_filters(rows, brand, category, search, min_orders, only_in_stock, anomaly_only)
    _assign_abc(rows)  # ABC по текущему (отфильтрованному) срезу
    summary = _build_summary(rows)
    synced = raw["price_synced_at"]
    has_bdr = raw["has_bdr"]

    if group_by == "sku":
        _sort_by_markup(rows)
        resp = PricingResponse(
            group_by="sku", data_rows=rows, data_groups=[], summary=summary,
            price_synced_at=synced, has_bdr=has_bdr,
        )
    elif group_by == "anomaly":
        resp = PricingResponse(
            group_by="anomaly", data_rows=[], data_groups=_group_by_anomaly(rows),
            summary=summary, price_synced_at=synced, has_bdr=has_bdr,
        )
    else:
        resp = PricingResponse(
            group_by="category", data_rows=[], data_groups=_group_by_category(rows),
            summary=summary, price_synced_at=synced, has_bdr=has_bdr,
        )
    return resp.model_dump(mode="json")
