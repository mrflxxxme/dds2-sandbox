"""
Health domain tools: anomalies detection, daily health check.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.tools.common import _json, build_tax_info


async def get_anomalies(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.anomalies import get_anomalies as _get_anomalies

    tax_info = build_tax_info(tax_rate)
    result = await _get_anomalies(
        db,
        project_id,
        tax_info,
        period_days=inp.get("period_days", 7),
        brand=brand,
        include_rf_stocks=True,
        min_orders=inp.get("min_orders", 5),
    )
    anomalies = result.get("anomalies", [])
    summary = result.get("summary", {})
    priority = {"critical": 0, "warning": 1}
    sorted_anomalies = sorted(
        anomalies,
        key=lambda a: (priority.get(a.get("severity", "warning"), 1), -(a.get("loss_amount") or 0)),
    )
    return _json(
        {
            "summary": summary,
            "anomalies": sorted_anomalies,
            "total_products": result.get("total_products", 0),
            "period_days": result.get("period_days", 7),
        }
    )


async def get_daily_health(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.health_check_service import get_daily_health as _get_daily_health

    tax_info = build_tax_info(tax_rate)
    result = await _get_daily_health(
        db,
        project_id,
        tax_info,
        brand=brand,
    )
    return _json(result)
