# ruff: noqa: RUF001, RUF002, RUF003
"""
Предраспределение машины в пути (pre-distribution).

Машина (``CostOrder`` в статусе CUSTOMS/DISPATCHED) везёт товар, которого ещё нет
на ФФ. До приёмки раскладываем её входящий товар по WB-складам как заявки на сборку
со статусом ``PRE_DISTRIBUTED`` — БЕЗ реального стока (никакого фейкового
``WarehouseStock``: предраспределение это статус+флаг+ссылка на машину). На разгрузке
машины (``accept_receipt``) заявки авто → ``IN_PROGRESS`` (резерв стал реальным
стоком в той же транзакции).

См. .claude/PREDIST_DESIGN.md (полный спек + критика).
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy import func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import CostOrder, CostOrderItem, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrderItem
from backend.models.warehouse import Warehouse, WarehouseType
# Единый источник «эффективной» кратности/габарита FactoryOrderItem (mix-вариант учтён) —
# переиспользуем, чтобы кратность машины на экране совпадала со справочником приёмок.
from backend.services.box_multiplicity_service import _foi_effective_box_size, _foi_effective_ppb
from backend.schemas.assembly import (
    AssemblyItemCreate,
    AssemblyRequestCreate,
    AssemblyRequestResponse,
    PackageTypeStr,
    PreDistPoolRow,
    PreDistributionCreate,
    PreDistributionCreateResult,
    PreDistVehicle,
    PreDistVehiclePool,
)

from .crud import _build_response, create_assembly_request, get_assembly_request
from .status import _check_transition, _log_status_change
from .weight import resolve_unit_weights

logger = logging.getLogger(__name__)

# Машины, товар которых можно предраспределять (в пути, ещё не разгружены).
PRE_DIST_VEHICLE_STATUSES = (VehicleStatus.CUSTOMS, VehicleStatus.DISPATCHED)


def _vehicle_status_str(status: str | None) -> str:
    """VehicleStatus-член → его строковое значение ('CUSTOMS'/'DISPATCHED')."""
    return status.value if isinstance(status, VehicleStatus) else (status or "")


def _is_machine_newcomer(first_sale: date | None) -> bool:
    """Новинка cold-start по первой продаже, БЕЗ требования ФФ-остатка (источник — машина).

    Зеркалит правило ``fetch_cold_start_segment``: нет продаж (``first_sale_date IS NULL``)
    ИЛИ первая продажа < 14 дней назад. Cold-start-сегмент дополнительно требует ``rf_qty>0``
    (товар уже на ФФ) — но машина везёт товар, которого на ФФ ещё нет, поэтому здесь этот
    гейт снят: засев машинных новинок идёт из остатка самой машины.
    """
    return first_sale is None or first_sale >= date.today() - timedelta(days=14)


# ─── Загрузка/валидация машины ─────────────────────────────────────────────


async def _load_distributable_vehicle(db: AsyncSession, project_id: int, vehicle_id: int) -> CostOrder:
    """Машина проекта в статусе, допускающем предраспределение. Иначе ValueError."""
    vehicle = (
        await db.execute(
            select(CostOrder).where(
                CostOrder.id == vehicle_id,
                CostOrder.project_id == project_id,
                CostOrder.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not vehicle:
        raise ValueError("Машина не найдена")
    if vehicle.status not in PRE_DIST_VEHICLE_STATUSES:
        raise ValueError("Предраспределять можно только машину в пути (на таможне или отправленную со склада)")
    return vehicle


async def _resolve_target_ff_warehouse(db: AsyncSession, project_id: int, vehicle: CostOrder) -> Warehouse:
    """ФФ-склад разгрузки машины (источник будущих сборок). M3: хард-блок если NULL/не-ФФ."""
    if vehicle.target_warehouse_id is None:
        raise ValueError("У машины не задан склад назначения — нельзя предраспределить")
    wh = (
        await db.execute(
            select(Warehouse).where(
                Warehouse.id == vehicle.target_warehouse_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not wh:
        raise ValueError("Склад назначения машины не найден")
    if wh.warehouse_type != WarehouseType.FULFILLMENT:
        raise ValueError("Склад назначения машины не является ФФ-складом")
    return wh


# ─── Пул машины (net-математика) ───────────────────────────────────────────


async def _vehicle_gross_by_barcode(db: AsyncSession, project_id: int, order_no: str) -> dict[str, int]:
    """Σ qty товара машины по ШК (позиции машины, project + is_deleted фильтр)."""
    result = await db.execute(
        select(CostOrderItem.barcode, func.sum(CostOrderItem.qty).label("qty"))
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.order_no == order_no,
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
        .group_by(CostOrderItem.barcode)
    )
    return {row.barcode: int(row.qty or 0) for row in result.all() if row.barcode}


async def _reserved_by_barcode(db: AsyncSession, project_id: int, vehicle_id: int) -> dict[str, int]:
    """Σ qty, уже разнесённое в заявки этой машины (любой не-CANCELLED статус).

    После разгрузки PRE_DISTRIBUTED → IN_PROGRESS, но ``source_vehicle_id`` остаётся,
    поэтому net-пул вычитает все активные статусы, а не только PRE_DISTRIBUTED.
    """
    result = await db.execute(
        select(AssemblyRequestItem.barcode, func.sum(AssemblyRequestItem.quantity).label("qty"))
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.source_vehicle_id == vehicle_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.status != AssemblyStatus.CANCELLED,
        )
        .group_by(AssemblyRequestItem.barcode)
    )
    return {row.barcode: int(row.qty or 0) for row in result.all() if row.barcode}


async def _resolve_nomenclature(db: AsyncSession, project_id: int, barcodes: list[str]) -> dict[str, Nomenclature]:
    """barcode → Nomenclature (project-scoped). ШК машины может не быть в номенклатуре."""
    if not barcodes:
        return {}
    rows = (
        await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(barcodes),
            )
        )
    ).scalars().all()
    return {n.barcode: n for n in rows}


async def _vehicle_box_meta_by_barcode(
    db: AsyncSession, project_id: int, order_no: str
) -> dict[str, tuple[int, str | None]]:
    """barcode → (box_qty, box_size) ИЗ САМОЙ машины: qty-weighted mode её строк.

    Кратность едущей машины ещё НЕ в справочнике приёмок (``_resolve_machine_box_qty``
    читает только ACCEPTED-приёмки), поэтому берём прямо со строк ``cost_order``:
    ``pcs_per_box_override`` → эффективная кратность связанного FactoryOrderItem.
    Габарит короба — параллельно, спарен с выбранной кратностью. ШК без валидной
    кратности (ppb NULL/≤0) в результат не попадают.
    """
    coi_result = await db.execute(
        select(
            CostOrderItem.barcode,
            CostOrderItem.qty,
            CostOrderItem.pcs_per_box_override,
            CostOrderItem.box_size_override,
            FactoryOrderItem.pcs_per_box.label("foi_pcs_per_box"),
            FactoryOrderItem.mix_pcs_per_box.label("foi_mix_pcs_per_box"),
            FactoryOrderItem.box_size.label("foi_box_size"),
            FactoryOrderItem.mix_box_size.label("foi_mix_box_size"),
            FactoryOrderItem.mix_group_id.label("foi_mix_group_id"),
        )
        .outerjoin(FactoryOrderItem, FactoryOrderItem.id == CostOrderItem.factory_order_item_id)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.order_no == order_no,
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
    )
    # barcode → {ppb: Σqty}, {ppb: box_size (first seen)}
    ppb_qty: dict[str, dict[int, int]] = {}
    ppb_size: dict[str, dict[int, str | None]] = {}
    for row in coi_result:  # type: ignore[assignment]
        if not row.barcode:
            continue
        ppb = row.pcs_per_box_override or _foi_effective_ppb(
            row.foi_pcs_per_box, row.foi_mix_pcs_per_box, row.foi_mix_group_id
        )
        box_size = row.box_size_override or _foi_effective_box_size(
            row.foi_box_size, row.foi_mix_box_size, row.foi_mix_group_id
        )
        qty = int(row.qty or 0)
        if ppb is None or ppb <= 0 or qty <= 0:
            continue
        ppb_int = int(ppb)
        ppb_qty.setdefault(row.barcode, {})[ppb_int] = ppb_qty.get(row.barcode, {}).get(ppb_int, 0) + qty
        ppb_size.setdefault(row.barcode, {}).setdefault(ppb_int, box_size)

    out: dict[str, tuple[int, str | None]] = {}
    for barcode, qmap in ppb_qty.items():
        # mode: ppb с наибольшей Σqty; при точной ничьей — МЕНЬШИЙ ppb (детерминизм: запрос
        # без ORDER BY → dict-порядок = порядок строк БД; иначе показанная кратность «плавает»).
        primary = max(qmap.items(), key=lambda x: (x[1], -x[0]))[0]
        out[barcode] = (primary, ppb_size.get(barcode, {}).get(primary))
    return out


async def get_vehicle_pre_dist_pool(db: AsyncSession, project_id: int, vehicle_id: int) -> PreDistVehiclePool:
    """Пул машины: по каждому ШК — всего на машине, уже разнесено, доступно к раскладке."""
    vehicle = await _load_distributable_vehicle(db, project_id, vehicle_id)
    target_wh = await _resolve_target_ff_warehouse(db, project_id, vehicle)  # M3: raises if NULL/не-ФФ

    gross = await _vehicle_gross_by_barcode(db, project_id, vehicle.order_no)
    reserved = await _reserved_by_barcode(db, project_id, vehicle_id)
    nom_map = await _resolve_nomenclature(db, project_id, list(gross.keys()))
    box_meta = await _vehicle_box_meta_by_barcode(db, project_id, vehicle.order_no)

    rows: list[PreDistPoolRow] = []
    total_qty = 0
    for barcode in sorted(gross.keys()):
        g = gross[barcode]
        used = reserved.get(barcode, 0)
        nom = nom_map.get(barcode)
        box_qty, box_size = box_meta.get(barcode, (None, None))
        total_qty += g
        rows.append(
            PreDistPoolRow(
                barcode=barcode,
                article_seller=nom.article_seller if nom else None,
                article_wb=str(nom.article_wb) if nom and nom.article_wb else None,
                name=nom.subject if nom else None,
                brand=nom.brand if nom else None,
                gross_qty=g,
                distributed_qty=used,
                available_qty=max(0, g - used),
                box_qty=box_qty,
                box_size=box_size,
                is_newcomer=_is_machine_newcomer(nom.first_sale_date if nom else None),
            )
        )

    vehicle_brief = PreDistVehicle(
        id=vehicle.id,
        order_no=vehicle.order_no,
        status=_vehicle_status_str(vehicle.status),
        target_warehouse_id=target_wh.id,
        target_warehouse_name=target_wh.name,
        eta=vehicle.estimated_arrival_date,
        total_qty=total_qty,
        sku_count=len(gross),
        distributed_qty=sum(reserved.values()),
        can_distribute=True,
        block_reason=None,
    )
    return PreDistVehiclePool(vehicle=vehicle_brief, rows=rows)


async def get_pre_distribution_vehicles(db: AsyncSession, project_id: int) -> list[PreDistVehicle]:
    """Машины в пути (CUSTOMS/DISPATCHED) с агрегатами для списка предраспределения."""
    vehicles = list(
        (
            await db.execute(
                select(CostOrder)
                .where(
                    CostOrder.project_id == project_id,
                    CostOrder.is_deleted == False,  # noqa: E712
                    CostOrder.status.in_(PRE_DIST_VEHICLE_STATUSES),
                )
                .order_by(nulls_last(CostOrder.estimated_arrival_date.asc()), CostOrder.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if not vehicles:
        return []

    order_nos = [v.order_no for v in vehicles]
    vehicle_ids = [v.id for v in vehicles]

    # Σ qty + distinct ШК на машину (один запрос)
    gross_rows = (
        await db.execute(
            select(
                CostOrderItem.order_no,
                func.sum(CostOrderItem.qty).label("qty"),
                func.count(func.distinct(CostOrderItem.barcode)).label("sku"),
            )
            .where(
                CostOrderItem.project_id == project_id,
                CostOrderItem.order_no.in_(order_nos),
                CostOrderItem.is_deleted == False,  # noqa: E712
            )
            .group_by(CostOrderItem.order_no)
        )
    ).all()
    gross_map = {row.order_no: (int(row.qty or 0), int(row.sku or 0)) for row in gross_rows}

    # Уже разнесено на машину (один запрос)
    dist_rows = (
        await db.execute(
            select(
                AssemblyRequest.source_vehicle_id,
                func.sum(AssemblyRequestItem.quantity).label("qty"),
            )
            .join(AssemblyRequestItem, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
            .where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.source_vehicle_id.in_(vehicle_ids),
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
            )
            .group_by(AssemblyRequest.source_vehicle_id)
        )
    ).all()
    dist_map = {row.source_vehicle_id: int(row.qty or 0) for row in dist_rows}

    # Склады назначения (один запрос)
    wh_ids = {v.target_warehouse_id for v in vehicles if v.target_warehouse_id is not None}
    wh_map: dict[int, tuple[str, str]] = {}
    if wh_ids:
        wh_rows = (
            await db.execute(
                select(Warehouse.id, Warehouse.name, Warehouse.warehouse_type).where(
                    Warehouse.project_id == project_id,
                    Warehouse.id.in_(wh_ids),
                    Warehouse.is_deleted == False,  # noqa: E712
                )
            )
        ).all()
        wh_map = {row.id: (row.name, row.warehouse_type) for row in wh_rows}

    out: list[PreDistVehicle] = []
    for v in vehicles:
        qty, sku = gross_map.get(v.order_no, (0, 0))
        wh = wh_map.get(v.target_warehouse_id) if v.target_warehouse_id is not None else None
        block_reason: str | None = None
        if v.target_warehouse_id is None:
            block_reason = "Не задан склад назначения машины"
        elif wh is None:
            block_reason = "Склад назначения не найден"
        elif wh[1] != WarehouseType.FULFILLMENT:
            block_reason = "Склад назначения не ФФ-типа"
        out.append(
            PreDistVehicle(
                id=v.id,
                order_no=v.order_no,
                status=_vehicle_status_str(v.status),
                target_warehouse_id=v.target_warehouse_id,
                target_warehouse_name=wh[0] if wh else None,
                eta=v.estimated_arrival_date,
                total_qty=qty,
                sku_count=sku,
                distributed_qty=dist_map.get(v.id, 0),
                can_distribute=block_reason is None,
                block_reason=block_reason,
            )
        )
    return out


# ─── Создание предраспределения ────────────────────────────────────────────


async def create_pre_distribution(
    db: AsyncSession, project_id: int, payload: PreDistributionCreate
) -> PreDistributionCreateResult:
    """Создать заявки PRE_DISTRIBUTED из строк раскладки.

    Строки группируются по (WB-склад, упаковка) → одна заявка на группу. Источник-склад
    всех заявок = ФФ разгрузки машины. Валидации ДО создания (fail-fast):
      - машина в пути + ФФ-склад назначения (M3);
      - все ШК есть в номенклатуре проекта;
      - Σ запрошенного по ШК ≤ доступного в пуле (over-commit guard).
    wb_fbo_supply_id допустим только при одном WB-складе назначения.
    """
    if not payload.rows:
        raise ValueError("Нет строк для предраспределения")

    vehicle = await _load_distributable_vehicle(db, project_id, payload.vehicle_id)
    target_wh = await _resolve_target_ff_warehouse(db, project_id, vehicle)

    # Нормализуем строки + агрегируем запрошенное по ШК
    requested: dict[str, int] = {}
    for r in payload.rows:
        if r.qty <= 0:
            raise ValueError(f"Некорректное количество для {r.barcode}")
        if not r.wb_warehouse_name.strip():
            raise ValueError(f"Не задан склад WB для {r.barcode}")
        requested[r.barcode] = requested.get(r.barcode, 0) + r.qty

    # Все ШК должны резолвиться в номенклатуру (иначе create_assembly_request упадёт мид-циклом)
    nom_map = await _resolve_nomenclature(db, project_id, list(requested.keys()))
    missing = [bc for bc in requested if bc not in nom_map]
    if missing:
        raise ValueError(f"Нет в номенклатуре проекта: {', '.join(sorted(missing))}")

    # Over-commit guard: запрошено ≤ доступно (gross − уже разнесено)
    gross = await _vehicle_gross_by_barcode(db, project_id, vehicle.order_no)
    reserved = await _reserved_by_barcode(db, project_id, payload.vehicle_id)
    for bc, req_qty in requested.items():
        if bc not in gross:
            raise ValueError(f"Товара {bc} нет на машине")
        available = max(0, gross[bc] - reserved.get(bc, 0))
        if req_qty > available:
            raise ValueError(f"Превышение по {bc}: запрошено {req_qty}, доступно {available}")

    # Группировка по (WB-склад, упаковка)
    groups: dict[tuple[str, str], dict[str, int]] = {}
    for r in payload.rows:
        key = (r.wb_warehouse_name.strip(), r.package_type)
        bucket = groups.setdefault(key, {})
        bucket[r.barcode] = bucket.get(r.barcode, 0) + r.qty

    if payload.wb_fbo_supply_id is not None and len(groups) > 1:
        raise ValueError("Привязка поставки WB возможна только при одном складе назначения")

    # Вес за 1 шт (справочник → машина) один раз на все ШК — заявка получит вес товаров,
    # разложенный на паллеты (`pallet_weight_kg = goods / pallets`), чтобы «Общий вес»
    # (= паллеты × вес-паллеты) совпадал с нетто-весом товаров, как у прочих поставок.
    unit_weights = await resolve_unit_weights(db, project_id, list(requested.keys()))

    created: list[AssemblyRequest] = []
    for (wb_name, pkg), bc_qty in groups.items():
        items = [AssemblyItemCreate(barcode=bc, quantity=q) for bc, q in bc_qty.items()]
        # Паллеты — из фронта (геометрия коробов = фронт); нет ключа → 0 (как раньше).
        pallets = max(0, int(payload.pallets_by_group.get(f"{wb_name}::{pkg}", 0)))
        # Вес товаров группы (нетто) ÷ паллеты → вес одной паллеты. Нет веса/паллет → 0.
        goods = Decimal("0")
        for bc, q in bc_qty.items():
            w = unit_weights.get(bc)
            if w is not None and w > 0:
                goods += w * q
        pallet_weight = (goods / pallets).quantize(Decimal("0.01")) if pallets > 0 and goods > 0 else Decimal("0")
        create_payload = AssemblyRequestCreate(
            warehouse_id=target_wh.id,
            wb_fbo_supply_id=payload.wb_fbo_supply_id if len(groups) == 1 else None,
            pallets_count=pallets,
            pallet_weight_kg=pallet_weight,
            wb_warehouse_name_manual=wb_name,
            package_type=cast(PackageTypeStr, pkg),
            items=items,
        )
        req = await create_assembly_request(
            db,
            project_id,
            create_payload,
            skip_stock_validation=True,
            status_override=AssemblyStatus.PRE_DISTRIBUTED,
            source_vehicle_id=vehicle.id,
            is_pre_distribution=True,
        )
        created.append(req)

    # create_assembly_request инвалидирует reports:* на каждом вызове — отдельно не нужно.
    # Перечитываем через get_assembly_request (selectinload) — релейшены загружены,
    # иначе _build_response ловит lazy-load (MissingGreenlet) на свежесозданном объекте.
    responses: list[AssemblyRequestResponse] = []
    for req in created:
        loaded = await get_assembly_request(db, project_id, req.id)
        if loaded is None:  # pragma: no cover — только что создали в этой же сессии
            continue
        resp = AssemblyRequestResponse(**await _build_response(db, loaded))
        resp.source_vehicle_order_no = vehicle.order_no
        responses.append(resp)
    return PreDistributionCreateResult(
        created=len(created),
        request_ids=[req.id for req in created],
        requests=responses,
    )


# ─── Авто-перевод на разгрузке + ручной фолбэк ─────────────────────────────


async def _advance_pre_distribution_assemblies(db: AsyncSession, project_id: int, vehicle_id: int) -> int:
    """Машина разгружена: PRE_DISTRIBUTED-заявки этой машины → IN_PROGRESS.

    НЕ коммитит — вызывается ВНУТРИ транзакции ``accept_receipt`` (резерв становится
    реальным стоком атомарно). Идемпотентно (берёт только PRE_DISTRIBUTED).
    ``is_pre_distribution`` остаётся True (бейдж/история). Хук по ``cost_order_id``,
    НЕ по переходу DISPATCHED→DELIVERED (машина могла стоять в CUSTOMS — критика H1).
    Возвращает число переведённых заявок.
    """
    reqs = list(
        (
            await db.execute(
                select(AssemblyRequest).where(
                    AssemblyRequest.project_id == project_id,
                    AssemblyRequest.source_vehicle_id == vehicle_id,
                    AssemblyRequest.is_deleted == False,  # noqa: E712
                    AssemblyRequest.status == AssemblyStatus.PRE_DISTRIBUTED,
                )
            )
        )
        .scalars()
        .all()
    )
    count = 0
    for req in reqs:
        _check_transition(AssemblyStatus(req.status), AssemblyStatus.IN_PROGRESS)
        req.status = AssemblyStatus.IN_PROGRESS
        await _log_status_change(
            db,
            project_id,
            req.id,
            AssemblyStatus.PRE_DISTRIBUTED,
            AssemblyStatus.IN_PROGRESS,
            changed_by="system",
            comment="Машина разгружена — предраспределение переведено в сборку",
        )
        count += 1
    return count


async def advance_pre_distribution_manual(db: AsyncSession, project_id: int, vehicle_id: int) -> int:
    """Ручной перевод PRE_DISTRIBUTED→IN_PROGRESS (фолбэк H2, если авто-хук не сработал)."""
    vehicle = (
        await db.execute(
            select(CostOrder).where(
                CostOrder.id == vehicle_id,
                CostOrder.project_id == project_id,
                CostOrder.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not vehicle:
        raise ValueError("Машина не найдена")

    count = await _advance_pre_distribution_assemblies(db, project_id, vehicle_id)
    await db.commit()
    if count:
        await invalidate_cache("reports:assembly_flow")
        await invalidate_cache("reports:assembly_link_anomalies")
        await invalidate_cache("reports:warehouse_need")
    return count
