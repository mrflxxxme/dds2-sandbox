"""
Planning — WB Payouts (CRUD, reconciliation, forecast).
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import PlannedIncome, Transaction, WbPayout
from backend.utils.time import utcnow

# ─── WB Payouts CRUD ─────────────────────────────────────────────────────────


async def upload_wb_payouts(db: AsyncSession, project_id: int, parsed: list[dict]):
    """Upsert WB payouts from parsed Excel data."""
    created, updated, skipped = 0, 0, 0
    for item in parsed:
        result = await db.execute(
            select(WbPayout).where(
                WbPayout.project_id == project_id,
                WbPayout.request_id == item["request_id"],
                WbPayout.is_deleted == False,
            )
        )
        obj = result.scalar_one_or_none()
        if obj:
            if obj.status != "RECEIVED":
                obj.wb_status_raw = item["wb_status_raw"]
                obj.status = item["status"]
                obj.bank_comment = item["bank_comment"]
                updated += 1
            else:
                skipped += 1
        else:
            obj = WbPayout(project_id=project_id, **item)
            db.add(obj)
            created += 1
    await db.commit()
    return created, updated, skipped


async def get_wb_payouts(
    db: AsyncSession, project_id: int, status: str | None = None, limit: int = 500, offset: int = 0
):
    q = (
        select(WbPayout)
        .where(WbPayout.project_id == project_id, WbPayout.is_deleted == False)
        .order_by(WbPayout.created_at.desc())
    )
    if status:
        q = q.where(WbPayout.status == status)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


async def delete_wb_payout(db: AsyncSession, project_id: int, payout_id: int):
    result = await db.execute(
        select(WbPayout).where(
            WbPayout.id == payout_id,
            WbPayout.project_id == project_id,
            WbPayout.is_deleted == False,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    obj.soft_delete()
    await db.commit()
    return True


async def manual_reconcile_wb(db: AsyncSession, project_id: int, payout_id: int, txn_id: str):
    """Manually match a WB payout with a bank transaction."""
    result = await db.execute(
        select(WbPayout).where(
            WbPayout.id == payout_id,
            WbPayout.project_id == project_id,
            WbPayout.is_deleted == False,
        )
    )
    payout = result.scalar_one_or_none()
    if not payout:
        return None, "Payout not found"

    txn = await db.execute(
        select(Transaction).where(
            Transaction.txn_id == txn_id,
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,
        )
    )
    if not txn.scalar_one_or_none():
        return None, "Transaction not found"

    payout.matched_txn_id = txn_id
    payout.matched_at = utcnow()
    payout.status = "RECEIVED"
    await db.commit()
    return True, None


# ─── WB payout reconciliation ───────────────────────────────────────────────


async def reconcile_wb_payouts(db: AsyncSession, project_id: int):
    """
    Match WB payouts (not RECEIVED) with bank income transactions.
    Criteria: WB counterparty, amount ±1%, date within [-2, +5] days.
    """
    unmatched_result = await db.execute(
        select(WbPayout).where(
            WbPayout.project_id == project_id,
            WbPayout.is_deleted == False,
            WbPayout.status.in_(["TRANSIT", "PROCESSING", "PENDING"]),
        )
    )
    payouts = unmatched_result.scalars().all()
    if not payouts:
        return

    matched_result = await db.execute(
        select(WbPayout.matched_txn_id).where(
            WbPayout.project_id == project_id,
            WbPayout.is_deleted == False,
            WbPayout.matched_txn_id.isnot(None),
        )
    )
    already_matched = {r[0] for r in matched_result}

    min_date = min(p.created_at for p in payouts) - timedelta(days=2)
    max_date = max(p.created_at for p in payouts) + timedelta(days=5)

    candidates_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,
            Transaction.income > 0,
            Transaction.date >= min_date,
            Transaction.date <= max_date,
            or_(
                and_(
                    Transaction.cat_lvl1_2 == "Маркетплейсы",
                    Transaction.cat_lvl2_2 == "Wildberries",
                ),
                Transaction.counterparty.ilike("%вайлдберриз%"),
                Transaction.counterparty.ilike("%wildberries%"),
            ),
        )
    )
    candidates = [t for t in candidates_result.scalars().all() if t.txn_id not in already_matched]

    if not candidates:
        return

    # Greedy matching: largest payouts first
    used_txn_ids: set[str] = set()
    for payout in sorted(payouts, key=lambda p: p.amount_rub, reverse=True):
        best_match = None
        best_diff = float("inf")

        for txn in candidates:
            if txn.txn_id in used_txn_ids:
                continue
            txn_date = txn.date.date() if hasattr(txn.date, "date") else txn.date
            payout_date = payout.created_at.date() if hasattr(payout.created_at, "date") else payout.created_at
            delta = (txn_date - payout_date).days
            if delta < -2 or delta > 5:
                continue
            diff = abs(float(txn.income) - float(payout.amount_rub))
            tolerance = float(payout.amount_rub) * 0.01
            if diff <= tolerance and diff < best_diff:
                best_diff = diff
                best_match = txn

        if best_match:
            payout.matched_txn_id = best_match.txn_id
            payout.matched_at = utcnow()
            payout.status = "RECEIVED"
            used_txn_ids.add(best_match.txn_id)

    await db.commit()


# ─── WB Forecast ─────────────────────────────────────────────────────────────


async def refresh_wb_forecast(
    db: AsyncSession,
    project_id: int,
    forecast_days: int = 60,
    trend_days: int = 7,
) -> dict:
    """
    Auto-generate PlannedIncome for next `forecast_days` days based on
    `trend_days` history of actual WB income, respecting day-of-week patterns
    (weekends = 0, Monday = accumulated payout for Sat+Sun+Mon).
    """
    today = date.today()
    lookback = today - timedelta(days=trend_days)

    # Get daily WB income for the lookback period
    result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.sum(Transaction.income).label("daily_income"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.income > 0,
            Transaction.date >= lookback,
            Transaction.date <= today,
            or_(
                and_(
                    Transaction.cat_lvl1_2 == "Маркетплейсы",
                    Transaction.cat_lvl2_2 == "Wildberries",
                ),
                Transaction.counterparty.ilike("%вайлдберриз%"),
                Transaction.counterparty.ilike("%wildberries%"),
                Transaction.counterparty.ilike("%вайлд%"),
            ),
        )
        .group_by(func.date(Transaction.date))
    )
    daily_data = {row.day: Decimal(str(row.daily_income or 0)) for row in result}

    if not daily_data:
        return {"ok": True, "daily_avg": 0, "message": f"Нет данных WB за последние {trend_days} дней"}

    # Build day-of-week pattern (0=Mon, 6=Sun)
    weekday_totals: dict[int, Decimal] = {i: Decimal("0") for i in range(7)}
    weekday_counts: dict[int, int] = {i: 0 for i in range(7)}

    for day_offset in range(trend_days):
        d = lookback + timedelta(days=day_offset + 1)
        if d > today:
            break
        wd = d.weekday()
        weekday_counts[wd] += 1
        if hasattr(d, "isoformat"):
            amount = daily_data.get(d, Decimal("0"))
            if amount == 0:
                amount = daily_data.get(d.isoformat(), Decimal("0"))
        else:
            amount = daily_data.get(d, Decimal("0"))
        weekday_totals[wd] += amount

    # Calculate average per weekday
    weekday_avg: dict[int, Decimal] = {}
    for wd in range(7):
        if weekday_counts[wd] > 0:
            weekday_avg[wd] = (weekday_totals[wd] / Decimal(str(weekday_counts[wd]))).quantize(Decimal("0.01"))
        else:
            weekday_avg[wd] = Decimal("0")

    # Calculate overall: total income / total days = true daily average
    total_income = sum(daily_data.values())
    daily_avg = (total_income / Decimal(str(trend_days))).quantize(Decimal("0.01")) if trend_days > 0 else Decimal("0")
    weekly_total = sum(weekday_avg.values())

    if daily_avg <= 0:
        return {"ok": True, "daily_avg": 0, "message": "Нет данных WB за последние 28 дней"}

    # Delete stale auto-forecast
    await db.execute(
        text("DELETE FROM planned_incomes WHERE source = 'WB_AUTO' AND project_id = :pid"),
        {"pid": project_id},
    )

    # Get manual WB income dates to avoid overlap
    manual_result = await db.execute(
        select(PlannedIncome.date).where(
            PlannedIncome.source == "WB",
            PlannedIncome.project_id == project_id,
            PlannedIncome.is_deleted == False,
        )
    )
    manual_dates = {row[0] for row in manual_result}

    created = 0
    weekday_detail = {}
    for i in range(1, forecast_days + 1):
        d = today + timedelta(days=i)
        if d in manual_dates:
            continue
        wd = d.weekday()
        amount = weekday_avg.get(wd, Decimal("0"))
        if amount <= 0:
            continue
        pi = PlannedIncome(date=d, amount_rub=amount, source="WB_AUTO", project_id=project_id)
        db.add(pi)
        created += 1

    await db.commit()

    # Build weekday detail for response
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for wd in range(7):
        weekday_detail[day_names[wd]] = float(weekday_avg[wd])

    return {
        "ok": True,
        "daily_avg": float(daily_avg),
        "weekly_total": float(weekly_total),
        "weekday_pattern": weekday_detail,
        "created": created,
        "forecast_days": forecast_days,
    }
