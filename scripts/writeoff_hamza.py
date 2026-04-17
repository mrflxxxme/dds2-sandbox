"""
One-shot writeoff of stock at warehouse "Хамза".

Creates an OutboundShipment (DRAFT → SHIPPED) for 12 barcodes (1644 units).
Uses existing services/warehouse_outbound.py — same path as UI.

Usage:
  docker compose exec backend python scripts/writeoff_hamza.py --project-slug default --dry-run
  docker compose exec backend python scripts/writeoff_hamza.py --project-id <ID> --commit
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from backend.database import get_db
from backend.models.auth import Project
from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType
from backend.services.warehouse_outbound import create_shipment, ship_shipment
from backend.services.warehouse_stock_engine import _resolve_barcodes_batch

ITEMS: list[tuple[str, int]] = [
    ("2043788816553", 19),
    ("2043788824176", 160),
    ("2047111538457", 247),
    ("2043788796855", 93),
    ("2047110489330", 14),
    ("2045932869491", 8),
    ("2045932869521", 24),
    ("2043788747611", 344),
    ("2047110740950", 24),
    ("2047213093069", 326),
    ("2047114210442", 44),
    ("2047213311729", 341),
]

COMMENT = "Списание остатков (ручное, Excel-загрузка)"


async def main(project_id: int | None, project_slug: str | None, commit: bool) -> int:
    async for db in get_db():
        if project_id is None:
            assert project_slug is not None
            proj_q = await db.execute(
                select(Project.id, Project.name).where(
                    Project.slug == project_slug,
                    Project.is_deleted == False,  # noqa: E712
                )
            )
            row = proj_q.first()
            if row is None:
                print(f"ERROR: project with slug {project_slug!r} not found", file=sys.stderr)
                return 1
            project_id, proj_name = row
            print(f"Project: id={project_id} slug={project_slug!r} name={proj_name!r}")

        wh_q = await db.execute(
            select(Warehouse).where(
                Warehouse.project_id == project_id,
                Warehouse.name.ilike("%хамза%"),
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
        warehouses = list(wh_q.scalars().all())
        if not warehouses:
            print(f"ERROR: warehouse matching 'хамза' not found in project {project_id}", file=sys.stderr)
            return 1
        if len(warehouses) > 1:
            print("ERROR: multiple matches — specify exactly:", file=sys.stderr)
            for w in warehouses:
                print(f"  id={w.id} name={w.name!r} type={w.warehouse_type}", file=sys.stderr)
            return 1

        wh = warehouses[0]
        print(f"Warehouse: id={wh.id} name={wh.name!r} type={wh.warehouse_type}")
        if wh.warehouse_type != WarehouseType.FULFILLMENT.value:
            print(f"ERROR: warehouse is {wh.warehouse_type}, OutboundShipment requires FULFILLMENT", file=sys.stderr)
            return 1

        barcodes = [b for b, _ in ITEMS]
        try:
            bmap = await _resolve_barcodes_batch(db, project_id, barcodes)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        stock_q = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == wh.id,
                WarehouseStock.nomenclature_id.in_([n.id for n in bmap.values()]),
            )
        )
        stock_by_nom = {s.nomenclature_id: s for s in stock_q.scalars().all()}

        print(f"\n{'Barcode':<16} {'Qty':>6} {'Stock':>8} {'After':>8}  Status")
        print("-" * 70)
        any_insufficient = False
        total_qty = 0
        for barcode, qty in ITEMS:
            nom = bmap[barcode]
            cur = stock_by_nom.get(nom.id)
            cur_qty = cur.quantity if cur else 0
            after = cur_qty - qty
            status = "OK" if after >= 0 else f"INSUFFICIENT (need {qty}, have {cur_qty})"
            if after < 0:
                any_insufficient = True
            total_qty += qty
            print(f"{barcode:<16} {qty:>6} {cur_qty:>8} {after:>8}  {status}")

        print("-" * 70)
        print(f"{'TOTAL':<16} {total_qty:>6}")

        if any_insufficient:
            print("\nERROR: insufficient stock for one or more items — aborting", file=sys.stderr)
            return 1

        if not commit:
            print("\n[DRY-RUN] No changes. Re-run with --commit to execute.")
            return 0

        payload = {
            "comment": COMMENT,
            "items": [{"barcode": b, "quantity": q} for b, q in ITEMS],
        }
        shipment = await create_shipment(db, project_id, wh.id, payload)
        print(f"\nCreated shipment: id={shipment.id} number={shipment.number} status={shipment.status}")

        shipped = await ship_shipment(db, project_id, shipment.id)
        print(f"Shipped: id={shipped.id} number={shipped.number} status={shipped.status} date={shipped.shipped_date}")
        print(f"Total written off: {total_qty} units")
        return 0

    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    proj_g = parser.add_mutually_exclusive_group(required=True)
    proj_g.add_argument("--project-id", type=int)
    proj_g.add_argument("--project-slug", type=str)
    mode_g = parser.add_mutually_exclusive_group(required=True)
    mode_g.add_argument("--dry-run", action="store_true")
    mode_g.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    sys.exit(
        asyncio.run(
            main(
                project_id=args.project_id,
                project_slug=args.project_slug,
                commit=args.commit,
            )
        )
    )
