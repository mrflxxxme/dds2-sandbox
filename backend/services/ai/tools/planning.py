"""
Planning domain tools: plan-fact analysis, order geography.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.tools.common import _json


async def get_plan_fact(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.planning.brand_plan import get_plan_fact_brands
    from backend.utils.time import utcnow

    now = utcnow()
    year = inp.get("year", now.year)
    month = inp.get("month", now.month)

    brands_data = await get_plan_fact_brands(db, project_id, year, month)
    if brand:
        brands_data = [b for b in brands_data if b.get("brand") == brand]

    return _json(
        {
            "year": year,
            "month": month,
            "brands": brands_data,
        }
    )


async def get_order_geography(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.order_geography_service import get_order_geography as _get_order_geography

    result = await _get_order_geography(
        db,
        project_id,
        date_from=inp["date_from"],
        date_to=inp["date_to"],
        brand=brand,
    )
    cities = result.get("cities", [])
    top_cities = sorted(cities, key=lambda c: c.get("order_count", 0), reverse=True)
    return _json(
        {
            "total_orders": result.get("total_orders", 0),
            "unique_cities": result.get("unique_cities", 0),
            "top_cities": top_cities,
        }
    )
