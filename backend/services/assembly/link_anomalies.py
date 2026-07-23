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

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.assembly_wb import AssemblyWbSupply, WbSupplySyncStatus
from backend.models.auth import Project
from backend.models.cost import Nomenclature
from backend.models.fulfillment import FfRequestKind, FulfillmentRequest, FulfillmentStock
from backend.models.integrations import IntegrationKey
from backend.models.warehouse import Warehouse, WarehouseStock
from backend.models.wb_fbo import WbFboSupply, WbSupplyStatus
from backend.services import fulfillment_service
from backend.services.fbo_supply import service as fbo_service
from backend.utils.time import utcnow

# Cap на построчные SKU-расхождения, отдаваемые на склад (UI-разворот); если их
# больше — список обрезается (truncated=True), детали — на вкладке «ФФ остатки».
_STOCK_MISMATCH_SKU_CAP = 200
# Cap на список самих аномальных FBO-поставок в каждой категории (новые сверху).
_FBO_SUPPLY_LIST_CAP = 50

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

    # Совместные сборки делят расхождение на группу (сверка по сумме) → одна строка
    # на группу с объединённой подписью (detail.assembly_number = «ASM-1, ASM-2»),
    # иначе обе сборки дали бы дубль-строки с одинаковыми combined-итогами.
    joint_members = await fulfillment_service._joint_members_for_assemblies(db, project_id, set(diverging_ids))
    seen_groups: set[frozenset[int]] = set()

    rows: list[dict] = []
    for aid in diverging_ids:
        members = joint_members.get(aid)
        if members:
            key = frozenset(members)
            if key in seen_groups:
                continue
            seen_groups.add(key)
        asm = asm_by_id[aid]
        detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db, project_id, aid)
        if detail is None:
            continue
        our_total = int(detail["our_total"])
        ff_total = int(detail["ff_total"])
        rows.append(
            {
                "assembly_id": aid,
                "number": detail.get("assembly_number") or asm.number,
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
    # archived+is_completed (skladbot сдал завершённую заявку в архив после отгрузки)
    # — это ОТРАБОТАВШАЯ связь, а не пропущенная: считаем её живой (тот же критерий,
    # что в авто-READY синке, fulfillment_service). Чисто архивная без завершения
    # (wmscelicom «Аннулирована»: archived, не completed) живой связью НЕ считается —
    # сборка остаётся в аномалии, ей нужна новая привязка.
    linked_rows = (
        await db.execute(
            select(FulfillmentRequest.assembly_request_id)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.assembly_request_id.is_not(None),
                FulfillmentRequest.local_archived == False,  # noqa: E712
                or_(
                    FulfillmentRequest.archived == False,  # noqa: E712
                    FulfillmentRequest.is_completed == True,  # noqa: E712
                ),
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


async def _ff_without_assembly(db: AsyncSession, project_id: int, warehouse_ids: list[int] | None) -> list[dict]:
    """Блок 3: заявки ФФ (kind=assembly) без привязанной нашей сборки.

    Показываем ВСЁ незвязанное, КРОМЕ архивных (провайдерский `archived` либо наш
    `local_archived`) — стадия/завершённость НЕ фильтруют: собранные/отгруженные
    без нашей сборки тоже сигнал «надо завести/привязать сборку», а обработанное
    оператор сам убирает кнопкой «В архив».
    """
    ff_filters = [
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.kind == FfRequestKind.ASSEMBLY.value,
        FulfillmentRequest.assembly_request_id.is_(None),
        FulfillmentRequest.archived == False,  # noqa: E712
        FulfillmentRequest.local_archived == False,  # noqa: E712
    ]
    if warehouse_ids:
        ff_filters.append(FulfillmentRequest.warehouse_id.in_(warehouse_ids))

    reqs = list((await db.execute(select(FulfillmentRequest).where(*ff_filters))).scalars().all())
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


async def _stock_mismatch(db: AsyncSession, project_id: int, warehouse_ids: list[int] | None) -> list[dict]:
    """Блок 5: расхождение остатков «наш склад vs ФФ-зеркало» по складам с API-интеграцией.

    diff = ff_good − (our_quantity + our_defect для migfull) (>0 — у ФФ больше,
    <0 — у нас больше). Остаток короба сводится к россыпи (qty_good × units_per_box,
    ключ = base_barcode|barcode), как в fulfillment_service.list_stocks — иначе diff
    несопоставим с нашим стоком. У migfull «ФФ годный» (stock_actual) ВКЛЮЧАЕТ брак
    (отдельного поля брака в API нет), поэтому сверяем с нашим ИТОГОМ (годный+брак) —
    иначе diff раздувается ровно на наш брак (как было в list_stocks до фикса). Наш
    склад (WarehouseStock) производен от документов; ФФ — зеркало провайдера (см.
    DOMAIN_FULFILLMENT). Строки с diff==0 не показываем.
    """
    providers = await _ff_warehouse_providers(db, project_id)  # warehouse_id → service
    ff_wh_ids = set(providers)
    if warehouse_ids:
        ff_wh_ids &= set(warehouse_ids)
    if not ff_wh_ids:
        return []
    ff_wh_list = list(ff_wh_ids)
    migfull_wids = {w for w in ff_wh_ids if providers.get(w) == "migfull"}

    # ФФ-сток (зеркало провайдера) по интегрированным складам. Без .limit() —
    # это полная агрегация (усечение исказило бы diff: уцелевшая сторона дала бы
    # ложное одностороннее расхождение); выборка ограничена ФФ-складами (подмн-во).
    ff_rows = (
        await db.execute(
            select(
                FulfillmentStock.warehouse_id,
                FulfillmentStock.barcode,
                FulfillmentStock.base_barcode,
                FulfillmentStock.nomenclature_id,
                FulfillmentStock.name,
                FulfillmentStock.qty_good,
                FulfillmentStock.units_per_box,
                FulfillmentStock.synced_at,
            ).where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id.in_(ff_wh_list),
            )
        )
    ).all()

    # Наш склад (производный от документов): ненулевой годный ИЛИ брак (брак нужен
    # для migfull — его «ФФ годный» включает брак, сверяем с нашим итогом).
    our_rows = (
        await db.execute(
            select(
                WarehouseStock.warehouse_id,
                WarehouseStock.barcode,
                WarehouseStock.nomenclature_id,
                WarehouseStock.quantity,
                WarehouseStock.defect_quantity,
            ).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(ff_wh_list),
                (WarehouseStock.quantity > 0) | (WarehouseStock.defect_quantity > 0),
            )
        )
    ).all()

    # Свод по (склад, эффективный ШК): короб сведён к россыпи под её ШК.
    cells: dict[tuple[int, str], dict] = {}

    def _cell(wid: int, key: str, nom_id: int | None) -> dict:
        c = cells.get((wid, key))
        if c is None:
            c = cells[(wid, key)] = {
                "barcode": key,
                "nomenclature_id": nom_id,
                "name": None,
                "ff_good": 0,
                "ff_logistics": 0,
                "our_quantity": 0,
                "our_defect": 0,
            }
        return c

    synced_by_wh: dict[int, datetime] = {}
    for r in ff_rows:
        units = r.units_per_box or 1
        key = r.base_barcode or r.barcode
        c = _cell(r.warehouse_id, key, r.nomenclature_id)
        c["ff_good"] += r.qty_good * units
        if c["nomenclature_id"] is None and r.nomenclature_id is not None:
            c["nomenclature_id"] = r.nomenclature_id
        # Имя — с россыпной строки (короб лишь заполняет пустое).
        if not r.base_barcode and r.name:
            c["name"] = r.name
        elif c["name"] is None:
            c["name"] = r.name
        if r.synced_at and (r.warehouse_id not in synced_by_wh or r.synced_at > synced_by_wh[r.warehouse_id]):
            synced_by_wh[r.warehouse_id] = r.synced_at

    for wr in our_rows:
        c = _cell(wr.warehouse_id, wr.barcode, wr.nomenclature_id)
        c["our_quantity"] += wr.quantity
        # Брак учитываем только для migfull (его ФФ-годный включает брак).
        if wr.warehouse_id in migfull_wids:
            c["our_defect"] += wr.defect_quantity
        if c["nomenclature_id"] is None and wr.nomenclature_id is not None:
            c["nomenclature_id"] = wr.nomenclature_id

    # Досчёт логистики к ff_good (паритет с fulfillment_service.list_stocks): товар,
    # который провайдер (skladbot) списал из годного на стадии «Указание вида работ
    # логистики», но физически на складе, а сборка ещё не отгружена (наш сток держит).
    # Без досчёта показывает ложную недостачу «у нас больше». Провайдер-специфично:
    # у migfull/wmscelicom стадии logistics_works нет → досчёт пустой, их не трогает.
    transit = await fulfillment_service._logistics_in_transit_multi(db, project_id, ff_wh_list)
    for wid, by_bc in transit.items():
        for barcode, (qty, nom_id) in by_bc.items():
            c = _cell(wid, barcode, nom_id)
            c["ff_good"] += qty
            c["ff_logistics"] += qty

    # Резолв article_seller/brand одним запросом (без N+1).
    nom_ids = {c["nomenclature_id"] for c in cells.values() if c["nomenclature_id"]}
    nom_by_id: dict[int, tuple[str | None, str | None]] = {}
    if nom_ids:
        nom_by_id = {
            row.id: (row.article_seller, row.brand)
            for row in (
                await db.execute(
                    select(Nomenclature.id, Nomenclature.article_seller, Nomenclature.brand).where(
                        Nomenclature.project_id == project_id,
                        Nomenclature.id.in_(nom_ids),
                    )
                )
            ).all()
        }

    # Агрегат по складу.
    wh_acc: dict[int, dict] = {}
    for (wid, _key), c in cells.items():
        # our_defect=0 для не-migfull → формула сводится к ff_good − our_quantity.
        diff = c["ff_good"] - (c["our_quantity"] + c["our_defect"])
        if diff == 0:
            continue
        acc = wh_acc.setdefault(
            wid,
            {
                "surplus_ff_qty": 0,
                "surplus_ff_sku": 0,
                "surplus_our_qty": 0,
                "surplus_our_sku": 0,
                "rows": [],
            },
        )
        if diff > 0:
            acc["surplus_ff_qty"] += diff
            acc["surplus_ff_sku"] += 1
        else:
            acc["surplus_our_qty"] += -diff
            acc["surplus_our_sku"] += 1
        article, brand = nom_by_id.get(c["nomenclature_id"], (None, None))
        acc["rows"].append(
            {
                "barcode": c["barcode"],
                "article_seller": article,
                "brand": brand,
                "name": c["name"],
                "ff_good": c["ff_good"],
                "our_quantity": c["our_quantity"],
                "our_defect": c["our_defect"],
                "diff": diff,
            }
        )

    if not wh_acc:
        return []

    wh_names = await _warehouse_names(db, project_id, set(wh_acc))

    out: list[dict] = []
    for wid, acc in wh_acc.items():
        rows = sorted(acc["rows"], key=lambda x: (-abs(x["diff"]), x["barcode"]))
        sku_total = len(rows)
        synced = synced_by_wh.get(wid)
        out.append(
            {
                "warehouse_id": wid,
                "warehouse_name": wh_names.get(wid),
                "provider": providers.get(wid),
                "surplus_ff_qty": acc["surplus_ff_qty"],
                "surplus_ff_sku": acc["surplus_ff_sku"],
                "surplus_our_qty": acc["surplus_our_qty"],
                "surplus_our_sku": acc["surplus_our_sku"],
                "net_diff": acc["surplus_ff_qty"] - acc["surplus_our_qty"],
                "sku_total": sku_total,
                "truncated": sku_total > _STOCK_MISMATCH_SKU_CAP,
                "synced_at": synced.isoformat() if synced else None,
                "rows": rows[:_STOCK_MISMATCH_SKU_CAP],
            }
        )
    # Крупнейшее суммарное расхождение первым.
    out.sort(key=lambda r: (-(r["surplus_ff_qty"] + r["surplus_our_qty"]), r["warehouse_id"]))
    return out


def _fbo_supply_row(d: dict) -> dict:
    """Enriched-словарь list_fbo_supplies → слим-строка FboAnomalySupply."""
    total = int(d.get("total_qty") or 0)
    accepted = int(d.get("accepted_qty") or 0)
    planned = d.get("planned_date")
    actual = d.get("actual_date")
    return {
        "supply_id": d["id"],
        "wb_supply_id": d.get("wb_supply_id"),
        "name": d.get("name"),
        "warehouse_name": d.get("warehouse_name"),
        "total_qty": total,
        "accepted_qty": accepted,
        "diff": accepted - total,
        "planned_date": planned.isoformat() if planned else None,
        "actual_date": actual.isoformat() if actual else None,
        "assembly_request_number": d.get("assembly_request_number"),
    }


async def _fbo_rollup(db: AsyncSession, project_id: int) -> dict:
    """Блок 4: сводка аномалий FBO-поставок ВБ (project-global, без warehouse-фильтра).

    Счётчики берутся из fbo_supply.service с тем же accounting_started_at, что и
    дефолтный вид /warehouse/fbo-supplies — иначе цифры разойдутся со страницей.
    Списки самих поставок (cap _FBO_SUPPLY_LIST_CAP) — через тот же list_fbo_supplies
    с теми же фильтрами, что и счётчики, чтобы разворот сходился со страницей.
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

    async def _supplies(
        *,
        without_assembly: bool = False,
        partial_only: bool = False,
        excess_only: bool = False,
    ) -> list[dict]:
        rows, _total = await fbo_service.list_fbo_supplies(
            db,
            project_id,
            accounting_started_at=acc,
            limit=_FBO_SUPPLY_LIST_CAP,
            sort_by="created_at_wb",
            sort_order="desc",
            without_assembly=without_assembly,
            partial_only=partial_only,
            excess_only=excess_only,
        )
        return [_fbo_supply_row(d) for d in rows]

    return {
        "without_assembly_count": int(counts["accepted_without_assembly"]),
        "under_accepted_count": int(counts["accepted_partial"]),
        "under_accepted_qty": int(partial["unaccepted_total"]),
        "excess_count": int(counts["accepted_excess"]),
        "excess_qty": int(excess_qty or 0),
        "without_assembly_supplies": await _supplies(without_assembly=True),
        "under_accepted_supplies": await _supplies(partial_only=True),
        "excess_supplies": await _supplies(excess_only=True),
    }


# Наша сборка «в дороге»: машина назначена либо уже отгружена (в пути).
_SUPPLY_SCOPE_STATUSES: tuple[str, ...] = (
    AssemblyStatus.VEHICLE_ASSIGNED.value,
    AssemblyStatus.SHIPPED.value,
)
# Окно приёмки WB: сдать можно за сутки до и сутки после брони (72ч суммарно) —
# расхождение считаем, только если |дата сдачи − дата брони| > 1 дня.
_SUPPLY_DATE_TOLERANCE_DAYS = 1
# WB-поставка закрыта — расхождение по дате/паллетам/пропуску уже неактуально.
# Кириллические буквы госномера → латиница (WB хранит их вперемешку):
# «В874УА» == «B874YA». Сверка номера машины ДДС↔ВБ идёт по нормализованному виду.
_PLATE_CYR_TO_LAT = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")


def _norm_plate(s: str | None) -> str:
    """Госномер к канону: upper, только буквы/цифры, кириллица→латиница."""
    if not s:
        return ""
    up = s.upper().translate(_PLATE_CYR_TO_LAT)
    return "".join(ch for ch in up if ch.isalnum())


_SUPPLY_CLOSED_WB_STATUSES: frozenset[str] = frozenset(
    {WbSupplyStatus.ACCEPTED.value, WbSupplyStatus.CANCELLED.value}
)


async def _supply_discrepancies(
    db: AsyncSession, project_id: int, warehouse_ids: list[int] | None
) -> list[dict]:
    """Блок 6: сборки с назначенной машиной / в пути, чья WB-поставка расходится с фактом.

    Три независимых флага (строка попадает в блок, если хотя бы один True):
      • date_mismatch — наша дата сдачи вне окна ±1 дня от даты брони WB;
      • pallet_mismatch — наши паллеты ≠ паллеты в пропуске WB (pass_pallets);
      • pass_missing — машина назначена/в пути, но пропуск WB ещё не оформлен.

    Всё по локальному зеркалу (без HTTP): AssemblyRequest + связанная WB-поставка
    (planned_date/статус) + реплей пропуска (pass_pallets/sync_status). Закрытые
    WB-поставки (ACCEPTED/CANCELLED) исключаем — расхождение для них неактуально.
    """
    asm_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        AssemblyRequest.status.in_(_SUPPLY_SCOPE_STATUSES),
    ]
    if warehouse_ids:
        asm_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))

    result = await db.execute(
        select(AssemblyRequest, WbFboSupply, AssemblyWbSupply)
        .outerjoin(
            WbFboSupply,
            (AssemblyRequest.wb_fbo_supply_id == WbFboSupply.id)
            & (WbFboSupply.project_id == project_id),
        )
        .outerjoin(
            AssemblyWbSupply,
            (AssemblyWbSupply.assembly_request_id == AssemblyRequest.id)
            & (AssemblyWbSupply.project_id == project_id),
        )
        .where(*asm_filters)
    )
    triples = result.all()
    if not triples:
        return []

    # Наши ФФ-склады — источник (откуда забрали товар).
    wh_names = await _warehouse_names(db, project_id, {t[0].warehouse_id for t in triples})

    rows: list[dict] = []
    for asm, supply, wb in triples:
        if supply is not None and supply.wb_status in _SUPPLY_CLOSED_WB_STATUSES:
            continue

        planned_date = supply.planned_date if supply is not None else None
        date_diff_days: int | None = None
        date_mismatch = False
        if asm.delivery_date is not None and planned_date is not None:
            date_diff_days = (asm.delivery_date - planned_date).days
            date_mismatch = abs(date_diff_days) > _SUPPLY_DATE_TOLERANCE_DAYS

        pass_pallets = wb.pass_pallets if wb is not None else None
        pallet_mismatch = pass_pallets is not None and pass_pallets != asm.pallets_count

        # Пропуск на ВБ = снимок кабинета говорит, что пропуск заведён.
        pass_on_wb = wb is not None and wb.wb_pass_present is True
        # Пропуск не оформлен НИГДЕ: нет на ВБ и наш реплей не дошёл до PASSED.
        pass_missing = not pass_on_wb and (
            wb is None or wb.sync_status != WbSupplySyncStatus.PASSED.value
        )
        # Пропуск на ВБ есть, а у нас поля пропуска пусты — «нет в ДДС».
        pass_missing_dds = pass_on_wb and not (wb.pass_car_number or wb.pass_driver_last)
        # Номер машины у нас ≠ номеру в кабинете WB (нормализованное сравнение).
        car_number_mismatch = (
            pass_on_wb
            and bool(wb.pass_car_number)
            and bool(wb.wb_pass_car_number)
            and _norm_plate(wb.pass_car_number) != _norm_plate(wb.wb_pass_car_number)
        )

        if not (
            date_mismatch
            or pallet_mismatch
            or pass_missing
            or pass_missing_dds
            or car_number_mismatch
        ):
            continue

        rows.append(
            {
                "assembly_id": asm.id,
                "number": asm.number,
                "status": asm.status,
                "source_warehouse_name": wh_names.get(asm.warehouse_id),
                "warehouse_name": supply.warehouse_name if supply is not None else None,
                "delivery_date": asm.delivery_date.isoformat() if asm.delivery_date else None,
                "planned_date": planned_date.isoformat() if planned_date else None,
                "date_diff_days": date_diff_days,
                "pallets_count": asm.pallets_count,
                "pass_pallets": pass_pallets,
                "wb_supply_id": supply.wb_supply_id if supply is not None else None,
                "wb_status": supply.wb_status if supply is not None else None,
                "sync_status": wb.sync_status if wb is not None else None,
                "date_mismatch": date_mismatch,
                "pallet_mismatch": pallet_mismatch,
                "pass_missing": pass_missing,
                "pass_on_wb": pass_on_wb,
                "pass_missing_dds": pass_missing_dds,
                "car_number_mismatch": car_number_mismatch,
                "wb_car_number": wb.wb_pass_car_number if wb is not None else None,
                "wb_pass_pallets": wb.wb_pass_pallets if wb is not None else None,
            }
        )

    # Сначала непроставленный пропуск, затем расхождение дат, затем по |Δ дней|.
    rows.sort(
        key=lambda r: (
            not r["pass_missing"],
            not r["date_mismatch"],
            -abs(r["date_diff_days"] or 0),
            r["assembly_id"],
        )
    )
    return rows


async def get_supply_discrepancies(
    db: AsyncSession, project_id: int, *, warehouse_ids: list[int] | None = None
) -> list[dict]:
    """Публичная (некэшированная) обёртка над _supply_discrepancies.

    Для планировщика TG-уведомлений: данные должны быть свежими на каждом прогоне,
    поэтому кэш вкладки здесь не задействован.
    """
    return await _supply_discrepancies(db, project_id, warehouse_ids)


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
        "stock_mismatch": await _stock_mismatch(db, project_id, warehouse_ids),
        "supply_discrepancies": await _supply_discrepancies(db, project_id, warehouse_ids),
        # FBO привязан к имени склада ВБ (не к нашему складу) → warehouse_ids не применяем.
        "fbo": await _fbo_rollup(db, project_id),
    }
