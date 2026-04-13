"""
Cost — shared helpers and utilities.
"""

import math
import re
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
    if "/" in s:
        parts = s.split("/")
        try:
            return int(parts[0]) * 100 + int(parts[1])
        except (ValueError, IndexError):  # noqa: S110
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
    if val is None:
        return Decimal(0)
    if isinstance(val, Decimal):
        return val
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return Decimal(0)
        return Decimal(str(f))
    except Exception:
        return Decimal(0)


def parse_box_volume_m3(box_size: str | None, pcs_per_box: int | None) -> Decimal:
    """Parse box dimensions '60x40x50' (cm) and return volume per unit in m³."""
    if not box_size or not pcs_per_box or pcs_per_box <= 0:
        return Decimal(0)
    parts = re.split(r"[*xXхХ×]", box_size)  # noqa: RUF001
    if len(parts) != 3:
        return Decimal(0)
    try:
        dims = [float(p.strip()) for p in parts]
    except (ValueError, TypeError):
        return Decimal(0)
    if not all(d > 0 for d in dims):
        return Decimal(0)
    box_vol_m3 = dims[0] * dims[1] * dims[2] / 1_000_000
    return Decimal(str(round(box_vol_m3 / pcs_per_box, 6)))


async def auto_link_customs_dt(order_no: str, dt_number: str, project_id: int, db: AsyncSession):
    """Auto-link CustomsDT records matching dt_number to this order."""
    try:
        order_no_int = int(order_no)
    except (ValueError, TypeError):
        return
    result = await db.execute(
        select(CustomsDT).where(
            CustomsDT.dt_number == dt_number,
            CustomsDT.project_id == project_id,
            CustomsDT.is_deleted == False,  # noqa: E712
        )
    )
    dts = result.scalars().all()
    for d in dts:
        if d.order_no != order_no_int:
            d.order_no = order_no_int
    if dts:
        await db.commit()
