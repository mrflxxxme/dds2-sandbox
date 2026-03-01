"""
Project-level data isolation middleware.

Provides FastAPI dependencies for:
- Extracting project_id from X-Project-Id header
- Verifying user has membership in the project
- Filtering queries by project_id

Usage in routers:
    @router.get("/items")
    async def get_items(
        project_id: int = Depends(get_project_id),
        db: AsyncSession = Depends(get_db),
    ):
        result = await db.execute(
            select(Item).where(Item.project_id == project_id)
        )
        ...
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import User, ProjectMember

logger = logging.getLogger("dds.projects")


async def get_project_id(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> int:
    """
    Extract and validate project_id from X-Project-Id header.
    Verifies the current user is a member of the project.
    Raises 400 if header missing, 403 if not a member.
    """
    project_id_str = request.headers.get("X-Project-Id")
    if not project_id_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Project-Id header is required",
        )

    try:
        project_id = int(project_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID",
        )

    # Verify membership
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        logger.warning(
            f"User {current_user.username} (id={current_user.id}) "
            f"attempted to access project {project_id} without membership"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="У вас нет доступа к этому проекту",
        )

    return project_id


async def get_optional_project_id(
    request: Request,
) -> Optional[int]:
    """
    Extract project_id from header without membership check.
    Returns None if header is missing. For backward compatibility.
    """
    project_id_str = request.headers.get("X-Project-Id")
    if not project_id_str:
        return None
    try:
        return int(project_id_str)
    except ValueError:
        return None
