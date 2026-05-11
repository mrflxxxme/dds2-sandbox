# ruff: noqa: RUF001, RUF002, E712
"""One-off: soft-delete orphan FactoryOrderItem — позиций, у которых
parent FactoryOrder уже soft-deleted, а сама позиция активна.

Чинит drift который раздувает `available` по баркоду в
`add_items_to_vehicle` и в подсчётах UI «Выберите позиции из
фабричных заказов» (фронт пользуется только активными FOI, бэк
по `_adjust_assigned_qty` смотрит на конкретный FOI.id и не учитывает
parent — если фронт случайно отправит ID orphan'а, упадёт ValueError).

Прецедент 2026-05-08: project_id=4 «default», FOI id=429
(barcode=2043788817697, qty=286) висел при удалённом FactoryOrder id=19.

Безопасность:
- Soft-delete'им только FOI без активных CostOrderItem ссылок.
- Если FOI используется в активной машине — WARN, не трогаем
  (значит надо восстанавливать parent FactoryOrder, а не скрывать FOI).
"""

import asyncio

from sqlalchemy import and_, exists, select

from backend.database import get_db
from backend.models.cost import CostOrderItem
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem


async def main() -> None:
    async for db in get_db():
        rows = (
            await db.execute(
                select(FactoryOrderItem, FactoryOrder.order_number)
                .join(FactoryOrder, FactoryOrder.id == FactoryOrderItem.factory_order_id)
                .where(
                    FactoryOrderItem.is_deleted == False,
                    FactoryOrder.is_deleted == True,
                )
            )
        ).all()

        if not rows:
            print("OK: orphan FactoryOrderItem не найдено")
            return

        deleted = 0
        skipped = 0
        for foi, fo_number in rows:
            has_active_ci = (
                await db.execute(
                    select(
                        exists().where(
                            and_(
                                CostOrderItem.factory_order_item_id == foi.id,
                                CostOrderItem.is_deleted == False,
                            )
                        )
                    )
                )
            ).scalar()
            if has_active_ci:
                print(
                    f"WARN: project={foi.project_id} foi.id={foi.id} "
                    f"barcode={foi.barcode} fo='{fo_number}' (deleted) — "
                    f"есть active cost_order_items, пропуск (восстанавливать parent FO)"
                )
                skipped += 1
                continue
            print(
                f"FIX: project={foi.project_id} foi.id={foi.id} "
                f"barcode={foi.barcode} qty={foi.qty} assigned={foi.assigned_qty} "
                f"fo='{fo_number}' (deleted)"
            )
            foi.soft_delete()
            deleted += 1

        await db.commit()
        print(f"DONE: deleted={deleted}, skipped_with_active_ci={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
