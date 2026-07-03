"""
Reports — dashboard summary (KPIs, daily cashflow, expense breakdown).
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_, or_, text, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.models import Transaction, Account, OpeningBalance
from backend.models.planning import Order, PlannedPayment
from backend.cache import cached
from backend.services.reports._cogs import cogs_include_clause, get_cogs_keys

logger = logging.getLogger("dds.dashboard")

# ─── Income-type split (marketplace / financing / other) ─────────────────────
# Categorizes each cashflow income row so the dashboard can show real marketplace
# payouts separately from financing inflows (loan tranches) and everything else.
# Marketplace is detected first by the counterparty→category mapping (e.g. РВБ
# carries «Маркетплейсы»); the name/purpose regex is a bootstrap fallback for
# rows not yet categorized via /refs.
# Short tokens are word-anchored (\m…\M) so they don't match inside unrelated
# names (e.g. «озон» must not fire on «МОРОЗОН»); long unambiguous names are left
# un-anchored. \m/\M are Postgres word boundaries (POSIX ARE).
_MARKETPLACE_RE = r"вайлдберриз|wildberries|\mрвб\M|\mозон\M|\mozon\M|\mяндекс|\myandex"
_FINANCING_RE = r"транш|займ|заём|заем|кредит"
_INCOME_TYPE_KEYS = ("marketplace", "financing", "other")
_INCOME_TYPE_LABELS = {"marketplace": "Маркетплейс", "financing": "Финансирование", "other": "Прочее"}


def _income_type_case():
    """SQL CASE → 'marketplace' | 'financing' | 'other' for an income row."""
    cat_lower = func.lower(func.coalesce(Transaction.cat_lvl1_2, ""))
    return case(
        (
            or_(
                cat_lower.like("маркетплейс%"),
                func.coalesce(Transaction.counterparty, "").op("~*")(_MARKETPLACE_RE),
            ),
            "marketplace",
        ),
        (
            or_(
                Transaction.loan_payment_type == "DISBURSEMENT",
                func.coalesce(Transaction.purpose, "").op("~*")(_FINANCING_RE),
                cat_lower.in_(("займы", "финансирование", "кредиты")),
            ),
            "financing",
        ),
        else_="other",
    )


async def _compute_income_by_type(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    rates_map: dict,
    fallback_rate: Optional[float],
) -> tuple[list[dict], list[dict]]:
    """Daily income split into marketplace/financing/other (CNY→RUB converted).

    Returns ``(daily_income_by_type, income_by_type)``. Uses the same
    ``is_cashflow2 == 1`` filter as the rest of the dashboard, so inter-account
    transfers (money moved between own accounts) are already excluded.
    """
    from backend.services import fx_service

    itype = _income_type_case().label("itype")
    result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            itype,
            Transaction.currency,
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            func.count().label("cnt"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.is_cashflow2 == 1,
            Transaction.income > 0,
        )
        .group_by(func.date(Transaction.date), itype, Transaction.currency)
        .order_by(func.date(Transaction.date))
    )

    daily: dict[str, dict] = {}
    totals: dict[str, dict] = {k: {"value": 0.0, "count": 0} for k in _INCOME_TYPE_KEYS}
    for row in result:
        day_val = row.day
        day_str = day_val.isoformat() if hasattr(day_val, "isoformat") else str(day_val)
        day_date = day_val if isinstance(day_val, date) else date.fromisoformat(day_str)
        val = float(row.income)
        if row.currency == "CNY":
            rate = fx_service.find_rate_for_date(rates_map, day_date) or fallback_rate or 1.0
            val *= rate
        key = row.itype if row.itype in _INCOME_TYPE_KEYS else "other"
        daily.setdefault(day_str, {k: 0.0 for k in _INCOME_TYPE_KEYS})
        daily[day_str][key] += val
        totals[key]["value"] += val
        totals[key]["count"] += row.cnt

    daily_income_by_type = [
        {"date": d, **{k: round(v[k], 2) for k in _INCOME_TYPE_KEYS}} for d, v in sorted(daily.items())
    ]
    income_by_type = [
        {
            "key": k,
            "name": _INCOME_TYPE_LABELS[k],
            "value": round(totals[k]["value"], 2),
            "count": totals[k]["count"],
        }
        for k in _INCOME_TYPE_KEYS
        if totals[k]["count"] > 0
    ]
    return daily_income_by_type, income_by_type


# ─── Expense structure: 2-level (counterparty type → expense category) ───────
_CP_TYPE_LABELS = {
    "SUPPLIER": "Поставщик",
    "FULFILLMENT": "Фулфилмент",
    "CARRIER": "Перевозчик",
    "CUSTOMS_BROKER": "Таможенный брокер",
    "DESIGNER": "Дизайнер",
    "LEGAL": "Юридические",
    "LANDLORD": "Аренда",
    "IT_SERVICE": "IT-сервисы",
    "MARKETPLACE": "Маркетплейс",
    "BANK": "Банк",
    "GOVERNMENT": "Госорганы",
    "AFFILIATED": "Аффилированные",
    "OTHER": "Прочее",
}


async def _compute_expense_by_type(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    avg_cny_rate: float,
    cogs_incl: ColumnElement[bool],
) -> list[dict]:
    """Expenses grouped by counterparty type (level 1) → category (level 2).

    Level 1 = ``counterparty.primary_type`` (what the user sets on the card);
    level 2 = ``cat_lvl1_2``. Transactions with no linked counterparty fall under
    «Без контрагента». CNY→RUB via the period's average rate (as elsewhere here).
    ``cogs_incl`` excludes «себестоимость»-категории (see _cogs.cogs_include_clause).
    """
    from backend.models.counterparty import Counterparty

    ptype = func.coalesce(Counterparty.primary_type, text("'__none__'")).label("ptype")
    cat = func.coalesce(func.nullif(Transaction.cat_lvl1_2, ""), text("'Без категории'")).label("cat")
    result = await db.execute(
        select(
            ptype,
            cat,
            Transaction.currency,
            func.coalesce(func.sum(Transaction.expense), 0).label("expense"),
            func.count().label("cnt"),
        )
        .select_from(Transaction)
        .join(
            Counterparty,
            and_(
                Counterparty.id == Transaction.counterparty_id,
                Counterparty.project_id == Transaction.project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            ),
            isouter=True,
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.expense > 0,
            Transaction.is_cashflow2 == 1,
            cogs_incl,
        )
        .group_by(ptype, cat, Transaction.currency)
    )

    types: dict = {}
    for row in result:
        val = float(row.expense)
        if row.currency == "CNY":
            val *= avg_cny_rate
        t = types.setdefault(row.ptype, {"value": 0.0, "count": 0, "cats": {}})
        t["value"] += val
        t["count"] += row.cnt
        c = t["cats"].setdefault(row.cat, {"value": 0.0, "count": 0})
        c["value"] += val
        c["count"] += row.cnt

    out = []
    for pt, t in types.items():
        label = "Без контрагента" if pt == "__none__" else _CP_TYPE_LABELS.get(pt, pt)
        cats = sorted(
            [{"name": k, "value": round(v["value"], 2), "count": v["count"]} for k, v in t["cats"].items()],
            key=lambda x: x["value"],
            reverse=True,
        )
        out.append(
            {
                "type": None if pt == "__none__" else pt,
                "type_label": label,
                "value": round(t["value"], 2),
                "count": t["count"],
                "categories": cats,
            }
        )
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


@cached(prefix="reports:dashboard", ttl=300)
async def get_dashboard_summary(
    db: AsyncSession,
    project_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """All dashboard KPIs in a single call."""
    from backend.services.reports.balance import get_balance

    today = date.today()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to.replace(day=1)

    # COGS («себестоимость») exclusion — drop COGS-flagged expense categories from
    # every expense aggregation below (income & balances are unaffected). Fetched
    # once; applied via CASE in mixed income/expense sums and via WHERE in the
    # expense-only breakdowns.
    cogs_l1, cogs_pairs = await get_cogs_keys(db, project_id)
    cogs_incl = cogs_include_clause(cogs_l1, cogs_pairs)
    expense_sum = func.coalesce(func.sum(case((cogs_incl, Transaction.expense), else_=0)), 0)

    # 1. Balances (always current — reflects actual bank state)
    balances = await get_balance(db, project_id)
    balance_rub = sum(b["balance"] for b in balances if b["currency"] == "RUB")
    balance_cny = sum(b["balance"] for b in balances if b["currency"] == "CNY")

    # 2. Period income / expense — split by currency, convert CNY→RUB
    from backend.services import fx_service

    # Load FX rates map for the period
    rates_map = await fx_service.get_rates_map(db, project_id, date_from, date_to)

    # Get a single fallback rate if no rates in period
    fallback_rate = await fx_service.get_rate_for_date(db, project_id, date_to) if not rates_map else None

    # RUB totals
    rub_result = await db.execute(
        select(
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            expense_sum.label("expense"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.is_cashflow2 == 1,
            Transaction.currency == "RUB",
        )
    )
    rub_row = rub_result.one()
    month_income_rub = float(rub_row.income)
    month_expense_rub = float(rub_row.expense)

    # CNY day-level totals for conversion
    cny_daily_result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            expense_sum.label("expense"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.is_cashflow2 == 1,
            Transaction.currency == "CNY",
        ).group_by(func.date(Transaction.date))
    )
    month_income_cny_rub = 0.0
    month_expense_cny_rub = 0.0
    month_income_cny_raw = 0.0
    month_expense_cny_raw = 0.0
    for row in cny_daily_result:
        day_date = row.day if isinstance(row.day, date) else date.fromisoformat(str(row.day))
        rate = fx_service.find_rate_for_date(rates_map, day_date) or fallback_rate or 1.0
        month_income_cny_rub += float(row.income) * rate
        month_expense_cny_rub += float(row.expense) * rate
        month_income_cny_raw += float(row.income)
        month_expense_cny_raw += float(row.expense)

    month_income = month_income_rub + month_income_cny_rub
    month_expense = month_expense_rub + month_expense_cny_rub

    # 3. Orders summary (all active, no date filter — like debt)
    orders_q = select(
        func.count().label("cnt"),
        func.coalesce(func.sum(Order.order_amount), 0).label("total"),
    ).where(
        Order.project_id == project_id,
        or_(Order.is_deleted == False, Order.is_deleted.is_(None)),
    )
    orders_result = await db.execute(orders_q)
    orders_row = orders_result.one()
    orders_count = orders_row.cnt
    orders_total_cny = float(orders_row.total)

    # 4. Unpaid debt (planned_payments) — global, all unpaid
    debt_result = await db.execute(
        select(
            PlannedPayment.currency,
            func.sum(PlannedPayment.amount_rub).label("total_amount_rub"),
            func.sum(PlannedPayment.paid_rub).label("total_paid_rub"),
            func.sum(PlannedPayment.amount).label("total_amount"),
            func.sum(PlannedPayment.paid_amount).label("total_paid"),
        ).where(
            PlannedPayment.project_id == project_id,
            PlannedPayment.is_paid == False,
            or_(PlannedPayment.is_deleted == False, PlannedPayment.is_deleted.is_(None)),
        ).group_by(PlannedPayment.currency)
    )
    debt_rub = 0.0
    debt_cny = 0.0
    for row in debt_result:
        is_foreign = row.currency and ("CNY" in row.currency or "USD" in row.currency)
        if is_foreign:
            total_amt = float(row.total_amount or 0)
            total_paid = float(row.total_paid or 0)
            remaining = total_amt - total_paid
            if remaining > 0:
                debt_cny += remaining
        else:
            total_amt = float(row.total_amount_rub or 0)
            total_paid = float(row.total_paid_rub or 0)
            remaining = total_amt - total_paid
            if remaining > 0:
                debt_rub += remaining

    # 5. Inbox count (unassigned transactions, within date range)
    inbox_result = await db.execute(
        select(func.count()).select_from(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            or_(
                Transaction.cat_lvl1.is_(None),
                Transaction.cat_lvl1 == "",
            ),
        )
    )
    inbox_count = inbox_result.scalar() or 0

    # 6. Daily cashflow — split by currency, convert CNY→RUB
    daily_rub_result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            expense_sum.label("expense"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.is_cashflow2 == 1,
            Transaction.currency == "RUB",
        ).group_by(func.date(Transaction.date)).order_by(func.date(Transaction.date))
    )
    daily_map: dict = {}  # day_str -> {income, expense}
    for row in daily_rub_result:
        day_val = row.day
        day_str = day_val.isoformat() if hasattr(day_val, 'isoformat') else str(day_val)
        daily_map[day_str] = {"income": float(row.income), "expense": float(row.expense)}

    daily_cny_result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            expense_sum.label("expense"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.is_cashflow2 == 1,
            Transaction.currency == "CNY",
        ).group_by(func.date(Transaction.date))
    )
    for row in daily_cny_result:
        day_val = row.day
        day_str = day_val.isoformat() if hasattr(day_val, 'isoformat') else str(day_val)
        day_date = day_val if isinstance(day_val, date) else date.fromisoformat(day_str)
        rate = fx_service.find_rate_for_date(rates_map, day_date) or fallback_rate or 1.0
        if day_str not in daily_map:
            daily_map[day_str] = {"income": 0.0, "expense": 0.0}
        daily_map[day_str]["income"] += float(row.income) * rate
        daily_map[day_str]["expense"] += float(row.expense) * rate

    daily_cashflow = [
        {"date": d, **v} for d, v in sorted(daily_map.items())
    ]

    # 7. Expense by category — with currency conversion
    cat_result = await db.execute(
        select(
            func.coalesce(Transaction.cat_lvl1_2, text("'Без категории'")).label("category"),
            Transaction.currency,
            func.coalesce(func.sum(Transaction.expense), 0).label("expense"),
            func.count().label("cnt"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.expense > 0,
            Transaction.is_cashflow2 == 1,
            cogs_incl,
        ).group_by(Transaction.cat_lvl1_2, Transaction.currency).order_by(
            func.sum(Transaction.expense).desc()
        )
    )
    # Merge categories across currencies
    cat_map: dict = {}  # category -> {value, count}
    avg_cny_rate = (month_expense_cny_rub / month_expense_cny_raw) if month_expense_cny_raw > 0 else (fallback_rate or 1.0)
    for row in cat_result:
        cat_name = row.category or "Без категории"
        exp_val = float(row.expense)
        if row.currency == "CNY":
            exp_val *= avg_cny_rate
        if cat_name in cat_map:
            cat_map[cat_name]["value"] += exp_val
            cat_map[cat_name]["count"] += row.cnt
        else:
            cat_map[cat_name] = {"value": exp_val, "count": row.cnt}
    expense_by_category = sorted(
        [{"name": k, "value": v["value"], "count": v["count"]} for k, v in cat_map.items()],
        key=lambda x: x["value"], reverse=True,
    )

    # 8. Top income counterparties — group by cp_key (INN) to avoid name splits
    cp_result = await db.execute(
        select(
            func.coalesce(Transaction.cp_key, Transaction.counterparty).label("key"),
            func.min(Transaction.counterparty).label("name"),
            func.sum(Transaction.income).label("total"),
            func.count().label("cnt"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.income > 0,
            Transaction.is_cashflow2 == 1,
        ).group_by(
            func.coalesce(Transaction.cp_key, Transaction.counterparty)
        ).order_by(
            func.sum(Transaction.income).desc()
        ).limit(20)
    )
    income_counterparties = []
    for row in cp_result:
        income_counterparties.append({
            "key": row.key or "",
            "name": row.name or "Без контрагента",
            "total": float(row.total),
            "count": row.cnt,
        })

    # 9. Income split by type — marketplace / financing / other (daily + totals)
    daily_income_by_type, income_by_type = await _compute_income_by_type(
        db, project_id, date_from, date_to, rates_map, fallback_rate
    )

    # 10. Expense structure — 2-level: counterparty type → category
    expense_by_type = await _compute_expense_by_type(
        db, project_id, date_from, date_to, avg_cny_rate, cogs_incl
    )

    return {
        "balance_rub": balance_rub,
        "balance_cny": balance_cny,
        "month_income": month_income,
        "month_expense": month_expense,
        "month_income_rub": month_income_rub,
        "month_expense_rub": month_expense_rub,
        "month_income_cny_rub": month_income_cny_rub,
        "month_expense_cny_rub": month_expense_cny_rub,
        "month_expense_cny_raw": month_expense_cny_raw,
        "avg_cny_rate": avg_cny_rate if month_expense_cny_raw > 0 else None,
        "orders_count": orders_count,
        "orders_total_cny": orders_total_cny,
        "debt_rub": debt_rub,
        "debt_cny": debt_cny,
        "inbox_count": inbox_count,
        "accounts_count": len(balances),
        "daily_cashflow": daily_cashflow,
        "daily_income_by_type": daily_income_by_type,
        "income_by_type": income_by_type,
        "expense_by_category": expense_by_category,
        "expense_by_type": expense_by_type,
        "income_counterparties": income_counterparties,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }
