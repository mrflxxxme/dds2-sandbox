# ruff: noqa: RUF002, RUF003
"""
Gazelka (gazelka.space) — audit-запись отправки заявки логиста перевозчику.

Каждая попытка отправки заявки из DDS в Газельку пишется строкой: что отправили
(snapshot ``payload`` — БЕЗ кредов), исход (``status``), номер у Газельки если
распознан (``gazelka_ref``), выдержка ответа (``response_excerpt``) для ручной сверки.

История попыток (не уникальность): повторная отправка той же сборки = новая строка.
"""

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin


class GazelkaOrderStatus:
    SENT = "SENT"  # портал редиректнул в список заявок — заявка создана
    UNCERTAIN = "UNCERTAIN"  # POST прошёл, но подтверждения нет — сверить вручную
    FAILED = "FAILED"  # исключение (сеть/авторизация/5xx) — заявка не ушла
    MATCHED = "MATCHED"  # ручная связь: существующая заявка портала ↔ наша сборка


class GazelkaOrder(Base, TimestampMixin):
    """Лог отправки заявки в Газельку (одна попытка = одна строка)."""

    __tablename__ = "gazelka_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    gazelka_ref: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # snapshot отправленных полей (без кредов)
    response_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # created_at (TimestampMixin) = момент отправки

    __table_args__ = (
        Index("ix_gazelka_orders_project_id", "project_id"),
        Index("ix_gazelka_orders_assembly_request_id", "assembly_request_id"),
    )
