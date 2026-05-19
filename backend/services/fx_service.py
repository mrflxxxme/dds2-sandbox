"""
FX Service — rate extraction from transactions, backfill, and lookup.
"""

import logging
import re
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import FxRate, Transaction

logger = logging.getLogger("dds.fx_service")

# Regex to extract CNY/RUB rate from VTB purpose text
# Example: "по курсу Банка CNY/RUB 11.472"
RATE_RE = re.compile(r"CNY/RUB\s+(\d+[\.,]\d+)", re.IGNORECASE)


async def extract_rate_from_purpose(purpose: str) -> float | None:
    """Extract CNY/RUB exchange rate from transaction purpose text."""
    if not purpose:
        return None
    m = RATE_RE.search(purpose)
    if m:
        rate_str = m.group(1).replace(",", ".")
        try:
            return float(rate_str)
        except ValueError:
            return None
    return None


async def backfill_rates_from_transactions(db: AsyncSession, project_id: int) -> dict:
    """
    Scan all is_fx=True RUB transactions, extract CNY/RUB rate from purpose,
    and insert into fx_rates table.
    Returns {"inserted": N, "skipped": M}.
    """
    result = await db.execute(
        select(Transaction)
        .where(
            Transaction.project_id == project_id,
            Transaction.is_fx == True,
            Transaction.currency == "RUB",
            Transaction.expense > 0,
            Transaction.is_deleted == False,
        )
        .order_by(Transaction.date)
    )
    rows = result.scalars().all()

    inserted = 0
    skipped = 0

    for txn in rows:
        rate = await extract_rate_from_purpose(txn.purpose or "")
        if rate is None:
            skipped += 1
            continue

        txn_date = txn.date.date() if hasattr(txn.date, "date") else txn.date

        # Upsert: insert if not exists
        stmt = (
            pg_insert(FxRate)
            .values(
                project_id=project_id,
                date=txn_date,
                pair="CNY/RUB",
                rate=Decimal(str(rate)),
                source="vtb_import",
                txn_id=txn.txn_id,
            )
            .on_conflict_do_nothing(constraint="uq_fx_rate_txn")
        )
        res = await db.execute(stmt)
        if res.rowcount > 0:  # type: ignore[attr-defined]
            inserted += 1
        else:
            skipped += 1

    await db.commit()
    logger.info(f"FX backfill project={project_id}: inserted={inserted}, skipped={skipped}")
    return {"inserted": inserted, "skipped": skipped}


async def get_rate_for_date(
    db: AsyncSession, project_id: int, target_date: date_type, pair: str = "CNY/RUB"
) -> float | None:
    """
    Get the exchange rate for a given date.
    If no exact match, returns the closest previous rate.
    """
    # Try exact date first
    result = await db.execute(
        select(func.avg(FxRate.rate)).where(
            FxRate.project_id == project_id,
            FxRate.date == target_date,
            FxRate.pair == pair,
        )
    )
    rate = result.scalar()
    if rate is not None:
        return float(rate)

    # Fallback: closest previous date
    result = await db.execute(
        select(FxRate.rate)
        .where(
            FxRate.project_id == project_id,
            FxRate.date <= target_date,
            FxRate.pair == pair,
        )
        .order_by(FxRate.date.desc())
        .limit(1)
    )
    rate = result.scalar()
    if rate is not None:
        return float(rate)

    # Fallback: closest future date
    result = await db.execute(
        select(FxRate.rate)
        .where(
            FxRate.project_id == project_id,
            FxRate.date >= target_date,
            FxRate.pair == pair,
        )
        .order_by(FxRate.date.asc())
        .limit(1)
    )
    rate = result.scalar()
    return float(rate) if rate is not None else None


async def get_rates_map(
    db: AsyncSession, project_id: int, date_from: date_type, date_to: date_type, pair: str = "CNY/RUB"
) -> dict[date_type, float]:
    """
    Build a date→rate mapping for the given period.
    For dates without an exact rate, fills from the closest previous.
    """
    result = await db.execute(
        select(FxRate.date, func.avg(FxRate.rate).label("rate"))
        .where(
            FxRate.project_id == project_id,
            FxRate.pair == pair,
        )
        .group_by(FxRate.date)
        .order_by(FxRate.date)
    )
    rates_raw = [(r.date, float(r.rate)) for r in result]

    if not rates_raw:
        return {}

    # Build a lookup: for any date, find the closest rate
    rates_map: dict[date_type, float] = {}
    for d, r in rates_raw:
        rates_map[d] = r

    return rates_map


def find_rate_for_date(rates_map: dict[date_type, float], target_date: date_type) -> float | None:
    """Find the closest rate from the rates_map for a given date."""
    if not rates_map:
        return None
    if target_date in rates_map:
        return rates_map[target_date]

    # Find closest previous
    prev_dates = [d for d in rates_map if d <= target_date]
    if prev_dates:
        return rates_map[max(prev_dates)]

    # Find closest future
    future_dates = [d for d in rates_map if d > target_date]
    if future_dates:
        return rates_map[min(future_dates)]

    return None
