"""Разовая правка: привести уже ПРИНЯТУЮ приёмку к фактически принятому ФФ.

Контекст: приёмка была авто-принята по ЗАЯВЛЕННОМУ (expected_qty), хотя ФФ
(skladbot) принял меньше. Скрипт тянет фактически принятое (acceptedAmount) из
деталки связанной ФФ-заявки и корректирует складской остаток на недовоз, обновляя
actual_qty позиций. Движения логируются (INBOUND_EDIT) — правка обратима.

Dry-run по умолчанию (только показывает разницу). Применение: --commit.

  docker compose exec -e PYTHONPATH=/app backend python scripts/fix_receipt_by_ff_fact.py \
      --project default --receipt 200            # dry-run
  docker compose exec -e PYTHONPATH=/app backend python scripts/fix_receipt_by_ff_fact.py \
      --project default --receipt 200 --commit   # применить
"""

import argparse
import asyncio

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.auth import Project
from backend.models.fulfillment import FulfillmentRequest
from backend.models.warehouse import InboundReceipt, InboundStatus, MovementType
from backend.integrations.skladbot_client import SkladbotClient
from backend.services.fulfillment_service import (
    _decrypt,
    _skladbot_accepted_by_barcode,
    get_integration,
)
from backend.services.warehouse_inbound import get_receipt
from backend.services.warehouse_stock_engine import _update_stock


async def main(project_slug: str, receipt_id: int, do_commit: bool) -> None:
    async with AsyncSessionLocal() as db:
        proj = (
            await db.execute(select(Project).where(Project.slug == project_slug))
        ).scalar_one_or_none()
        if not proj:
            raise SystemExit(f"Проект '{project_slug}' не найден")
        project_id = proj.id

        receipt = await get_receipt(db, project_id, receipt_id)
        if not receipt:
            raise SystemExit(f"Приёмка {receipt_id} не найдена в проекте {project_slug}")
        if receipt.status != InboundStatus.ACCEPTED:
            raise SystemExit(f"Приёмка в статусе {receipt.status}, ожидался ACCEPTED — нечего править")

        req = (
            await db.execute(
                select(FulfillmentRequest).where(
                    FulfillmentRequest.project_id == project_id,
                    FulfillmentRequest.inbound_receipt_id == receipt_id,
                )
            )
        ).scalars().first()
        if not req:
            raise SystemExit(f"К приёмке {receipt_id} не привязана ФФ-заявка")
        if req.provider != "skladbot":
            raise SystemExit(f"Провайдер заявки — {req.provider}, скрипт только для skladbot")

        key = await get_integration(db, project_id, req.warehouse_id)
        if not key:
            raise SystemExit("Нет активного ключа skladbot на складе приёмки")
        client = SkladbotClient(_decrypt(key.encrypted_key), project_id=project_id)
        accepted = await _skladbot_accepted_by_barcode(client, req.external_id)
        if accepted is None:
            raise SystemExit("Не удалось получить факт (acceptedAmount) из skladbot — повторите позже")

        print(f"Приёмка #{receipt_id} · склад {receipt.warehouse_id} · ФФ-заявка {req.number or req.external_id}")
        print(f"{'ШК':<20} {'заявл':>7} {'сейчас':>7} {'факт ФФ':>8} {'дельта':>7}")
        total_delta = 0
        changes: list[tuple] = []
        for item in receipt.items:
            fact = accepted.get(item.barcode or "")
            mark = ""
            if fact is None:
                mark = "  (нет в факте — не трогаем)"
                fact_show = "-"
                delta = 0
            else:
                delta = item.actual_qty - fact  # >0 = перекнижено (недовоз)
                fact_show = str(fact)
                if delta != 0:
                    changes.append((item, fact, delta))
                    total_delta += delta
            print(
                f"{(item.barcode or '?'):<20} {item.expected_qty:>7} {item.actual_qty:>7} "
                f"{fact_show:>8} {delta:>7}{mark}"
            )

        print(f"\nИтого недовоз к списанию с остатка: {total_delta} шт по {len(changes)} позициям")
        if not changes:
            print("Расхождений нет — правка не нужна.")
            return
        if not do_commit:
            print("DRY-RUN. Для применения добавь --commit")
            return

        for item, fact, delta in changes:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=receipt.warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=-delta,  # уменьшаем годный остаток на недовоз
                movement_type=MovementType.INBOUND_EDIT,
                reference_type="RECEIPT",
                reference_id=receipt.id,
                comment="Корректировка приёмки по факту ФФ (недовоз)",
            )
            item.actual_qty = fact
        await db.commit()

        from backend.cache import invalidate_cache

        await invalidate_cache("reports:balance")
        await invalidate_cache("reports:assembly_link_anomalies")
        print(f"✓ Применено: остаток уменьшен на {total_delta} шт, actual_qty приведены к факту.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="default")
    ap.add_argument("--receipt", type=int, required=True)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.project, args.receipt, args.commit))
