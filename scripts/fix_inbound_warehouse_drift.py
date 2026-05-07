# ruff: noqa: RUF001, RUF002, E712
"""Recovery: синхронизировать `inbound_receipts.warehouse_id` с
`cost_orders.target_warehouse_id` для машин, у которых склад был сменён
после создания auto-приёмки (DISPATCHED).

Чинит инцидент 2026-05-07 (V-0001 → IN-76 на старом складе «Транзит»,
не было видно на странице нового склада «натали»).

Безопасно: правит только приёмки в EXPECTED. ACCEPTED — выводит warning,
ничего не трогает (там уже есть stock movements).
"""

import asyncio

from sqlalchemy import select

from backend.database import get_db
from backend.models.cost import CostOrder
from backend.models.warehouse import InboundReceipt, InboundStatus


async def main() -> None:
    async for db in get_db():
        rows = (
            await db.execute(
                select(CostOrder, InboundReceipt)
                .join(InboundReceipt, InboundReceipt.id == CostOrder.inbound_receipt_id)
                .where(
                    CostOrder.is_deleted == False,
                    CostOrder.target_warehouse_id.is_not(None),
                    CostOrder.inbound_receipt_id.is_not(None),
                    CostOrder.target_warehouse_id != InboundReceipt.warehouse_id,
                    InboundReceipt.is_deleted == False,
                )
            )
        ).all()

        if not rows:
            print("OK: рассинхронизированных приёмок не найдено")
            return

        fixed = 0
        skipped = 0
        for vehicle, receipt in rows:
            if receipt.status == InboundStatus.ACCEPTED:
                print(
                    f"WARN: project={vehicle.project_id} {vehicle.order_no} → "
                    f"{receipt.number} ACCEPTED, warehouse_id={receipt.warehouse_id} ≠ "
                    f"vehicle.target={vehicle.target_warehouse_id} — пропуск (есть движения)"
                )
                skipped += 1
                continue
            old_wh = receipt.warehouse_id
            receipt.warehouse_id = vehicle.target_warehouse_id
            print(
                f"FIX: project={vehicle.project_id} {vehicle.order_no} → "
                f"{receipt.number} ({receipt.status}): warehouse_id {old_wh} → "
                f"{vehicle.target_warehouse_id}"
            )
            fixed += 1

        await db.commit()
        print(f"DONE: fixed={fixed}, skipped_accepted={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
