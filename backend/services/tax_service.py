"""
Tax rate service — CRUD for project-level monthly tax rates.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.tax import TaxRate

logger = logging.getLogger("dds.tax_service")

# Internal constant — brand column exists in DB but tax is project-level
_PROJECT_BRAND = "__project__"


async def get_tax_rates(db: AsyncSession, project_id: int, year: int) -> dict:
    """Get tax rates for a project/year (project-level, not per-brand)."""
    stmt = (
        select(TaxRate)
        .where(TaxRate.project_id == project_id)
        .where(TaxRate.year == year)
        .where(TaxRate.brand == _PROJECT_BRAND)
        .order_by(TaxRate.month)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    tax_regime = "usn_income"
    months = []
    for r in rows:
        tax_regime = r.tax_regime
        months.append(
            {
                "month": r.month,
                "usn_rate": float(r.usn_rate or 0),
                "nds_rate": float(r.nds_rate or 0),
                "cost_as_expense": r.cost_as_expense or False,
            }
        )

    # Ensure all 12 months exist
    existing = {m["month"] for m in months}
    for m in range(1, 13):
        if m not in existing:
            months.append(
                {
                    "month": m,
                    "usn_rate": 0,
                    "nds_rate": 0,
                    "cost_as_expense": False,
                }
            )
    months.sort(key=lambda x: x["month"])

    return {
        "year": year,
        "tax_regime": tax_regime,
        "months": months,
    }


async def save_tax_rates(
    db: AsyncSession,
    project_id: int,
    year: int,
    tax_regime: str,
    months: list[dict],
) -> dict:
    """Save/update project-level tax rates for one year (upsert 12 months)."""
    # Delete existing rows for this project/year
    await db.execute(
        delete(TaxRate).where(
            TaxRate.project_id == project_id,
            TaxRate.brand == _PROJECT_BRAND,
            TaxRate.year == year,
        )
    )

    # Insert new rows
    for m in months:
        # Support both Pydantic objects and plain dicts
        if isinstance(m, dict):
            month_val = m.get("month", 1)
            usn_val = m.get("usn_rate", 0)
            nds_val = m.get("nds_rate", 0)
            cost_val = m.get("cost_as_expense", False)
        else:
            month_val = m.month
            usn_val = m.usn_rate
            nds_val = m.nds_rate
            cost_val = m.cost_as_expense
        row = TaxRate(
            project_id=project_id,
            brand=_PROJECT_BRAND,
            tax_regime=tax_regime,
            year=year,
            month=month_val,
            usn_rate=usn_val,
            nds_rate=nds_val,
            cost_as_expense=cost_val,
        )
        db.add(row)

    await db.commit()

    # Invalidate reports cached against the previous rates (security.md rule:
    # tax_rate mutation → reports:wb_bdr, reports:opiu, reports:dashboard, funnel).
    # Without this, @cached(ttl=3600) on BDR/ОПИУ keeps old values for up to 1 hour.
    await invalidate_cache(f"reports:wb_bdr:project_id={project_id}")
    await invalidate_cache(f"reports:opiu:project_id={project_id}")
    await invalidate_cache(f"reports:dashboard:project_id={project_id}")
    await invalidate_cache(f"funnel:tariff_map:project_id={project_id}")
    await invalidate_cache(f"funnel:avg_buyout:project_id={project_id}")

    logger.info("Saved tax rates: project=%s year=%s regime=%s", project_id, year, tax_regime)
    return {"status": "ok", "year": year}
