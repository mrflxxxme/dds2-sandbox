# ruff: noqa: RUF001, RUF002
"""One-off: починить разъехавшиеся цены наполнения машины V-0010 (project «Default», id=4).

Баг (исправлен в коде, sc20.3 — buildPriceOverwrites): overwrite-цен в наполнении
машины обновлял цену только у ОДНОГО источника (FOI) на баркод. В V-0010 у 8
баркодов источник из ФЗ-20 «тиамо мальдивы и дорогой» сохранил старую цену
(55.08 / 99.36), а ФЗ-76 «Дивандеки» получил правильную (43 / 77).

Скрипт приводит цену 8 баркодов к целевой (из присланного Excel) на ВСЕХ активных
FOI (ФЗ-76 — no-op, ФЗ-20 — фикс), затем делает price-resync V-0010, чтобы
синхронизировать позиции машины (cost_order_items) + пересчёт себестоимости.

Идемпотентно: уже верные цены пропускаются (no-op).

По умолчанию — DRY-RUN (только показать план). Записать: APPLY=1.
Запуск (из корня проекта на сервере):
  docker compose exec -T backend python -m scripts.fix_v0010_prices            # dry-run
  docker compose exec -e APPLY=1 -T backend python -m scripts.fix_v0010_prices # запись
"""

import asyncio
import os
from decimal import Decimal

from sqlalchemy import select

from backend.database import get_db
from backend.models.auth import Project
from backend.models.cost import CostOrderItem
from backend.models.supply_chain import FactoryOrderItem
from backend.services.supply_chain.factory_orders import bulk_update_item_prices
from backend.services.supply_chain.vehicle_delivery import apply_price_resync

PID = 4
VEHICLE = "V-0010"
APPLY = os.environ.get("APPLY") == "1"

# баркод → целевая цена (¥), из присланного Excel «Новая таблица (12).xlsx»
TARGET: dict[str, Decimal] = {
    "2049989649785": Decimal("77"),
    "2049989649815": Decimal("77"),
    "2049989649853": Decimal("77"),
    "2049989649914": Decimal("77"),
    "2049989649792": Decimal("43"),
    "2049989649822": Decimal("43"),
    "2049989649860": Decimal("43"),
    "2049989649921": Decimal("43"),
}


async def main() -> None:
    async for db in get_db():
        name = (await db.execute(select(Project.name).where(Project.id == PID))).scalar_one_or_none()
        if (name or "").lower() != "default":
            raise RuntimeError(f"project_id={PID} не Default (name={name!r}) — abort")

        foi_rows = (
            await db.execute(
                select(
                    FactoryOrderItem.id,
                    FactoryOrderItem.barcode,
                    FactoryOrderItem.factory_order_id,
                    FactoryOrderItem.price_cny,
                ).where(
                    FactoryOrderItem.project_id == PID,
                    FactoryOrderItem.barcode.in_(list(TARGET)),
                    FactoryOrderItem.is_deleted == False,  # noqa: E712
                )
            )
        ).all()

        print(f"Project: {name} (id={PID})  APPLY={APPLY}")
        print(f"Активных FOI по {len(TARGET)} баркодам: {len(foi_rows)}\n")

        updates = []
        for r in sorted(foi_rows, key=lambda x: (x.barcode, x.factory_order_id)):
            tgt = TARGET[r.barcode]
            if r.price_cny == tgt:
                print(f"  FOI {r.id} ФЗ-{r.factory_order_id} {r.barcode}: {r.price_cny} (no-op)")
            else:
                print(f"  FOI {r.id} ФЗ-{r.factory_order_id} {r.barcode}: {r.price_cny} → {tgt}")
                updates.append({"factory_order_item_id": r.id, "new_price_cny": tgt})

        print(f"\nFOI к изменению: {len(updates)}")

        if not APPLY:
            ci_rows = (
                await db.execute(
                    select(CostOrderItem.id, CostOrderItem.barcode, CostOrderItem.price_cny).where(
                        CostOrderItem.project_id == PID,
                        CostOrderItem.order_no == VEHICLE,
                        CostOrderItem.barcode.in_(list(TARGET)),
                        CostOrderItem.is_deleted == False,  # noqa: E712
                    )
                )
            ).all()
            print(f"\nПозиции машины {VEHICLE} (станут целевыми после resync):")
            for c in sorted(ci_rows, key=lambda x: x.barcode):
                tgt = TARGET[c.barcode]
                mark = "(no-op)" if c.price_cny == tgt else f"→ {tgt}"
                print(f"  cost {c.id} {c.barcode}: {c.price_cny} {mark}")
            print("\nDRY-RUN — ничего не записано. Запись: APPLY=1 (см. docstring).")
            return

        if updates:
            res = await bulk_update_item_prices(db, PID, updates, user_name="fix_v0010_prices.py")
            print(f"\nЦены FOI обновлены: {res}")

        resync = await apply_price_resync(db, PID, VEHICLE)
        print(f"Resync {VEHICLE}: {resync}")
        print("\nГОТОВО")


if __name__ == "__main__":
    asyncio.run(main())
