"""
Router: /projects — CRUD for projects + team management.
"""

import uuid
from datetime import datetime, timezone

from backend.utils.time import utcnow
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import User, Project, ProjectMember, ProjectInvite

router = APIRouter(prefix="/projects", tags=["Projects"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str

class ProjectResponse(BaseModel):
    id: int
    name: str
    slug: str
    owner_id: int
    created_at: datetime
    members_count: int = 0

class ProjectMemberResponse(BaseModel):
    id: int
    user_id: int
    username: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    joined_at: datetime

class InviteResponse(BaseModel):
    id: int
    project_id: int
    email: Optional[str] = None
    invite_token: str
    status: str
    created_at: datetime
    accepted_at: Optional[datetime] = None


# ─── Project CRUD ─────────────────────────────────────────────────────────────

def _make_slug(name: str) -> str:
    """Generate URL-safe slug from project name."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9а-яё\s-]', '', slug)
    slug = re.sub(r'[\s]+', '-', slug)
    slug = slug[:80]
    # Add short UUID to ensure uniqueness
    return f"{slug}-{uuid.uuid4().hex[:6]}"


@router.post("", response_model=ProjectResponse)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new project. Creator becomes the owner and first member."""
    slug = _make_slug(body.name)

    project = Project(
        name=body.name,
        slug=slug,
        owner_id=user.id,
    )
    db.add(project)
    await db.flush()

    # Owner is also a member
    member = ProjectMember(project_id=project.id, user_id=user.id)
    db.add(member)
    await db.commit()
    await db.refresh(project)
    return {**project.__dict__, "members_count": 1}


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all projects the user is a member of."""
    result = await db.execute(
        select(
            Project,
            func.count(ProjectMember.id).label("members_count"),
        )
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Project.id.in_(
                select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
            )
        )
        .group_by(Project.id)
        .order_by(Project.created_at.desc())
    )
    rows = result.all()
    return [
        {**p.__dict__, "members_count": cnt}
        for p, cnt in rows
    ]


@router.get("/{slug}", response_model=ProjectResponse)
async def get_project(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get project by slug."""
    result = await db.execute(
        select(Project, func.count(ProjectMember.id).label("cnt"))
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.slug == slug)
        .group_by(Project.id)
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Проект не найден")
    project, cnt = row
    # Check membership
    mem = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    if not mem.scalar_one_or_none():
        raise HTTPException(403, "Нет доступа к проекту")
    return {**project.__dict__, "members_count": cnt}


@router.delete("/{slug}")
async def delete_project(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete project. Only owner can delete."""
    result = await db.execute(select(Project).where(Project.slug == slug))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Проект не найден")
    if project.owner_id != user.id:
        raise HTTPException(403, "Только владелец может удалить проект")
    await db.delete(project)
    await db.commit()
    return {"deleted": True, "slug": slug}


# ─── Team / Members ──────────────────────────────────────────────────────────

@router.get("/{slug}/members", response_model=list[ProjectMemberResponse])
async def list_members(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all members of a project."""
    project = await _get_project_with_access(slug, user, db)
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id)
        .order_by(ProjectMember.joined_at)
    )
    return [
        {
            "id": m.id,
            "user_id": u.id,
            "username": u.username,
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "joined_at": m.joined_at,
        }
        for m, u in result.all()
    ]


@router.delete("/{slug}/members/{user_id}")
async def remove_member(
    slug: str,
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from a project. Owner only."""
    project = await _get_project_with_access(slug, user, db)
    if project.owner_id != user.id:
        raise HTTPException(403, "Только владелец может удалять участников")
    if user_id == user.id:
        raise HTTPException(400, "Нельзя удалить себя из проекта")
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Участник не найден")
    await db.delete(member)
    await db.commit()
    return {"removed": True, "user_id": user_id}


# ─── Invitations ──────────────────────────────────────────────────────────────

@router.post("/{slug}/invite", response_model=InviteResponse)
async def invite_by_email(
    slug: str,
    email: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invite a user to the project by email."""
    project = await _get_project_with_access(slug, user, db)
    token = uuid.uuid4().hex
    invite = ProjectInvite(
        project_id=project.id,
        email=email,
        invite_token=token,
        status="pending",
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


@router.get("/{slug}/invite-link")
async def get_invite_link(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new unique invite link for the project."""
    project = await _get_project_with_access(slug, user, db)

    # Always create a new invite link
    token = uuid.uuid4().hex
    invite = ProjectInvite(
        project_id=project.id,
        email=None,
        invite_token=token,
        status="pending",
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    return {
        "invite_token": invite.invite_token,
        "link": f"/invite/{invite.invite_token}",
    }


@router.get("/{slug}/invites", response_model=list[InviteResponse])
async def list_invites(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all invitations for a project (history)."""
    project = await _get_project_with_access(slug, user, db)
    result = await db.execute(
        select(ProjectInvite)
        .where(ProjectInvite.project_id == project.id)
        .order_by(ProjectInvite.created_at.desc())
    )
    return result.scalars().all()


# ─── Accept invite (public, auth needed) ──────────────────────────────────────

@router.post("/invite/accept/{token}")
async def accept_invite(
    token: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Accept an invitation to join a project."""
    result = await db.execute(
        select(ProjectInvite).where(
            ProjectInvite.invite_token == token,
            ProjectInvite.status == "pending",
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "Приглашение не найдено или уже использовано")

    # Check if user is already a member
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == invite.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        return {"message": "Вы уже участник этого проекта"}

    # Add as member
    member = ProjectMember(project_id=invite.project_id, user_id=user.id)
    db.add(member)

    # Mark invite as accepted (both email and link invites)
    invite.status = "accepted"
    invite.accepted_at = utcnow()
    invite.accepted_by_id = user.id

    await db.commit()

    # Get project name
    p = await db.execute(select(Project).where(Project.id == invite.project_id))
    project = p.scalar_one()
    return {"message": f"Вы присоединились к проекту «{project.name}»", "project_slug": project.slug}


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _get_project_with_access(slug: str, user: User, db: AsyncSession) -> Project:
    """Get project and verify user has access."""
    result = await db.execute(select(Project).where(Project.slug == slug))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Проект не найден")

    mem = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    if not mem.scalar_one_or_none():
        raise HTTPException(403, "Нет доступа к проекту")
    return project
