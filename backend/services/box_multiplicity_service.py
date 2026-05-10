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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.cost import CostOrder, CostOrderItem, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrderItem

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

    # ─── Latest DELIVERED cost_order_item per barcode ──────────────────────
    vehicle_by_bc: dict[str, dict] = {}
    coi_result = await db.execute(
        select(
            CostOrderItem.barcode,
            CostOrderItem.pcs_per_box_override,
            FactoryOrderItem.pcs_per_box.label("foi_pcs_per_box"),
            FactoryOrderItem.mix_pcs_per_box.label("foi_mix_pcs_per_box"),
            FactoryOrderItem.mix_group_id.label("foi_mix_group_id"),
            CostOrder.order_no.label("order_no"),
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
    for row in coi_result:  # type: ignore[assignment]
        if row.barcode in vehicle_by_bc:
            continue  # already kept the most recent (sorted desc)
        foi_ppb = _foi_effective_ppb(row.foi_pcs_per_box, row.foi_mix_pcs_per_box, row.foi_mix_group_id)
        ppb = row.pcs_per_box_override or foi_ppb
        if ppb is None or ppb <= 0:
            continue
        vehicle_by_bc[row.barcode] = {
            "ppb": int(ppb),
            "order_no": row.order_no,
            "received_at": row.arrival_date.isoformat() if row.arrival_date else None,
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

    # ─── Build rows ────────────────────────────────────────────────────────
    rows: list[dict] = []
    for n in nomenclatures:
        veh = vehicle_by_bc.get(n.barcode)
        veh_ppb = veh["ppb"] if veh else None
        foi_ppb = foi_by_bc.get(n.barcode)
        effective = n.box_qty_override or veh_ppb or foi_ppb

        rows.append(
            {
                "nm_id": n.article_wb,
                "vendor_code": n.article_seller,
                "barcode": n.barcode,
                "brand": n.brand,
                "subject": n.subject,
                "box_qty_override": n.box_qty_override,
                "box_qty_from_vehicle": veh_ppb,
                "vehicle_order_no": veh["order_no"] if veh else None,
                "vehicle_received_at": veh["received_at"] if veh else None,
                "box_qty_from_factory": foi_ppb,
                "effective_box_qty": effective,
                "use_box_multiplicity": n.use_box_multiplicity,
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
