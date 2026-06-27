# ruff: noqa: RUF002, RUF003
"""
migfull-портал (plusvb.migfull.app) — audit-запись создания заявки на отгрузку.

НЕ путать с read-only API-клиентом ``migfull_client.py`` (Bearer-токен). Здесь —
браузерная интеграция с порталом ФФ «Натали» (Filament/Livewire): логин почта+пароль,
создание заявки (шапка) + загрузка описи .xlsx. Креды — отдельный
``IntegrationKey(service="migfull_portal")``.

Каждая попытка отправки = строка: что отправили (``payload`` — шапка БЕЗ кредов),
исход (``status``), guid заявки на портале (``shipment_guid``), её reference
(``shipment_number`` = PVB-…), выдержка ответа (``response_excerpt``) для ручной сверки.

⚠ У клиента портала НЕТ delete/cancel — создание заявки НЕОБРАТИМО. Анти-дубль гейт
(см. service): без ``force_resend`` повторная отправка той же сборки → 409.
"""

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin


class MigfullShipmentStatus:
    SENT = "SENT"  # портал создал заявку (есть guid) + опись прикреплена
    UNCERTAIN = "UNCERTAIN"  # шапка создана, но опись не подтвердилась — сверить вручную
    FAILED = "FAILED"  # исключение (сеть/авторизация/валидация) — заявка не ушла


class MigfullShipmentOrder(Base, TimestampMixin):
    """Лог создания заявки на отгрузку в migfull-портале (одна попытка = одна строка)."""

    __tablename__ = "migfull_shipment_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    shipment_guid: Mapped[str | None] = mapped_column(String(64), nullable=True)  # guid заявки на портале
    shipment_number: Mapped[str | None] = mapped_column(String(100), nullable=True)  # PVB-0000xxx / № поставки
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # шапка + строки описи (без кредов)
    opis_filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    response_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # created_at (TimestampMixin) = момент отправки

    __table_args__ = (
        Index("ix_migfull_shipment_orders_project_id", "project_id"),
        Index("ix_migfull_shipment_orders_assembly_request_id", "assembly_request_id"),
    )
