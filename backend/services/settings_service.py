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
    """Set excluded warehouses. Normalizes names (strips parenthesized suffix)."""
    import re

    parens_re = re.compile(r"\s*\([^)]*\)\s*$")
    valid = sorted({parens_re.sub("", w).strip() for w in warehouses if w and w.strip()})
    await set_setting(db, project_id, "excluded_warehouses", json.dumps(valid, ensure_ascii=False))
    logger.info("Set excluded warehouses for project %s: %s", project_id, valid)
    return valid


async def get_all_warehouses(db: AsyncSession | None = None, project_id: int | None = None) -> list[dict]:
    """Return all known WB warehouses: union of WAREHOUSE_COORDS keys and any
    warehouse names that appear in wb_warehouse_stocks for this project.

    Names are normalized (parenthesized suffix stripped). For warehouses without
    coordinates, lat/lng are 0 (UI may show them but not on the map).
    """
    import re

    parens_re = re.compile(r"\s*\([^)]*\)\s*$")

    def norm(name: str) -> str:
        return parens_re.sub("", name or "").strip()

    by_name: dict[str, dict] = {}
    for raw_name, (lat, lng) in WAREHOUSE_COORDS.items():
        n = norm(raw_name)
        if n and n not in by_name:
            by_name[n] = {"name": n, "lat": lat, "lng": lng, "is_sorting_center": n.startswith("СЦ ")}

    if db is not None and project_id is not None:
        from backend.models import WbWarehouseStock

        result = await db.execute(
            select(WbWarehouseStock.warehouse_name).where(WbWarehouseStock.project_id == project_id).distinct()
        )
        for (raw,) in result:
            n = norm(raw)
            if not n or n in by_name:
                continue
            by_name[n] = {"name": n, "lat": 0.0, "lng": 0.0, "is_sorting_center": n.startswith("СЦ ")}

    return sorted(by_name.values(), key=lambda x: x["name"])


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
