# ruff: noqa: RUF001, RUF002
"""One-off: отменить + soft-delete ASM-6 в проекте «вяткин».

Перепутали FBO supply, отгрузят заново. SHIPPED → CANCELLED (rollback
остатков на склад, отвязка WbFboSupply, soft-delete OutboundShipment) →
soft-delete самой заявки.
"""

import asyncio

from sqlalchemy import select

from backend.database import get_db
from backend.models.assembly import AssemblyRequest
from backend.models.auth import Project
from backend.services.assembly.status import cancel_request, delete_request


async def main() -> None:
    async for db in get_db():
        pid = 15
        name = (await db.execute(select(Project.name).where(Project.id == pid))).scalar_one_or_none()
        if not name or "вяткин" not in name.lower():
            raise RuntimeError(f"project_id={pid} не вяткин (name={name!r}) — abort")

        req = (
            await db.execute(
                select(AssemblyRequest).where(
                    AssemblyRequest.project_id == pid,
                    AssemblyRequest.number == "ASM-6",
                    AssemblyRequest.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if not req:
            print("ASM-6 не найдена (или уже удалена)")
            return

        print(f"Найдено: id={req.id} project_id={pid} status={req.status}")

        if req.status == "SHIPPED":
            await cancel_request(db, pid, req.id)
            print("OK: ASM-6 отменена (rollback остатков, отвязка FBO supply)")

        await delete_request(db, pid, req.id)
        print("DONE: ASM-6 soft-deleted")


if __name__ == "__main__":
    asyncio.run(main())
