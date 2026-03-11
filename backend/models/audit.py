"""
Model: AuditLog — tracks all API mutations (POST/PUT/PATCH/DELETE).

Provides an audit trail: who did what, when, on which project.
"""

from datetime import datetime

from sqlalchemy import (
    Integer, String, Text, DateTime, ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.time import utcnow


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)  # POST, PUT, DELETE, PATCH
    endpoint: Mapped[str] = mapped_column(String(300), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=True)  # IPv4/IPv6
    payload_summary: Mapped[str] = mapped_column(Text, nullable=True)  # Truncated request body
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
