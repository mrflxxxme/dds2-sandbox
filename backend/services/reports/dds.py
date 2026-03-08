"""
Reports — DDS month and PnL reports.
"""

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Transaction
from backend.cache import cached


@cached(prefix="reports:dds_month", ttl=300)
async def get_dds_month(
    db: AsyncSession,
    project_id: int,
    year: int,
    month: int,
    currency: str = "RUB",
) -> list[dict]:
    """DDS grouped by cat_lvl1_2/cat_lvl2_2 for a given month."""
    month_start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    month_end = date(year, month, last_day)

    result = await db.execute(
        select(
            Transaction.cat_lvl1_2,
            Transaction.cat_lvl2_2,
            func.sum(Transaction.income).label("income"),
            func.sum(Transaction.expense).label("expense"),
            func.sum(Transaction.net).label("net"),
        )
        .where(
            and_(
                Transaction.project_id == project_id,
                Transaction.date >= month_start,
                Transaction.date <= month_end,
                Transaction.currency == currency,
                Transaction.is_cashflow2 == 1,
            )
        )
        .group_by(Transaction.cat_lvl1_2, Transaction.cat_lvl2_2)
        .order_by(Transaction.cat_lvl1_2, Transaction.cat_lvl2_2)
    )

    rows = []
    for row in result:
        rows.append({
            "cat_lvl1": row.cat_lvl1_2,
            "cat_lvl2": row.cat_lvl2_2,
            "income": float(row.income or 0),
            "expense": float(row.expense or 0),
            "net": float(row.net or 0),
        })
    return rows


async def get_dds_pnl(
    db: AsyncSession,
    project_id: int,
    year: int,
) -> dict:
    """
    PnL-style DDS report: categories × months with counterparty drill-down.
    CNY amounts converted to RUB via fx_service.
    """
    from backend.services import fx_service

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    # Load FX rates
    rates_map = await fx_service.get_rates_map(db, project_id, year_start, year_end)
    avg_rate = (sum(rates_map.values()) / len(rates_map)) if rates_map else 1.0

    # Fetch all cashflow transactions for the year
    result = await db.execute(
        select(
            func.extract("month", Transaction.date).label("m"),
            Transaction.cat_lvl1_2,
            Transaction.counterparty,
            Transaction.currency,
            func.sum(Transaction.income).label("income"),
            func.sum(Transaction.expense).label("expense"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,
            Transaction.is_cashflow2 == 1,
            Transaction.date >= year_start,
            Transaction.date <= year_end,
        )
        .group_by(
            func.extract("month", Transaction.date),
            Transaction.cat_lvl1_2,
            Transaction.counterparty,
            Transaction.currency,
        )
    )
    rows = result.all()

    # Build month labels (only months with data)
    active_months: set[int] = set()
    for r in rows:
        active_months.add(int(r.m))
    month_names = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
    }
    months = sorted(active_months)
    month_labels = [{"key": m, "label": f"{month_names[m]} {year}"} for m in months]

    # Aggregate: category → counterparty → month → amounts
    cat_cp_data: dict = {}  # {cat: {cp: {month: {income, expense}}}}
    revenue_by_month: dict = {m: 0.0 for m in months}
    revenue_by_month["total"] = 0.0

    for r in rows:
        m = int(r.m)
        cat = r.cat_lvl1_2 or "Без категории"
        cp = r.counterparty or "—"
        inc = float(r.income or 0)
        exp = float(r.expense or 0)

        # CNY → RUB conversion
        if r.currency == "CNY":
            inc *= avg_rate
            exp *= avg_rate

        if cat not in cat_cp_data:
            cat_cp_data[cat] = {}
        if cp not in cat_cp_data[cat]:
            cat_cp_data[cat][cp] = {mm: {"income": 0.0, "expense": 0.0} for mm in months}
            cat_cp_data[cat][cp]["total"] = {"income": 0.0, "expense": 0.0}

        cat_cp_data[cat][cp][m]["income"] += inc
        cat_cp_data[cat][cp][m]["expense"] += exp
        cat_cp_data[cat][cp]["total"]["income"] += inc
        cat_cp_data[cat][cp]["total"]["expense"] += exp

        revenue_by_month[m] += inc
        revenue_by_month["total"] += inc

    # Build categories output
    categories = []
    total_expense_by_month = {m: 0.0 for m in months}
    total_expense_by_month["total"] = 0.0
    total_income_by_month = {m: 0.0 for m in months}
    total_income_by_month["total"] = 0.0

    for cat_name, cp_dict in sorted(cat_cp_data.items()):
        # Determine if this category is income or expense
        cat_total_income = sum(
            sum(cp_months[m]["income"] for m in months)
            for cp_months in cp_dict.values()
        )
        cat_total_expense = sum(
            sum(cp_months[m]["expense"] for m in months)
            for cp_months in cp_dict.values()
        )
        is_income = cat_total_income > cat_total_expense

        # Category monthly totals
        cat_monthly = {}
        for m in months:
            val = sum(cp_months[m]["income" if is_income else "expense"] for cp_months in cp_dict.values())
            cat_monthly[str(m)] = round(val, 2)
            if is_income:
                total_income_by_month[m] += val
            else:
                total_expense_by_month[m] += val
        cat_total = sum(cp_months["total"]["income" if is_income else "expense"] for cp_months in cp_dict.values())
        cat_monthly["total"] = round(cat_total, 2)
        if is_income:
            total_income_by_month["total"] += cat_total
        else:
            total_expense_by_month["total"] += cat_total

        # Counterparties
        counterparties = []
        for cp_name, cp_months in sorted(cp_dict.items(), key=lambda x: -x[1]["total"]["income" if is_income else "expense"]):
            cp_monthly = {}
            for m in months:
                cp_monthly[str(m)] = round(cp_months[m]["income" if is_income else "expense"], 2)
            cp_monthly["total"] = round(cp_months["total"]["income" if is_income else "expense"], 2)
            if cp_monthly["total"] > 0:
                counterparties.append({
                    "name": cp_name,
                    "monthly": cp_monthly,
                })

        categories.append({
            "name": cat_name,
            "type": "income" if is_income else "expense",
            "monthly": cat_monthly,
            "counterparties": counterparties,
        })

    # Sort: income categories first, then expenses by total desc
    categories.sort(key=lambda c: (0 if c["type"] == "income" else 1, -c["monthly"]["total"]))

    # Revenue & summary
    revenue = {str(m): round(revenue_by_month[m], 2) for m in months}
    revenue["total"] = round(revenue_by_month["total"], 2)

    total_exp = {str(m): round(total_expense_by_month[m], 2) for m in months}
    total_exp["total"] = round(total_expense_by_month["total"], 2)

    total_inc = {str(m): round(total_income_by_month[m], 2) for m in months}
    total_inc["total"] = round(total_income_by_month["total"], 2)

    net_profit = {}
    for m in months:
        net_profit[str(m)] = round(total_income_by_month[m] - total_expense_by_month[m], 2)
    net_profit["total"] = round(total_income_by_month["total"] - total_expense_by_month["total"], 2)

    return {
        "months": month_labels,
        "revenue": revenue,
        "categories": categories,
        "summary": {
            "total_income": total_inc,
            "total_expense": total_exp,
            "net_profit": net_profit,
        },
    }
