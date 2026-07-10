"""
Синк замеров WB: контрольные замеры складов + удержания за занижение габаритов.

WB Analytics API → upsert в wb_warehouse_measurements / wb_measurement_penalties.
Идемпотентно по естественному ключу (dim_id для замеров).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.wb_api import WBApiClient
from backend.models.wb_measurements import WbMeasurementPenalty, WbWarehouseMeasurement
from backend.services import integrations_service

logger = structlog.get_logger("dds.wb_measurements")


def _parse_dt(value: object) -> datetime | None:
    """WB отдаёт RFC3339 ('2026-04-10T04:07:01Z'). Возвращаем tz-aware datetime."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _to_int(value):
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _map_warehouse_row(project_id: int, row: dict) -> dict | None:
    dim_id = _to_int(row.get("dimId"))
    nm_id = _to_int(row.get("nmId"))
    if dim_id is None or nm_id is None:
        return None
    return {
        "project_id": project_id,
        "dim_id": dim_id,
        "nm_id": nm_id,
        "subject_name": (row.get("subjectName") or None),
        "length": _to_int(row.get("length")),
        "width": _to_int(row.get("width")),
        "height": _to_int(row.get("height")),
        "volume": row.get("volume"),
        "photo_urls": row.get("photoUrls") or [],
        "measured_at": _parse_dt(row.get("dt")),
    }


async def _upsert_warehouse(db: AsyncSession, project_id: int, rows: list[dict]) -> int:
    mapped: dict[int, dict] = {}
    for r in rows:
        m = _map_warehouse_row(project_id, r)
        if m:
            mapped[m["dim_id"]] = m  # dedup by natural key → last wins (avoid CardinalityViolation)
    if not mapped:
        return 0
    batch = list(mapped.values())
    stmt = pg_insert(WbWarehouseMeasurement).values(batch)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_wb_wh_measurement_project_dim",
        set_={
            "nm_id": stmt.excluded.nm_id,
            "subject_name": stmt.excluded.subject_name,
            "length": stmt.excluded.length,
            "width": stmt.excluded.width,
            "height": stmt.excluded.height,
            "volume": stmt.excluded.volume,
            "photo_urls": stmt.excluded.photo_urls,
            "measured_at": stmt.excluded.measured_at,
        },
    )
    await db.execute(stmt)
    return len(batch)


def _map_penalty_row(project_id: int, row: dict) -> dict | None:
    dim_id = _to_int(row.get("dimId"))
    nm_id = _to_int(row.get("nmId"))
    if dim_id is None or nm_id is None:
        return None
    return {
        "project_id": project_id,
        "dim_id": dim_id,
        "nm_id": nm_id,
        "subject_name": (row.get("subjectName") or None),
        "prc_over": row.get("prcOver"),
        # факт замера WB
        "act_length": _to_int(row.get("length")),
        "act_width": _to_int(row.get("width")),
        "act_height": _to_int(row.get("height")),
        "act_volume": row.get("volume"),
        # заявлено продавцом (*Sup)
        "dec_length": _to_int(row.get("lengthSup")),
        "dec_width": _to_int(row.get("widthSup")),
        "dec_height": _to_int(row.get("heightSup")),
        "dec_volume": row.get("volumeSup"),
        "penalty_amount": row.get("penaltyAmount"),
        "reversal_amount": row.get("reversalAmount"),
        "is_valid": row.get("isValid"),
        "is_valid_at": _parse_dt(row.get("isValidDt")),
        "penalty_date": _parse_dt(row.get("dtBonus")),
        "photo_urls": row.get("photoUrls") or [],
    }


def _aggregate_penalties(project_id: int, rows: list[dict]) -> list[dict]:
    """WB шлёт удержание ОТДЕЛЬНОЙ строкой на каждую единицу товара — по одному
    замеру за день приходит N одинаковых строк (N единиц). Отчёт WB суммирует их
    все, поэтому агрегируем по (dim_id, penalty_date): Σ penalty, Σ reversal,
    units_count = число строк. Прочие поля (габариты/предмет/фото) одинаковы в
    группе — берём из первой строки.
    """
    groups: dict[tuple, dict] = {}
    for r in rows:
        m = _map_penalty_row(project_id, r)
        if not m:
            continue
        key = (m["dim_id"], m["penalty_date"])
        g = groups.get(key)
        if g is None:
            g = dict(m)
            g["penalty_amount"] = Decimal("0")
            g["reversal_amount"] = Decimal("0")
            g["units_count"] = 0
            groups[key] = g
        g["penalty_amount"] += Decimal(str(m["penalty_amount"] or 0))
        g["reversal_amount"] += Decimal(str(m["reversal_amount"] or 0))
        g["units_count"] += 1
    return list(groups.values())


async def _upsert_penalties(db: AsyncSession, project_id: int, rows: list[dict]) -> int:
    batch = _aggregate_penalties(project_id, rows)
    if not batch:
        return 0
    stmt = pg_insert(WbMeasurementPenalty).values(batch)
    update_cols = {
        c: getattr(stmt.excluded, c)
        for c in batch[0].keys()
        if c not in ("project_id", "dim_id", "penalty_date")
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_wb_measurement_penalty_project_dim_date",
        set_=update_cols,
    )
    await db.execute(stmt)
    return len(batch)


async def sync_warehouse_measurements(
    db: AsyncSession, project_id: int, date_from: date, date_to: date
) -> int:
    """Тянет замеры складов за период и upsert-ит их. Возвращает число строк."""
    _key, api_key = await integrations_service._get_wb_key(db, project_id)
    client = WBApiClient(api_key, project_id=project_id)
    rows = await client.get_warehouse_measurements(date_from, date_to)
    count = await _upsert_warehouse(db, project_id, rows)
    await db.commit()
    logger.info("wb_measurements.warehouse_synced", project_id=project_id, rows=count)
    return count


async def sync_measurement_penalties(
    db: AsyncSession, project_id: int, date_from: date, date_to: date
) -> int:
    """Тянет удержания за габариты за период и upsert-ит их. Возвращает число строк."""
    _key, api_key = await integrations_service._get_wb_key(db, project_id)
    client = WBApiClient(api_key, project_id=project_id)
    rows = await client.get_measurement_penalties(date_from, date_to)
    count = await _upsert_penalties(db, project_id, rows)
    await db.commit()
    logger.info("wb_measurements.penalties_synced", project_id=project_id, rows=count)
    return count


async def sync_all_measurements(
    db: AsyncSession, project_id: int, date_from: date, date_to: date
) -> dict:
    """Синк обоих наборов за период. Возвращает {'warehouse': n, 'penalties': m}."""
    wh = await sync_warehouse_measurements(db, project_id, date_from, date_to)
    pen = await sync_measurement_penalties(db, project_id, date_from, date_to)
    return {"warehouse": wh, "penalties": pen}
