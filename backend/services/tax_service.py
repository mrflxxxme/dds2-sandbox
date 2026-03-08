"""
Tax rate service — CRUD for per-brand monthly tax rates.
"""

import logging
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.tax import TaxRate
from backend.models.cost import Nomenclature

logger = logging.getLogger("dds.tax_service")


async def get_brands(db: AsyncSession, project_id: int) -> list[str]:
    """Get unique brands from nomenclature for this project."""
    stmt = (
        select(Nomenclature.brand)
        .where(Nomenclature.project_id == project_id)
        .where(Nomenclature.brand.isnot(None))
        .where(Nomenclature.brand != "")
        .distinct()
        .order_by(Nomenclature.brand)
    )
    result = await db.execute(stmt)
    return [r[0] for r in result.all()]


async def get_tax_rates(db: AsyncSession, project_id: int, year: int) -> dict:
    """Get all tax rates for a project/year, grouped by brand."""
    brands = await get_brands(db, project_id)

    stmt = (
        select(TaxRate)
        .where(TaxRate.project_id == project_id)
        .where(TaxRate.year == year)
        .order_by(TaxRate.brand, TaxRate.month)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # Group by brand
    brand_map: dict[str, dict] = {}
    for r in rows:
        if r.brand not in brand_map:
            brand_map[r.brand] = {
                "brand": r.brand,
                "tax_regime": r.tax_regime,
                "months": [],
            }
        brand_map[r.brand]["months"].append({
            "month": r.month,
            "usn_rate": float(r.usn_rate or 0),
            "nds_rate": float(r.nds_rate or 0),
            "cost_as_expense": r.cost_as_expense or False,
        })

    # Ensure all 12 months exist for each configured brand
    for brand_data in brand_map.values():
        existing_months = {m["month"] for m in brand_data["months"]}
        for m in range(1, 13):
            if m not in existing_months:
                brand_data["months"].append({
                    "month": m,
                    "usn_rate": 0,
                    "nds_rate": 0,
                    "cost_as_expense": False,
                })
        brand_data["months"].sort(key=lambda x: x["month"])

    # Build rates list (configured brands first, then unconfigured)
    rates = []
    for b in brands:
        if b in brand_map:
            rates.append(brand_map[b])
        # Don't auto-add unconfigured brands — they can be added via UI

    # Also include brands that have rates but are not in nomenclature anymore
    for b, data in brand_map.items():
        if b not in brands:
            rates.append(data)

    return {
        "year": year,
        "brands": brands,
        "rates": rates,
    }


async def save_tax_rates(
    db: AsyncSession,
    project_id: int,
    brand: str,
    year: int,
    tax_regime: str,
    months: list[dict],
) -> dict:
    """Save/update tax rates for one brand for one year (upsert 12 months)."""
    # Delete existing rows for this brand/year
    await db.execute(
        delete(TaxRate).where(
            TaxRate.project_id == project_id,
            TaxRate.brand == brand,
            TaxRate.year == year,
        )
    )

    # Insert new rows
    for m in months:
        row = TaxRate(
            project_id=project_id,
            brand=brand,
            tax_regime=tax_regime,
            year=year,
            month=m["month"],
            usn_rate=m.get("usn_rate", 0),
            nds_rate=m.get("nds_rate", 0),
            cost_as_expense=m.get("cost_as_expense", False),
        )
        db.add(row)

    await db.commit()
    logger.info("Saved tax rates: project=%s brand=%s year=%s regime=%s", project_id, brand, year, tax_regime)
    return {"status": "ok", "brand": brand, "year": year}
