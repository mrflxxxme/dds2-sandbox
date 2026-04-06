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
