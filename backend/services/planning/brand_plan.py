"""
Brand Plan service — CRUD for monthly revenue plans per brand + plan-fact analysis.
"""

import calendar
import logging
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.planning import BrandPlan
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


async def get_brand_plans(db: AsyncSession, project_id: int, year: int) -> list[BrandPlan]:
    result = await db.execute(
        select(BrandPlan)
        .where(BrandPlan.project_id == project_id, BrandPlan.year == year)
        .order_by(BrandPlan.brand, BrandPlan.month)
        .limit(500)
    )
    return list(result.scalars().all())


async def upsert_brand_plan(
    db: AsyncSession,
    project_id: int,
    brand: str,
    year: int,
    month: int,
    plan_amount: Decimal,
) -> BrandPlan:
    result = await db.execute(
        select(BrandPlan).where(
            BrandPlan.project_id == project_id,
            BrandPlan.brand == brand,
            BrandPlan.year == year,
            BrandPlan.month == month,
        )
    )
    obj = result.scalar_one_or_none()
    if obj:
        obj.plan_amount = plan_amount
        obj.created_at = utcnow()
    else:
        obj = BrandPlan(
            project_id=project_id,
            brand=brand,
            year=year,
            month=month,
            plan_amount=plan_amount,
            created_at=utcnow(),
        )
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    await invalidate_cache(f"reports:plan_fact:project_id={project_id}")
    return obj


async def delete_brand_plan(db: AsyncSession, project_id: int, plan_id: int) -> bool:
    result = await db.execute(select(BrandPlan).where(BrandPlan.id == plan_id, BrandPlan.project_id == project_id))
    obj = result.scalar_one_or_none()
    if not obj:
        return False
    await db.delete(obj)  # no-soft-delete-check: BrandPlan has no SoftDeleteMixin
    await db.commit()
    await invalidate_cache(f"reports:plan_fact:project_id={project_id}")
    return True


async def get_wb_brands(db: AsyncSession, project_id: int) -> list[str]:
    result = await db.execute(
        text(
            "SELECT DISTINCT brand_name FROM wb_finance_rows "
            "WHERE project_id = :pid AND brand_name IS NOT NULL AND brand_name != '' "
            "ORDER BY brand_name"
        ),
        {"pid": project_id},
    )
    return [r[0] for r in result]


async def _get_fact_daily(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    start: date,
    end: date,
) -> dict[date, Decimal]:
    brand_clause = "AND brand_name = :brand" if brand else ""
    params: dict = {"pid": project_id, "start": start, "end": end}
    if brand:
        params["brand"] = brand

    result = await db.execute(
        text(
            f"SELECT COALESCE(sale_dt, rr_dt) AS dt, "
            f"  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' "
            f"    THEN retail_price_withdisc_rub ELSE 0 END), 0) "
            f"  - COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' "
            f"    THEN retail_price_withdisc_rub ELSE 0 END), 0) AS realization "
            f"FROM wb_finance_rows "
            f"WHERE project_id = :pid AND COALESCE(sale_dt, rr_dt) BETWEEN :start AND :end "
            f"{brand_clause} "
            f"GROUP BY dt ORDER BY dt"
        ),
        params,
    )
    return {row[0]: Decimal(str(row[1])) for row in result}


async def _get_month_fact_total(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    year: int,
    month: int,
) -> Decimal:
    days_in_month = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days_in_month)
    daily = await _get_fact_daily(db, project_id, brand, start, end)
    return sum(daily.values(), ZERO)


async def _get_last_fact_date(
    db: AsyncSession,
    project_id: int,
    start: date,
    end: date,
) -> date | None:
    """Last date in [start, end] with any sale/return fact in wb_finance_rows.

    Drives the forecast denominator: WB data lags a day or two, and if the
    container runs in UTC the `date.today()` can race ahead of the data, so
    dividing by the calendar day instead of the last day with data produces
    an off-by-one forecast.
    """
    result = await db.execute(
        text(
            "SELECT MAX(COALESCE(sale_dt, rr_dt)) FROM wb_finance_rows "
            "WHERE project_id = :pid "
            "AND COALESCE(sale_dt, rr_dt) BETWEEN :start AND :end"
        ),
        {"pid": project_id, "start": start, "end": end},
    )
    return result.scalar()


async def _get_plan_amount(
    db: AsyncSession,
    project_id: int,
    brand: str,
    year: int,
    month: int,
) -> Decimal:
    result = await db.execute(
        select(BrandPlan.plan_amount).where(
            BrandPlan.project_id == project_id,
            BrandPlan.brand == brand,
            BrandPlan.year == year,
            BrandPlan.month == month,
        )
    )
    row = result.scalar_one_or_none()
    return Decimal(str(row)) if row else ZERO


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


async def _get_unplanned_brand_facts(
    db: AsyncSession,
    project_id: int,
    start: date,
    end: date,
    planned_brands: set[str],
) -> list[tuple[str, Decimal]]:
    """Выручка по брендам БЕЗ плана за [start, end] — нужна, чтобы итог план-факта
    совпадал по сумме ОПиУ и прогноз не терял эти продажи.

    Возвращает [(brand_label, fact)] для брендов, которых нет в `planned_brands`
    (включая бакет «(без бренда)» для пустого brand_name), отсортированных
    по убыванию факта. `end` ограничивается сверху сегодняшним днём — симметрично
    `fact_mtd` плановых брендов (будущих строк в WB нет, но для прошедших периодов
    это не повредит)."""
    result = await db.execute(
        text(
            "SELECT COALESCE(NULLIF(brand_name, ''), '') AS brand, "
            "  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub "
            "    WHEN doc_type_name = 'Возврат' THEN -retail_price_withdisc_rub ELSE 0 END), 0) AS realization "
            "FROM wb_finance_rows "
            "WHERE project_id = :pid AND COALESCE(sale_dt, rr_dt) BETWEEN :start AND :end "
            "GROUP BY 1"
        ),
        {"pid": project_id, "start": start, "end": end},
    )
    out: list[tuple[str, Decimal]] = []
    for row in result:
        brand = row[0]
        fact = Decimal(str(row[1]))
        if fact == ZERO or (brand and brand in planned_brands):
            continue
        out.append((brand or "(без бренда)", fact))
    return sorted(out, key=lambda x: x[1], reverse=True)


def _unplanned_row(brand: str, fact: Decimal, days: int, current_day: int) -> dict:
    """Строка план-факта для бренда без плана: план=0, прогноз по тому же темпу."""
    forecast = float(fact / current_day * days) if current_day > 0 else 0.0
    return {
        "brand": brand,
        "plan_month": 0.0,
        "debt_prev": 0.0,
        "surplus_prev": 0.0,
        "plan_adjusted": 0.0,
        "fact_mtd": float(fact),
        "pct": None,
        "forecast": round(forecast, 2),
        "days_in_month": days,
        "current_day": current_day,
        "no_plan": True,
    }


@cached(prefix="reports:plan_fact", ttl=300)
async def get_plan_fact_daily(
    db: AsyncSession,
    project_id: int,
    brand: str,
    year: int,
    month: int,
) -> dict:
    days_in_month = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days_in_month)

    daily_fact = await _get_fact_daily(db, project_id, brand, start, end)
    plan_month = await _get_plan_amount(db, project_id, brand, year, month)

    prev_y, prev_m = _prev_month(year, month)
    plan_prev = await _get_plan_amount(db, project_id, brand, prev_y, prev_m)
    fact_prev = await _get_month_fact_total(db, project_id, brand, prev_y, prev_m)
    # Двусторонний перенос: долг (недовыполнение) или бонус (перевыполнение)
    adjustment_prev = plan_prev - fact_prev if plan_prev > ZERO else ZERO
    plan_adjusted = max(ZERO, plan_month + adjustment_prev)

    today = date.today()
    if today.year == year and today.month == month:
        # Current month: drive current_day by the last day with actual WB data,
        # not today.day — WB data lags and may trail the calendar day.
        last_fact_day = max((dt.day for dt in daily_fact), default=0)
        current_day = min(last_fact_day, days_in_month) if last_fact_day else 0
    elif date(year, month, 1) < today:
        current_day = days_in_month
    else:
        current_day = 0

    rows = []
    fact_cumulative = ZERO
    for day_num in range(1, days_in_month + 1):
        dt = date(year, month, day_num)
        is_future = dt > today

        fact_day = daily_fact.get(dt, ZERO) if not is_future else ZERO
        if not is_future:
            fact_cumulative += fact_day

        remaining = days_in_month - day_num
        if not is_future:
            plan_day = (plan_adjusted - fact_cumulative) / remaining if remaining > 0 else ZERO
        else:
            # Для будущих дней: равномерно распределяем остаток
            future_remaining = days_in_month - current_day
            plan_day = (plan_adjusted - fact_cumulative) / future_remaining if future_remaining > 0 else ZERO

        plan_cumulative = plan_adjusted * day_num / days_in_month
        pct = float(fact_cumulative / plan_cumulative * 100) if plan_cumulative > 0 else None

        rows.append(
            {
                "dt": dt.isoformat(),
                "fact_day": float(fact_day),
                "plan_day": float(max(ZERO, plan_day)),
                "fact_cumulative": float(fact_cumulative),
                "plan_cumulative": float(plan_cumulative),
                "pct": round(pct, 1) if pct is not None else None,
                "is_future": is_future,
            }
        )

    fact_mtd = fact_cumulative
    forecast = float(fact_mtd / current_day * days_in_month) if current_day > 0 else 0
    pct_total = float(fact_mtd / plan_adjusted * 100) if plan_adjusted > 0 else None

    debt_prev = max(ZERO, adjustment_prev)
    surplus_prev = max(ZERO, -adjustment_prev)

    return {
        "rows": rows,
        "forecast": round(forecast, 2),
        "plan_month": float(plan_month),
        "debt_prev": float(debt_prev),
        "surplus_prev": float(surplus_prev),
        "plan_adjusted": float(plan_adjusted),
        "fact_mtd": float(fact_mtd),
        "pct": round(pct_total, 1) if pct_total is not None else None,
        "days_in_month": days_in_month,
        "current_day": current_day,
    }


@cached(prefix="reports:plan_fact", ttl=300)
async def get_plan_fact_brands(
    db: AsyncSession,
    project_id: int,
    year: int,
    month: int,
) -> list[dict]:
    result = await db.execute(
        select(BrandPlan)
        .where(BrandPlan.project_id == project_id, BrandPlan.year == year, BrandPlan.month == month)
        .order_by(BrandPlan.brand)
        .limit(100)
    )
    plans = result.scalars().all()
    if not plans:
        return []

    days_in_month = calendar.monthrange(year, month)[1]
    today = date.today()
    start = date(year, month, 1)
    end = date(year, month, days_in_month)
    if today.year == year and today.month == month:
        # Current month: anchor on the last day with actual WB data for the
        # whole project — forecasts must not divide by days that have no facts.
        last_fact = await _get_last_fact_date(db, project_id, start, end)
        current_day = last_fact.day if last_fact else 0
    elif date(year, month, 1) < today:
        current_day = days_in_month
    else:
        current_day = 0

    prev_y, prev_m = _prev_month(year, month)
    rows = []
    for plan in plans:
        fact_mtd = await _get_month_fact_total(db, project_id, plan.brand, year, month)
        plan_prev = await _get_plan_amount(db, project_id, plan.brand, prev_y, prev_m)
        fact_prev = await _get_month_fact_total(db, project_id, plan.brand, prev_y, prev_m)

        adjustment_prev = plan_prev - fact_prev if plan_prev > ZERO else ZERO
        plan_adjusted = max(ZERO, plan.plan_amount + adjustment_prev)
        debt_prev = max(ZERO, adjustment_prev)
        surplus_prev = max(ZERO, -adjustment_prev)
        pct = float(fact_mtd / plan_adjusted * 100) if plan_adjusted > 0 else None
        forecast = float(fact_mtd / current_day * days_in_month) if current_day > 0 else 0

        rows.append(
            {
                "brand": plan.brand,
                "plan_month": float(plan.plan_amount),
                "debt_prev": float(debt_prev),
                "surplus_prev": float(surplus_prev),
                "plan_adjusted": float(plan_adjusted),
                "fact_mtd": float(fact_mtd),
                "pct": round(pct, 1) if pct is not None else None,
                "forecast": round(forecast, 2),
                "days_in_month": days_in_month,
                "current_day": current_day,
            }
        )

    # Бренды, имеющие продажи, но без плана (+ бакет «без бренда») → план=0, чтобы
    # «Итого Факт» совпадал по сумме ОПиУ и прогноз не терял эти продажи.
    end_eff = min(end, today)
    planned = {p.brand for p in plans}
    for brand, fact in await _get_unplanned_brand_facts(db, project_id, start, end_eff, planned):
        rows.append(_unplanned_row(brand, fact, days_in_month, current_day))

    return rows


def _iter_months(year_from: int, month_from: int, year_to: int, month_to: int) -> Iterator[tuple[int, int]]:
    """Yield (year, month) pairs from start to end inclusive."""
    y, m = year_from, month_from
    while (y, m) <= (year_to, month_to):
        yield y, m
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


@cached(prefix="reports:plan_fact", ttl=300)
async def get_plan_fact_daily_range(
    db: AsyncSession,
    project_id: int,
    brand: str,
    year_from: int,
    month_from: int,
    year_to: int,
    month_to: int,
) -> dict:
    """Plan-fact daily view across a range of months."""
    today = date.today()
    months_list = list(_iter_months(year_from, month_from, year_to, month_to))

    # Gather full date range
    first_start = date(year_from, month_from, 1)
    last_dim = calendar.monthrange(year_to, month_to)[1]
    last_end = date(year_to, month_to, last_dim)
    total_days = (last_end - first_start).days + 1

    daily_fact = await _get_fact_daily(db, project_id, brand, first_start, last_end)

    # Sum plan across all months + debt from month before first
    plan_total = ZERO
    for y, m in months_list:
        plan_total += await _get_plan_amount(db, project_id, brand, y, m)

    prev_y, prev_m = _prev_month(year_from, month_from)
    plan_prev = await _get_plan_amount(db, project_id, brand, prev_y, prev_m)
    fact_prev = await _get_month_fact_total(db, project_id, brand, prev_y, prev_m)
    adjustment_prev = plan_prev - fact_prev if plan_prev > ZERO else ZERO
    plan_adjusted = max(ZERO, plan_total + adjustment_prev)

    # Current day within the range. Anchor on last day with actual WB data
    # in the range so the forecast denominator matches reality (WB lags by
    # a day or two — dividing by the calendar day off-by-ones the forecast).
    if today >= last_end:
        current_day = total_days
    elif today < first_start:
        current_day = 0
    elif daily_fact:
        last_dt = max(daily_fact.keys())
        current_day = (last_dt - first_start).days + 1
    else:
        current_day = 0

    rows = []
    fact_cumulative = ZERO
    day_idx = 0
    for y, m in months_list:
        dim = calendar.monthrange(y, m)[1]
        for d in range(1, dim + 1):
            day_idx += 1
            dt = date(y, m, d)
            is_future = dt > today

            fact_day = daily_fact.get(dt, ZERO) if not is_future else ZERO
            if not is_future:
                fact_cumulative += fact_day

            remaining = total_days - day_idx
            if not is_future:
                plan_day = (plan_adjusted - fact_cumulative) / remaining if remaining > 0 else ZERO
            else:
                future_remaining = total_days - current_day
                plan_day = (plan_adjusted - fact_cumulative) / future_remaining if future_remaining > 0 else ZERO

            plan_cumulative = plan_adjusted * day_idx / total_days
            pct = float(fact_cumulative / plan_cumulative * 100) if plan_cumulative > 0 else None

            rows.append(
                {
                    "dt": dt.isoformat(),
                    "fact_day": float(fact_day),
                    "plan_day": float(max(ZERO, plan_day)),
                    "fact_cumulative": float(fact_cumulative),
                    "plan_cumulative": float(plan_cumulative),
                    "pct": round(pct, 1) if pct is not None else None,
                    "is_future": is_future,
                }
            )

    fact_mtd = fact_cumulative
    forecast = float(fact_mtd / current_day * total_days) if current_day > 0 else 0
    pct_total = float(fact_mtd / plan_adjusted * 100) if plan_adjusted > 0 else None

    debt_prev = max(ZERO, adjustment_prev)
    surplus_prev = max(ZERO, -adjustment_prev)

    return {
        "rows": rows,
        "forecast": round(forecast, 2),
        "plan_month": float(plan_total),
        "debt_prev": float(debt_prev),
        "surplus_prev": float(surplus_prev),
        "plan_adjusted": float(plan_adjusted),
        "fact_mtd": float(fact_mtd),
        "pct": round(pct_total, 1) if pct_total is not None else None,
        "days_in_month": total_days,
        "current_day": current_day,
    }


@cached(prefix="reports:plan_fact", ttl=300)
async def get_plan_fact_brands_range(
    db: AsyncSession,
    project_id: int,
    year_from: int,
    month_from: int,
    year_to: int,
    month_to: int,
) -> list[dict]:
    """Plan-fact brands summary across a range of months."""
    months_list = list(_iter_months(year_from, month_from, year_to, month_to))
    today = date.today()

    # Collect all brands that have plans in any month of the range
    brand_plans: dict[str, Decimal] = {}
    for y, m in months_list:
        result = await db.execute(
            select(BrandPlan)
            .where(
                BrandPlan.project_id == project_id,
                BrandPlan.year == y,
                BrandPlan.month == m,
            )
            .limit(100)
        )
        for plan in result.scalars().all():
            brand_plans[plan.brand] = brand_plans.get(plan.brand, ZERO) + plan.plan_amount

    if not brand_plans:
        return []

    first_start = date(year_from, month_from, 1)
    last_dim = calendar.monthrange(year_to, month_to)[1]
    last_end = date(year_to, month_to, last_dim)
    total_days = (last_end - first_start).days + 1

    # Current day: last WB data date in the range, fallback to calendar.
    if today >= last_end:
        current_day = total_days
    elif today < first_start:
        current_day = 0
    else:
        last_fact = await _get_last_fact_date(db, project_id, first_start, last_end)
        current_day = (last_fact - first_start).days + 1 if last_fact else 0

    prev_y, prev_m = _prev_month(year_from, month_from)

    rows = []
    for brand_name in sorted(brand_plans):
        plan_total = brand_plans[brand_name]

        # Fact across full range
        daily = await _get_fact_daily(db, project_id, brand_name, first_start, last_end)
        fact_mtd = sum(
            (v for dt, v in daily.items() if dt <= today),
            ZERO,
        )

        # Debt/surplus from month before the range
        plan_prev = await _get_plan_amount(db, project_id, brand_name, prev_y, prev_m)
        fact_prev = await _get_month_fact_total(db, project_id, brand_name, prev_y, prev_m)
        adjustment_prev = plan_prev - fact_prev if plan_prev > ZERO else ZERO
        plan_adjusted = max(ZERO, plan_total + adjustment_prev)
        debt_prev = max(ZERO, adjustment_prev)
        surplus_prev = max(ZERO, -adjustment_prev)

        pct = float(fact_mtd / plan_adjusted * 100) if plan_adjusted > 0 else None
        forecast = float(fact_mtd / current_day * total_days) if current_day > 0 else 0

        rows.append(
            {
                "brand": brand_name,
                "plan_month": float(plan_total),
                "debt_prev": float(debt_prev),
                "surplus_prev": float(surplus_prev),
                "plan_adjusted": float(plan_adjusted),
                "fact_mtd": float(fact_mtd),
                "pct": round(pct, 1) if pct is not None else None,
                "forecast": round(forecast, 2),
                "days_in_month": total_days,
                "current_day": current_day,
            }
        )

    # Бренды, имеющие продажи, но без плана (+ «без бренда») за весь диапазон → план=0.
    end_eff = min(last_end, today)
    for brand, fact in await _get_unplanned_brand_facts(db, project_id, first_start, end_eff, set(brand_plans)):
        rows.append(_unplanned_row(brand, fact, total_days, current_day))

    return rows
