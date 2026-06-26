# ruff: noqa: RUF002
"""
Router: /assembly/drafts — Assembly draft CRUD + commit to N AssemblyRequests.

Drafts are persistent NxM distribution plans (RF source warehouses x WB
target warehouses) used by the «Создать сборку» flow on the warehouse
analytics page. Commit turns a balanced draft into one AssemblyRequest per
unique (source_ff, target_wb) pair with non-zero qty, then soft-deletes
the draft.
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.assembly_draft import (
    AssemblyDraftAddRows,
    AssemblyDraftCommitResponse,
    AssemblyDraftCreate,
    AssemblyDraftMergeRequest,
    AssemblyDraftRead,
    AssemblyDraftUnitEdit,
    AssemblyDraftUnitMove,
    AssemblyDraftUnitRef,
    AssemblyDraftUpdate,
    CommitDraftOptions,
)
from backend.services import assembly_draft_service
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/assembly/drafts", tags=["Assembly Drafts"])


@router.get("", response_model=list[AssemblyDraftRead])
async def list_drafts(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[AssemblyDraftRead]:
    """List non-deleted drafts of current project, newest-updated first."""
    drafts = await assembly_draft_service.list_drafts(db, project.id)
    return [await assembly_draft_service.to_read_model(db, project.id, d) for d in drafts]


@router.post("", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def create_draft(
    payload: AssemblyDraftCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Create a new draft."""
    draft = await assembly_draft_service.create_draft(db, project.id, payload)
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.post("/current", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def get_or_create_current_draft(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Единственный «текущий» черновик проекта (синглтон).

    Лениво консолидирует: нет → создаёт пустой; один → возвращает его; несколько →
    объединяет все в один (merge_drafts) и возвращает survivor. Единая страница
    «Сборка» зовёт это на входе — гарантия ровно одного активного черновика.

    POST (не GET): может создавать/сливать/soft-delete'ить черновики → честнее по
    семантике + покрыт `rate_limit_write`. Идемпотентен в установившемся состоянии
    (один черновик → тот же объект).
    """
    draft = await assembly_draft_service.get_or_create_current_draft(db, project.id)
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.get("/{draft_id}", response_model=AssemblyDraftRead)
async def get_draft(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Get a single draft by id (404 if missing or deleted)."""
    draft = await assembly_draft_service.get_draft(db, project.id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.put("/{draft_id}", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def update_draft(
    draft_id: int,
    payload: AssemblyDraftUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Update mutable fields of a draft."""
    draft = await assembly_draft_service.update_draft(db, project.id, draft_id, payload)
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.post("/{draft_id}/rows", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def add_rows_to_draft(
    draft_id: int,
    payload: AssemblyDraftAddRows,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Дозалить пачку строк в существующий черновик, не теряя ручных правок.

    В отличие от PUT (full-replace, затирает handed_units), строки сливаются с
    `distribution.rows`: совпадающий (nm_id, package_type) суммируется поэлементно,
    новые ключи дописываются. source/target склады объединяются; handed_units,
    cold_start_shares, pallets_* не трогаются. 404 если черновик не найден.
    """
    draft = await assembly_draft_service.add_rows_to_draft(db, project.id, draft_id, payload.rows)
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.post("/merge", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def merge_drafts(
    payload: AssemblyDraftMergeRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Объединить N черновиков в один.

    Строки с совпадающим (nm_id, package_type) суммируются поэлементно;
    source/target склады объединяются; cold_start_shares сбрасывается.
    handed_units переносятся в survivor (юниты одного ключа сливаются,
    'handed' побеждает 'draft'). Несуществующие id → 404.
    Возвращает объединённый черновик (survivor).
    """
    draft = await assembly_draft_service.merge_drafts(db, project.id, payload.draft_ids)
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.delete("/{draft_id}", status_code=204, dependencies=[Depends(rate_limit_write)])
async def delete_draft(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a draft."""
    await assembly_draft_service.delete_draft(db, project.id, draft_id)


@router.post(
    "/{draft_id}/commit",
    response_model=AssemblyDraftCommitResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def commit_draft(
    draft_id: int,
    package_type: str | None = Query(
        default=None,
        description="Коммитить только этот тип упаковки (BOX/MONOPALLET); остальное остаётся в черновике",
    ),
    options: CommitDraftOptions = Body(default_factory=CommitDraftOptions),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftCommitResponse:
    """Turn a draft into N AssemblyRequests (one per non-zero pair).

    `package_type` (короб/моно) — партиальный коммит по упаковке: всё не выбранное
    остаётся в черновике. Без фильтра коммитит весь черновик. Новинки и обычные
    товары на один склад идут одной заявкой.

    `options.pallet_counts` (опц.) — паллет на заявку по ключу
    `"{ff_id}::{wb_name}::{pkg}"`; иначе плоский `distribution.pallets_count`.
    """
    return await assembly_draft_service.commit_draft(
        db, project.id, draft_id, package_type, options.pallet_counts, options.supplies,
    )


@router.post(
    "/{draft_id}/units/hand-off",
    response_model=AssemblyDraftRead,
    dependencies=[Depends(rate_limit_write)],
)
async def hand_off_unit(
    draft_id: int,
    unit: AssemblyDraftUnitRef,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """«Передать на ФФ»: заморозить заявку-юнит (вырезать из rows в handed_units)."""
    return await assembly_draft_service.hand_off_unit(
        db,
        project.id,
        draft_id,
        unit.source_ff_id,
        unit.target_wb_name,
        unit.package_type,
    )


@router.post(
    "/{draft_id}/units/revert",
    response_model=AssemblyDraftRead,
    dependencies=[Depends(rate_limit_write)],
)
async def revert_unit(
    draft_id: int,
    unit: AssemblyDraftUnitRef,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """«Вернуть в черновик»: вернуть позиции замороженного юнита обратно в rows."""
    return await assembly_draft_service.revert_unit(
        db,
        project.id,
        draft_id,
        unit.source_ff_id,
        unit.target_wb_name,
        unit.package_type,
    )


@router.post(
    "/{draft_id}/units/commit",
    response_model=AssemblyDraftCommitResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def commit_unit(
    draft_id: int,
    unit: AssemblyDraftUnitRef,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftCommitResponse:
    """«В сборку»: создать AssemblyRequest из замороженного юнита (в общий список)."""
    return await assembly_draft_service.commit_unit(
        db,
        project.id,
        draft_id,
        unit.source_ff_id,
        unit.target_wb_name,
        unit.package_type,
    )


@router.post(
    "/{draft_id}/units/items",
    response_model=AssemblyDraftRead,
    dependencies=[Depends(rate_limit_write)],
)
async def set_unit_items(
    draft_id: int,
    edit: AssemblyDraftUnitEdit,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Заменить наполнение заявки-юнита (правка черновика): фиксирует авто-юнит и
    сохраняет новый состав. Переданный на ФФ юнит править нельзя."""
    return await assembly_draft_service.set_unit_items(
        db,
        project.id,
        draft_id,
        edit.source_ff_id,
        edit.target_wb_name,
        edit.package_type,
        edit.items,
    )


@router.post(
    "/{draft_id}/units/move",
    response_model=AssemblyDraftRead,
    dependencies=[Depends(rate_limit_write)],
)
async def move_unit(
    draft_id: int,
    move: AssemblyDraftUnitMove,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """«Сменить склад WB»: перенести заявку-юнит этого ФФ на другой WB-склад
    (поток ff→wb); на складе-получателе сливается с существующим черновиком."""
    return await assembly_draft_service.move_unit(
        db,
        project.id,
        draft_id,
        move.source_ff_id,
        move.target_wb_name,
        move.package_type,
        move.new_target_wb_name,
    )


@router.post(
    "/{draft_id}/units/delete",
    response_model=AssemblyDraftRead,
    dependencies=[Depends(rate_limit_write)],
)
async def delete_unit(
    draft_id: int,
    unit: AssemblyDraftUnitRef,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Удалить заявку-юнит из черновика целиком (товар остаётся на ФФ)."""
    return await assembly_draft_service.delete_unit(
        db,
        project.id,
        draft_id,
        unit.source_ff_id,
        unit.target_wb_name,
        unit.package_type,
    )
