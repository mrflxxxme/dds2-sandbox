"""Предзаявка (бронь) на моно.

Целая моно-паллета на WB-склад БЕЗ лимита приёмки (⌛) — сдать её можно только
предзаявкой. Из вкладки «Предбронь → Предзаявка» пользователь создаёт заявки на
сборку сразу с флагом ``is_prebooking`` (реальный сток на ФФ, статус IN_PROGRESS —
обычный поток; отличие от предраспределения машины — товар уже на складе).

Строки группируются по (ФФ-источник, WB-склад, упаковка) → одна заявка на группу.
Валидация ⌛/whitelist делается на фронте (там уже загружена приёмка); бэк только
проставляет пометку и создаёт заявки с обычной проверкой стока. См.
``.claude/PREBOOKING_DESIGN.md``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest
from backend.models.cost import Nomenclature
from backend.schemas.assembly import (
    AssemblyItemCreate,
    AssemblyRequestCreate,
    AssemblyRequestResponse,
    PackageTypeStr,
    PrebookingCreate,
    PrebookingCreateResult,
)

from .crud import _build_response, create_assembly_request, get_assembly_request


async def create_prebooking(
    db: AsyncSession, project_id: int, payload: PrebookingCreate
) -> PrebookingCreateResult:
    """Создать заявки-предзаявки (is_prebooking=True) из строк предброни.

    Строки группируются по (ФФ-источник, WB-склад, упаковка) → одна заявка на группу.
    Товар реально на ФФ → обычная валидация стока, статус по умолчанию IN_PROGRESS.
    Fail-fast ДО создания: непустой список, qty>0, заданы склады, все ШК в номенклатуре
    (иначе create_assembly_request упал бы мид-циклом, оставив частично созданные заявки).
    """
    if not payload.rows:
        raise ValueError("Нет строк для предзаявки")

    for r in payload.rows:
        if r.qty <= 0:
            raise ValueError(f"Некорректное количество для {r.barcode}")
        if not r.wb_warehouse_name.strip():
            raise ValueError(f"Не задан склад WB для {r.barcode}")
        if r.warehouse_id <= 0:
            raise ValueError(f"Не задан ФФ-склад-источник для {r.barcode}")

    # Все ШК должны резолвиться в номенклатуру проекта (fail-fast).
    barcodes = {r.barcode for r in payload.rows}
    nom_result = await db.execute(
        select(Nomenclature.barcode).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(list(barcodes)),
        )
    )
    found = set(nom_result.scalars().all())
    missing = sorted(barcodes - found)
    if missing:
        raise ValueError(f"Нет в номенклатуре проекта: {', '.join(missing)}")

    # Группировка по (ФФ-источник, WB-склад, упаковка) → одна заявка на группу.
    groups: dict[tuple[int, str, str], dict[str, int]] = {}
    for r in payload.rows:
        key = (r.warehouse_id, r.wb_warehouse_name.strip(), r.package_type)
        bucket = groups.setdefault(key, {})
        bucket[r.barcode] = bucket.get(r.barcode, 0) + r.qty

    created: list[AssemblyRequest] = []
    for (ff_id, wb_name, pkg), bc_qty in groups.items():
        items = [AssemblyItemCreate(barcode=bc, quantity=q) for bc, q in bc_qty.items()]
        create_payload = AssemblyRequestCreate(
            warehouse_id=ff_id,
            pallets_count=0,  # NOT NULL → 0 при создании (как в предраспределении)
            pallet_weight_kg=Decimal("0"),
            wb_warehouse_name_manual=wb_name,
            package_type=cast(PackageTypeStr, pkg),
            items=items,
        )
        req = await create_assembly_request(
            db, project_id, create_payload, is_prebooking=True
        )
        created.append(req)

    # create_assembly_request инвалидирует reports:* на каждом вызове.
    # Перечитываем через get_assembly_request (selectinload) перед _build_response —
    # иначе lazy-load релейшенов на свежесозданном объекте (MissingGreenlet).
    responses: list[AssemblyRequestResponse] = []
    for req in created:
        loaded = await get_assembly_request(db, project_id, req.id)
        if loaded is None:  # pragma: no cover — только что создали в этой сессии
            continue
        responses.append(AssemblyRequestResponse(**await _build_response(db, loaded)))

    return PrebookingCreateResult(
        created=len(created),
        request_ids=[r.id for r in created],
        requests=responses,
    )
