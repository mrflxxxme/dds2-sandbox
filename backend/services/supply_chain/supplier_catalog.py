"""
Supply Chain — Supplier catalog service.
Returns all items ever ordered from a supplier, grouped by subject.
Also provides a shipment matrix view (otgruzochnaya karta).
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.supply_chain import Supplier
from backend.schemas.supply_chain import (
    ShipmentMatrixItem,
    ShipmentMatrixResponse,
    ShipmentMatrixSubjectGroup,
    ShipmentMatrixSummary,
    ShipmentMatrixVehicle,
    SkuOrderHistoryEntry,
    SupplierCatalogItem,
    SupplierCatalogResponse,
    SupplierCatalogSubjectGroup,
    SupplierCatalogSummary,
    SupplierCatalogSupplierInfo,
)

logger = logging.getLogger(__name__)

_DELIVERED = "DELIVERED"
# Статусы машин, которые считаются «реально отгруженными с фабрики»  # noqa: RUF003
# (в отличие от FORMING — которые ещё наполняются)
_REALLY_SHIPPED_STATUSES = frozenset({"SHIPPED", "CUSTOMS", "DISPATCHED", "DELIVERED"})
_MAX_FOI_PER_SUPPLIER = 10000


async def _verify_supplier(db: AsyncSession, project_id: int, supplier_id: int) -> Supplier:
    """Fetch and verify supplier exists in the given project."""
    result = await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.project_id == project_id,
            Supplier.is_deleted == False,  # noqa: E712
        )
    )
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


async def _fetch_supplier_fois(db: AsyncSession, project_id: int, supplier_id: int, supplier_name: str) -> list:
    """Fetch all FactoryOrderItems for a supplier (shared by catalog & matrix)."""
    foi_rows = await db.execute(
        text("""
            SELECT
                foi.id            AS foi_id,
                foi.barcode,
                COALESCE(foi.subject, n.subject)               AS subject,
                COALESCE(foi.article_seller, n.article_seller) AS article_seller,
                n.brand,
                foi.qty,
                foi.price_cny,
                foi.box_size,
                foi.pcs_per_box,
                foi.weight_kg,
                fo.id             AS factory_order_id,
                fo.order_number,
                fo.order_date
            FROM factory_order_items foi
            JOIN factory_orders fo ON fo.id = foi.factory_order_id
            LEFT JOIN nomenclature n
                   ON n.barcode = foi.barcode AND n.project_id = fo.project_id
            WHERE (fo.supplier_id = :supplier_id
                   OR (fo.supplier_id IS NULL AND fo.factory_name = :supplier_name))
              AND fo.project_id  = :project_id
              AND fo.is_deleted  = false
              AND foi.is_deleted = false
            ORDER BY fo.order_date DESC NULLS LAST, fo.id DESC
            LIMIT :limit
        """),
        {
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "project_id": project_id,
            "limit": _MAX_FOI_PER_SUPPLIER,
        },
    )
    foi_list = list(foi_rows.mappings().all())

    if len(foi_list) >= _MAX_FOI_PER_SUPPLIER:
        logger.warning(
            "Supplier catalog truncated at %d items (project_id=%d, supplier_id=%d)",
            _MAX_FOI_PER_SUPPLIER,
            project_id,
            supplier_id,
        )

    return foi_list


async def _fetch_coi_map(
    db: AsyncSession, project_id: int, foi_ids: list[int]
) -> dict[int, list[tuple[str | None, str | None, int]]]:
    """Fetch CostOrderItems for given FOI ids. Returns foi_id → [(order_no, status, qty)]."""
    coi_rows = await db.execute(
        text("""
            SELECT
                coi.factory_order_item_id,
                co.order_no   AS vehicle_order_no,
                co.status     AS vehicle_status,
                coi.qty
            FROM cost_order_items coi
            JOIN cost_orders co ON co.order_no = coi.order_no
            WHERE co.project_id               = :project_id
              AND co.is_deleted               = false
              AND coi.is_deleted              = false
              AND coi.factory_order_item_id   = ANY(:foi_ids)
        """),
        {"project_id": project_id, "foi_ids": foi_ids},
    )
    coi_map: dict[int, list[tuple[str | None, str | None, int]]] = defaultdict(list)
    for coi in coi_rows.mappings().all():
        coi_map[coi["factory_order_item_id"]].append((coi["vehicle_order_no"], coi["vehicle_status"], coi["qty"]))
    return coi_map


@cached(prefix="supply_chain:supplier_catalog", ttl=300)
async def get_supplier_catalog(
    db: AsyncSession,
    project_id: int,
    supplier_id: int,
) -> dict:
    """
    Return all items ever ordered from a supplier, grouped by subject → barcode.

    Uses two queries (no N+1):
    1. All FactoryOrderItems for this supplier's orders.
    2. All CostOrderItems linked to those factory order items.
    """
    supplier = await _verify_supplier(db, project_id, supplier_id)

    foi_list = await _fetch_supplier_fois(db, project_id, supplier_id, supplier.name)

    if not foi_list:
        return _build_empty_response(supplier)

    foi_ids = [r["foi_id"] for r in foi_list]

    coi_map = await _fetch_coi_map(db, project_id, foi_ids)

    # ── 4. Aggregate in memory ─────────────────────────────────────────────────
    # subject → barcode → list of foi rows
    subject_barcode: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for row in foi_list:
        subject_key = row["subject"] if row["subject"] else "Без предмета"
        subject_barcode[subject_key][row["barcode"]].append(row)

    subjects: list[SupplierCatalogSubjectGroup] = []

    all_factory_order_ids: set[int] = set()
    all_barcodes: set[str] = set()
    grand_total_qty = 0
    grand_total_amount = Decimal("0")
    grand_delivered_amount = Decimal("0")

    for subject_key in sorted(subject_barcode.keys()):
        barcode_map = subject_barcode[subject_key]
        items: list[SupplierCatalogItem] = []

        subj_total_qty = 0
        subj_total_amount = Decimal("0")
        subj_delivered_qty = 0
        subj_distributed_qty = 0

        for barcode in sorted(barcode_map.keys()):
            rows = barcode_map[barcode]

            total_qty = 0
            total_amount = Decimal("0")
            unique_fo_ids: set[int] = set()
            last_order_date: date | None = None
            last_price = Decimal("0")
            delivered_qty = 0
            distributed_qty = 0
            item_delivered_amount = Decimal("0")

            order_history: list[SkuOrderHistoryEntry] = []

            for r in rows:
                qty = int(r["qty"])
                price_cny = Decimal(str(r["price_cny"])) if r["price_cny"] is not None else Decimal("0")
                amount = price_cny * qty

                total_qty += qty
                total_amount += amount
                unique_fo_ids.add(r["factory_order_id"])
                all_factory_order_ids.add(r["factory_order_id"])
                all_barcodes.add(barcode)

                # Track last_order_date / last_price
                row_date: date | None = r["order_date"]
                if row_date is not None and (last_order_date is None or row_date > last_order_date):
                    last_order_date = row_date
                    last_price = price_cny

                # Vehicle info for this foi
                foi_vehicles = coi_map.get(r["foi_id"], [])

                foi_delivered_qty = sum(v_qty for _, v_status, v_qty in foi_vehicles if v_status == _DELIVERED)
                foi_distributed_qty = sum(v_qty for _, _, v_qty in foi_vehicles)
                delivered_qty += foi_delivered_qty
                distributed_qty += foi_distributed_qty
                # Accumulate delivered amount using actual price_cny (not avg)
                item_delivered_amount += price_cny * foi_delivered_qty

                # Pick representative vehicle (first one found)
                v_order_no: str | None = None
                v_status: str | None = None
                foi_is_delivered = False
                if foi_vehicles:
                    v_order_no, v_status, _ = foi_vehicles[0]
                    foi_is_delivered = any(v_st == _DELIVERED for _, v_st, _ in foi_vehicles)

                order_history.append(
                    SkuOrderHistoryEntry(
                        factory_order_id=r["factory_order_id"],
                        order_number=r["order_number"],
                        order_date=row_date,
                        qty=qty,
                        price_cny=price_cny,
                        amount=amount,
                        vehicle_order_no=v_order_no,
                        vehicle_status=v_status,
                        is_delivered=foi_is_delivered,
                    )
                )

            # If no dated row updated last_price, use first row price
            if last_order_date is None and rows:
                last_price = Decimal(str(rows[0]["price_cny"])) if rows[0]["price_cny"] is not None else Decimal("0")

            avg_price = (total_amount / total_qty) if total_qty > 0 else Decimal("0")

            # Sort history by order_date DESC (None last)
            order_history.sort(key=lambda x: x.order_date or date.min, reverse=True)

            items.append(
                SupplierCatalogItem(
                    barcode=barcode,
                    article_seller=rows[0]["article_seller"],
                    subject=rows[0]["subject"],
                    brand=rows[0]["brand"],
                    box_size=rows[0]["box_size"],
                    pcs_per_box=rows[0]["pcs_per_box"],
                    weight_kg=Decimal(str(rows[0]["weight_kg"])) if rows[0]["weight_kg"] is not None else None,
                    total_qty=total_qty,
                    last_price=last_price,
                    avg_price=avg_price,
                    total_amount=total_amount,
                    orders_count=len(unique_fo_ids),
                    last_order_date=last_order_date,
                    delivered_qty=delivered_qty,
                    distributed_qty=distributed_qty,
                    order_history=order_history,
                )
            )

            subj_total_qty += total_qty
            subj_total_amount += total_amount
            subj_delivered_qty += delivered_qty
            subj_distributed_qty += distributed_qty
            grand_delivered_amount += item_delivered_amount

        subjects.append(
            SupplierCatalogSubjectGroup(
                subject=subject_key,
                sku_count=len(items),
                total_qty=subj_total_qty,
                total_amount=subj_total_amount,
                delivered_qty=subj_delivered_qty,
                distributed_qty=subj_distributed_qty,
                items=items,
            )
        )

        grand_total_qty += subj_total_qty
        grand_total_amount += subj_total_amount

    summary = SupplierCatalogSummary(
        orders_count=len(all_factory_order_ids),
        sku_count=len(all_barcodes),
        total_qty=grand_total_qty,
        total_amount=grand_total_amount,
        delivered_amount=grand_delivered_amount,
    )

    response = SupplierCatalogResponse(
        supplier=SupplierCatalogSupplierInfo(
            id=supplier.id,
            name=supplier.name,
            country=supplier.country,
            currency=supplier.currency,
        ),
        summary=summary,
        subjects=subjects,
    )
    return response.model_dump(mode="json")


def _build_empty_response(supplier: Supplier) -> dict:
    return SupplierCatalogResponse(
        supplier=SupplierCatalogSupplierInfo(
            id=supplier.id,
            name=supplier.name,
            country=supplier.country,
            currency=supplier.currency,
        ),
        summary=SupplierCatalogSummary(
            orders_count=0,
            sku_count=0,
            total_qty=0,
            total_amount=Decimal("0"),
            delivered_amount=Decimal("0"),
        ),
        subjects=[],
    ).model_dump(mode="json")


def _supplier_info(supplier: Supplier) -> SupplierCatalogSupplierInfo:
    return SupplierCatalogSupplierInfo(
        id=supplier.id,
        name=supplier.name,
        country=supplier.country,
        currency=supplier.currency,
    )


# ── Shipment Matrix ─────────────────────────────────────────────────────────


@cached(prefix="supply_chain:shipment_matrix", ttl=300)
async def get_shipment_matrix(
    db: AsyncSession,
    project_id: int,
    supplier_id: int,
) -> dict:
    """
    Return a shipment matrix for a supplier: rows=barcodes grouped by subject,
    columns=vehicles, cells=allocated qty.
    """
    supplier = await _verify_supplier(db, project_id, supplier_id)

    foi_list = await _fetch_supplier_fois(db, project_id, supplier_id, supplier.name)

    if not foi_list:
        return ShipmentMatrixResponse(
            supplier=_supplier_info(supplier),
            vehicles=[],
            summary=ShipmentMatrixSummary(
                total_qty=0, total_boxes=0, shipped_qty=0, really_shipped_qty=0, remaining_qty=0
            ),
            subjects=[],
        ).model_dump(mode="json")

    foi_ids = [r["foi_id"] for r in foi_list]

    # ── Enhanced COI query: also fetch container_type and ship_date ───────────
    coi_rows = await db.execute(
        text("""
            SELECT
                coi.factory_order_item_id,
                co.order_no        AS vehicle_order_no,
                co.status          AS vehicle_status,
                co.container_type,
                co.ship_date,
                coi.qty
            FROM cost_order_items coi
            JOIN cost_orders co ON co.order_no = coi.order_no
            WHERE co.project_id             = :project_id
              AND co.is_deleted             = false
              AND coi.is_deleted            = false
              AND coi.factory_order_item_id = ANY(:foi_ids)
        """),
        {"project_id": project_id, "foi_ids": foi_ids},
    )

    # Build COI map and vehicles dict
    coi_map: dict[int, list[tuple[str, int]]] = defaultdict(list)  # foi_id → [(order_no, qty)]
    vehicles_dict: dict[str, ShipmentMatrixVehicle] = {}
    vehicle_status_map: dict[str, str | None] = {}  # order_no → status (для really_shipped подсчёта)

    for coi in coi_rows.mappings().all():
        order_no = coi["vehicle_order_no"]
        coi_map[coi["factory_order_item_id"]].append((order_no, coi["qty"]))
        if order_no not in vehicles_dict:
            vehicles_dict[order_no] = ShipmentMatrixVehicle(
                order_no=order_no,
                status=coi["vehicle_status"],
                container_type=coi["container_type"],
                ship_date=coi["ship_date"],
            )
            vehicle_status_map[order_no] = coi["vehicle_status"]

    # ── Aggregate by subject → barcode ───────────────────────────────────────
    subject_barcode: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in foi_list:
        subject_key = row["subject"] if row["subject"] else "Без предмета"
        subject_barcode[subject_key][row["barcode"]].append(row)

    subjects: list[ShipmentMatrixSubjectGroup] = []
    grand_total_qty = 0
    grand_total_boxes = 0
    grand_shipped_qty = 0
    grand_really_shipped_qty = 0

    for subject_key in sorted(subject_barcode.keys()):
        barcode_map = subject_barcode[subject_key]
        items: list[ShipmentMatrixItem] = []
        subj_total_qty = 0
        subj_total_boxes = 0
        subj_remaining = 0

        for barcode in sorted(barcode_map.keys()):
            rows = barcode_map[barcode]

            total_qty = sum(int(r["qty"]) for r in rows)
            pcs = rows[0]["pcs_per_box"]
            pcs_per_box = int(pcs) if pcs else None
            total_boxes = math.ceil(total_qty / pcs_per_box) if pcs_per_box and pcs_per_box > 0 else 0

            # Latest order date across all rows (для UI приоритизации по возрасту)
            order_dates = [r["order_date"] for r in rows if r["order_date"] is not None]
            latest_order_date = max(order_dates) if order_dates else None

            # Accumulate vehicle allocations + really_shipped (машина >= SHIPPED)
            allocations: dict[str, int] = defaultdict(int)
            really_shipped = 0
            for r in rows:
                for v_order_no, v_qty in coi_map.get(r["foi_id"], []):
                    allocations[v_order_no] += v_qty
                    if vehicle_status_map.get(v_order_no) in _REALLY_SHIPPED_STATUSES:
                        really_shipped += v_qty

            shipped_qty = sum(allocations.values())
            remaining = total_qty - shipped_qty
            pct = Decimal(str(round(shipped_qty / total_qty * 100, 1))) if total_qty > 0 else Decimal("0")

            items.append(
                ShipmentMatrixItem(
                    barcode=barcode,
                    article_seller=rows[0]["article_seller"],
                    subject=rows[0]["subject"],
                    brand=rows[0]["brand"],
                    total_qty=total_qty,
                    total_boxes=total_boxes,
                    pcs_per_box=pcs_per_box,
                    remaining_qty=remaining,
                    shipped_pct=pct,
                    really_shipped_qty=really_shipped,
                    latest_order_date=latest_order_date,
                    vehicle_allocations=dict(allocations),
                )
            )

            subj_total_qty += total_qty
            subj_total_boxes += total_boxes
            subj_remaining += remaining
            grand_really_shipped_qty += really_shipped

        subj_shipped = subj_total_qty - subj_remaining
        subj_pct = Decimal(str(round(subj_shipped / subj_total_qty * 100, 1))) if subj_total_qty > 0 else Decimal("0")

        subjects.append(
            ShipmentMatrixSubjectGroup(
                subject=subject_key,
                items=items,
                total_qty=subj_total_qty,
                total_boxes=subj_total_boxes,
                remaining_qty=subj_remaining,
                shipped_pct=subj_pct,
            )
        )

        grand_total_qty += subj_total_qty
        grand_total_boxes += subj_total_boxes
        grand_shipped_qty += subj_shipped

    # Sort: most remaining first
    subjects.sort(key=lambda s: s.remaining_qty, reverse=True)

    # Sort vehicles by ship_date ASC (nulls last)
    vehicles = sorted(
        vehicles_dict.values(),
        key=lambda v: v.ship_date or date.max,
    )

    response = ShipmentMatrixResponse(
        supplier=_supplier_info(supplier),
        vehicles=vehicles,
        summary=ShipmentMatrixSummary(
            total_qty=grand_total_qty,
            total_boxes=grand_total_boxes,
            shipped_qty=grand_shipped_qty,
            really_shipped_qty=grand_really_shipped_qty,
            remaining_qty=grand_total_qty - grand_shipped_qty,
        ),
        subjects=subjects,
    )
    return response.model_dump(mode="json")


async def invalidate_supplier_catalog(project_id: int) -> None:
    """Invalidate supplier catalog and shipment matrix cache for a project."""
    await invalidate_cache(f"supply_chain:supplier_catalog:project_id={project_id}")
    await invalidate_cache(f"supply_chain:shipment_matrix:project_id={project_id}")
