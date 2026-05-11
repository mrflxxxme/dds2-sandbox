# ruff: noqa: RUF002, RUF003
"""Box-multiplicity service: per-SKU effective pcs-per-box for assembly distribution.

Sources, in priority order:
  1. `Nomenclature.box_qty_override` — manual (UI input)
  2. `cost_order_items.pcs_per_box_override` of the most recent DELIVERED vehicle
     (fallback to the linked `factory_order_items.pcs_per_box`)
  3. Latest active `factory_order_items.pcs_per_box` (or `mix_pcs_per_box`)

The first non-null wins → `effective_box_qty`. If all three are null,
`effective_box_qty` is null and assembly distribution skips box-rounding for
that SKU.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models import WbWarehouseStock
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import BoxQtyPerWarehouse, CostOrder, CostOrderItem, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrderItem
from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType

logger = logging.getLogger("dds.box_multiplicity")


def _foi_effective_ppb(
    foi_pcs_per_box: int | None, mix_pcs_per_box: int | None, mix_group_id: str | None
) -> int | None:
    """Resolve FOI's effective pcs_per_box (mix variant if mixed, else regular)."""
    if mix_group_id and mix_pcs_per_box:
        return mix_pcs_per_box
    return foi_pcs_per_box


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

    # ─── DELIVERED cost_order_items per barcode: qty-weighted mode ppb ─────
    # В одной машине у одного barcode могут быть несколько строк с разной
    # ppb (часть в коробах по 10, часть по 12). Берём наиболее «весомую»
    # (по сумме qty), остальные показываем как alts. Информация — из самой
    # последней DELIVERED-машины с этим barcode.
    vehicle_by_bc: dict[str, dict] = {}
    coi_result = await db.execute(
        select(
            CostOrderItem.barcode,
            CostOrderItem.qty,
            CostOrderItem.pcs_per_box_override,
            FactoryOrderItem.pcs_per_box.label("foi_pcs_per_box"),
            FactoryOrderItem.mix_pcs_per_box.label("foi_mix_pcs_per_box"),
            FactoryOrderItem.mix_group_id.label("foi_mix_group_id"),
            CostOrder.order_no.label("order_no"),
            CostOrder.id.label("co_id"),
            CostOrder.actual_arrival_date.label("arrival_date"),
        )
        .join(CostOrder, CostOrder.order_no == CostOrderItem.order_no)
        .outerjoin(FactoryOrderItem, FactoryOrderItem.id == CostOrderItem.factory_order_item_id)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status == VehicleStatus.DELIVERED,
            CostOrderItem.barcode.in_(barcodes),
        )
        .order_by(CostOrder.actual_arrival_date.desc().nullslast(), CostOrder.id.desc())
    )
    # Группируем по barcode → берём строки последней машины (первый встретившийся
    # co_id для barcode, т.к. отсортировано desc), считаем mode ppb по qty.
    coi_first_co_per_bc: dict[str, int] = {}  # barcode → первый встретившийся co_id
    coi_grouped: dict[str, list[tuple[int | None, int | None, int]]] = {}
    # ^ barcode → list of (ppb_or_none, foi_ppb, qty)
    coi_meta_per_bc: dict[str, dict] = {}  # barcode → {order_no, received_at}
    for row in coi_result:  # type: ignore[assignment]
        bc = row.barcode
        co_id = row.co_id
        # Берём строки только из самой последней машины per barcode.
        if bc not in coi_first_co_per_bc:
            coi_first_co_per_bc[bc] = co_id
            coi_meta_per_bc[bc] = {
                "order_no": row.order_no,
                "received_at": row.arrival_date.isoformat() if row.arrival_date else None,
            }
        if co_id != coi_first_co_per_bc[bc]:
            continue
        foi_ppb = _foi_effective_ppb(row.foi_pcs_per_box, row.foi_mix_pcs_per_box, row.foi_mix_group_id)
        coi_grouped.setdefault(bc, []).append((row.pcs_per_box_override, foi_ppb, int(row.qty or 0)))

    for bc, entries in coi_grouped.items():
        # Распределяем qty по ppb (override → или foi_ppb).
        ppb_to_qty: dict[int, int] = {}
        for override, foi_ppb, qty in entries:
            ppb = override or foi_ppb
            if ppb is None or ppb <= 0 or qty <= 0:
                continue
            ppb_to_qty[int(ppb)] = ppb_to_qty.get(int(ppb), 0) + qty
        if not ppb_to_qty:
            continue
        # Mode: ppb с наибольшей суммарной qty.
        primary = max(ppb_to_qty.items(), key=lambda x: x[1])[0]
        alts = sorted(p for p in ppb_to_qty if p != primary)
        vehicle_by_bc[bc] = {
            "ppb": primary,
            "alts": alts,
            **coi_meta_per_bc[bc],
        }

    # ─── Latest active FOI per barcode (fallback) ──────────────────────────
    foi_by_bc: dict[str, int] = {}
    foi_result = await db.execute(
        select(
            FactoryOrderItem.barcode,
            FactoryOrderItem.pcs_per_box,
            FactoryOrderItem.mix_pcs_per_box,
            FactoryOrderItem.mix_group_id,
        )
        .where(
            FactoryOrderItem.project_id == project_id,
            FactoryOrderItem.is_deleted == False,  # noqa: E712
            FactoryOrderItem.barcode.in_(barcodes),
        )
        .order_by(FactoryOrderItem.id.desc())
    )
    for row in foi_result:  # type: ignore[assignment]
        if row.barcode in foi_by_bc:
            continue
        ppb = _foi_effective_ppb(row.pcs_per_box, row.mix_pcs_per_box, row.mix_group_id)
        if ppb and ppb > 0:
            foi_by_bc[row.barcode] = int(ppb)

    # ─── Active RF warehouses (для per-RF блока) ──────────────────────────
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

    # ─── Per-RF override map: (barcode, warehouse_id) → {box_qty, use} ────
    per_rf_map: dict[tuple[str, int], dict] = {}
    if barcodes:
        per_rf_result = await db.execute(
            select(BoxQtyPerWarehouse).where(
                BoxQtyPerWarehouse.project_id == project_id,
                BoxQtyPerWarehouse.barcode.in_(barcodes),
            )
        )
        for row in per_rf_result.scalars().all():  # type: ignore[assignment]
            per_rf_map[(row.barcode, row.warehouse_id)] = {
                "box_qty": row.box_qty,
                "use_box_multiplicity": row.use_box_multiplicity,
            }

    # ─── Per-RF stock per (nm_id, warehouse_id) ───────────────────────────
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

    # FF (RF fulfillment) сток — сумма по всем активным RF-складам проекта.
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
        veh = vehicle_by_bc.get(n.barcode)
        veh_ppb = veh["ppb"] if veh else None
        foi_ppb = foi_by_bc.get(n.barcode)
        effective = n.box_qty_override or veh_ppb or foi_ppb

        rf_stock = rf_stock_map.get(n.article_wb, 0) if n.article_wb else 0
        in_asm = in_assembly_map.get(n.article_wb, 0) if n.article_wb else 0
        in_tr = in_transit_map.get(n.article_wb, 0) if n.article_wb else 0
        wb_stock = wb_stock_map.get(n.article_wb, 0) if n.article_wb else 0

        # Per-RF block: одна строка на каждый активный RF-склад
        per_warehouse: list[dict] = []
        for wh_id, wh_name in rf_warehouses:
            rf_override = per_rf_map.get((n.barcode, wh_id))
            per_warehouse.append(
                {
                    "warehouse_id": wh_id,
                    "warehouse_name": wh_name,
                    "box_qty": rf_override["box_qty"] if rf_override else None,
                    "use_box_multiplicity": rf_override["use_box_multiplicity"] if rf_override else True,
                    "rf_stock": per_rf_stock_map.get((n.article_wb, wh_id), 0) if n.article_wb else 0,
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
                "box_qty_from_vehicle": veh_ppb,
                "box_qty_from_vehicle_alts": veh["alts"] if veh else [],
                "vehicle_order_no": veh["order_no"] if veh else None,
                "vehicle_received_at": veh["received_at"] if veh else None,
                "box_qty_from_factory": foi_ppb,
                "effective_box_qty": effective,
                "use_box_multiplicity": n.use_box_multiplicity,
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
    """Partial update: only fields explicitly passed are touched.

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
            nom.box_qty_override = box_qty_override  # type: ignore[assignment]
        if use_box_multiplicity is not _UNSET:
            nom.use_box_multiplicity = bool(use_box_multiplicity)
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


async def update_per_warehouse(
    db: AsyncSession,
    project_id: int,
    barcode: str,
    warehouse_id: int,
    *,
    box_qty: object = _UNSET,  # only applied if not _UNSET; can be None to clear
    use_box_multiplicity: object = _UNSET,
) -> bool:
    """Partial update per-RF override. Создаёт строку если её нет.

    Returns False if (project_id, barcode, warehouse_id) ссылка невалидна
    (нет такого склада или barcode), True если применили (включая no-op).
    """
    if box_qty is _UNSET and use_box_multiplicity is _UNSET:
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
        # Создаём — с дефолтами для непереданных полей
        row = BoxQtyPerWarehouse(
            project_id=project_id,
            barcode=barcode,
            warehouse_id=warehouse_id,
            box_qty=None if box_qty is _UNSET else box_qty,  # type: ignore[assignment]
            use_box_multiplicity=True if use_box_multiplicity is _UNSET else bool(use_box_multiplicity),
        )
        db.add(row)
    else:
        if box_qty is not _UNSET:
            row.box_qty = box_qty  # type: ignore[assignment]
        if use_box_multiplicity is not _UNSET:
            row.use_box_multiplicity = bool(use_box_multiplicity)

    await db.commit()
    await invalidate_cache("reports:warehouse_need")
    logger.info(
        "per_warehouse updated: project=%s bc=%s wh=%s ppb=%s use=%s",
        project_id,
        barcode,
        warehouse_id,
        box_qty,
        use_box_multiplicity,
    )
    return True


async def resolve_effective_ppb_for_assembly(
    db: AsyncSession,
    project_id: int,
    barcode: str,
    warehouse_id: int,
) -> tuple[int | None, bool]:
    """Резолвить (effective_ppb, use_flag) для (barcode, RF) для алгоритма сборки.

    Приоритет:
      1. box_qty_per_warehouse (per-RF) — если задан, его use_flag тоже per-RF
      2. Nomenclature.box_qty_override + Nomenclature.use_box_multiplicity
      3. vehicle qty-weighted mode (latest DELIVERED)
      4. factory ppb

    Возвращает (None, _) если ни одно правило не дало ppb — алгоритм сборки
    в этом случае пропускает округление.
    """
    # 1. per-RF
    per_rf = await db.execute(
        select(BoxQtyPerWarehouse).where(
            BoxQtyPerWarehouse.project_id == project_id,
            BoxQtyPerWarehouse.barcode == barcode,
            BoxQtyPerWarehouse.warehouse_id == warehouse_id,
        )
    )
    per_rf_row = per_rf.scalar_one_or_none()
    if per_rf_row and per_rf_row.box_qty:
        return per_rf_row.box_qty, per_rf_row.use_box_multiplicity

    # 2. SKU-level override
    nom = await db.execute(
        select(Nomenclature)
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode == barcode,
        )
        .limit(1)
    )
    nom_row = nom.scalar_one_or_none()
    if nom_row and nom_row.box_qty_override:
        # use-флаг: если есть per-RF строка, её флаг важнее даже когда box_qty=None
        use = per_rf_row.use_box_multiplicity if per_rf_row else nom_row.use_box_multiplicity
        return nom_row.box_qty_override, use

    # 3-4. собираем через get_box_multiplicity_table эффективный ppb
    rows = await get_box_multiplicity_table(
        db,
        project_id,
        nm_id_filter=nom_row.article_wb if nom_row and nom_row.article_wb else None,
    )
    eff = rows[0]["effective_box_qty"] if rows else None
    use = per_rf_row.use_box_multiplicity if per_rf_row else (nom_row.use_box_multiplicity if nom_row else True)
    return eff, use


async def bulk_update_by_barcode(
    db: AsyncSession,
    project_id: int,
    items: list[dict],
) -> dict:
    """Bulk paste-update: match by barcode, partial-update fields.

    Each item is `{barcode, box_qty_override?, use_box_multiplicity?,
    warehouse_id?}`. Если `warehouse_id` задан — апдейтим строку
    `box_qty_per_warehouse` (per-RF override). Иначе — SKU-level
    `Nomenclature` поля.

    Returns:
      {
        "matched_barcodes": set of barcodes that existed,
        "not_found": list of barcodes that don't exist (sorted, dedup),
        "updated_nm_ids": set of nm_ids whose row was actually changed,
      }
    """
    if not items:
        return {"matched_barcodes": set(), "not_found": [], "updated_nm_ids": set()}

    requested_bcs = [it["barcode"] for it in items if it.get("barcode")]
    if not requested_bcs:
        return {"matched_barcodes": set(), "not_found": [], "updated_nm_ids": set()}

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

    # Pre-load existing per-RF rows if any item has warehouse_id.
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
    any_change = False

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
        use_set = "use_box_multiplicity" in it
        if not ppb_set and not use_set:
            continue

        if wh_id is not None:
            # Per-RF update: создаём или обновляем box_qty_per_warehouse.
            row = per_rf_existing.get((bc, wh_id))
            if row is None:
                row = BoxQtyPerWarehouse(
                    project_id=project_id,
                    barcode=bc,
                    warehouse_id=wh_id,
                    box_qty=it["box_qty_override"] if ppb_set else None,
                    use_box_multiplicity=bool(it["use_box_multiplicity"]) if use_set else True,
                )
                db.add(row)
                per_rf_existing[(bc, wh_id)] = row
                any_change = True
            else:
                if ppb_set and row.box_qty != it["box_qty_override"]:
                    row.box_qty = it["box_qty_override"]
                    any_change = True
                if use_set and row.use_box_multiplicity != bool(it["use_box_multiplicity"]):
                    row.use_box_multiplicity = bool(it["use_box_multiplicity"])
                    any_change = True
            for nom in noms:
                if nom.article_wb is not None:
                    updated_nm_ids.add(nom.article_wb)
        else:
            # SKU-level update (Nomenclature).
            for nom in noms:
                changed = False
                if ppb_set and nom.box_qty_override != it["box_qty_override"]:
                    nom.box_qty_override = it["box_qty_override"]
                    changed = True
                if use_set and nom.use_box_multiplicity != bool(it["use_box_multiplicity"]):
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
        "bulk_update_by_barcode: project=%s requested=%s matched=%s changed=%s not_found=%s",
        project_id,
        len(requested_bcs),
        len(matched_barcodes),
        len(updated_nm_ids),
        len(not_found),
    )
    return {
        "matched_barcodes": matched_barcodes,
        "not_found": not_found,
        "updated_nm_ids": updated_nm_ids,
    }
