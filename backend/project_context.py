"""
Project context dependency — extracts current project from request.
All data-access endpoints use this to scope queries by project_id.
"""

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import Project, ProjectMember, User


async def get_current_project(
    x_project_id: int | None = Header(None, alias="X-Project-Id"),
    project_id: int | None = Query(None, alias="project_id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """
    Resolve current project from X-Project-Id header or ?project_id= query.
    Verifies the user is a member of the project.
    """
    pid = x_project_id or project_id

    if pid is None:
        # Default: return first project the user is a member of
        result = await db.execute(
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                ProjectMember.user_id == user.id,
                Project.is_deleted == False,  # noqa: E712
                ProjectMember.is_deleted == False,  # noqa: E712
            )
            .order_by(Project.id)
            .limit(1)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(
                status_code=400,
                detail="У вас нет проектов. Создайте проект через POST /api/v1/projects",  # noqa: RUF001
            )
        return project

    # Check membership
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Project.id == pid,
            ProjectMember.user_id == user.id,
            Project.is_deleted == False,  # noqa: E712
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=403,
            detail="Нет доступа к проекту или проект не найден",
        )
    return project
