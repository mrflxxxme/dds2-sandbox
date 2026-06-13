# ruff: noqa: RUF001, RUF002, RUF003
"""One-off: удалить массовую загрузку себеса от 08.03.2026 в проекте «Default» (id=4).

Батч: 265 строк wb_cost_override, updated_at 2026-03-08 15:19:23–24 (одна
загрузка через «Массовая себестоимость»). Часть значений (чехлы Уютопия)
получила чужой себес панелей (263/343 ₽) и из-за override-приоритета в
funnel/sync.py перебивает свежий себес из заказов → дутая маржа в воронке.

Догрузки 11.03–18.05 НЕ трогаем.

После удаления — перештамповка wb_funnel_daily.cost_price по затронутым nm_id:
средневзвешенный себес из заказов (load_avg_costs, как в БДР), а где заказов
нет — NULL (артикул всплывёт в «Проверке себеса»). В конце —
invalidate_project_reports(pid), как в bulk-эндпоинте.

Guard: abort, если project_id=4 не «Default». Идемпотентно: повторный запуск
найдёт 0 строк батча.

DRY_RUN=1 — только показать план, без записи.
"""

import asyncio
import os
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select

from backend.database import get_db
from backend.models import WbCostOverride, WbFunnelDaily
from backend.models.auth import Project
from backend.services.bdr_loaders import load_avg_costs

PID = 4
BATCH_FROM = datetime(2026, 3, 8)
BATCH_TO = datetime(2026, 3, 9)
DRY_RUN = os.environ.get("DRY_RUN") == "1"


async def main() -> None:
    async for db in get_db():
        name = (await db.execute(select(Project.name).where(Project.id == PID))).scalar_one_or_none()
        if (name or "").lower() != "default":
            raise RuntimeError(f"project_id={PID} не Default (name={name!r}) — abort")

        rows = (
            await db.execute(
                select(WbCostOverride.nm_id, WbCostOverride.cost_price)
                .where(
                    WbCostOverride.project_id == PID,
                    WbCostOverride.updated_at >= BATCH_FROM,
                    WbCostOverride.updated_at < BATCH_TO,
                )
                .order_by(WbCostOverride.nm_id)
            )
        ).all()
        print(f"Project: {name} (id={PID})  DRY_RUN={DRY_RUN}")
        print(f"Строк батча 08.03: {len(rows)}")
        if not rows:
            print("Батч уже удалён — выход.")
            return

        nm_ids = [r.nm_id for r in rows]

        # vendor_code по nm_id из воронки (для маппинга на себес из заказов)
        vc_rows = (
            await db.execute(
                select(WbFunnelDaily.nm_id, func.max(WbFunnelDaily.vendor_code).label("vc"))
                .where(WbFunnelDaily.project_id == PID, WbFunnelDaily.nm_id.in_(nm_ids))
                .group_by(WbFunnelDaily.nm_id)
            )
        ).all()
        vc_map = {r.nm_id: (r.vc or "") for r in vc_rows}

        avg_costs = await load_avg_costs(db, PID)  # article_seller_lower → cost

        with_fallback = 0
        without_fallback = 0
        for r in rows:
            vc = vc_map.get(r.nm_id, "")
            avg = avg_costs.get(vc.lower()) if vc else None
            if avg and avg > 0:
                with_fallback += 1
                new_val = f"{avg:.2f} (по заказам)"
            else:
                without_fallback += 1
                new_val = "NULL (нет заказов — в «Проверку себеса»)"
            print(f"  nm_id={r.nm_id} {vc or '(не в воронке)'}: {r.cost_price} → {new_val}")

        print(f"\nИтого: {with_fallback} получат себес из заказов, {without_fallback} останутся без себеса")

        if DRY_RUN:
            print("DRY-RUN — ничего не записано")
            return

        # 1. Удаление батча (WbCostOverride без SoftDeleteMixin — hard delete)
        res = await db.execute(
            delete(WbCostOverride).where(
                WbCostOverride.project_id == PID,
                WbCostOverride.updated_at >= BATCH_FROM,
                WbCostOverride.updated_at < BATCH_TO,
            )
        )
        print(f"Удалено override-строк: {res.rowcount}")

        # 2. Перештамповка wb_funnel_daily.cost_price по затронутым nm_id
        restamped = 0
        for nm_id in nm_ids:
            vc = vc_map.get(nm_id, "")
            avg = avg_costs.get(vc.lower()) if vc else None
            new_cost = Decimal(str(round(avg, 2))) if avg and avg > 0 else None
            upd = await db.execute(
                WbFunnelDaily.__table__.update()
                .where(WbFunnelDaily.project_id == PID, WbFunnelDaily.nm_id == nm_id)
                .values(cost_price=new_cost)
            )
            restamped += upd.rowcount
        print(f"Перештамповано строк wb_funnel_daily: {restamped}")

        await db.commit()

        from backend.cache import invalidate_project_reports

        await invalidate_project_reports(PID)
        print("Кэш отчётов инвалидирован")

        # Сверка
        left = (
            await db.execute(
                select(func.count())
                .select_from(WbCostOverride)
                .where(
                    WbCostOverride.project_id == PID,
                    WbCostOverride.updated_at >= BATCH_FROM,
                    WbCostOverride.updated_at < BATCH_TO,
                )
            )
        ).scalar_one()
        sample = (
            await db.execute(
                select(WbFunnelDaily.date, WbFunnelDaily.cost_price)
                .where(WbFunnelDaily.project_id == PID, WbFunnelDaily.nm_id == 758442320)
                .order_by(WbFunnelDaily.date.desc())
                .limit(3)
            )
        ).all()
        print(
            f"Сверка: строк батча осталось {left}; SERPANTIN_бежевый cost_price: {[(str(s.date), str(s.cost_price)) for s in sample]}"
        )
        print("ГОТОВО")


if __name__ == "__main__":
    asyncio.run(main())
