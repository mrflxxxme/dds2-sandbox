"""
Cost — shared helpers and utilities.
"""

import math
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CustomsDT


DEFAULT_VAT_RATE = Decimal("0.22")


def _order_no_to_int(s: str) -> int:
    """Convert order_no string to integer for planning.orders FK.
    '41' -> 41, '41/2' -> 4102, '41/3' -> 4103.
    """
    s = str(s).strip()
    if '/' in s:
        parts = s.split('/')
        try:
            return int(parts[0]) * 100 + int(parts[1])
        except (ValueError, IndexError):
            pass
    try:
        return int(s)
    except ValueError:
        return abs(hash(s)) % (10**9)


def safe_float(val) -> float:
    """Convert value to float, treating NaN/None/invalid as 0.0."""
    try:
        f = float(val) if val is not None else 0.0
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return 0.0


def safe_decimal(val) -> Decimal:
    """Convert value to Decimal, treating NaN/None/invalid as 0."""
    try:
        f = float(val) if val is not None else 0.0
        if math.isnan(f) or math.isinf(f):
            return Decimal(0)
        return Decimal(str(f))
    except Exception:
        return Decimal(0)


async def auto_link_customs_dt(order_no: str, dt_number: str, db: AsyncSession):
    """Auto-link CustomsDT records matching dt_number to this order."""
    try:
        order_no_int = int(order_no)
    except (ValueError, TypeError):
        return
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.dt_number == dt_number)
    )
    dts = result.scalars().all()
    for d in dts:
        if d.order_no != order_no_int:
            d.order_no = order_no_int
    if dts:
        await db.commit()
