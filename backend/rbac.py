"""
RBAC (Role-Based Access Control) — dependency module for DDS2.

Roles: owner > admin > editor > viewer
Pages: granular page-level access for editor/viewer roles.
"""

import json
import logging

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models.auth import Project, ProjectMember, User
from backend.project_context import get_current_project

logger = logging.getLogger(__name__)

ROLE_HIERARCHY: dict[str, int] = {
    "owner": 4,
    "admin": 3,
    "editor": 2,
    "viewer": 1,
}

ALL_PAGES: list[str] = [
    "dashboard",
    "import",
    "txn",
    "inbox",
    "reports",
    "cost",
    "refs",
    "assembly",
    "logistics",
    "fbo",
    "stocks",
    "stock-analytics",
    "planning",
    "container",
    "funnel",
    "trends",
    "opiu",
    "plan-fact",
    "geography",
    "monitoring",
    "project-settings",
    "team",
]

SECTION_PAGES: dict[str, list[str]] = {
    "finance": ["import", "txn", "inbox", "reports", "cost", "refs"],
    "warehouse": ["assembly", "logistics", "fbo", "stocks", "stock-analytics"],
    "orders": ["planning", "container"],
    "sales": ["funnel", "trends", "opiu", "plan-fact", "geography"],
    "settings": ["monitoring", "project-settings", "team"],
}


def parse_pages(pages_json: str | None) -> list[str]:
    """Parse pages JSON string to list, returning empty list on None/invalid."""
    if not pages_json:
        return []
    try:
        result = json.loads(pages_json)
        if isinstance(result, list):
            return result
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def get_effective_pages(role: str, pages_json: str | None) -> list[str]:
    """Return effective page list based on role. Owner/admin get all pages."""
    if role in ("owner", "admin"):
        return list(ALL_PAGES)
    return parse_pages(pages_json)


def require_role(min_role: str = "viewer", page: str | None = None):
    """FastAPI dependency factory — checks role hierarchy + page access."""

    async def dependency(
        project: Project = Depends(get_current_project),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        member = (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()

        if not member:
            raise HTTPException(403, "Нет доступа к проекту")

        role_level = ROLE_HIERARCHY.get(member.role, 0)
        required_level = ROLE_HIERARCHY.get(min_role, 0)
        if role_level < required_level:
            raise HTTPException(403, f"Требуется роль {min_role} или выше")

        if page and member.role in ("editor", "viewer"):
            member_pages = parse_pages(member.pages)
            if page not in member_pages:
                raise HTTPException(403, f"Нет доступа к странице: {page}")

        return user

    return dependency
