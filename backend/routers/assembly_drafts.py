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
    AssemblyDraftClearResponse,
    AssemblyDraftCommitResponse,
    AssemblyDraftCreate,
    AssemblyDraftMergeRequest,
    AssemblyDraftRead,
    AssemblyDraftUnitEdit,
    AssemblyDraftUnitMove,
    AssemblyDraftUnitRef,
    AssemblyDraftUpdate,
    CommitDraftOptions,
    DraftCategoryHistoryPoint,
    DraftCategoryHistoryResponse,
    DraftEventRevertResponse,
    DraftHistoryResponse,
    DraftsReservedResponse,
    ForecastResponse,
)
from backend.services import assembly_draft_service, assembly_load_forecast_service
from backend.services.assembly import draft_category_history, draft_history
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


@router.get("/reserved", response_model=DraftsReservedResponse)
async def get_drafts_reserved(
    exclude_draft_id: int | None = Query(None, description="Черновик, чей собственный план не считать резервом"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> DraftsReservedResponse:
    """Резерв ФФ-стока черновиками: barcode → {ff_warehouse_id → qty}.

    Σ rows.src + prebook.src + handed_units.items всех не-удалённых черновиков
    проекта (кроме exclude_draft_id). Фронт вычитает резерв из доступного ФФ,
    чтобы параллельные черновики (в т.ч. категорийные) не планировали один товар
    дважды. ⚠ Маршрут обязан стоять ВЫШЕ `GET /{draft_id}` — иначе «reserved»
    уйдёт в int-конвертер и даст 422.
    """
    reserved = await assembly_draft_service.get_drafts_reserved(db, project.id, exclude_draft_id)
    return DraftsReservedResponse(reserved=reserved)


@router.get("/category-history", response_model=DraftCategoryHistoryResponse)
async def get_category_history(
    days: int = Query(14, ge=1, le=60, description="Глубина истории в днях"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> DraftCategoryHistoryResponse:
    """Почасовая история наполнения черновиков по категориям (вкладка «Динамика
    черновика»): что «Сборка» предлагала отправить час за часом — видно,
    разбирают ли менеджеры черновик в заявки или он висит. Точки пишет
    почасовая джоба; старые → новые. ⚠ Маршрут обязан стоять ВЫШЕ
    `GET /{draft_id}` — иначе «category-history» уйдёт в int-конвертер (422).
    """
    points = await draft_category_history.get_history(db, project.id, days)
    return DraftCategoryHistoryResponse(
        points=[DraftCategoryHistoryPoint.model_validate(p) for p in points]
    )


@router.post(
    "/category-history/snapshot",
    response_model=DraftCategoryHistoryResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def snapshot_category_history(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> DraftCategoryHistoryResponse:
    """Ручной срез текущего часа (кнопка «Снять срез сейчас»): не ждать джобу
    после правок черновика. Idempotent — срез часа перезаписывается. Возвращает
    точки записанного часа."""
    await draft_category_history.snapshot_project(db, project.id)
    points = await draft_category_history.get_history(db, project.id, 1)
    last_hour = max((p.taken_at for p in points), default=None)
    return DraftCategoryHistoryResponse(
        points=[DraftCategoryHistoryPoint.model_validate(p) for p in points if p.taken_at == last_hour]
    )


@router.get("/{draft_id}/forecast", response_model=ForecastResponse)
async def forecast_draft(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ForecastResponse:
    """Предпросмотр загрузки WB-складов с учётом черновика (вкладка «Прогноз»).

    Текущий остаток WB + входящая поставка − продажи за lead-time → прогноз
    остатка, дней покрытия и светофор по складу; + примерный индекс локализации
    до/после поставки. Read-only, без мутаций. 404 если черновик не найден.
    """
    return await assembly_load_forecast_service.forecast_draft_load(db, project.id, draft_id)


@router.post("/{draft_id}/remove-rows", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def remove_rows(
    draft_id: int,
    nm_ids: list[int] = Body(..., embed=True, description="nm_id товаров для удаления из черновика"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Убрать указанные SKU из черновика (удаление неликвида из вкладки «Прогноз»).

    Удаляет строки и очищает handed-юниты от этих nm_id. Дозабивку паллет фронт
    делает следом (consolidatePalletsInDraftRows). 404 если черновик не найден.
    """
    draft = await assembly_draft_service.remove_rows_by_nm(db, project.id, draft_id, nm_ids)
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
    draft = await assembly_draft_service.update_draft(db, project.id, draft_id, payload, changed_by="user")
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
    Разный категорийный скоуп черновиков → 400 (ValueError сервиса).
    Возвращает объединённый черновик (survivor).
    """
    try:
        draft = await assembly_draft_service.merge_drafts(db, project.id, payload.draft_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.post("/{draft_id}/clear", response_model=AssemblyDraftClearResponse, dependencies=[Depends(rate_limit_write)])
async def clear_draft(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftClearResponse:
    """«Очистить черновик»: сброс наполнения + удаление категорийных черновиков.

    Обнуляет rows/prebook/источники/цели/cold_start_shares (handed_units и
    category_scope живут). Для основного (бесскоупного) черновика дополнительно
    soft-delete'ит категорийные черновики проекта — кроме тех, где есть живые
    handed-юниты (переданы на ФФ): их имена возвращаются в kept_scoped.
    404 если черновик не найден.
    """
    draft, deleted_scoped, kept_scoped = await assembly_draft_service.clear_draft(
        db, project.id, draft_id, changed_by="user"
    )
    return AssemblyDraftClearResponse(
        draft=await assembly_draft_service.to_read_model(db, project.id, draft),
        deleted_scoped=deleted_scoped,
        kept_scoped=kept_scoped,
    )


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
    source_ff_id: int | None = Query(
        default=None,
        description="Коммитить только заявки этого склада-ФФ (источник); порции других ФФ остаются в черновике",
    ),
    options: CommitDraftOptions = Body(default_factory=CommitDraftOptions),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftCommitResponse:
    """Turn a draft into N AssemblyRequests (one per non-zero pair).

    `package_type` (короб/моно) — партиальный коммит по упаковке: всё не выбранное
    остаётся в черновике. Без фильтра коммитит весь черновик. Новинки и обычные
    товары на один склад идут одной заявкой.

    `source_ff_id` — партиальный коммит по складу-ФФ: заявки только из порций этого
    ФФ, остальное (другие ФФ) остаётся в черновике. Идёт через pro-rata, не supplies.

    `options.pallet_counts` (опц.) — паллет на заявку по ключу
    `"{ff_id}::{wb_name}::{pkg}"`; иначе плоский `distribution.pallets_count`.
    """
    return await assembly_draft_service.commit_draft(
        db, project.id, draft_id, package_type, options.pallet_counts, options.supplies,
        source_ff_id=source_ff_id, changed_by="user",
    )


@router.get("/{draft_id}/history", response_model=DraftHistoryResponse)
async def get_draft_history(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> DraftHistoryResponse:
    """История изменений черновика (дозабор / запись из матрицы / создание заявки),
    новейшие первыми, с флагом `can_revert` и причиной запрета отката."""
    return await draft_history.get_draft_history(db, project.id, draft_id)


@router.post(
    "/{draft_id}/history/{event_id}/revert",
    response_model=DraftEventRevertResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def revert_draft_event(
    draft_id: int,
    event_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> DraftEventRevertResponse:
    """Откатить событие истории. 409 если откат недоступен (черновик изменился /
    заявка на ФФ / уже WB-поставка)."""
    return await draft_history.revert_draft_event(db, project.id, draft_id, event_id, changed_by="user")


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
