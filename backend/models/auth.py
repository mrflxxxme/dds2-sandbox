"""
Auth models: User, Project, ProjectMember, ProjectInvite.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin
from backend.utils.time import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), unique=True)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(20), default="member", server_default="member", nullable=False)
    # External account (fulfillment operator, e.g. Хамза). Such users work ONLY
    # via the /api/v1/ff/* portal — a middleware 403s them on any other API path,
    # so they never see cost/margin/analytics of the projects they belong to.
    is_external: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    memberships: Mapped[list["ProjectMember"]] = relationship(back_populates="user")


class Project(Base, SoftDeleteMixin):
    """A project groups all data (transactions, orders, accounts, etc.)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=6)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=22)
    # Cut-off date for legacy data: anything before this is considered archive
    # and hidden from default list views (FBO supplies and similar).
    # NULL = show everything (no archive cut-off configured for this project).
    accounting_started_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project")
    invites: Mapped[list["ProjectInvite"]] = relationship(back_populates="project")


class ProjectMember(Base, SoftDeleteMixin):
    """Link between users and projects."""

    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="editor", server_default="editor", nullable=False)
    pages: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: ["assembly","funnel",...]
    # Когда `pages` выставляли явно — водяной знак каталога страниц: разделы,
    # добавленные позже, наследуются по секции (backend/rbac.inherited_pages).
    pages_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)


class ProjectInvite(Base):
    """Invitations to join a project (by email or link)."""

    __tablename__ = "project_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    invite_token: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="editor", server_default="editor", nullable=False)
    pages: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: ["assembly","funnel",...]
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, accepted, expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Invite link stops working after this moment (7 days from creation by default).
    # NULL = legacy invite with no expiry (pre-dating this column).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    accepted_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))

    __table_args__ = (Index("ix_project_invites_project_id", "project_id"),)

    project: Mapped["Project"] = relationship(back_populates="invites")
