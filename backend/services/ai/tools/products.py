"""
Product domain tools: cost data, product info.
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.tools.common import _json, build_tax_info


async def get_cost_data(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.queries import get_cost_overrides, get_missing_costs

    result = await get_cost_overrides(db, project_id)
    overrides_list = result.get("overrides", []) if isinstance(result, dict) else []
    missing = await get_missing_costs(db, project_id)
    return _json(
        {
            "cost_overrides_count": len(overrides_list),
            "cost_overrides": overrides_list,
            "missing_costs_count": len(missing),
            "missing_costs": missing,
        }
    )


async def get_product_info(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.product_trends import get_product_trends

    tax_info = build_tax_info(tax_rate)
    result = await get_product_trends(
        db,
        project_id,
        tax_info,
        trend_days=7,
        brand=brand,
        search=inp.get("search"),
    )
    products = result.get("products", [])
    if not products:
        return json.dumps({"message": "Product not found: " + str(inp.get("search"))})
    return _json({"products": products})
