# ruff: noqa: RUF002, RUF003
"""Box-multiplicity service: per-(SKU, ФФ-склад) кратность коробки для сборки.

Кратность коробки = сколько штук SKU в коробе. Используется при сборке для
округления распределения до целых коробов.

Резолв кратности для пары (товар barcode, ФФ-склад warehouse_id) — первое
сработавшее правило побеждает:
  1. machine  — последняя `ACCEPTED`-приёмка этого товара на этот ФФ → её машина
     (`cost_order`) → наполняемость строк машины (qty-weighted mode). Ячейка
     заблокирована (`editable=false`, `source="machine"`).
  2. manual   — `BoxQtyPerWarehouse.box_qty` для пары (barcode, warehouse_id),
     если не NULL. `editable=true`, `source="manual"`.
  3. default  — `Nomenclature.box_qty_override` (SKU-уровневый дефолт).
     `editable=true`, `source="default"`.
  4. none     — ничего не задано: `box_qty=null`, `source="none"`.

Флаг `use_box_multiplicity` per-ФФ берётся из строки `BoxQtyPerWarehouse`, если
она есть, иначе — SKU-уровневый `Nomenclature.use_box_multiplicity`.

Заказы фабрики как отдельный источник кратности НЕ используются — `factory`
строки остаются только в read-only drill-down (`get_sku_box_sources`).
"""

import logging
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models import WbWarehouseStock
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import (
    BoxMultiplicityChangeLog,
    BoxQtyPerWarehouse,
    CostOrder,
    CostOrderItem,
    Nomenclature,
)
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    Warehouse,
    WarehouseStock,
    WarehouseType,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.box_multiplicity")


def _serialize_value(value: object) -> str | None:
    """Сериализация значения поля для журнала изменений (`old_value`/`new_value`).

    int → str(v); bool → "true"/"false"; str → как есть; None → None (NULL).
    bool проверяется ДО int (в Python `bool` — подкласс `int`).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return str(value)


def _log_change(
    db: AsyncSession,
    project_id: int,
    barcode: str,
    warehouse_id: int | None,
    field: str,
    old: object,
    new: object,
    source: str,
    batch_id: str | None = None,
) -> None:
    """Добавить запись в журнал изменений кратности коробки.

    Только регистрирует строку в сессии (`db.add`) — коммит делает вызывающая
    функция вместе с самим изменением. `warehouse_id=None` → SKU-уровень.
    `batch_id` группирует записи одной bulk-операции (для отката всей вставки).
    """
    db.add(
        BoxMultiplicityChangeLog(
            project_id=project_id,
            barcode=barcode,
            warehouse_id=warehouse_id,
            field=field,
            old_value=_serialize_value(old),
            new_value=_serialize_value(new),
            change_source=source,
            batch_id=batch_id,
            created_at=utcnow(),
        )
    )


def _foi_effective_ppb(
    foi_pcs_per_box: int | None, mix_pcs_per_box: int | None, mix_group_id: str | None
) -> int | None:
    """Resolve FOI's effective pcs_per_box (mix variant if mixed, else regular)."""
    if mix_group_id and mix_pcs_per_box:
        return mix_pcs_per_box
    return foi_pcs_per_box


def _foi_effective_box_size(foi_box_size: str | None, mix_box_size: str | None, mix_group_id: str | None) -> str | None:
    """Resolve FOI's effective box_size (mix variant if mixed, else regular)."""
    if mix_group_id and mix_box_size:
        return mix_box_size
    return foi_box_size


async def resolve_source_machine_box_qty(
    db: AsyncSession,
    project_id: int,
    vehicle_id_to_order_no: dict[int, str],
) -> dict[tuple[int, str], int]:
    """Кратность короба per (vehicle_id, barcode) прямо со строк машины-источника.

    В отличие от ``_resolve_machine_box_qty`` (читает только ACCEPTED-приёмки),
    берёт кратность с СОБСТВЕННЫХ строк машины (``pcs_per_box_override`` →
    эффективная кратность связанного FactoryOrderItem) — работает и для ЕДУЩЕЙ
    (ещё не принятой) машины. Фолбэк «коробов» в списке сборок для заявок
    предраспределения, чьих ШК ещё нет в справочнике приёмок склада. Батч по всем
    order_no за один запрос; qty-weighted mode (зеркало ``_vehicle_box_meta_by_barcode``).
    """
    if not vehicle_id_to_order_no:
        return {}
    order_to_vehicle = {ono: vid for vid, ono in vehicle_id_to_order_no.items()}
    rows = await db.execute(
        select(
            CostOrderItem.order_no,
            CostOrderItem.barcode,
            CostOrderItem.qty,
            CostOrderItem.pcs_per_box_override,
            FactoryOrderItem.pcs_per_box.label("foi_ppb"),
            FactoryOrderItem.mix_pcs_per_box.label("foi_mix_ppb"),
            FactoryOrderItem.mix_group_id.label("foi_mix_group_id"),
        )
        .outerjoin(FactoryOrderItem, FactoryOrderItem.id == CostOrderItem.factory_order_item_id)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.order_no.in_(list(order_to_vehicle.keys())),
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
    )
    # (vehicle_id, barcode) → {ppb: Σqty} для qty-weighted mode
    ppb_qty: dict[tuple[int, str], dict[int, int]] = {}
    for row in rows:
        vid = order_to_vehicle.get(row.order_no)
        if vid is None or not row.barcode:
            continue
        ppb = row.pcs_per_box_override or _foi_effective_ppb(row.foi_ppb, row.foi_mix_ppb, row.foi_mix_group_id)
        qty = int(row.qty or 0)
        if not ppb or ppb <= 0 or qty <= 0:
            continue
        key = (vid, row.barcode)
        ppb_qty.setdefault(key, {})[int(ppb)] = ppb_qty.get(key, {}).get(int(ppb), 0) + qty

    out: dict[tuple[int, str], int] = {}
    for key, qmap in ppb_qty.items():
        # mode: ppb с наибольшей Σqty; при ничьей — МЕНЬШИЙ ppb (детерминизм).
        out[key] = max(qmap.items(), key=lambda x: (x[1], -x[0]))[0]
    return out


async def _resolve_machine_box_qty(
    db: AsyncSession,
    project_id: int,
    barcodes: list[str],
) -> dict[tuple[str, int], dict]:
    """Резолв кратности из машин per пара (barcode, ФФ-склад).

    Для каждой пары (barcode, warehouse_id) ищем последнюю ACCEPTED-приёмку
    этого товара на этот ФФ, берём связанную машину (`cost_order`) и считаем
    qty-weighted mode наполняемости её строк с этим barcode.

    Возвращает dict: (barcode, warehouse_id) → {
        box_qty, box_size, order_no, received_at, machine_variants
    }. `machine_variants` — число иных машин этого ФФ с отличной кратностью.
    Пары без машинного резолва в dict отсутствуют.
    """
    if not barcodes:
        return {}

    # ─── Шаг 1: ACCEPTED-приёмки, содержащие нужные товары ────────────────
    # Приёмка: project_id, is_deleted==False, status==ACCEPTED, cost_order_id NOT NULL.
    # Содержит товар: JOIN inbound_receipt_items по receipt_id, barcode совпадает,
    # actual_qty > 0.
    receipt_result = await db.execute(
        select(
            InboundReceiptItem.barcode,
            InboundReceipt.warehouse_id,
            InboundReceipt.cost_order_id,
            InboundReceipt.actual_date,
            InboundReceipt.id.label("receipt_id"),
        )
        .join(InboundReceipt, InboundReceipt.id == InboundReceiptItem.receipt_id)
        .where(
            InboundReceiptItem.project_id == project_id,
            InboundReceiptItem.barcode.in_(barcodes),
            InboundReceiptItem.actual_qty > 0,
            InboundReceipt.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
            InboundReceipt.status == InboundStatus.ACCEPTED.value,
            InboundReceipt.cost_order_id.isnot(None),
        )
    )

    # Для каждой пары (barcode, warehouse_id) собираем ВСЕ ACCEPTED-приёмки.
    # Лучшая (max actual_date, receipt_id) даёт основную машину-источник;
    # остальные нужны для подсчёта расхождений кратности между машинами
    # одного ФФ (machine_variants). actual_date NULL → трактуем как date.min.
    receipts_by_key: dict[tuple[str, int], list[tuple]] = {}
    # ^ key → list of (sort_key, cost_order_id, actual_date)
    for r in receipt_result:  # type: ignore[assignment]
        key = (r.barcode, r.warehouse_id)
        sort_key = (r.actual_date or date.min, r.receipt_id)
        receipts_by_key.setdefault(key, []).append((sort_key, r.cost_order_id, r.actual_date))

    if not receipts_by_key:
        return {}

    # ─── Шаг 2: строки cost_order_items нужных машин с нужным barcode ─────
    cost_order_ids = {co_id for receipts in receipts_by_key.values() for _, co_id, _ in receipts}
    coi_result = await db.execute(
        select(
            CostOrder.id.label("co_id"),
            CostOrder.order_no.label("order_no"),
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
        .join(CostOrder, CostOrder.order_no == CostOrderItem.order_no)
        .outerjoin(FactoryOrderItem, FactoryOrderItem.id == CostOrderItem.factory_order_item_id)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.isnot(None),  # планово-затратные cost_orders (status NULL) — не машины снабжения
            CostOrder.id.in_(cost_order_ids),
            CostOrderItem.barcode.in_(barcodes),
        )
    )
    # (co_id, barcode) → list of (ppb_or_none, box_size_or_none, qty)
    coi_grouped: dict[tuple[int, str], list[tuple[int | None, str | None, int]]] = {}
    order_no_by_co: dict[int, str] = {}  # co_id → order_no
    for row in coi_result:  # type: ignore[assignment]
        ppb = row.pcs_per_box_override or _foi_effective_ppb(
            row.foi_pcs_per_box, row.foi_mix_pcs_per_box, row.foi_mix_group_id
        )
        box_size = row.box_size_override or _foi_effective_box_size(
            row.foi_box_size, row.foi_mix_box_size, row.foi_mix_group_id
        )
        order_no_by_co[row.co_id] = row.order_no
        coi_grouped.setdefault((row.co_id, row.barcode), []).append((ppb, box_size, int(row.qty or 0)))

    # ─── Шаг 3: qty-weighted mode наполняемости одной машины для barcode ───
    def _machine_primary(co_id: int, barcode: str) -> tuple[int, str | None] | None:
        """(box_qty, box_size) машины co_id для barcode или None, если нет ppb."""
        entries = coi_grouped.get((co_id, barcode))
        if not entries:
            return None
        # Распределяем qty по ppb; параллельно собираем box_size per ppb.
        ppb_to_qty: dict[int, int] = {}
        ppb_to_box_size: dict[int, str | None] = {}
        for ppb, box_size, qty in entries:
            if ppb is None or ppb <= 0 or qty <= 0:
                continue
            ppb_int = int(ppb)
            ppb_to_qty[ppb_int] = ppb_to_qty.get(ppb_int, 0) + qty
            if ppb_int not in ppb_to_box_size:
                ppb_to_box_size[ppb_int] = box_size
        if not ppb_to_qty:
            return None
        # Mode: ppb с наибольшей суммарной qty.
        primary = max(ppb_to_qty.items(), key=lambda x: x[1])[0]
        return primary, ppb_to_box_size.get(primary)

    # Для каждой пары (barcode, ФФ): основная машина = лучшая приёмка;
    # machine_variants = число иных машин этого ФФ с отличной кратностью.
    resolved: dict[tuple[str, int], dict] = {}
    for (barcode, warehouse_id), receipts in receipts_by_key.items():
        receipts.sort(key=lambda x: x[0], reverse=True)
        _best_sort, best_co_id, best_actual_date = receipts[0]
        best = _machine_primary(best_co_id, barcode)
        if best is None:
            continue
        primary, box_size = best
        # Уникальные кратности прочих машин этого ФФ, отличные от основной.
        other_ppbs: set[int] = set()
        seen_co: set[int] = {best_co_id}
        for _sort, co_id, _date in receipts[1:]:
            if co_id in seen_co:
                continue
            seen_co.add(co_id)
            other = _machine_primary(co_id, barcode)
            if other is not None and other[0] != primary:
                other_ppbs.add(other[0])
        resolved[(barcode, warehouse_id)] = {
            "box_qty": primary,
            "box_size": box_size,
            "order_no": order_no_by_co.get(best_co_id),
            "received_at": best_actual_date.isoformat() if best_actual_date else None,
            "machine_variants": len(other_ppbs),
        }
    return resolved


async def _resolve_mixed_ppb_barcodes(
    db: AsyncSession,
    project_id: int,
    barcodes: list[str],
) -> set[str]:
    """Barcodes, у которых в истории машин >1 различных эффективных ppb.

    Считаем все cost_order_items по машинам снабжения (`cost_orders.status IS NOT NULL`)
    независимо от статуса приёмки — товар «имел разные кратности», даже если разные
    машины ещё в пути. Эффективная ppb: `pcs_per_box_override` либо derived из FOI.
    """
    if not barcodes:
        return set()

    result = await db.execute(
        select(
            CostOrderItem.barcode,
            CostOrderItem.pcs_per_box_override,
            FactoryOrderItem.pcs_per_box.label("foi_pcs_per_box"),
            FactoryOrderItem.mix_pcs_per_box.label("foi_mix_pcs_per_box"),
            FactoryOrderItem.mix_group_id.label("foi_mix_group_id"),
        )
        .join(CostOrder, CostOrder.order_no == CostOrderItem.order_no)
        .outerjoin(FactoryOrderItem, FactoryOrderItem.id == CostOrderItem.factory_order_item_id)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.isnot(None),
            CostOrderItem.barcode.in_(barcodes),
            CostOrderItem.qty > 0,
        )
    )
    ppb_by_barcode: dict[str, set[int]] = {}
    for r in result:  # type: ignore[assignment]
        ppb = r.pcs_per_box_override or _foi_effective_ppb(r.foi_pcs_per_box, r.foi_mix_pcs_per_box, r.foi_mix_group_id)
        if ppb is None or ppb <= 0:
            continue
        ppb_by_barcode.setdefault(r.barcode, set()).add(int(ppb))
    return {bc for bc, s in ppb_by_barcode.items() if len(s) > 1}


async def get_box_multiplicity_table(
    db: AsyncSession,
    project_id: int,
    *,
    nm_id_filter: int | None = None,
) -> list[dict]:
    """Build per-SKU box-multiplicity rows for the project.

    `nm_id_filter` returns at most one row (used after PATCH).
    """
    nom_query = select(Nomenclature).where(
        Nomenclature.project_id == project_id,
        Nomenclature.article_wb.isnot(None),
    )
    if nm_id_filter is not None:
        nom_query = nom_query.where(Nomenclature.article_wb == nm_id_filter)
    nom_query = nom_query.order_by(Nomenclature.article_seller)

    nom_result = await db.execute(nom_query)
    nomenclatures = nom_result.scalars().all()
    if not nomenclatures:
        return []

    barcodes = [n.barcode for n in nomenclatures]

    # ─── Active ФФ warehouses ─────────────────────────────────────────────
    rf_wh_result = await db.execute(
        select(Warehouse.id, Warehouse.name)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
            Warehouse.is_deleted == False,  # noqa: E712
            Warehouse.is_active == True,  # noqa: E712
        )
        .order_by(Warehouse.sort_order, Warehouse.id)
    )
    rf_warehouses: list[tuple[int, str]] = [(r.id, r.name) for r in rf_wh_result]

    # ─── Машинный резолв per (barcode, ФФ) ───────────────────────────────
    machine_map = await _resolve_machine_box_qty(db, project_id, barcodes)

    # ─── SKU-уровень: barcodes с >1 различных ppb по всем машинам (любой статус)
    mixed_ppb_barcodes = await _resolve_mixed_ppb_barcodes(db, project_id, barcodes)

    # ─── Ручная per-ФФ кратность: (barcode, warehouse_id) → row ──────────
    per_rf_map: dict[tuple[str, int], dict] = {}
    if barcodes:
        per_rf_result = await db.execute(
            select(BoxQtyPerWarehouse).where(
                BoxQtyPerWarehouse.project_id == project_id,
                BoxQtyPerWarehouse.barcode.in_(barcodes),
            )
        )
        for pr in per_rf_result.scalars().all():
            per_rf_map[(pr.barcode, pr.warehouse_id)] = {
                "box_qty": pr.box_qty,
                "box_size": pr.box_size,
                "use_box_multiplicity": pr.use_box_multiplicity,
            }

    # ─── Per-ФФ stock per (nm_id, warehouse_id) ──────────────────────────
    per_rf_stock_map: dict[tuple[int, int], int] = {}  # (nm_id, wh_id) → qty
    if rf_warehouses:
        rf_wh_ids = [wh_id for wh_id, _ in rf_warehouses]
        per_rf_stock_result = await db.execute(
            select(
                Nomenclature.article_wb,
                WarehouseStock.warehouse_id,
                func.coalesce(func.sum(WarehouseStock.quantity), 0).label("qty"),
            )
            .join(Nomenclature, Nomenclature.id == WarehouseStock.nomenclature_id)
            .where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(rf_wh_ids),
                Nomenclature.article_wb.isnot(None),
                Nomenclature.barcode.in_(barcodes),
            )
            .group_by(Nomenclature.article_wb, WarehouseStock.warehouse_id)
        )
        for row in per_rf_stock_result:  # type: ignore[assignment]
            per_rf_stock_map[(row.article_wb, row.warehouse_id)] = int(row.qty or 0)

    # ─── Stock metrics per nm_id (для фильтров на странице) ───────────────
    nm_ids = [n.article_wb for n in nomenclatures if n.article_wb is not None]

    # ФФ-сток — сумма по всем активным ФФ-складам проекта.
    rf_stock_map: dict[int, int] = {}
    if nm_ids:
        rf_wh_subq = (
            select(Warehouse.id)
            .where(
                Warehouse.project_id == project_id,
                Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
                Warehouse.is_deleted == False,  # noqa: E712
                Warehouse.is_active == True,  # noqa: E712
            )
            .scalar_subquery()
        )
        rf_stock_result = await db.execute(
            select(
                Nomenclature.article_wb,
                func.coalesce(func.sum(WarehouseStock.quantity), 0).label("qty"),
            )
            .join(Nomenclature, Nomenclature.id == WarehouseStock.nomenclature_id)
            .where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(rf_wh_subq),
                Nomenclature.article_wb.in_(nm_ids),
            )
            .group_by(Nomenclature.article_wb)
        )
        for row in rf_stock_result:  # type: ignore[assignment]
            rf_stock_map[row.article_wb] = int(row.qty or 0)

    # In-assembly (резерв в активных сборках до отгрузки) per nm_id.
    in_assembly_map: dict[int, int] = {}
    in_transit_map: dict[int, int] = {}
    if nm_ids:
        active_statuses = [
            AssemblyStatus.PENDING,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.READY,
            AssemblyStatus.VEHICLE_ASSIGNED,
        ]
        asm_result = await db.execute(
            select(
                Nomenclature.article_wb,
                AssemblyRequest.status,
                func.sum(AssemblyRequestItem.quantity).label("qty"),
            )
            .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
            .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
            .where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status.in_([*active_statuses, AssemblyStatus.SHIPPED]),
                Nomenclature.article_wb.in_(nm_ids),
            )
            .group_by(Nomenclature.article_wb, AssemblyRequest.status)
        )
        for row in asm_result:  # type: ignore[assignment]
            nm = row.article_wb
            qty = int(row.qty or 0)
            target = in_transit_map if row.status == AssemblyStatus.SHIPPED else in_assembly_map
            target[nm] = target.get(nm, 0) + qty

    # WB warehouses сток — сумма по всем WB-складам проекта.
    wb_stock_map: dict[int, int] = {}
    if nm_ids:
        wb_result = await db.execute(
            select(
                WbWarehouseStock.nm_id,
                func.coalesce(func.sum(WbWarehouseStock.quantity), 0).label("qty"),
            )
            .where(
                WbWarehouseStock.project_id == project_id,
                WbWarehouseStock.nm_id.in_(nm_ids),
            )
            .group_by(WbWarehouseStock.nm_id)
        )
        for row in wb_result:  # type: ignore[assignment]
            wb_stock_map[row.nm_id] = int(row.qty or 0)

    # ─── Build rows ────────────────────────────────────────────────────────
    rows: list[dict] = []
    for n in nomenclatures:
        rf_stock = rf_stock_map.get(n.article_wb, 0) if n.article_wb else 0
        in_asm = in_assembly_map.get(n.article_wb, 0) if n.article_wb else 0
        in_tr = in_transit_map.get(n.article_wb, 0) if n.article_wb else 0
        wb_stock = wb_stock_map.get(n.article_wb, 0) if n.article_wb else 0

        # Per-ФФ block: одна строка на каждый активный ФФ-склад.
        per_warehouse: list[dict] = []
        has_machine = False
        for wh_id, wh_name in rf_warehouses:
            machine = machine_map.get((n.barcode, wh_id))
            manual = per_rf_map.get((n.barcode, wh_id))

            # use_box_multiplicity: per-ФФ строка важнее SKU-уровня.
            use_flag = manual["use_box_multiplicity"] if manual else n.use_box_multiplicity

            # box_size для не-машинных источников: из BoxQtyPerWarehouse.box_size
            # (если per-ФФ строка есть), иначе None.
            manual_box_size = manual["box_size"] if manual else None

            machine_variants = 0
            if machine is not None:
                has_machine = True
                box_qty = machine["box_qty"]
                box_size = machine["box_size"]
                source = "machine"
                editable = False
                order_no = machine["order_no"]
                received_at = machine["received_at"]
                machine_variants = machine["machine_variants"]
            elif manual is not None and manual["box_qty"] is not None:
                box_qty = manual["box_qty"]
                box_size = manual_box_size
                source = "manual"
                editable = True
                order_no = None
                received_at = None
            elif n.box_qty_override is not None:
                box_qty = n.box_qty_override
                box_size = manual_box_size
                source = "default"
                editable = True
                order_no = None
                received_at = None
            else:
                box_qty = None
                box_size = manual_box_size
                source = "none"
                editable = True
                order_no = None
                received_at = None

            per_warehouse.append(
                {
                    "warehouse_id": wh_id,
                    "warehouse_name": wh_name,
                    "box_qty": box_qty,
                    "box_size": box_size,
                    "source": source,
                    "editable": editable,
                    "use_box_multiplicity": use_flag,
                    "rf_stock": per_rf_stock_map.get((n.article_wb, wh_id), 0) if n.article_wb else 0,
                    "machine_order_no": order_no,
                    "machine_received_at": received_at,
                    "machine_variants": machine_variants,
                }
            )

        rows.append(
            {
                "nm_id": n.article_wb,
                "vendor_code": n.article_seller,
                "barcode": n.barcode,
                "brand": n.brand,
                "subject": n.subject,
                "box_qty_override": n.box_qty_override,
                "use_box_multiplicity": n.use_box_multiplicity,
                "has_machine_data": has_machine,
                "has_mixed_ppb": n.barcode in mixed_ppb_barcodes,
                "rf_stock": rf_stock,
                "in_assembly": in_asm,
                "in_transit": in_tr,
                "wb_stock": wb_stock,
                "per_warehouse": per_warehouse,
            }
        )

    return rows


_UNSET = object()


async def update_box_multiplicity(
    db: AsyncSession,
    project_id: int,
    nm_id: int,
    *,
    box_qty_override: object = _UNSET,  # only applied if not _UNSET
    use_box_multiplicity: object = _UNSET,
) -> bool:
    """Partial update SKU-уровня: только переданные поля затрагиваются.

    SKU-уровневое поле `box_qty_override` редактируется всегда (машинная
    блокировка — только на уровне ячейки ФФ).

    Returns False if no nomenclature row matches (project_id, article_wb).
    """
    if box_qty_override is _UNSET and use_box_multiplicity is _UNSET:
        return True  # nothing to update — no-op success
    result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb == nm_id,
        )
    )
    rows = result.scalars().all()
    if not rows:
        return False
    for nom in rows:
        if box_qty_override is not _UNSET:
            old = nom.box_qty_override
            if old != box_qty_override:
                _log_change(
                    db,
                    project_id,
                    nom.barcode,
                    None,
                    "box_qty_override",
                    old,
                    box_qty_override,
                    "manual",
                )
            nom.box_qty_override = box_qty_override  # type: ignore[assignment]
        if use_box_multiplicity is not _UNSET:
            new_use = bool(use_box_multiplicity)
            old_use = nom.use_box_multiplicity
            if old_use != new_use:
                _log_change(
                    db,
                    project_id,
                    nom.barcode,
                    None,
                    "use_box_multiplicity",
                    old_use,
                    new_use,
                    "manual",
                )
            nom.use_box_multiplicity = new_use
    await db.commit()
    await invalidate_cache("reports:warehouse_need")
    logger.info(
        "box_multiplicity updated: project=%s nm_id=%s ppb=%s use=%s rows=%s",
        project_id,
        nm_id,
        box_qty_override,
        use_box_multiplicity,
        len(rows),
    )
    return True


# Backward-compat shim for existing callers/tests — delegates to update_box_multiplicity.
async def set_box_qty_override(
    db: AsyncSession,
    project_id: int,
    nm_id: int,
    value: int | None,
) -> bool:
    return await update_box_multiplicity(
        db,
        project_id,
        nm_id,
        box_qty_override=value,
    )


async def has_machine_box_qty(
    db: AsyncSession,
    project_id: int,
    barcode: str,
    warehouse_id: int,
) -> bool:
    """True если на пару (barcode, ФФ) есть машинный резолв кратности.

    Используется роутером для 409-guard на per-ФФ PATCH.
    """
    machine_map = await _resolve_machine_box_qty(db, project_id, [barcode])
    return (barcode, warehouse_id) in machine_map


async def update_per_warehouse(
    db: AsyncSession,
    project_id: int,
    barcode: str,
    warehouse_id: int,
    *,
    box_qty: object = _UNSET,  # only applied if not _UNSET; can be None to clear
    box_size: object = _UNSET,  # only applied if not _UNSET; can be None to clear
    use_box_multiplicity: object = _UNSET,
) -> bool:
    """Partial update ручной per-ФФ кратности/размера. Создаёт строку если её нет.

    Returns False if (project_id, barcode, warehouse_id) ссылка невалидна
    (нет такого склада или barcode), True если применили (включая no-op).

    NB: машинная блокировка (409) проверяется в роутере — этот сервис
    переданные поля применяет как есть.
    """
    if box_qty is _UNSET and box_size is _UNSET and use_box_multiplicity is _UNSET:
        return True

    # Проверяем что warehouse существует и принадлежит проекту
    wh_exists = await db.execute(
        select(Warehouse.id).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
        )
    )
    if wh_exists.scalar_one_or_none() is None:
        return False

    # Проверяем что barcode есть в номенклатуре проекта
    nom_exists = await db.execute(
        select(Nomenclature.id)
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode == barcode,
        )
        .limit(1)
    )
    if nom_exists.scalar_one_or_none() is None:
        return False

    existing = await db.execute(
        select(BoxQtyPerWarehouse).where(
            BoxQtyPerWarehouse.project_id == project_id,
            BoxQtyPerWarehouse.barcode == barcode,
            BoxQtyPerWarehouse.warehouse_id == warehouse_id,
        )
    )
    row = existing.scalar_one_or_none()

    if row is None:
        # Создаём — с дефолтами для непереданных полей. old_value = None (строки не было).
        new_box_qty = None if box_qty is _UNSET else box_qty
        new_box_size = None if box_size is _UNSET else box_size
        new_use = True if use_box_multiplicity is _UNSET else bool(use_box_multiplicity)
        row = BoxQtyPerWarehouse(
            project_id=project_id,
            barcode=barcode,
            warehouse_id=warehouse_id,
            box_qty=new_box_qty,  # type: ignore[assignment]
            box_size=new_box_size,  # type: ignore[assignment]
            use_box_multiplicity=new_use,
        )
        db.add(row)
        if box_qty is not _UNSET and new_box_qty is not None:
            _log_change(db, project_id, barcode, warehouse_id, "box_qty", None, new_box_qty, "manual")
        if box_size is not _UNSET and new_box_size is not None:
            _log_change(db, project_id, barcode, warehouse_id, "box_size", None, new_box_size, "manual")
        if use_box_multiplicity is not _UNSET and new_use is not True:
            _log_change(db, project_id, barcode, warehouse_id, "use_box_multiplicity", True, new_use, "manual")
    else:
        if box_qty is not _UNSET:
            if row.box_qty != box_qty:
                _log_change(db, project_id, barcode, warehouse_id, "box_qty", row.box_qty, box_qty, "manual")
            row.box_qty = box_qty  # type: ignore[assignment]
        if box_size is not _UNSET:
            if row.box_size != box_size:
                _log_change(db, project_id, barcode, warehouse_id, "box_size", row.box_size, box_size, "manual")
            row.box_size = box_size  # type: ignore[assignment]
        if use_box_multiplicity is not _UNSET:
            new_use = bool(use_box_multiplicity)
            if row.use_box_multiplicity != new_use:
                _log_change(
                    db,
                    project_id,
                    barcode,
                    warehouse_id,
                    "use_box_multiplicity",
                    row.use_box_multiplicity,
                    new_use,
                    "manual",
                )
            row.use_box_multiplicity = new_use

    await db.commit()
    await invalidate_cache("reports:warehouse_need")
    logger.info(
        "per_warehouse updated: project=%s bc=%s wh=%s ppb=%s size=%s use=%s",
        project_id,
        barcode,
        warehouse_id,
        box_qty,
        box_size,
        use_box_multiplicity,
    )
    return True


async def resolve_effective_ppb_for_assembly(
    db: AsyncSession,
    project_id: int,
    barcode: str,
    warehouse_id: int,
) -> tuple[int | None, bool]:
    """Резолвить (box_qty, use_flag) для пары (barcode, ФФ) для алгоритма сборки.

    Использует ту же цепочку, что и table-builder:
      1. machine  — последняя ACCEPTED-приёмка → машина → qty-weighted mode
      2. manual   — BoxQtyPerWarehouse.box_qty (если не NULL)
      3. default  — Nomenclature.box_qty_override
      4. none     — (None, use_flag)

    `use_flag` per-ФФ: из строки BoxQtyPerWarehouse если она есть, иначе
    SKU-уровневый Nomenclature.use_box_multiplicity.

    Возвращает (None, use_flag) если ни одно правило не дало кратность —
    алгоритм сборки в этом случае пропускает округление.
    """
    rows = await get_box_multiplicity_table(db, project_id)
    for row in rows:
        if row["barcode"] != barcode:
            continue
        for pw in row["per_warehouse"]:
            if pw["warehouse_id"] == warehouse_id:
                return pw["box_qty"], pw["use_box_multiplicity"]
        # SKU есть, но ФФ нет в активных складах — вернём SKU-дефолт + флаг.
        return row["box_qty_override"], row["use_box_multiplicity"]

    # SKU вообще не найден — попробуем достать use-флаг из per-ФФ строки.
    per_rf = await db.execute(
        select(BoxQtyPerWarehouse).where(
            BoxQtyPerWarehouse.project_id == project_id,
            BoxQtyPerWarehouse.barcode == barcode,
            BoxQtyPerWarehouse.warehouse_id == warehouse_id,
        )
    )
    per_rf_row = per_rf.scalar_one_or_none()
    if per_rf_row is not None:
        return per_rf_row.box_qty, per_rf_row.use_box_multiplicity
    return None, True


def _pick_box_qty(
    machine_entry: dict | None,
    manual_row: "BoxQtyPerWarehouse | None",
    sku_override: int | None,
    sku_use: bool,
) -> int | None:
    """Единая цепочка кратности per (barcode, ФФ): machine → manual → SKU-override.

    use-флаг per-ФФ строки важнее SKU-уровня (зеркало table-builder). Источник
    истины для `resolve_box_qty_map` И `resolve_box_qty_map_multi` — держать
    выбор в ОДНОМ месте, чтобы список/раскладка/apply не расходились.
    """
    use_flag = manual_row.use_box_multiplicity if manual_row is not None else sku_use
    if not use_flag:
        return None
    if machine_entry is not None:
        bq = machine_entry.get("box_qty")
        return int(bq) if bq is not None else None
    if manual_row is not None and manual_row.box_qty is not None:
        return manual_row.box_qty
    if sku_override is not None:
        return sku_override
    return None


async def resolve_box_qty_map(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    barcodes: list[str],
) -> dict[str, int | None]:
    """Batch-резолв кратности коробки для МНОГИХ barcode на ОДНОМ ФФ-складе.

    Та же цепочка приоритетов, что и `resolve_effective_ppb_for_assembly`
    (machine → manual → default → none), но без перестроения всей таблицы
    проекта: ровно три батч-запроса —
      1. машинный резолв (`_resolve_machine_box_qty`, сам батчевый),
      2. ручные per-ФФ строки (`BoxQtyPerWarehouse`) для (warehouse_id, barcodes),
      3. `Nomenclature` (box_qty_override + use_box_multiplicity) по barcodes.

    Возвращает {barcode: box_qty}. Когда эффективный `use_box_multiplicity`
    выключен для пары (barcode, ФФ) — кратность подавляется (None). Barcode без
    кратности → None. Без N+1: per-barcode резолвер в цикле НЕ вызывается.
    """
    uniq = sorted({bc for bc in barcodes if bc})
    if not uniq:
        return {}

    # 1. Машинный резолв (батч) — пары (barcode, warehouse_id) → {box_qty, ...}.
    machine_map = await _resolve_machine_box_qty(db, project_id, uniq)

    # 2. Ручные per-ФФ строки для этого склада.
    manual_rows = await db.execute(
        select(BoxQtyPerWarehouse).where(
            BoxQtyPerWarehouse.project_id == project_id,
            BoxQtyPerWarehouse.warehouse_id == warehouse_id,
            BoxQtyPerWarehouse.barcode.in_(uniq),
        )
    )
    manual_by_bc: dict[str, BoxQtyPerWarehouse] = {r.barcode: r for r in manual_rows.scalars().all()}

    # 3. SKU-уровень: box_qty_override + use_box_multiplicity (один barcode → одна карточка).
    nom_rows = await db.execute(
        select(Nomenclature.barcode, Nomenclature.box_qty_override, Nomenclature.use_box_multiplicity).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(uniq),
        )
    )
    nom_by_bc: dict[str, tuple[int | None, bool]] = {
        bc: (override, bool(use)) for bc, override, use in nom_rows.all()
    }

    out: dict[str, int | None] = {}
    for bc in uniq:
        override, sku_use = nom_by_bc.get(bc, (None, True))
        out[bc] = _pick_box_qty(machine_map.get((bc, warehouse_id)), manual_by_bc.get(bc), override, sku_use)
    return out


async def resolve_box_qty_map_multi(
    db: AsyncSession,
    project_id: int,
    wh_to_barcodes: dict[int, list[str]],
) -> dict[tuple[int, str], int | None]:
    """Кратность коробки per (warehouse_id, barcode) для НЕСКОЛЬКИХ ФФ-складов.

    Как `resolve_box_qty_map`, но warehouse-независимые части (машинный резолв,
    SKU-override) и ручные строки грузятся ОДНИМ батчем на ВСЕ склады (≈4 запроса
    total, НЕ ×складов) — для списка сборок без N+1. Та же цепочка `_pick_box_qty`.
    """
    all_bcs = sorted({bc for bcs in wh_to_barcodes.values() for bc in bcs if bc})
    wh_ids = [wh for wh in wh_to_barcodes if wh is not None]
    if not all_bcs or not wh_ids:
        return {}

    machine_map = await _resolve_machine_box_qty(db, project_id, all_bcs)

    manual_rows = await db.execute(
        select(BoxQtyPerWarehouse).where(
            BoxQtyPerWarehouse.project_id == project_id,
            BoxQtyPerWarehouse.warehouse_id.in_(wh_ids),
            BoxQtyPerWarehouse.barcode.in_(all_bcs),
        )
    )
    manual_by_key: dict[tuple[int, str], BoxQtyPerWarehouse] = {
        (r.warehouse_id, r.barcode): r for r in manual_rows.scalars().all()
    }

    nom_rows = await db.execute(
        select(Nomenclature.barcode, Nomenclature.box_qty_override, Nomenclature.use_box_multiplicity).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(all_bcs),
        )
    )
    nom_by_bc: dict[str, tuple[int | None, bool]] = {
        bc: (override, bool(use)) for bc, override, use in nom_rows.all()
    }

    out: dict[tuple[int, str], int | None] = {}
    for wh_id, bcs in wh_to_barcodes.items():
        if wh_id is None:
            continue
        for bc in {b for b in bcs if b}:
            override, sku_use = nom_by_bc.get(bc, (None, True))
            out[(wh_id, bc)] = _pick_box_qty(
                machine_map.get((bc, wh_id)), manual_by_key.get((wh_id, bc)), override, sku_use
            )
    return out


async def bulk_update_by_barcode(
    db: AsyncSession,
    project_id: int,
    items: list[dict],
) -> dict:
    """Bulk paste-update: match by barcode, partial-update fields.

    Each item is `{barcode, box_qty_override?, use_box_multiplicity?,
    warehouse_id?}`. Если `warehouse_id` задан — апдейтим строку
    `box_qty_per_warehouse` (ручная per-ФФ кратность). Иначе — SKU-level
    `Nomenclature` поля.

    Машинно-заблокированные для изменения `box_qty` пары (barcode, ФФ)
    попадают в `locked` и не апдейтятся (только `use_box_multiplicity` для
    такой пары всё ещё применяется).

    Returns:
      {
        "matched_barcodes": set of barcodes that existed,
        "not_found": list of barcodes that don't exist (sorted, dedup),
        "updated_nm_ids": set of nm_ids whose row was actually changed,
        "locked": sorted list of barcodes machine-locked for box_qty change,
        "batch_id": UUID этой bulk-операции (если были изменения), иначе None,
      }
    """

    def _empty() -> dict:
        return {
            "batch_id": None,
            "matched_barcodes": set(),
            "not_found": [],
            "updated_nm_ids": set(),
            "locked": [],
        }

    if not items:
        return _empty()

    requested_bcs = [it["barcode"] for it in items if it.get("barcode")]
    if not requested_bcs:
        return _empty()

    # Fetch all matching Nomenclature rows in one query (project_id + barcode).
    nom_result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(set(requested_bcs)),
        )
    )
    by_bc: dict[str, list[Nomenclature]] = {}
    for nom in nom_result.scalars().all():
        by_bc.setdefault(nom.barcode, []).append(nom)

    # Machine-locked pairs (barcode, warehouse_id) — для per-ФФ items.
    machine_map = await _resolve_machine_box_qty(db, project_id, list(set(requested_bcs)))

    # Pre-load existing per-ФФ rows if any item has warehouse_id.
    per_rf_keys = {(it["barcode"], it["warehouse_id"]) for it in items if it.get("warehouse_id") and it.get("barcode")}
    per_rf_existing: dict[tuple[str, int], BoxQtyPerWarehouse] = {}
    if per_rf_keys:
        bcs_with_wh = list({bc for bc, _ in per_rf_keys})
        wh_ids_with_bc = list({wh for _, wh in per_rf_keys})
        per_rf_result = await db.execute(
            select(BoxQtyPerWarehouse).where(
                BoxQtyPerWarehouse.project_id == project_id,
                BoxQtyPerWarehouse.barcode.in_(bcs_with_wh),
                BoxQtyPerWarehouse.warehouse_id.in_(wh_ids_with_bc),
            )
        )
        for r in per_rf_result.scalars().all():
            per_rf_existing[(r.barcode, r.warehouse_id)] = r

    matched_barcodes: set[str] = set()
    updated_nm_ids: set[int] = set()
    locked: set[str] = set()
    any_change = False
    # UUID одной bulk-операции — общий для всех записей этой вставки.
    # Сохраним в журнал только если any_change=True (иначе rollback).
    batch_id = str(uuid.uuid4())

    for it in items:
        bc = it.get("barcode")
        if not bc:
            continue
        noms = by_bc.get(bc)
        if not noms:
            continue
        matched_barcodes.add(bc)

        wh_id = it.get("warehouse_id")
        ppb_set = "box_qty_override" in it
        size_set = "box_size" in it
        use_set = "use_box_multiplicity" in it
        if not ppb_set and not size_set and not use_set:
            continue

        if wh_id is not None:
            # Per-ФФ update: создаём или обновляем box_qty_per_warehouse.
            machine_locked = (bc, wh_id) in machine_map
            # Машинная блокировка — на box_qty и box_size; use_flag применяется всегда.
            apply_ppb = ppb_set and not machine_locked
            apply_size = size_set and not machine_locked
            if (ppb_set or size_set) and machine_locked:
                locked.add(bc)
            if not apply_ppb and not apply_size and not use_set:
                continue

            new_ppb = it.get("box_qty_override")
            new_size = it.get("box_size")
            new_use = bool(it["use_box_multiplicity"]) if use_set else True
            row = per_rf_existing.get((bc, wh_id))
            item_changed = False  # реально ли что-то поменялось для этого item
            if row is None:
                row = BoxQtyPerWarehouse(
                    project_id=project_id,
                    barcode=bc,
                    warehouse_id=wh_id,
                    box_qty=new_ppb if apply_ppb else None,
                    box_size=new_size if apply_size else None,
                    use_box_multiplicity=new_use,
                )
                db.add(row)
                per_rf_existing[(bc, wh_id)] = row
                any_change = True
                item_changed = True
                if apply_ppb and new_ppb is not None:
                    _log_change(db, project_id, bc, wh_id, "box_qty", None, new_ppb, "bulk", batch_id=batch_id)
                if apply_size and new_size is not None:
                    _log_change(db, project_id, bc, wh_id, "box_size", None, new_size, "bulk", batch_id=batch_id)
                if use_set and new_use is not True:
                    _log_change(
                        db, project_id, bc, wh_id, "use_box_multiplicity", True, new_use, "bulk", batch_id=batch_id
                    )
            else:
                if apply_ppb and row.box_qty != new_ppb:
                    _log_change(db, project_id, bc, wh_id, "box_qty", row.box_qty, new_ppb, "bulk", batch_id=batch_id)
                    row.box_qty = new_ppb
                    any_change = True
                    item_changed = True
                if apply_size and row.box_size != new_size:
                    _log_change(
                        db, project_id, bc, wh_id, "box_size", row.box_size, new_size, "bulk", batch_id=batch_id
                    )
                    row.box_size = new_size
                    any_change = True
                    item_changed = True
                if use_set and row.use_box_multiplicity != new_use:
                    _log_change(
                        db,
                        project_id,
                        bc,
                        wh_id,
                        "use_box_multiplicity",
                        row.use_box_multiplicity,
                        new_use,
                        "bulk",
                        batch_id=batch_id,
                    )
                    row.use_box_multiplicity = new_use
                    any_change = True
                    item_changed = True
            if not item_changed:
                continue
            for nom in noms:
                if nom.article_wb is not None:
                    updated_nm_ids.add(nom.article_wb)
        else:
            # SKU-level update (Nomenclature). Всегда разрешён.
            for nom in noms:
                changed = False
                if ppb_set and nom.box_qty_override != it["box_qty_override"]:
                    _log_change(
                        db,
                        project_id,
                        nom.barcode,
                        None,
                        "box_qty_override",
                        nom.box_qty_override,
                        it["box_qty_override"],
                        "bulk",
                        batch_id=batch_id,
                    )
                    nom.box_qty_override = it["box_qty_override"]
                    changed = True
                if use_set and nom.use_box_multiplicity != bool(it["use_box_multiplicity"]):
                    _log_change(
                        db,
                        project_id,
                        nom.barcode,
                        None,
                        "use_box_multiplicity",
                        nom.use_box_multiplicity,
                        bool(it["use_box_multiplicity"]),
                        "bulk",
                        batch_id=batch_id,
                    )
                    nom.use_box_multiplicity = bool(it["use_box_multiplicity"])
                    changed = True
                if changed:
                    any_change = True
                    if nom.article_wb is not None:
                        updated_nm_ids.add(nom.article_wb)

    if any_change:
        await db.commit()
        await invalidate_cache("reports:warehouse_need")
    else:
        await db.rollback()

    not_found = sorted({bc for bc in requested_bcs if bc not in matched_barcodes})
    logger.info(
        "bulk_update_by_barcode: project=%s requested=%s matched=%s changed=%s not_found=%s locked=%s",
        project_id,
        len(requested_bcs),
        len(matched_barcodes),
        len(updated_nm_ids),
        len(not_found),
        len(locked),
    )
    return {
        "batch_id": batch_id if any_change else None,
        "matched_barcodes": matched_barcodes,
        "not_found": not_found,
        "updated_nm_ids": updated_nm_ids,
        "locked": sorted(locked),
    }


async def get_sku_box_sources(
    db: AsyncSession,
    project_id: int,
    barcode: str,
) -> list[dict]:
    """Drill-down: вся история снабжения одного SKU (по barcode), read-only.

    Возвращает строки двух типов, отсортированные по дате (свежие сверху):
      - source_type="vehicle" — позиция машины (cost_order_items);
        `accepted` = есть ACCEPTED-приёмка этой машины; ФФ-склад = приёмка
        либо `cost_orders.target_warehouse_id`.
      - source_type="factory" — позиция заказа на фабрику (factory_order_items).

    Заказы фабрики НЕ являются источником кратности — показываются только
    как историческая справка.
    """
    rows: list[tuple[date | None, dict]] = []

    # ─── ACCEPTED-приёмки per cost_order: co_id → (accepted, warehouse_name) ─
    accepted_receipt_result = await db.execute(
        select(
            InboundReceipt.cost_order_id,
            Warehouse.name.label("warehouse_name"),
        )
        .join(Warehouse, Warehouse.id == InboundReceipt.warehouse_id)
        .where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.is_deleted == False,  # noqa: E712
            InboundReceipt.status == InboundStatus.ACCEPTED.value,
            InboundReceipt.cost_order_id.isnot(None),
        )
    )
    accepted_co: dict[int, str | None] = {}
    for r in accepted_receipt_result:  # type: ignore[assignment]
        accepted_co.setdefault(r.cost_order_id, r.warehouse_name)

    # ─── Vehicle lines (cost_order_items) ─────────────────────────────────
    veh_result = await db.execute(
        select(
            CostOrder.id.label("co_id"),
            CostOrderItem.qty,
            CostOrderItem.pcs_per_box_override,
            CostOrderItem.box_size_override,
            CostOrder.order_no,
            CostOrder.status.label("co_status"),
            CostOrder.actual_arrival_date,
            CostOrder.ship_date,
            CostOrder.created_at.label("co_created_at"),
            Warehouse.name.label("target_warehouse_name"),
            FactoryOrderItem.pcs_per_box.label("foi_pcs_per_box"),
            FactoryOrderItem.mix_pcs_per_box.label("foi_mix_pcs_per_box"),
            FactoryOrderItem.box_size.label("foi_box_size"),
            FactoryOrderItem.mix_box_size.label("foi_mix_box_size"),
            FactoryOrderItem.mix_group_id.label("foi_mix_group_id"),
        )
        .join(CostOrder, CostOrder.order_no == CostOrderItem.order_no)
        .outerjoin(FactoryOrderItem, FactoryOrderItem.id == CostOrderItem.factory_order_item_id)
        .outerjoin(Warehouse, Warehouse.id == CostOrder.target_warehouse_id)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.isnot(None),  # планово-затратные cost_orders (status NULL) — не машины снабжения
            CostOrderItem.barcode == barcode,
        )
    )
    for r in veh_result:  # type: ignore[assignment]
        sort_date = r.actual_arrival_date or r.ship_date or (r.co_created_at.date() if r.co_created_at else None)
        ppb = r.pcs_per_box_override or _foi_effective_ppb(r.foi_pcs_per_box, r.foi_mix_pcs_per_box, r.foi_mix_group_id)
        box_size = r.box_size_override or _foi_effective_box_size(
            r.foi_box_size, r.foi_mix_box_size, r.foi_mix_group_id
        )
        accepted = r.co_id in accepted_co
        # ФФ-склад: из приёмки (если принята), иначе target_warehouse машины.
        warehouse_name = accepted_co.get(r.co_id) if accepted else r.target_warehouse_name
        rows.append(
            (
                sort_date,
                {
                    "source_type": "vehicle",
                    "order_no": r.order_no,
                    "warehouse_name": warehouse_name,
                    "qty": int(r.qty or 0),
                    "box_qty": int(ppb) if ppb and ppb > 0 else None,
                    "box_size": box_size,
                    "date": sort_date.isoformat() if sort_date else None,
                    "status": getattr(r.co_status, "value", r.co_status),
                    "accepted": accepted,
                },
            )
        )

    # ─── Factory order lines (factory_order_items) — read-only справка ────
    foi_result = await db.execute(
        select(
            FactoryOrderItem.qty,
            FactoryOrderItem.pcs_per_box,
            FactoryOrderItem.mix_pcs_per_box,
            FactoryOrderItem.box_size,
            FactoryOrderItem.mix_box_size,
            FactoryOrderItem.mix_group_id,
            FactoryOrder.order_number,
            FactoryOrder.order_date,
        )
        .join(FactoryOrder, FactoryOrder.id == FactoryOrderItem.factory_order_id)
        .where(
            FactoryOrderItem.project_id == project_id,
            FactoryOrderItem.is_deleted == False,  # noqa: E712
            FactoryOrder.is_deleted == False,  # noqa: E712
            FactoryOrderItem.barcode == barcode,
        )
    )
    for r in foi_result:  # type: ignore[assignment]
        ppb = _foi_effective_ppb(r.pcs_per_box, r.mix_pcs_per_box, r.mix_group_id)
        box_size = _foi_effective_box_size(r.box_size, r.mix_box_size, r.mix_group_id)
        rows.append(
            (
                r.order_date,
                {
                    "source_type": "factory",
                    "order_no": r.order_number,
                    "warehouse_name": None,
                    "qty": int(r.qty or 0),
                    "box_qty": int(ppb) if ppb and ppb > 0 else None,
                    "box_size": box_size,
                    "date": r.order_date.isoformat() if r.order_date else None,
                    "status": None,
                    "accepted": False,
                },
            )
        )

    # Свежие сверху; строки без даты — в конец.
    rows.sort(key=lambda x: x[0] or date.min, reverse=True)
    return [row for _, row in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Журнал изменений + откат (box_multiplicity_change_log)
# ═══════════════════════════════════════════════════════════════════════════════


_CHANGE_LOG_LIMIT = 200


async def get_change_log(
    db: AsyncSession,
    project_id: int,
    barcode: str,
) -> list[dict]:
    """История изменений кратности/размера коробки для одного SKU (по barcode).

    Свежие сверху (`created_at desc, id desc`). `warehouse_name` резолвится
    JOIN-ом к `Warehouse`; для SKU-уровневых записей (`warehouse_id IS NULL`) —
    `None`. Ограничено `_CHANGE_LOG_LIMIT` записями.
    """
    result = await db.execute(
        select(
            BoxMultiplicityChangeLog.id,
            BoxMultiplicityChangeLog.warehouse_id,
            Warehouse.name.label("warehouse_name"),
            BoxMultiplicityChangeLog.field,
            BoxMultiplicityChangeLog.old_value,
            BoxMultiplicityChangeLog.new_value,
            BoxMultiplicityChangeLog.change_source,
            BoxMultiplicityChangeLog.batch_id,
            BoxMultiplicityChangeLog.created_at,
        )
        .outerjoin(Warehouse, Warehouse.id == BoxMultiplicityChangeLog.warehouse_id)
        .where(
            BoxMultiplicityChangeLog.project_id == project_id,
            BoxMultiplicityChangeLog.barcode == barcode,
        )
        .order_by(BoxMultiplicityChangeLog.created_at.desc(), BoxMultiplicityChangeLog.id.desc())
        .limit(_CHANGE_LOG_LIMIT)
    )
    return [
        {
            "id": r.id,
            "warehouse_id": r.warehouse_id,
            "warehouse_name": r.warehouse_name,
            "field": r.field,
            "old_value": r.old_value,
            "new_value": r.new_value,
            "change_source": r.change_source,
            "batch_id": r.batch_id,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in result  # type: ignore[assignment]
    ]


def _parse_logged_value(field: str, raw: str | None) -> object:
    """Парсинг строкового значения журнала обратно в типизированное по `field`."""
    if field in ("box_qty", "box_qty_override"):
        return None if raw is None else int(raw)
    if field == "box_size":
        return raw  # str | None — как есть
    if field == "use_box_multiplicity":
        return raw == "true"
    return raw


async def revert_change(
    db: AsyncSession,
    project_id: int,
    change_id: int,
) -> dict:
    """Откатить одно изменение из журнала: применить `old_value` обратно.

    Returns:
      {"barcode": str|None, "machine_locked": bool}
      - barcode is None → запись журнала не найдена (роутер → 404).
      - machine_locked True → ФФ сейчас машинно-заблокирован, откат box_qty/
        box_size не применён (роутер → 409).
    """
    log_result = await db.execute(
        select(BoxMultiplicityChangeLog).where(
            BoxMultiplicityChangeLog.project_id == project_id,
            BoxMultiplicityChangeLog.id == change_id,
        )
    )
    log = log_result.scalar_one_or_none()
    if log is None:
        return {"barcode": None, "machine_locked": False}

    barcode = log.barcode
    warehouse_id = log.warehouse_id
    field = log.field
    target_value = _parse_logged_value(field, log.old_value)

    # Машинная блокировка: откат box_qty/box_size на машинном ФФ запрещён.
    if warehouse_id is not None and field in ("box_qty", "box_size"):
        machine_map = await _resolve_machine_box_qty(db, project_id, [barcode])
        if (barcode, warehouse_id) in machine_map:
            return {"barcode": barcode, "machine_locked": True}

    if warehouse_id is None:
        # SKU-уровень — поле Nomenclature.
        nom_result = await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode == barcode,
            )
        )
        noms = nom_result.scalars().all()
        if not noms:
            return {"barcode": None, "machine_locked": False}
        for nom in noms:
            if field == "box_qty_override":
                old = nom.box_qty_override
                if old != target_value:
                    _log_change(db, project_id, barcode, None, field, old, target_value, "revert")
                nom.box_qty_override = target_value  # type: ignore[assignment]
            elif field == "use_box_multiplicity":
                old = nom.use_box_multiplicity
                if old != target_value:
                    _log_change(db, project_id, barcode, None, field, old, target_value, "revert")
                nom.use_box_multiplicity = bool(target_value)
    else:
        # Per-ФФ — поле BoxQtyPerWarehouse. Создаём строку если её нет.
        per_result = await db.execute(
            select(BoxQtyPerWarehouse).where(
                BoxQtyPerWarehouse.project_id == project_id,
                BoxQtyPerWarehouse.barcode == barcode,
                BoxQtyPerWarehouse.warehouse_id == warehouse_id,
            )
        )
        row = per_result.scalar_one_or_none()
        if row is None:
            row = BoxQtyPerWarehouse(
                project_id=project_id,
                barcode=barcode,
                warehouse_id=warehouse_id,
                box_qty=None,
                box_size=None,
                use_box_multiplicity=True,
            )
            db.add(row)
        old_pw: object
        if field == "box_qty":
            old_pw = row.box_qty
            if old_pw != target_value:
                _log_change(db, project_id, barcode, warehouse_id, field, old_pw, target_value, "revert")
            row.box_qty = target_value  # type: ignore[assignment]
        elif field == "box_size":
            old_pw = row.box_size
            if old_pw != target_value:
                _log_change(db, project_id, barcode, warehouse_id, field, old_pw, target_value, "revert")
            row.box_size = target_value  # type: ignore[assignment]
        elif field == "use_box_multiplicity":
            old_pw = row.use_box_multiplicity
            if old_pw != target_value:
                _log_change(db, project_id, barcode, warehouse_id, field, old_pw, target_value, "revert")
            row.use_box_multiplicity = bool(target_value)

    await db.commit()
    await invalidate_cache("reports:warehouse_need")
    logger.info(
        "box_multiplicity revert: project=%s change_id=%s bc=%s wh=%s field=%s -> %s",
        project_id,
        change_id,
        barcode,
        warehouse_id,
        field,
        target_value,
    )
    return {"barcode": barcode, "machine_locked": False}


async def list_recent_batches(
    db: AsyncSession,
    project_id: int,
    limit: int = 50,
) -> list[dict]:
    """Список последних bulk-операций (batch_id != NULL) для проекта.

    Группирует записи журнала по `batch_id`, отдаёт сводку:
    - batch_id
    - created_at (минимум по группе — момент применения)
    - changes_count (число строк журнала в batch)
    - affected_barcodes (число уникальных barcode в batch, для UI)
    Сортировка: новейший batch первым.
    """
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200
    result = await db.execute(
        select(
            BoxMultiplicityChangeLog.batch_id,
            func.min(BoxMultiplicityChangeLog.created_at).label("created_at"),
            func.count(BoxMultiplicityChangeLog.id).label("changes_count"),
            func.count(func.distinct(BoxMultiplicityChangeLog.barcode)).label("affected_barcodes"),
        )
        .where(
            BoxMultiplicityChangeLog.project_id == project_id,
            BoxMultiplicityChangeLog.batch_id.isnot(None),
        )
        .group_by(BoxMultiplicityChangeLog.batch_id)
        .order_by(func.min(BoxMultiplicityChangeLog.created_at).desc())
        .limit(limit)
    )
    return [
        {
            "batch_id": r.batch_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "changes_count": int(r.changes_count),
            "affected_barcodes": int(r.affected_barcodes),
        }
        for r in result
    ]


async def revert_batch(
    db: AsyncSession,
    project_id: int,
    batch_id: str,
) -> dict:
    """Откатить всю bulk-операцию (все записи журнала с этим batch_id).

    Применяет `old_value` записей в обратном хронологическом порядке (новейшая
    первая), чтобы возможные промежуточные шаги внутри batch разворачивались
    корректно. Машинно-заблокированные ФФ для `box_qty`/`box_size` пропускаются
    и попадают в `locked_barcodes` — их кратность зафиксирована машиной и
    отменить ручную правку поверх машины невозможно.

    Returns:
      {
        "found": bool,                # был ли вообще такой batch_id у проекта
        "reverted": int,              # сколько записей отменено
        "locked_barcodes": list[str], # barcodes, где откат поля заблокирован машиной
        "affected_barcodes": list[str], # уникальные barcodes, которых коснулся откат
      }
    """
    log_result = await db.execute(
        select(BoxMultiplicityChangeLog)
        .where(
            BoxMultiplicityChangeLog.project_id == project_id,
            BoxMultiplicityChangeLog.batch_id == batch_id,
        )
        .order_by(BoxMultiplicityChangeLog.created_at.desc(), BoxMultiplicityChangeLog.id.desc())
    )
    logs: list[BoxMultiplicityChangeLog] = list(log_result.scalars().all())
    if not logs:
        return {"found": False, "reverted": 0, "locked_barcodes": [], "affected_barcodes": []}

    barcodes = list({log.barcode for log in logs})
    # Подгружаем все Nomenclature по barcode одним запросом — для SKU-level откатов.
    nom_rows = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(barcodes),
        )
    )
    noms_by_bc: dict[str, list[Nomenclature]] = {}
    for nom in nom_rows.scalars().all():
        noms_by_bc.setdefault(nom.barcode, []).append(nom)

    # Машинные блокировки.
    machine_map = await _resolve_machine_box_qty(db, project_id, barcodes)

    # Подгружаем все per-ФФ строки одним запросом.
    per_rf_keys = [(log.barcode, log.warehouse_id) for log in logs if log.warehouse_id is not None]
    per_rf_existing: dict[tuple[str, int], BoxQtyPerWarehouse] = {}
    if per_rf_keys:
        bcs = list({bc for bc, _ in per_rf_keys})
        whs = list({wh for _, wh in per_rf_keys})
        per_rf_rows = await db.execute(
            select(BoxQtyPerWarehouse).where(
                BoxQtyPerWarehouse.project_id == project_id,
                BoxQtyPerWarehouse.barcode.in_(bcs),
                BoxQtyPerWarehouse.warehouse_id.in_(whs),
            )
        )
        for r in per_rf_rows.scalars().all():
            per_rf_existing[(r.barcode, r.warehouse_id)] = r

    reverted = 0
    locked_barcodes: set[str] = set()
    affected: set[str] = set()

    for log in logs:
        target_value = _parse_logged_value(log.field, log.old_value)
        if (
            log.warehouse_id is not None
            and log.field in ("box_qty", "box_size")
            and (log.barcode, log.warehouse_id) in machine_map
        ):
            locked_barcodes.add(log.barcode)
            continue

        if log.warehouse_id is None:
            noms = noms_by_bc.get(log.barcode, [])
            if not noms:
                continue
            for nom in noms:
                if log.field == "box_qty_override":
                    old = nom.box_qty_override
                    if old != target_value:
                        _log_change(db, project_id, log.barcode, None, log.field, old, target_value, "revert")
                    nom.box_qty_override = target_value  # type: ignore[assignment]
                elif log.field == "use_box_multiplicity":
                    old = nom.use_box_multiplicity
                    if old != target_value:
                        _log_change(db, project_id, log.barcode, None, log.field, old, target_value, "revert")
                    nom.use_box_multiplicity = bool(target_value)
            reverted += 1
            affected.add(log.barcode)
        else:
            key = (log.barcode, log.warehouse_id)
            row = per_rf_existing.get(key)
            if row is None:
                row = BoxQtyPerWarehouse(
                    project_id=project_id,
                    barcode=log.barcode,
                    warehouse_id=log.warehouse_id,
                    box_qty=None,
                    box_size=None,
                    use_box_multiplicity=True,
                )
                db.add(row)
                per_rf_existing[key] = row
            old_pw: object
            if log.field == "box_qty":
                old_pw = row.box_qty
                if old_pw != target_value:
                    _log_change(
                        db, project_id, log.barcode, log.warehouse_id, log.field, old_pw, target_value, "revert"
                    )
                row.box_qty = target_value  # type: ignore[assignment]
            elif log.field == "box_size":
                old_pw = row.box_size
                if old_pw != target_value:
                    _log_change(
                        db, project_id, log.barcode, log.warehouse_id, log.field, old_pw, target_value, "revert"
                    )
                row.box_size = target_value  # type: ignore[assignment]
            elif log.field == "use_box_multiplicity":
                old_pw = row.use_box_multiplicity
                if old_pw != target_value:
                    _log_change(
                        db, project_id, log.barcode, log.warehouse_id, log.field, old_pw, target_value, "revert"
                    )
                row.use_box_multiplicity = bool(target_value)
            reverted += 1
            affected.add(log.barcode)

    await db.commit()
    await invalidate_cache("reports:warehouse_need")
    logger.info(
        "box_multiplicity revert_batch: project=%s batch=%s reverted=%s locked=%s affected=%s",
        project_id,
        batch_id,
        reverted,
        len(locked_barcodes),
        len(affected),
    )
    return {
        "found": True,
        "reverted": reverted,
        "locked_barcodes": sorted(locked_barcodes),
        "affected_barcodes": sorted(affected),
    }
