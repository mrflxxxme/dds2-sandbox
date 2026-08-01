# ruff: noqa: RUF002, RUF003
"""
FF billing — тарифы услуг ФФ по складам: CRUD + резолв активной ставки на дату.

История без пересечений держится сервисом: при создании нового тарифа той же
услуги предыдущий «открытый» (valid_to IS NULL) закрывается днём накануне.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.ff_billing import WarehouseTariff
from backend.models.warehouse import Warehouse
from backend.schemas.ff_billing import (
    UNIT_SERVICES,
    FfTariffPayload,
    FfTariffRow,
    FfTariffUpdatePayload,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ff_billing")

_TARIFFS_LIMIT = 500


async def _get_warehouse(db: AsyncSession, project_id: int, warehouse_id: int) -> Warehouse:
    res = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712
        )
    )
    wh = res.scalar_one_or_none()
    if wh is None:
        raise HTTPException(status_code=404, detail="Склад не найден")
    return wh


@cached(prefix="ff_billing:tariffs", ttl=300)
async def list_tariffs(db: AsyncSession, project_id: int, warehouse_id: int) -> list[dict]:
    """JSON-сериализуемые строки (кэшируются) — роутер валидирует в FfTariffRow."""
    await _get_warehouse(db, project_id, warehouse_id)
    rows = await db.execute(
        select(WarehouseTariff)
        .where(
            WarehouseTariff.project_id == project_id,
            WarehouseTariff.warehouse_id == warehouse_id,
            WarehouseTariff.is_deleted == False,  # noqa: E712
        )
        .order_by(WarehouseTariff.service_type.asc(), WarehouseTariff.valid_from.desc())
        .limit(_TARIFFS_LIMIT)
    )
    return [FfTariffRow.model_validate(t).model_dump(mode="json") for t in rows.scalars().all()]


async def create_tariff(
    db: AsyncSession, project_id: int, warehouse_id: int, payload: FfTariffPayload
) -> WarehouseTariff:
    """Создать ставку; предыдущая открытая ставка той же услуги закрывается
    (valid_to = новый valid_from − 1 день) — история остаётся без пересечений."""
    await _get_warehouse(db, project_id, warehouse_id)
    valid_from = payload.valid_from or utcnow().date()
    if payload.valid_to is not None and payload.valid_to < valid_from:
        raise HTTPException(status_code=422, detail="valid_to раньше valid_from")

    open_rows = await db.execute(
        select(WarehouseTariff).where(
            WarehouseTariff.project_id == project_id,
            WarehouseTariff.warehouse_id == warehouse_id,
            WarehouseTariff.service_type == payload.service_type,
            WarehouseTariff.is_deleted == False,  # noqa: E712
            WarehouseTariff.valid_to.is_(None),
        )
    )
    for prev in open_rows.scalars().all():
        if prev.valid_from >= valid_from:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Уже есть открытая ставка {payload.service_type} с valid_from="
                    f"{prev.valid_from.isoformat()} — новая должна начинаться позже"
                ),
            )
        prev.valid_to = valid_from - timedelta(days=1)

    tariff = WarehouseTariff(
        project_id=project_id,
        warehouse_id=warehouse_id,
        service_type=payload.service_type,
        unit=payload.unit,
        rate=payload.rate,
        valid_from=valid_from,
        valid_to=payload.valid_to,
    )
    db.add(tariff)
    await db.commit()
    await db.refresh(tariff)
    await invalidate_cache("ff_billing:tariffs")
    return tariff


async def _get_tariff(db: AsyncSession, project_id: int, tariff_id: int) -> WarehouseTariff:
    res = await db.execute(
        select(WarehouseTariff).where(
            WarehouseTariff.id == tariff_id,
            WarehouseTariff.project_id == project_id,
            WarehouseTariff.is_deleted == False,  # noqa: E712
        )
    )
    tariff = res.scalar_one_or_none()
    if tariff is None:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    return tariff


async def update_tariff(
    db: AsyncSession, project_id: int, tariff_id: int, payload: FfTariffUpdatePayload
) -> WarehouseTariff:
    tariff = await _get_tariff(db, project_id, tariff_id)
    if payload.unit is not None and tariff.service_type not in UNIT_SERVICES:
        raise HTTPException(
            status_code=422, detail="unit допустим только для LOADING и TRANSFER_ASSEMBLY"
        )
    if payload.unit is not None:
        tariff.unit = payload.unit
    if payload.rate is not None:
        tariff.rate = payload.rate
    if payload.valid_from is not None:
        tariff.valid_from = payload.valid_from
    if payload.valid_to is not None:
        tariff.valid_to = payload.valid_to
    if tariff.valid_to is not None and tariff.valid_to < tariff.valid_from:
        raise HTTPException(status_code=422, detail="valid_to раньше valid_from")
    await db.commit()
    await db.refresh(tariff)
    await invalidate_cache("ff_billing:tariffs")
    return tariff


async def delete_tariff(db: AsyncSession, project_id: int, tariff_id: int) -> None:
    tariff = await _get_tariff(db, project_id, tariff_id)
    tariff.soft_delete()
    await db.commit()
    await invalidate_cache("ff_billing:tariffs")


async def resolve_rates(
    db: AsyncSession, project_id: int, warehouse_id: int, on_date: date
) -> dict[str, tuple[Decimal, str | None]]:
    """Активные ставки склада на дату: {service_type: (rate, unit)}.

    Активная = is_deleted=False, valid_from<=d<=(valid_to or ∞); при пересечении
    периодов побеждает max(valid_from) (при равенстве — более поздняя запись)."""
    rows = await db.execute(
        select(WarehouseTariff)
        .where(
            WarehouseTariff.project_id == project_id,
            WarehouseTariff.warehouse_id == warehouse_id,
            WarehouseTariff.is_deleted == False,  # noqa: E712
            WarehouseTariff.valid_from <= on_date,
            or_(WarehouseTariff.valid_to.is_(None), WarehouseTariff.valid_to >= on_date),
        )
        .order_by(WarehouseTariff.valid_from.asc(), WarehouseTariff.id.asc())
        .limit(_TARIFFS_LIMIT)
    )
    out: dict[str, tuple[Decimal, str | None]] = {}
    for t in rows.scalars().all():
        out[t.service_type] = (t.rate, t.unit)  # asc-порядок → последняя запись побеждает
    return out
