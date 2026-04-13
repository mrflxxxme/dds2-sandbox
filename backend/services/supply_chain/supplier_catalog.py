"""
Supply Chain — Supplier catalog service.
Returns all items ever ordered from a supplier, grouped by subject.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.supply_chain import Supplier
from backend.schemas.supply_chain import (
    SkuOrderHistoryEntry,
    SupplierCatalogItem,
    SupplierCatalogResponse,
    SupplierCatalogSubjectGroup,
    SupplierCatalogSummary,
    SupplierCatalogSupplierInfo,
)

logger = logging.getLogger(__name__)

_DELIVERED = "DELIVERED"
# Safety cap for supplier catalog: ignore a supplier with an absurd number of
# line-items to prevent OOM / PgBouncer statement-too-large errors. A single
# real supplier rarely crosses a few thousand items over many years; the cap
# exists purely as a DoS safeguard.
_MAX_FOI_PER_SUPPLIER = 10000


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
    # ── 1. Verify supplier exists in this project ─────────────────────────────
    supplier_result = await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.project_id == project_id,
            Supplier.is_deleted == False,  # noqa: E712
        )
    )
    supplier = supplier_result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    # ── 2. Fetch all factory order items for this supplier ────────────────────
    # LEFT JOIN nomenclature to enrich missing subject/article_seller (Bug fix:
    # factory order detail page enriches in-memory via _enrich_items_from_nomenclature,
    # but catalog used raw DB values — items with NULL subject showed "Без предмета").
    # Also match orders by factory_name fallback when supplier_id is NULL (Bug fix:
    # some orders were created without supplier_id but with matching factory_name).
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
            "supplier_name": supplier.name,
            "project_id": project_id,
            "limit": _MAX_FOI_PER_SUPPLIER,
        },
    )
    foi_list = foi_rows.mappings().all()

    if not foi_list:
        return _build_empty_response(supplier)

    if len(foi_list) >= _MAX_FOI_PER_SUPPLIER:
        logger.warning(
            "Supplier catalog truncated at %d items (project_id=%d, supplier_id=%d) — " "consider pagination",
            _MAX_FOI_PER_SUPPLIER,
            project_id,
            supplier_id,
        )

    foi_ids = [r["foi_id"] for r in foi_list]

    # ── 3. Fetch CostOrderItems linked to those factory order items ───────────
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
    # foi_id → list[(vehicle_order_no, vehicle_status, qty)]
    coi_map: dict[int, list[tuple[str | None, str | None, int]]] = defaultdict(list)
    for coi in coi_rows.mappings().all():
        coi_map[coi["factory_order_item_id"]].append((coi["vehicle_order_no"], coi["vehicle_status"], coi["qty"]))

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

        for barcode in sorted(barcode_map.keys()):
            rows = barcode_map[barcode]

            total_qty = 0
            total_amount = Decimal("0")
            unique_fo_ids: set[int] = set()
            last_order_date: date | None = None
            last_price = Decimal("0")
            delivered_qty = 0
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
                delivered_qty += foi_delivered_qty
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
                    order_history=order_history,
                )
            )

            subj_total_qty += total_qty
            subj_total_amount += total_amount
            subj_delivered_qty += delivered_qty
            grand_delivered_amount += item_delivered_amount

        subjects.append(
            SupplierCatalogSubjectGroup(
                subject=subject_key,
                sku_count=len(items),
                total_qty=subj_total_qty,
                total_amount=subj_total_amount,
                delivered_qty=subj_delivered_qty,
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


async def invalidate_supplier_catalog(project_id: int) -> None:
    """Invalidate supplier catalog cache for a project."""
    await invalidate_cache(f"supply_chain:supplier_catalog:project_id={project_id}")
