"""
Router: /projects — CRUD for projects + team management + RBAC.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import Project, ProjectInvite, ProjectMember, User
from backend.rbac import (
    ALL_PAGES,
    ROLE_HIERARCHY,
    get_effective_pages,
    inherited_pages,
    parse_pages,
)
from backend.utils.rate_limit import rate_limit_write
from backend.utils.time import utcnow

# Invite links expire this many days after creation.
INVITE_TTL_DAYS = 7

logger = logging.getLogger(__name__)

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
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    telegram_username: str | None = None
    role: str = "editor"
    pages: list[str] = []
    # Подмножество `pages`, доставшееся по наследству (раздел появился после
    # настройки доступа) — «Команда» подсвечивает его владельцу.
    pages_inherited: list[str] = []
    joined_at: datetime


class InviteResponse(BaseModel):
    id: int
    project_id: int
    email: str | None = None
    invite_token: str
    role: str = "editor"
    pages: list[str] = []
    status: str
    created_at: datetime
    expires_at: datetime | None = None
    accepted_at: datetime | None = None


class UpdateRoleRequest(BaseModel):
    role: str
    pages: list[str] = []


class MyPermissionsResponse(BaseModel):
    role: str
    pages: list[str]
    is_owner: bool


# ─── Project CRUD ─────────────────────────────────────────────────────────────


def _make_slug(name: str) -> str:
    """Generate URL-safe slug from project name."""
    import re

    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9а-яё\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = slug[:80]
    # Add short UUID to ensure uniqueness
    return f"{slug}-{uuid.uuid4().hex[:6]}"


@router.post("", response_model=ProjectResponse, dependencies=[Depends(rate_limit_write)])
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
    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role="owner",
    )
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
            Project.is_deleted == False,  # noqa: E712
            ProjectMember.is_deleted == False,  # noqa: E712
            Project.id.in_(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == user.id,
                    ProjectMember.is_deleted == False,  # noqa: E712
                )
            ),
        )
        .group_by(Project.id)
        .order_by(Project.created_at.desc())
    )
    rows = result.all()
    return [{**p.__dict__, "members_count": cnt} for p, cnt in rows]


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
        .where(Project.slug == slug, Project.is_deleted == False, ProjectMember.is_deleted == False)  # noqa: E712
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
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    if not mem.scalar_one_or_none():
        raise HTTPException(403, "Нет доступа к проекту")
    return {**project.__dict__, "members_count": cnt}


@router.delete("/{slug}", dependencies=[Depends(rate_limit_write)])
async def delete_project(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete project. Only owner can delete."""
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_deleted == False))  # noqa: E712
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Проект не найден")
    if project.owner_id != user.id:
        raise HTTPException(403, "Только владелец может удалить проект")
    project.soft_delete()
    await db.commit()
    return {"deleted": True, "slug": slug}


# ─── Team / Members ──────────────────────────────────────────────────────────


@router.get("/{slug}/members", response_model=list[ProjectMemberResponse])
async def list_members(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all members of a project with their roles and pages."""
    project = await _get_project_with_access(slug, user, db)
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id, ProjectMember.is_deleted == False)  # noqa: E712
        .order_by(ProjectMember.joined_at)
    )
    rows = result.all()
    # Разбивку «что выдано, а что доехало наследованием» показываем только тем,
    # кто правит доступы: остальным это карта чужих прав, а не их работа.
    # Роль запрашивающего берём из уже загруженных строк — он тоже участник.
    actor_role = next((m.role for m, u in rows if u.id == user.id), None)
    show_inherited = actor_role in ("owner", "admin")
    return [
        {
            "id": m.id,
            "user_id": u.id,
            "username": u.username,
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "telegram_username": u.telegram_username,
            "role": m.role,
            "pages": get_effective_pages(m.role, m.pages, m.pages_updated_at),
            "pages_inherited": inherited_pages(parse_pages(m.pages), m.pages_updated_at)
            if show_inherited and m.role in ("editor", "viewer")
            else [],
            "joined_at": m.joined_at,
        }
        for m, u in rows
    ]


@router.delete("/{slug}/members/{user_id}", dependencies=[Depends(rate_limit_write)])
async def remove_member(
    slug: str,
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from a project. Actor role must be higher than target role."""
    project = await _get_project_with_access(slug, user, db)

    if user_id == user.id:
        raise HTTPException(400, "Нельзя удалить себя из проекта")

    # Get actor membership
    actor_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    actor_member = actor_result.scalar_one_or_none()
    if not actor_member:
        raise HTTPException(403, "Нет доступа к проекту")

    # Get target membership
    target_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user_id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    target_member = target_result.scalar_one_or_none()
    if not target_member:
        raise HTTPException(404, "Участник не найден")

    # Cannot remove owner
    if target_member.role == "owner":
        raise HTTPException(403, "Нельзя удалить владельца проекта")

    # Actor role must be strictly higher than target role
    actor_level = ROLE_HIERARCHY.get(actor_member.role, 0)
    target_level = ROLE_HIERARCHY.get(target_member.role, 0)
    if actor_level <= target_level:
        raise HTTPException(403, "Недостаточно прав для удаления этого участника")

    target_member.soft_delete()
    await db.commit()
    logger.info(
        "Member removed: project_id=%d, actor_id=%d, target_user_id=%d",
        project.id,
        user.id,
        user_id,
    )
    return {"removed": True, "user_id": user_id}


# ─── Role management ─────────────────────────────────────────────────────────


@router.put("/{slug}/members/{user_id}/role", dependencies=[Depends(rate_limit_write)])
async def update_member_role(
    slug: str,
    user_id: int,
    body: UpdateRoleRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update member role and pages. Only owner can change roles."""
    project = await _get_project_with_access(slug, user, db)

    # Only owner can change roles
    actor_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    actor_member = actor_result.scalar_one_or_none()
    if not actor_member or actor_member.role != "owner":
        raise HTTPException(403, "Только владелец может менять роли")

    # Cannot change owner's role
    target_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user_id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    target_member = target_result.scalar_one_or_none()
    if not target_member:
        raise HTTPException(404, "Участник не найден")

    if target_member.role == "owner":
        raise HTTPException(403, "Нельзя изменить роль владельца")

    # Cannot assign owner role (only through transfer)
    if body.role == "owner":
        raise HTTPException(400, "Нельзя назначить роль owner — используйте transfer")

    # Validate role
    if body.role not in ROLE_HIERARCHY:
        raise HTTPException(400, f"Неизвестная роль: {body.role}")

    # Validate page keys
    invalid_pages = [p for p in body.pages if p not in ALL_PAGES]
    if invalid_pages:
        raise HTTPException(400, f"Неизвестные страницы: {', '.join(invalid_pages)}")

    target_member.role = body.role
    target_member.pages = json.dumps(body.pages) if body.pages else None
    # Явная настройка = новый водяной знак каталога: снятые сейчас галочки больше
    # не вернутся наследованием, а разделы, которые появятся позже, — доедут.
    target_member.pages_updated_at = utcnow()
    await db.commit()

    logger.info(
        "Role updated: project_id=%d, user_id=%d, new_role=%s",
        project.id,
        user_id,
        body.role,
    )
    return {
        "updated": True,
        "user_id": user_id,
        "role": body.role,
        "pages": get_effective_pages(body.role, target_member.pages, target_member.pages_updated_at),
    }


# ─── Telegram username ────────────────────────────────────────────────────────


class UpdateTelegramUsernameRequest(BaseModel):
    telegram_username: str | None = None


_TG_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{5,32}$")


@router.put("/{slug}/members/{user_id}/telegram-username", dependencies=[Depends(rate_limit_write)])
async def update_telegram_username(
    slug: str,
    user_id: int,
    body: UpdateTelegramUsernameRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update telegram_username for a project member. Only owner or admin can do this."""
    project = await _get_project_with_access(slug, user, db)

    # Check actor is owner or admin
    actor_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    actor_member = actor_result.scalar_one_or_none()
    if not actor_member or actor_member.role not in ("owner", "admin"):
        raise HTTPException(403, "Только владелец или админ может менять telegram_username")

    # Check target is a project member
    target_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user_id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    target_member = target_result.scalar_one_or_none()
    if not target_member:
        raise HTTPException(404, "Участник не найден")

    # Get the User object
    user_result = await db.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(404, "Пользователь не найден")

    # Normalize or clear
    raw = body.telegram_username
    if not raw or not raw.strip():
        target_user.telegram_username = None
        await db.commit()
        return {"updated": True, "user_id": user_id, "telegram_username": None}

    normalized = raw.strip().lstrip("@").lower()

    # Validate format
    if not _TG_USERNAME_RE.match(normalized):
        raise HTTPException(400, "Некорректный Telegram username (5-32 символа, a-z, 0-9, _)")

    # Check uniqueness
    existing = await db.execute(
        select(User).where(
            func.lower(User.telegram_username) == normalized,
            User.id != user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Этот Telegram username уже привязан к другому пользователю")

    target_user.telegram_username = normalized
    await db.commit()

    logger.info(
        "Telegram username updated: project_id=%d, user_id=%d, username=%s",
        project.id,
        user_id,
        normalized,
    )
    return {"updated": True, "user_id": user_id, "telegram_username": normalized}


# ─── My permissions ──────────────────────────────────────────────────────────


@router.get("/{slug}/my-permissions", response_model=MyPermissionsResponse)
async def get_my_permissions(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return current user's role and effective pages for the project."""
    project = await _get_project_with_access(slug, user, db)

    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Нет доступа к проекту")

    return {
        "role": member.role,
        "pages": get_effective_pages(member.role, member.pages, member.pages_updated_at),
        "is_owner": member.role == "owner",
    }


# ─── Invitations ──────────────────────────────────────────────────────────────


@router.post("/{slug}/invite", response_model=InviteResponse, dependencies=[Depends(rate_limit_write)])
async def invite_by_email(
    slug: str,
    email: str,
    role: str = Query("editor"),
    pages: str | None = Query(None, description="JSON array of page keys"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invite a user to the project by email with role and pages."""
    project = await _require_invite_manager(slug, user, db)

    # Validate role — cannot invite as owner
    if role not in ROLE_HIERARCHY or role == "owner":
        raise HTTPException(400, f"Недопустимая роль для приглашения: {role}")

    # Parse and validate pages
    pages_list = parse_pages(pages) if pages else []
    invalid_pages = [p for p in pages_list if p not in ALL_PAGES]
    if invalid_pages:
        raise HTTPException(400, f"Неизвестные страницы: {', '.join(invalid_pages)}")

    token = uuid.uuid4().hex
    invite = ProjectInvite(
        project_id=project.id,
        email=email,
        invite_token=token,
        role=role,
        pages=json.dumps(pages_list) if pages_list else None,
        status="pending",
        expires_at=utcnow() + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    return _invite_to_response(invite)


@router.get("/{slug}/invite-link")
async def get_invite_link(
    slug: str,
    role: str = Query("editor"),
    pages: str | None = Query(None, description="JSON array of page keys"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new unique invite link for the project with role and pages."""
    project = await _require_invite_manager(slug, user, db)

    # Validate role — cannot invite as owner
    if role not in ROLE_HIERARCHY or role == "owner":
        raise HTTPException(400, f"Недопустимая роль для приглашения: {role}")

    # Parse and validate pages
    pages_list = parse_pages(pages) if pages else []
    invalid_pages = [p for p in pages_list if p not in ALL_PAGES]
    if invalid_pages:
        raise HTTPException(400, f"Неизвестные страницы: {', '.join(invalid_pages)}")

    token = uuid.uuid4().hex
    invite = ProjectInvite(
        project_id=project.id,
        email=None,
        invite_token=token,
        role=role,
        pages=json.dumps(pages_list) if pages_list else None,
        status="pending",
        expires_at=utcnow() + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    return {
        "invite_token": invite.invite_token,
        "link": f"/invite/{invite.invite_token}",
        "role": role,
        "pages": pages_list,
    }


@router.get("/{slug}/invites", response_model=list[InviteResponse])
async def list_invites(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all invitations for a project (history).

    Manager-only: the response exposes raw invite tokens, so a viewer/editor must
    not be able to read (and then replay) a link granting a higher role.
    """
    project = await _require_invite_manager(slug, user, db)
    result = await db.execute(
        select(ProjectInvite).where(ProjectInvite.project_id == project.id).order_by(ProjectInvite.created_at.desc())
    )
    return [_invite_to_response(inv) for inv in result.scalars().all()]


# ─── Accept invite (public, auth needed) ──────────────────────────────────────


@router.post("/invite/accept/{token}", dependencies=[Depends(rate_limit_write)])
async def accept_invite(
    token: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Accept an invitation to join a project. Copies role and pages from invite."""
    result = await db.execute(
        select(ProjectInvite).where(
            ProjectInvite.invite_token == token,
            ProjectInvite.status == "pending",
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "Приглашение не найдено или уже использовано")
    if invite.expires_at is not None and invite.expires_at < utcnow():
        invite.status = "expired"
        await db.commit()
        raise HTTPException(410, "Срок действия приглашения истёк")

    # Check if user is already a member — include soft-deleted rows, because the
    # uq_project_member (project_id, user_id) constraint ignores is_deleted: a
    # previously removed member still occupies the unique slot. A blind INSERT
    # would raise IntegrityError → 500.
    existing_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == invite.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing is not None and not existing.is_deleted:
        # Already an active member — idempotent, leave the invite pending.
        return {"message": "Вы уже участник этого проекта"}

    if existing is not None:
        # Previously removed (soft-deleted) member — restore the row and refresh
        # role/pages from the invite instead of inserting a new row (which would
        # violate uq_project_member). Falls through to the common tail below.
        existing.restore()
        existing.role = invite.role
        existing.pages = invite.pages
        # Знак — момент СОЗДАНИЯ инвайта, а не акцепта: набор галочек владелец
        # выбрал тогда, и раздел, вышедший внутри недельного окна инвайта, не
        # должен читаться как «владелец его не дал».
        existing.pages_updated_at = invite.created_at
    else:
        # Brand-new member.
        member = ProjectMember(
            project_id=invite.project_id,
            user_id=user.id,
            role=invite.role,
            pages=invite.pages,
            pages_updated_at=invite.created_at,
        )
        db.add(member)

    # Mark invite as accepted (both email and link invites)
    invite.status = "accepted"
    invite.accepted_at = utcnow()
    invite.accepted_by_id = user.id

    await db.commit()

    # Get project name
    p = await db.execute(select(Project).where(Project.id == invite.project_id))
    project = p.scalar_one()

    logger.info(
        "Invite accepted: project_id=%d, user_id=%d, role=%s",
        invite.project_id,
        user.id,
        invite.role,
    )
    return {
        "message": f"Вы присоединились к проекту «{project.name}»",
        "project_slug": project.slug,
        "role": invite.role,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _get_project_with_access(slug: str, user: User, db: AsyncSession) -> Project:
    """Get project and verify user has access."""
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_deleted == False))  # noqa: E712
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Проект не найден")

    mem = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    if not mem.scalar_one_or_none():
        raise HTTPException(403, "Нет доступа к проекту")
    return project


async def _require_invite_manager(slug: str, user: User, db: AsyncSession) -> Project:
    """Like `_get_project_with_access`, but also requires the actor to be
    owner/admin. Only project managers may create invitations — otherwise a
    viewer/editor could mint a link granting a role at or above their own.
    """
    project = await _get_project_with_access(slug, user, db)
    # The project owner always manages invites, even if their ProjectMember.role
    # is not literally "owner" (personal projects created before the register fix
    # carry role="editor" for the owner — owner_id is the source of truth).
    if project.owner_id == user.id:
        return project
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
                ProjectMember.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    actor_role = member.role if member else ""
    if ROLE_HIERARCHY.get(actor_role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, "Приглашать участников может только владелец или администратор проекта")
    return project


def _invite_to_response(invite: ProjectInvite) -> dict:
    """Convert ProjectInvite model to response dict with parsed pages."""
    return {
        "id": invite.id,
        "project_id": invite.project_id,
        "email": invite.email,
        "invite_token": invite.invite_token,
        "role": invite.role,
        "pages": parse_pages(invite.pages),
        "status": invite.status,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
        "accepted_at": invite.accepted_at,
    }
