# ruff: noqa: RUF001, RUF002, RUF003 — смешанная кириллица/латиница намеренно (русские комментарии)
"""One-off: чинит уже подтверждённые брак-приёмки, у которых сток ошибочно ушёл в годный.

Баг (исправлен в accept_receipt): при Accept приёмки с is_defect=True (возвраты WB
с ПВЗ) сток зачислялся в WarehouseStock.quantity через INBOUND вместо
defect_quantity через DEFECT_RECEIVE. Итог — возвраты-брак лежали в годном остатке
и не были видны в «Браке». Код-фикс правит только будущие подтверждения; этот
скрипт переносит уже зачисленный годный сток → брак для прошлых приёмок.

Что делает (для каждой ACCEPTED + is_defect + не удалённой приёмки):
  - already-fixed: если уже есть движение reference_type='DEFECT_FIX' на эту
    приёмку → пропуск (идемпотентность).
  - no-inbound: если у приёмки нет INBOUND-движения (значит принята уже
    исправленным кодом — DEFECT_RECEIVE) → пропуск, чинить нечего. Этим же
    правилом безопасно отсекаются брак-приёмки из других источников, где брак
    зачисляется сразу через DEFECT_RECEIVE без INBOUND: `warehouse_defect.py::
    receive_defect_bulk` и FBW-недоприёмка `fbo_supply/returns.py::
    _create_return_receipt`. ВАЖНО: корректность скрипта опирается на инвариант
    «is_defect=True ⟹ у приёмки нет INBOUND-движений». Сейчас он держится тем,
    что _create_return_receipt ставит is_defect=True только когда ВСЕ строки
    DEFECT (mixed-приёмка → is_defect=False). Если этот инвариант сломать
    (напр. is_defect=True на mixed GOODS/DEFECT приёмке) — скрипт перенесёт в
    брак и легитимный годный сток. Не ломать без пересмотра детекции здесь.
  - ambiguous: если по любой позиции текущий годный остаток < actual_qty (часть
    ошибочно зачисленного уже продана/отгружена/перемещена как годная) → НЕ трогаем
    всю приёмку, выводим в отчёт для ручного разбора.
  - fixable: переносим годный→брак одним движением на позицию
    (_update_stock: delta=-qty, defect_delta=+qty, DEFECT_RECEIVE,
    reference_type='DEFECT_FIX'). All-or-nothing на приёмку: маркер DEFECT_FIX
    появляется только если перенесены ВСЕ позиции.

Append-only: исходное ошибочное INBOUND-движение НЕ удаляется (это audit-лог) —
корректировка добавляется отдельной строкой.

По умолчанию DRY-RUN (только план). Применить:  APPLY=1 python scripts/fix_return_defect_misbooking.py
"""

import asyncio
import os

from sqlalchemy import select

from backend.database import get_db
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    StockMovement,
    WarehouseStock,
)
from backend.services.warehouse_stock_engine import _update_stock

APPLY = os.environ.get("APPLY") == "1"


async def _good_qty(db, project_id: int, warehouse_id: int, nomenclature_id: int) -> int:
    """Текущий годный остаток (WarehouseStock.quantity) для пары склад+номенклатура; 0 если строки нет."""
    q = await db.execute(
        select(WarehouseStock.quantity).where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.nomenclature_id == nomenclature_id,
        )
    )
    return q.scalar() or 0


async def _has_movement(db, project_id: int, receipt_id: int, movement_type: MovementType) -> bool:
    q = await db.execute(
        select(StockMovement.id)
        .where(
            StockMovement.project_id == project_id,
            StockMovement.reference_type == "RECEIPT",
            StockMovement.reference_id == receipt_id,
            StockMovement.movement_type == movement_type,
        )
        .limit(1)
    )
    return q.first() is not None


async def _already_fixed(db, project_id: int, receipt_id: int) -> bool:
    q = await db.execute(
        select(StockMovement.id)
        .where(
            StockMovement.project_id == project_id,
            StockMovement.reference_type == "DEFECT_FIX",
            StockMovement.reference_id == receipt_id,
        )
        .limit(1)
    )
    return q.first() is not None


async def main() -> None:
    async for db in get_db():
        receipts = (
            (
                await db.execute(
                    select(InboundReceipt)
                    .where(
                        InboundReceipt.is_defect.is_(True),
                        InboundReceipt.status == InboundStatus.ACCEPTED,
                        InboundReceipt.is_deleted == False,  # noqa: E712
                    )
                    .order_by(InboundReceipt.project_id, InboundReceipt.id)
                )
            )
            .scalars()
            .all()
        )

        print(f"APPLY={APPLY}  кандидатов (ACCEPTED is_defect): {len(receipts)}")

        fixed: list[str] = []
        ambiguous: list[str] = []
        skipped_no_inbound = 0
        skipped_already = 0

        for r in receipts:
            tag = f"p{r.project_id}/{r.number}(#{r.id})"

            if await _already_fixed(db, r.project_id, r.id):
                skipped_already += 1
                continue
            if not await _has_movement(db, r.project_id, r.id, MovementType.INBOUND):
                # Принята уже исправленным кодом (DEFECT_RECEIVE) — чинить нечего.
                skipped_no_inbound += 1
                continue

            items = (
                (
                    await db.execute(
                        select(InboundReceiptItem).where(
                            InboundReceiptItem.receipt_id == r.id,
                            InboundReceiptItem.actual_qty > 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not items:
                skipped_no_inbound += 1
                continue

            # Проверяем достаточность годного остатка по КАЖДОЙ позиции (all-or-nothing).
            plan: list[tuple[InboundReceiptItem, int]] = []
            shortfall = False
            for it in items:
                good = await _good_qty(db, r.project_id, r.warehouse_id, it.nomenclature_id)
                # Учитываем уже запланированные в этой же приёмке переносы на тот же nom.
                planned_same_nom = sum(q for pit, q in plan if pit.nomenclature_id == it.nomenclature_id)
                if good - planned_same_nom < it.actual_qty:
                    shortfall = True
                    break
                plan.append((it, it.actual_qty))

            if shortfall:
                ambiguous.append(tag)
                continue

            print(f"  fix {tag}: " + ", ".join(f"{it.barcode}×{q} годный→брак" for it, q in plan))
            if APPLY:
                for it, q in plan:
                    await _update_stock(
                        db,
                        project_id=r.project_id,
                        warehouse_id=r.warehouse_id,
                        nomenclature_id=it.nomenclature_id,
                        barcode=it.barcode,
                        delta=-q,
                        defect_delta=q,
                        movement_type=MovementType.DEFECT_RECEIVE,
                        reference_type="DEFECT_FIX",
                        reference_id=r.id,
                        comment=f"Корректировка возврат-брак {r.number}: годный→брак",
                    )
                await db.commit()
            fixed.append(tag)

        print("\n── Итог ──")
        print(f"  {'исправлено' if APPLY else 'будет исправлено'}: {len(fixed)}")
        print(f"  уже исправлено ранее: {skipped_already}")
        print(f"  без INBOUND (принято новым кодом): {skipped_no_inbound}")
        print(f"  ambiguous (годного < нужного, РУЧНОЙ разбор): {len(ambiguous)}")
        for tag in ambiguous:
            print(f"      ⚠ {tag}")
        if not APPLY and fixed:
            print("\n  DRY-RUN. Применить:  APPLY=1 python scripts/fix_return_defect_misbooking.py")
        break


if __name__ == "__main__":
    asyncio.run(main())
