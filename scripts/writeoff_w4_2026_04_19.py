"""Quick writeoff: warehouse_id=4 (project default). 30 barcodes, 490 units."""

import asyncio

from sqlalchemy import select

from backend.database import get_db
from backend.services.warehouse_outbound import create_shipment, ship_shipment

ITEMS = [
    ("2043788796855", 80),
    ("2047213093069", 44),
    ("2047114210466", 38),
    ("2047114210312", 34),
    ("2043788816553", 30),
    ("2047111368443", 30),
    ("2044388647257", 18),
    ("2047213311729", 18),
    ("2045933030968", 16),
    ("2045932869446", 16),
    ("2047111368313", 14),
    ("2044388475430", 14),
    ("2047114210343", 14),
    ("2045933030944", 14),
    ("2045932869361", 13),
    ("2044388560068", 13),
    ("2044388647196", 12),
    ("2047114210473", 12),
    ("2044388623824", 11),
    ("2044388564998", 10),
    ("2044388647219", 8),
    ("2044388564813", 6),
    ("2043788824176", 5),
    ("2047111538457", 4),
    ("2043788808268", 4),
    ("2044388632109", 4),
    ("2047111368634", 3),
    ("2045932869521", 2),
    ("2045932869163", 2),
    ("2048219456759", 1),
]


async def main():
    async for db in get_db():
        from backend.models.auth import Project

        pid = (await db.execute(select(Project.id).where(Project.slug == "default"))).scalar_one()
        payload = {
            "comment": "Списание остатков 2026-04-19",
            "items": [{"barcode": b, "quantity": q} for b, q in ITEMS],
        }
        s = await create_shipment(db, pid, 4, payload)
        s = await ship_shipment(db, pid, s.id)
        print(f"OK: shipment #{s.number} ({sum(q for _, q in ITEMS)} units)")


if __name__ == "__main__":
    asyncio.run(main())
