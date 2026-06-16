# ruff: noqa: RUF001, RUF002, RUF003
"""
Router: интеграция с фулфилментом (skladbot, wmscelicom).

- /warehouse/fulfillment/overview — сводка по всем складам с интеграцией;
- /warehouse/{warehouse_id}/fulfillment/* — per-warehouse endpoints.

Сводный роут живёт на отдельном sub-router'е со статическим префиксом и
подключается к композитному router'у РАНЬШЕ параметризованного — чтобы
«fulfillment» не матчился как {warehouse_id}.

Тонкий HTTP-слой: вся логика — в services/fulfillment_service.py.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.fulfillment import (
    FfBoxOverridePayload,
    FfBoxPack,
    FfCreateAssemblyResult,
    FfLinkCandidatesResponse,
    FfLinkPayload,
    FfNomenclatureOption,
    FfOverviewResponse,
    FfRequestDetail,
    FfRequestRow,
    FfStatusEvent,
    FfStocksResponse,
    FfSyncResult,
    FfSyncRun,
    FfUnlinkedAssembly,
    FulfillmentConnectPayload,
    FulfillmentStatus,
)
from backend.services import fulfillment_service
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(tags=["Fulfillment"])
overview_router = APIRouter(prefix="/warehouse/fulfillment", tags=["Fulfillment"])
wh_router = APIRouter(prefix="/warehouse/{warehouse_id}/fulfillment", tags=["Fulfillment"])


# ─── Сводка по всем складам (без warehouse_id в пути) ────────────────────────


@overview_router.get("/overview", response_model=FfOverviewResponse)
async def fulfillment_overview(
    kind: Literal["assembly", "inbound", "other"] = "assembly",
    warehouse_id: int | None = None,
    only_unlinked: bool = False,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сводка ФФ: все интегрированные склады + заявки зеркала + кандидаты мэтчинга."""
    return await fulfillment_service.get_overview(
        db,
        project.id,
        kind=kind,
        warehouse_id=warehouse_id,
        only_unlinked=only_unlinked,
    )


# ─── Per-warehouse endpoints ─────────────────────────────────────────────────


@wh_router.get("/status", response_model=FulfillmentStatus)
async def get_status(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Статус подключения фулфилмента к складу."""
    return await fulfillment_service.get_status(db, project.id, warehouse_id)


@wh_router.post("/connect", response_model=FulfillmentStatus, dependencies=[Depends(rate_limit_write)])
async def connect(
    warehouse_id: int,
    payload: FulfillmentConnectPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Подключить фулфилмент: валидация токена + сохранение ключа."""
    try:
        return await fulfillment_service.connect(
            db,
            project.id,
            warehouse_id,
            payload.provider,
            payload.token,
            base_url=payload.base_url,
            tenant_guid=payload.tenant_guid,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@wh_router.delete("/connect", dependencies=[Depends(rate_limit_write)])
async def disconnect(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отключить фулфилмент (soft-delete ключа, зеркальные данные остаются)."""
    ok = await fulfillment_service.disconnect(db, project.id, warehouse_id)
    if not ok:
        raise HTTPException(404, "Фулфилмент не подключён к этому складу")
    return {"ok": True}


@wh_router.post("/sync", response_model=FfSyncResult, dependencies=[Depends(rate_limit_write)])
async def sync(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ручной синк остатков и заявок с фулфилмента (с записью в журнал sync_log)."""
    try:
        return await fulfillment_service.sync_warehouse_logged(db, project.id, warehouse_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@wh_router.get("/stocks", response_model=FfStocksResponse)
async def list_stocks(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сверка остатков: ФФ vs наш склад (UNION по barcode)."""
    return await fulfillment_service.list_stocks(db, project.id, warehouse_id)


@wh_router.get("/box-packs", response_model=list[FfBoxPack])
async def list_box_packs(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сопоставление короб→россыпь: ШК короба → ШК россыпи → штук → номенклатура (auto/manual/unmapped)."""
    return await fulfillment_service.list_box_packs(db, project.id, warehouse_id)


@wh_router.get("/box-packs/nomenclature-search", response_model=list[FfNomenclatureOption])
async def box_pack_nomenclature_search(
    warehouse_id: int,
    q: str = "",
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Поиск нашей номенклатуры с ШК для ручной привязки короба (по артикулу/ШК)."""
    return await fulfillment_service.search_nomenclature(db, project.id, q)


@wh_router.put("/box-packs/{box_barcode}/override", response_model=FfBoxPack, dependencies=[Depends(rate_limit_write)])
async def set_box_override(
    warehouse_id: int,
    box_barcode: str,
    payload: FfBoxOverridePayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ручное сопоставление короба: привязать нашу номенклатуру + штук в коробе."""
    try:
        result = await fulfillment_service.set_box_override(
            db, project.id, warehouse_id, box_barcode, payload.nomenclature_id, payload.units_per_box
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if result is None:
        raise HTTPException(404, "Короб с таким ШК не найден в остатках склада")
    return result


@wh_router.delete(
    "/box-packs/{box_barcode}/override", response_model=FfBoxPack | None, dependencies=[Depends(rate_limit_write)]
)
async def delete_box_override(
    warehouse_id: int,
    box_barcode: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Снять ручное сопоставление короба — вернуть к авто-выводу."""
    return await fulfillment_service.delete_box_override(db, project.id, warehouse_id, box_barcode)


@wh_router.get("/requests", response_model=list[FfRequestRow])
async def list_requests(
    warehouse_id: int,
    kind: str | None = None,
    show_archived: bool = False,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Зеркало заявок ФФ (kind: assembly | inbound | other; show_archived — вид «Архив»)."""
    return await fulfillment_service.list_requests(db, project.id, warehouse_id, kind, show_archived=show_archived)


@wh_router.get("/unlinked-assemblies", response_model=list[FfUnlinkedAssembly])
async def unlinked_assemblies(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Наши активные заявки на сборку склада без связанной ФФ-заявки (обратный линк)."""
    return await fulfillment_service.list_unlinked_assemblies(db, project.id, warehouse_id)


@wh_router.get("/status-history", response_model=list[FfStatusEvent])
async def status_history(
    warehouse_id: int,
    kind: str | None = None,
    ff_request_id: int | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История синхронизации: журнал смены статусов/стадий заявок ФФ склада.

    kind — фильтр по типу (assembly|inbound|other); ff_request_id — история
    конкретной заявки (для деталки).
    """
    return await fulfillment_service.list_status_events(
        db, project.id, warehouse_id, kind=kind, ff_request_id=ff_request_id
    )


@wh_router.get("/sync-runs", response_model=list[FfSyncRun])
async def sync_runs(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Журнал прогонов синхронизации ФФ-склада: когда синкали, статус, объём.

    Питает вкладку «ФФ синхронизация» — авто-синк по расписанию + ручной запуск.
    """
    return await fulfillment_service.list_sync_runs(db, project.id, warehouse_id)


@wh_router.post(
    "/requests/{ff_request_id}/archive",
    response_model=FfRequestRow,
    dependencies=[Depends(rate_limit_write)],
)
async def archive_request(
    warehouse_id: int,
    ff_request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Убрать ФФ-заявку в локальный архив DDS (синк пометку не трогает)."""
    row = await fulfillment_service.archive_request(db, project.id, ff_request_id, warehouse_id)
    if row is None:
        raise HTTPException(404, "ФФ-заявка не найдена")
    return row


@wh_router.delete(
    "/requests/{ff_request_id}/archive",
    response_model=FfRequestRow,
    dependencies=[Depends(rate_limit_write)],
)
async def unarchive_request(
    warehouse_id: int,
    ff_request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Вернуть ФФ-заявку из локального архива."""
    row = await fulfillment_service.unarchive_request(db, project.id, ff_request_id, warehouse_id)
    if row is None:
        raise HTTPException(404, "ФФ-заявка не найдена")
    return row


@wh_router.get("/requests/{ff_request_id}/link-candidates", response_model=FfLinkCandidatesResponse)
async def link_candidates(
    warehouse_id: int,
    ff_request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Кандидаты для модалки «Связать»: наши документы склада со скорингом по составу."""
    try:
        data = await fulfillment_service.get_link_candidates(db, project.id, warehouse_id, ff_request_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if data is None:
        raise HTTPException(404, "ФФ-заявка не найдена")
    return data


@wh_router.post(
    "/requests/{ff_request_id}/create-assembly",
    response_model=FfCreateAssemblyResult,
    dependencies=[Depends(rate_limit_write)],
)
async def create_assembly_from_ff(
    warehouse_id: int,
    ff_request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать заявку на сборку из состава ФФ-заявки и сразу связать их."""
    try:
        data = await fulfillment_service.create_assembly_from_ff(db, project.id, warehouse_id, ff_request_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if data is None:
        raise HTTPException(404, "ФФ-заявка не найдена")
    return data


@wh_router.get("/requests/{ff_request_id}/detail", response_model=FfRequestDetail)
async def request_detail(
    warehouse_id: int,
    ff_request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Деталка ФФ-заявки: шапка + живой состав от провайдера (товары, поля, стадии)."""
    try:
        row = await fulfillment_service.get_request_detail(db, project.id, warehouse_id, ff_request_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if row is None:
        raise HTTPException(404, "ФФ-заявка не найдена")
    return row


@wh_router.post(
    "/requests/{ff_request_id}/link",
    response_model=FfRequestRow,
    dependencies=[Depends(rate_limit_write)],
)
async def link_request(
    warehouse_id: int,
    ff_request_id: int,
    payload: FfLinkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Привязать ФФ-заявку к нашему документу (заявка на сборку / приёмка)."""
    try:
        row = await fulfillment_service.link_request(
            db,
            project.id,
            ff_request_id,
            assembly_request_id=payload.assembly_request_id,
            inbound_receipt_id=payload.inbound_receipt_id,
            warehouse_id=warehouse_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if row is None:
        raise HTTPException(404, "ФФ-заявка не найдена")
    return row


@wh_router.delete(
    "/requests/{ff_request_id}/link",
    response_model=FfRequestRow,
    dependencies=[Depends(rate_limit_write)],
)
async def unlink_request(
    warehouse_id: int,
    ff_request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Снять связь ФФ-заявки с нашими документами."""
    row = await fulfillment_service.unlink_request(db, project.id, ff_request_id, warehouse_id=warehouse_id)
    if row is None:
        raise HTTPException(404, "ФФ-заявка не найдена")
    return row


# Статический префикс — раньше параметризованного (защита от матча
# «fulfillment» как {warehouse_id}; сегментность путей и так различает их).
router.include_router(overview_router)
router.include_router(wh_router)
