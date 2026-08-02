# ruff: noqa: RUF001, RUF002, RUF003
"""
Backfill: забор (``OutboundShipment``) для уже уехавших переездов.

Забор переезда — НОСИТЕЛЬ ДЕНЕГ: через него перевозка попадает в «Оплаты», в
сверку счетов ФФ и в отчёт «Логистика переездов». Исторически он создавался
только в момент отправки и только при заполненной логистике, поэтому у переездов
с дописанной ПОЗЖЕ машиной (или у тех, где отправку делал старый код) забора нет
— и перевозка не видна нигде.

Скрипт добирает ровно этот пробел: для переездов в SHIPPED / DELIVERED /
RETURNED / CLOSED, у которых логистика ЕСТЬ, а живого забора НЕТ, зовёт тот же
``_create_transfer_pickup``, что и отправка. Идемпотентен: повторный прогон
находит забор и обновляет снимок, второй документ на ту же перевозку не рождается
(партиальный уникальный ``uq_outbound_shipments_stock_transfer``).

🔴 Переезды БЕЗ логистики скрипт не чинит и чинить не может — взять перевозчика
и сумму неоткуда. Он их честно считает и печатает: оживить такие можно только
руками через ``POST /warehouse/transfers/{id}/logistics`` (или пачкой через
``/transfers/logistics-bulk``). На 01.08.2026 это ВСЕ 28 переездов проекта.

Usage:
    python -m scripts.backfill_transfer_pickups --project-id=4            # dry-run
    python -m scripts.backfill_transfer_pickups --project-id=4 --commit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models.warehouse import OutboundShipment, StockTransfer, TransferStatus  # noqa: E402
from backend.services.warehouse_outbound import (  # noqa: E402
    _create_transfer_pickup,
    _transfer_has_logistics,
)

logger = logging.getLogger("backfill_transfer_pickups")

#: Уехавшие: перевозка состоялась, значит у неё есть цена и её кто-то вёз.
#: CANCELLED сюда не входит — отменённый переезд никто не вёз (зеркало
#: `_TRANSFER_LOGISTICS_RETRO_STATUSES`).
_SHIPPED_STATUSES = [
    TransferStatus.SHIPPED.value,
    TransferStatus.DELIVERED.value,
    TransferStatus.RETURNED.value,
    TransferStatus.CLOSED.value,
]


async def _run(project_id: int, commit: bool) -> None:
    async with AsyncSessionLocal() as db:
        transfers = list(
            (
                await db.execute(
                    select(StockTransfer)
                    # Состав нужен целиком: забор копирует позиции переезда.
                    # selectinload, а не ленивый доступ — в async-сессии он
                    # взорвался бы MissingGreenlet на первом же `.items`.
                    .options(selectinload(StockTransfer.items))
                    .where(
                        StockTransfer.project_id == project_id,
                        StockTransfer.is_deleted == False,  # noqa: E712
                        StockTransfer.status.in_(_SHIPPED_STATUSES),
                    )
                    .order_by(StockTransfer.id)
                )
            )
            .scalars()
            .all()
        )

        # Живые заборы одним запросом: поштучный SELECT внутри цикла дал бы N+1
        # на разовом скрипте, который и так читает всю таблицу переездов.
        with_pickup = {
            int(tid)
            for (tid,) in (
                await db.execute(
                    select(OutboundShipment.stock_transfer_id).where(
                        OutboundShipment.project_id == project_id,
                        OutboundShipment.is_deleted == False,  # noqa: E712
                        OutboundShipment.stock_transfer_id.in_([t.id for t in transfers]),
                    )
                )
            ).all()
            if tid is not None
        }

        created = 0
        skipped_have = 0
        no_logistics: list[str] = []
        for transfer in transfers:
            if transfer.id in with_pickup:
                skipped_have += 1
                continue
            if not _transfer_has_logistics(transfer):
                no_logistics.append(transfer.number)
                continue
            shipment = await _create_transfer_pickup(db, project_id, transfer)
            if shipment is not None:
                created += 1
                logger.info("  %s → забор %s", transfer.number, shipment.number)

        logger.info(
            "project=%s: переездов уехавших %s, забор уже был у %s, создано %s, "
            "без логистики %s",
            project_id,
            len(transfers),
            skipped_have,
            created,
            len(no_logistics),
        )
        if no_logistics:
            # Печатаем поимённо: это рабочий список для ретро-ввода логистики,
            # а не статистика. Молчаливое «пропущено N» заставило бы искать их
            # заново руками.
            logger.info(
                "  БЕЗ ЛОГИСТИКИ (скрипт бессилен, нужен POST /transfers/{id}/logistics): %s",
                ", ".join(no_logistics),
            )

        if commit:
            await db.commit()
            logger.info("COMMITTED: %s заборов", created)
        else:
            await db.rollback()
            logger.info("DRY-RUN (откачено) — передайте --commit, чтобы применить")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Создать заборы уехавшим переездам с логистикой")
    ap.add_argument("--project-id", type=int, required=True)
    ap.add_argument("--commit", action="store_true", help="применить изменения (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(_run(args.project_id, args.commit))


if __name__ == "__main__":
    main()
