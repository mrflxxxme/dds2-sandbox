# ruff: noqa: RUF002, RUF003
"""
Анализ сборки → вкладка «Связи и расхождения».

Четыре блока (read-only, по зеркалу, без HTTP):
  1. ff_composition_mismatch — наша сборка ≠ привязанные заявки ФФ по наполнению
     (только активные: IN_PROGRESS / READY / VEHICLE_ASSIGNED).
  2. assemblies_without_ff — наши сборки на ФФ-складах без привязанной заявки ФФ.
  3. ff_without_assembly — заявки ФФ (kind=assembly) без привязанной нашей сборки.
  4. fbo — сводка аномалий FBO-поставок ВБ (без заявки / недоприёмка / излишек),
     drill-through на /warehouse/fbo-supplies (реюз fbo_supply.service).

Контракт (сигнатура + shape ответа = LinkAnomaliesResponse) — в
backend/schemas/assembly.py.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.auth import Project
from backend.models.fulfillment import FfRequestKind, FulfillmentRequest
from backend.models.integrations import IntegrationKey
from backend.models.warehouse import Warehouse
from backend.models.wb_fbo import WbFboSupply, WbSupplyStatus
from backend.services import fulfillment_service
from backend.services.fbo_supply import service as fbo_service
from backend.utils.time import utcnow

# Активные статусы для блока ff_composition_mismatch («в сборке» + «готово»).
# SHIPPED/закрытые НЕ участвуют — расхождение состава с ФФ для них неактуально.
_MISMATCH_STATUSES: tuple[str, ...] = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
)
# Для assemblies_without_ff включаем и SHIPPED: отгруженная сборка без привязки
# к ФФ на ФФ-складе — тоже сигнал пропущенной связи.
_UNLINKED_STATUSES: tuple[str, ...] = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
    AssemblyStatus.SHIPPED.value,
)


async def _warehouse_names(db: AsyncSession, project_id: int, wh_ids: set[int]) -> dict[int, str]:
    """Батч-резолв warehouse_id → name (без N+1). project_id обязателен.

    Включаем soft-deleted (display-only FK-резолв), как в analytics.py.
    """
    if not wh_ids:
        return {}
    rows = (
        await db.execute(
            select(Warehouse.id, Warehouse.name).where(
                Warehouse.project_id == project_id,
                Warehouse.id.in_(wh_ids),
            )
        )
    ).all()
    return {row.id: row.name for row in rows}


async def _assembly_qty_map(db: AsyncSession, project_id: int, asm_ids: list[int]) -> dict[int, int]:
    """Батч-агрегат SUM(quantity) по сборкам (без N+1)."""
    if not asm_ids:
        return {}
    rows = (
        await db.execute(
            select(
                AssemblyRequestItem.assembly_request_id,
                func.coalesce(func.sum(AssemblyRequestItem.quantity), 0).label("qty"),
            )
            .where(
                AssemblyRequestItem.project_id == project_id,
                AssemblyRequestItem.assembly_request_id.in_(asm_ids),
            )
            .group_by(AssemblyRequestItem.assembly_request_id)
        )
    ).all()
    return {row.assembly_request_id: int(row.qty) for row in rows}


async def _ff_composition_mismatch(db: AsyncSession, project_id: int, warehouse_ids: list[int] | None) -> list[dict]:
    """Блок 1: активные сборки с расхождением состава против привязанных заявок ФФ."""
    asm_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        AssemblyRequest.status.in_(_MISMATCH_STATUSES),
    ]
    if warehouse_ids:
        asm_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))

    asms = list((await db.execute(select(AssemblyRequest).where(*asm_filters))).scalars().all())
    if not asms:
        return []
    asm_by_id = {a.id: a for a in asms}

    mismatch_map = await fulfillment_service.get_assembly_ff_mismatch_map(db, project_id, set(asm_by_id))
    diverging_ids = [aid for aid, verdict in mismatch_map.items() if verdict is True]
    if not diverging_ids:
        return []

    wh_names = await _warehouse_names(db, project_id, {asm_by_id[aid].warehouse_id for aid in diverging_ids})

    rows: list[dict] = []
    for aid in diverging_ids:
        asm = asm_by_id[aid]
        detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db, project_id, aid)
        if detail is None:
            continue
        our_total = int(detail["our_total"])
        ff_total = int(detail["ff_total"])
        rows.append(
            {
                "assembly_id": aid,
                "number": asm.number,
                "status": asm.status,
                "warehouse_id": asm.warehouse_id,
                "warehouse_name": wh_names.get(asm.warehouse_id),
                "ff_request_numbers": [n for n in detail["ff_request_numbers"] if n],
                "our_total": our_total,
                "ff_total": ff_total,
                "diff": ff_total - our_total,
                "mode": detail["mode"],
            }
        )
    # Стабильный порядок: крупнейшее расхождение первым, затем по id.
    rows.sort(key=lambda r: (-abs(r["diff"]), r["assembly_id"]))
    return rows


async def _ff_warehouse_providers(db: AsyncSession, project_id: int) -> dict[int, str]:
    """warehouse_id → провайдер ФФ-интеграции (IntegrationKey.service).

    При нескольких ключах на склад берётся первый по id (детерминированно).
    """
    rows = (
        await db.execute(
            select(IntegrationKey.warehouse_id, IntegrationKey.service)
            .where(
                IntegrationKey.project_id == project_id,
                IntegrationKey.warehouse_id.is_not(None),
                IntegrationKey.service.in_(fulfillment_service.FF_SERVICES),
                IntegrationKey.is_deleted == False,  # noqa: E712
            )
            .order_by(IntegrationKey.id)
        )
    ).all()
    providers: dict[int, str] = {}
    for wh_id, service in rows:
        providers.setdefault(wh_id, service)
    return providers


async def _assemblies_without_ff(db: AsyncSession, project_id: int, warehouse_ids: list[int] | None) -> list[dict]:
    """Блок 2: активные сборки на ФФ-интегрированных складах без привязки к ФФ."""
    # ФФ-интегрированные склады = DISTINCT warehouse_id из FulfillmentRequest проекта.
    ff_wh_rows = (
        await db.execute(
            select(FulfillmentRequest.warehouse_id).where(FulfillmentRequest.project_id == project_id).distinct()
        )
    ).all()
    ff_warehouse_ids = {row[0] for row in ff_wh_rows if row[0] is not None}
    if not ff_warehouse_ids:
        return []

    # Сборки, у которых УЖЕ есть привязанная (живая) заявка ФФ.
    linked_rows = (
        await db.execute(
            select(FulfillmentRequest.assembly_request_id)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.assembly_request_id.is_not(None),
                FulfillmentRequest.archived == False,  # noqa: E712
                FulfillmentRequest.local_archived == False,  # noqa: E712
            )
            .distinct()
        )
    ).all()
    linked_assembly_ids = {row[0] for row in linked_rows if row[0] is not None}

    asm_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        AssemblyRequest.status.in_(_UNLINKED_STATUSES),
        AssemblyRequest.warehouse_id.in_(ff_warehouse_ids),
    ]
    if warehouse_ids:
        asm_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))
    if linked_assembly_ids:
        asm_filters.append(AssemblyRequest.id.not_in(linked_assembly_ids))

    asms = list((await db.execute(select(AssemblyRequest).where(*asm_filters))).scalars().all())
    if not asms:
        return []

    qty_map = await _assembly_qty_map(db, project_id, [a.id for a in asms])
    wh_names = await _warehouse_names(db, project_id, {a.warehouse_id for a in asms})
    providers = await _ff_warehouse_providers(db, project_id)
    now = utcnow()

    rows: list[dict] = [
        {
            "assembly_id": a.id,
            "number": a.number,
            "status": a.status,
            "warehouse_id": a.warehouse_id,
            "warehouse_name": wh_names.get(a.warehouse_id),
            "provider": providers.get(a.warehouse_id),
            "total_qty": qty_map.get(a.id, 0),
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "age_days": (now - a.created_at).days if a.created_at else 0,
        }
        for a in asms
    ]
    rows.sort(key=lambda r: (-r["age_days"], r["assembly_id"]))
    return rows


def _ff_request_is_active(
    provider: str, stage_code: str | None, stage_title: str | None, is_completed: bool, expired: bool
) -> bool:
    """Заявка ФФ ещё в работе («в сборке»/«новая»), а не собрана/закрыта/завершена.

    Завершённые исключаем (их в блок «без нашей сборки» класть не нужно):
    - is_completed (отгружено/закрыто у всех провайдеров) или expired;
    - migfull: stage_code 'ready' («Собран») / 'closed' («Закрыт»);
    - wmscelicom: stage_title «Собрана»/«Ожидает отгрузки».
    skladbot и прочие — активна, пока не is_completed (агрессивный deny-list
    READY-сигнала тут НЕ применяем: ранние стадии skladbot, помеченные им как
    «готово», на деле ещё актуальны для привязки).
    """
    if is_completed or expired:
        return False
    if provider == "migfull":
        return (stage_code or "").strip().lower() not in ("ready", "closed")
    if provider == "wmscelicom":
        return (stage_title or "").strip() not in fulfillment_service.WMS_ASSEMBLY_READY_TITLES
    return True


async def _ff_without_assembly(db: AsyncSession, project_id: int, warehouse_ids: list[int] | None) -> list[dict]:
    """Блок 3: АКТИВНЫЕ заявки ФФ (kind=assembly) без привязанной нашей сборки.

    Собранные/закрытые/завершённые не показываем — только то, что ещё в работе
    (см. _ff_request_is_active): это сигнал «надо завести/привязать сборку».
    """
    ff_filters = [
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.kind == FfRequestKind.ASSEMBLY.value,
        FulfillmentRequest.assembly_request_id.is_(None),
        FulfillmentRequest.archived == False,  # noqa: E712
        FulfillmentRequest.local_archived == False,  # noqa: E712
        FulfillmentRequest.is_completed == False,  # noqa: E712
        FulfillmentRequest.expired == False,  # noqa: E712
    ]
    if warehouse_ids:
        ff_filters.append(FulfillmentRequest.warehouse_id.in_(warehouse_ids))

    reqs = [
        r
        for r in (await db.execute(select(FulfillmentRequest).where(*ff_filters))).scalars().all()
        if _ff_request_is_active(r.provider, r.stage_code, r.stage_title, r.is_completed, r.expired)
    ]
    if not reqs:
        return []

    wh_names = await _warehouse_names(db, project_id, {r.warehouse_id for r in reqs})

    rows: list[dict] = [
        {
            "ff_request_id": r.id,
            "provider": r.provider,
            "number": r.number,
            "warehouse_id": r.warehouse_id,
            "warehouse_name": wh_names.get(r.warehouse_id),
            "stage_title": r.stage_title,
            "status": r.status,
            "total_qty": r.total_qty,
            "external_created_at": r.external_created_at.isoformat() if r.external_created_at else None,
        }
        for r in reqs
    ]
    rows.sort(key=lambda r: r["ff_request_id"])
    return rows


async def _fbo_rollup(db: AsyncSession, project_id: int) -> dict:
    """Блок 4: сводка аномалий FBO-поставок ВБ (project-global, без warehouse-фильтра).

    Счётчики берутся из fbo_supply.service с тем же accounting_started_at, что и
    дефолтный вид /warehouse/fbo-supplies — иначе цифры разойдутся со страницей.
    """
    acc = (await db.execute(select(Project.accounting_started_at).where(Project.id == project_id))).scalar_one_or_none()

    counts = await fbo_service.get_fbo_summary(db, project_id, accounting_started_at=acc)
    partial = await fbo_service.get_partial_acceptance_summary(db, project_id, accounting_started_at=acc)

    # Излишек (необработанный): SUM(accepted_qty - total_qty) по принятым поставкам,
    # где принято больше заявленного и излишек ещё не списан.
    excess_qty = (
        await db.execute(
            select(func.sum(WbFboSupply.accepted_qty - WbFboSupply.total_qty)).where(
                WbFboSupply.project_id == project_id,
                WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
                WbFboSupply.total_qty > 0,
                WbFboSupply.accepted_qty > WbFboSupply.total_qty,
                WbFboSupply.excess_processed_at.is_(None),
            )
        )
    ).scalar()

    return {
        "without_assembly_count": int(counts["accepted_without_assembly"]),
        "under_accepted_count": int(counts["accepted_partial"]),
        "under_accepted_qty": int(partial["unaccepted_total"]),
        "excess_count": int(counts["accepted_excess"]),
        "excess_qty": int(excess_qty or 0),
    }


@cached(prefix="reports:assembly_link_anomalies", ttl=300)
async def get_link_anomalies(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_ids: list[int] | None = None,
) -> dict:
    """LinkAnomaliesResponse-shape (см. backend/schemas/assembly.py)."""
    return {
        "ff_composition_mismatch": await _ff_composition_mismatch(db, project_id, warehouse_ids),
        "assemblies_without_ff": await _assemblies_without_ff(db, project_id, warehouse_ids),
        "ff_without_assembly": await _ff_without_assembly(db, project_id, warehouse_ids),
        # FBO привязан к имени склада ВБ (не к нашему складу) → warehouse_ids не применяем.
        "fbo": await _fbo_rollup(db, project_id),
    }
