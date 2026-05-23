# ruff: noqa: RUF001, RUF002
"""One-off: cancel + soft-delete ASM-252..298 в проекте «Default» (id=4).

Все 47 заявок в статусе IN_PROGRESS — остатки не списаны (списание только при
SHIP). Для каждой: IN_PROGRESS → CANCELLED (запись в status_history, без движения
остатков) → soft-delete. Идемпотентно: уже удалённые/отсутствующие пропускаются.

Guard: abort, если project_id=4 не «Default» или встретился неожиданный статус
(SHIPPED/READY/VEHICLE_ASSIGNED — там есть отгрузка/остатки, разбирать вручную).

DRY_RUN=1 — только показать план, без записи.
"""

import asyncio
import os

from sqlalchemy import select

from backend.database import get_db
from backend.models.assembly import AssemblyRequest
from backend.models.auth import Project
from backend.services.assembly.status import cancel_request, delete_request

PID = 4
NUMBERS = [f"ASM-{n}" for n in range(252, 299)]  # 252..298 включительно (47 шт)
DRY_RUN = os.environ.get("DRY_RUN") == "1"
SAFE_STATUSES = {"IN_PROGRESS", "PENDING", "CANCELLED"}  # без stock/отгрузки


async def main() -> None:
    async for db in get_db():
        name = (await db.execute(select(Project.name).where(Project.id == PID))).scalar_one_or_none()
        if (name or "").lower() != "default":
            raise RuntimeError(f"project_id={PID} не Default (name={name!r}) — abort")

        rows = (
            await db.execute(
                select(
                    AssemblyRequest.number,
                    AssemblyRequest.id,
                    AssemblyRequest.status,
                ).where(
                    AssemblyRequest.project_id == PID,
                    AssemblyRequest.number.in_(NUMBERS),
                    AssemblyRequest.is_deleted == False,  # noqa: E712
                )
            )
        ).all()
        by_num = {r.number: (r.id, r.status) for r in rows}

        print(f"Project: {name} (id={PID})  DRY_RUN={DRY_RUN}")
        print(f"Запрошено: {len(NUMBERS)}, найдено активных: {len(rows)}")

        bad = [(n, by_num[n][1]) for n in by_num if by_num[n][1] not in SAFE_STATUSES]
        if bad:
            raise RuntimeError(f"Неожиданные статусы (отгрузка/остатки): {bad} — abort, разбери вручную")

        done = 0
        for num in NUMBERS:
            hit = by_num.get(num)
            if hit is None:
                print(f"  {num}: нет активной (пропуск)")
                continue
            rid, status = hit
            if DRY_RUN:
                print(f"  {num}: id={rid} status={status} → CANCEL+DELETE (dry-run)")
                continue
            if status != "CANCELLED":
                await cancel_request(db, PID, rid)
            await delete_request(db, PID, rid)
            done += 1
            print(f"  {num}: id={rid} → cancelled+deleted")

        print(f"\n{'DRY-RUN — ничего не записано' if DRY_RUN else f'ГОТОВО: удалено {done}'}")


if __name__ == "__main__":
    asyncio.run(main())
