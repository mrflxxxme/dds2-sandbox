"""
Auth models: User, Project, ProjectMember, ProjectInvite.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    memberships: Mapped[list["ProjectMember"]] = relationship(back_populates="user")


class Project(Base):
    """A project groups all data (transactions, orders, accounts, etc.)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=6)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=22)

    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project")
    invites: Mapped[list["ProjectInvite"]] = relationship(back_populates="project")


class ProjectMember(Base):
    """Link between users and projects."""

    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="editor", server_default="editor", nullable=False)
    pages: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: ["assembly","funnel",...]
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
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    accepted_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))

    project: Mapped["Project"] = relationship(back_populates="invites")
