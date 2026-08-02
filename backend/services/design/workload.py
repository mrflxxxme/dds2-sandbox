# ruff: noqa: RUF002, RUF003
"""Экран «Загрузка команды»: один GROUP BY по DESIGN_ACTIVE_STATUSES.

ON_HOLD и терминалы в загрузку не входят (Ф0-константа). Просрочка —
due_date < сегодня; задачи без срока просроченными НЕ считаются (STATUS,
«Открытые вопросы PRD»).
"""

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.auth import User
from backend.models.design import DESIGN_ACTIVE_STATUSES, DesignTask, DesignTaskStatus
from backend.schemas.design import DesignWorkloadRow
from backend.utils.time import utcnow

_MAX_TEAM = 200


async def get_workload(db: AsyncSession, project_id: int) -> list[DesignWorkloadRow]:
    """Строки по исполнителям: активные, просроченные, в REVISION, ближайший срок."""
    today = utcnow().date()
    active_values = [s.value for s in DESIGN_ACTIVE_STATUSES]

    res = await db.execute(
        select(
            DesignTask.assignee_user_id,
            func.count().label("active_tasks"),
            func.count(case((DesignTask.due_date < today, 1))).label("overdue"),
            func.count(
                case((DesignTask.status == DesignTaskStatus.REVISION.value, 1))
            ).label("in_revision"),
            func.min(DesignTask.due_date).label("nearest_due"),
        )
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.assignee_user_id.isnot(None),
            DesignTask.status.in_(active_values),
        )
        .group_by(DesignTask.assignee_user_id)
        # ORDER BY до LIMIT: усечение детерминировано — остаются самые загруженные.
        .order_by(func.count().desc(), DesignTask.assignee_user_id)
        .limit(_MAX_TEAM)
    )
    rows = res.all()
    if not rows:
        return []

    user_ids = [row[0] for row in rows]
    users_res = await db.execute(
        select(User.id, User.first_name, User.last_name, User.username)
        .where(User.id.in_(user_ids))
        .limit(len(user_ids))
    )
    names: dict[int, str] = {}
    for row in users_res.all():
        full = f"{row.first_name or ''} {row.last_name or ''}".strip()
        names[row.id] = full or row.username

    result = [
        DesignWorkloadRow(
            user_id=row[0],
            user_name=names.get(row[0], f"user:{row[0]}"),
            active_tasks=row[1],
            overdue=row[2],
            in_revision=row[3],
            nearest_due=row[4],
        )
        for row in rows
    ]
    result.sort(key=lambda r: r.user_name.lower())
    return result
