# ruff: noqa: RUF002, RUF003
"""
Gazelka (gazelka.space) — audit-запись отправки заявки логиста перевозчику.

Каждая попытка отправки заявки из DDS в Газельку пишется строкой: что отправили
(snapshot ``payload`` — БЕЗ кредов), исход (``status``), номер у Газельки если
распознан (``gazelka_ref``), выдержка ответа (``response_excerpt``) для ручной сверки.

История попыток (не уникальность): повторная отправка той же сборки = новая строка.
"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin


class GazelkaOrderStatus:
    SENT = "SENT"  # портал редиректнул в список заявок — заявка создана
    UNCERTAIN = "UNCERTAIN"  # POST прошёл, но подтверждения нет — сверить вручную
    FAILED = "FAILED"  # исключение (сеть/авторизация/5xx) — заявка не ушла
    MATCHED = "MATCHED"  # ручная связь: существующая заявка портала ↔ наша сборка
    # Связь снята логистом («Отвязать»), сама попытка отправки в аудите остаётся.
    # Нужен ПЕРЕЕЗДУ: у него ручное назначение машины закрыто гардом «логистику
    # ведёт Газелька», а отправка — гейтом «сначала оформи логистику». Если
    # агрегатор заказ так и не подтвердил (отменили по телефону), документ
    # запирался навсегда: ни назначить, ни снять, ни отправить. Удалять SENT-строку
    # нельзя — это аудит РЕАЛЬНОГО, необратимого создания заявки в чужом сервисе.
    CANCELLED = "CANCELLED"


class GazelkaOrder(Base, TimestampMixin):
    """Лог отправки заявки в Газельку (одна попытка = одна строка)."""

    __tablename__ = "gazelka_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    # Второй тип документа, который Газелька умеет везти, — ПЕРЕЕЗД между нашими
    # складами (StockTransfer). Отдельная колонка, а не полиморфная пара
    # (kind, id): FK ловит битую ссылку в БД, а не в коде, и `ondelete=SET NULL`
    # оставляет audit-запись жить после удаления документа — попытка отправки
    # состоялась, и её нельзя терять вместе с заявкой.
    #
    # 🔴 Ровно ОДНА из двух ссылок не NULL (CHECK ниже). Обе сразу означали бы,
    # что один заказ портала закрывает и сборку, и переезд: `_linked_map`
    # показал бы его дважды, а `_reconcile_active_order` дважды применил бы
    # машину — второй раз к чужому документу.
    stock_transfer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stock_transfers.id", ondelete="SET NULL"), nullable=True
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
        Index("ix_gazelka_orders_stock_transfer_id", "stock_transfer_id"),
        # Обе ссылки NULL — законно: так выглядит попытка отправки, чей документ
        # потом удалили (ondelete=SET NULL). Запрещена только пара «обе сразу».
        CheckConstraint(
            "assembly_request_id IS NULL OR stock_transfer_id IS NULL",
            name="ck_gazelka_orders_single_link",
        ),
    )
