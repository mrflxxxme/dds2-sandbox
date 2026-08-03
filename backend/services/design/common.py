# ruff: noqa: RUF002, RUF003
"""Общие хелперы пакета design: выборка задачи в скоупе проекта, роли, имена.

Отдельный модуль, чтобы crud/files/board/state не импортировали друг друга
по кругу (все зависят только от common).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.design import DesignTask

LEAD_ROLES = ("owner", "admin")


def is_lead(member_role: str) -> bool:
    """Ведущий дизайнер PRD = owner/admin проекта (CHARTER §1)."""
    return member_role in LEAD_ROLES


def actor_name(user: Any) -> str:
    """Имя для журнала событий (changed_by)."""
    return getattr(user, "username", None) or f"user:{getattr(user, 'id', '?')}"


async def get_task_row(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    *,
    for_update: bool = False,
) -> DesignTask:
    """Живая задача строго в скоупе проекта; ValueError, если нет (изоляция §6.1)."""
    stmt = select(DesignTask).where(
        DesignTask.id == task_id,
        DesignTask.project_id == project_id,
        DesignTask.is_deleted == False,  # noqa: E712
    )
    if for_update:
        # populate_existing: перечитать строку под замком, а не отдать устаревший
        # снапшот из identity map сессии.
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()
    if task is None:
        raise ValueError("Задача не найдена")
    return task
