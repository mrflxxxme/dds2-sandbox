"""Settings service — project-level key-value settings."""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.refs import ProjectSetting
from backend.services.warehouse_geo import WAREHOUSE_COORDS

logger = logging.getLogger("dds.settings")


async def get_setting(db: AsyncSession, project_id: int, key: str) -> str | None:
    """Get a raw setting value by key."""
    result = await db.execute(
        select(ProjectSetting).where(ProjectSetting.project_id == project_id, ProjectSetting.key == key)
    )
    row = result.scalar_one_or_none()
    return row.value if row else None


async def set_setting(db: AsyncSession, project_id: int, key: str, value: str) -> None:
    """Upsert a setting value."""
    result = await db.execute(
        select(ProjectSetting).where(ProjectSetting.project_id == project_id, ProjectSetting.key == key)
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(ProjectSetting(project_id=project_id, key=key, value=value))
    await db.commit()


async def get_excluded_warehouses(db: AsyncSession, project_id: int) -> list[str]:
    """Get list of excluded warehouse names for a project."""
    raw = await get_setting(db, project_id, "excluded_warehouses")
    if not raw:
        return []
    try:
        parsed: list[str] = json.loads(raw)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return []


async def set_excluded_warehouses(db: AsyncSession, project_id: int, warehouses: list[str]) -> list[str]:
    """Set excluded warehouses. Validates names against WAREHOUSE_COORDS."""
    valid = [w for w in warehouses if w in WAREHOUSE_COORDS]
    await set_setting(db, project_id, "excluded_warehouses", json.dumps(valid, ensure_ascii=False))
    logger.info("Set excluded warehouses for project %s: %s", project_id, valid)
    return valid


def get_all_warehouses() -> list[dict]:
    """Return all warehouses from WAREHOUSE_COORDS with their coordinates."""
    return [{"name": name, "lat": coords[0], "lng": coords[1]} for name, coords in sorted(WAREHOUSE_COORDS.items())]


# ─── Stock forecast: default RF→WB lead time (used when WarehouseDeliveryTime is empty)


_FORECAST_RF_DEFAULT_DAYS_KEY = "forecast_rf_default_days"
_FORECAST_RF_DEFAULT_DAYS_FALLBACK = 8  # 3 (assembly) + 3 (delivery) + 2 (acceptance)


async def get_forecast_rf_default_days(db: AsyncSession, project_id: int) -> int:
    raw = await get_setting(db, project_id, _FORECAST_RF_DEFAULT_DAYS_KEY)
    if raw is None:
        return _FORECAST_RF_DEFAULT_DAYS_FALLBACK
    try:
        v = int(raw)
        return v if v >= 0 else _FORECAST_RF_DEFAULT_DAYS_FALLBACK
    except (TypeError, ValueError):
        return _FORECAST_RF_DEFAULT_DAYS_FALLBACK


async def set_forecast_rf_default_days(db: AsyncSession, project_id: int, days: int) -> int:
    days = max(0, min(int(days), 365))
    await set_setting(db, project_id, _FORECAST_RF_DEFAULT_DAYS_KEY, str(days))
    logger.info("Set forecast_rf_default_days for project %s: %d", project_id, days)
    return days
