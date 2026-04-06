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
    await db.delete(obj)
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
        current_day = min(today.day, days_in_month)
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
    if today.year == year and today.month == month:
        current_day = min(today.day, days_in_month)
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

    # Current day within the range
    if today >= last_end:
        current_day = total_days
    elif today < first_start:
        current_day = 0
    else:
        current_day = (today - first_start).days + 1

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

    if today >= last_end:
        current_day = total_days
    elif today < first_start:
        current_day = 0
    else:
        current_day = (today - first_start).days + 1

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

    return rows
