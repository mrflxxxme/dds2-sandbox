# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Product Cards — зеркало карточек товаров WB (публичный API, без ключа).

Источник: card.json с basket-хостов WB (basket-XX.wbbasket.ru/vol.../part.../nm/info/ru/card.json)
+ опционально card.wb.ru/cards/v4/detail (бренд, число фото). См. services/wb_cards_service.py.

Зачем: название/описание/характеристики/ссылки на фото карточки — фактология для
базы знаний автоответов (импорт в wb_product_kb с source='card') и для UI.

Байты фото НЕ скачиваются — хранятся только URL (photo_urls).
TODO: извлечение фактов с фото (размерные сетки, состав на этикетке) отложено —
нужен vision LLM (см. wb_cards_service).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin


class WBProductCard(Base, TimestampMixin):
    """Снапшот публичной карточки WB по nm_id (upsert по project_id+nm_id)."""

    __tablename__ = "wb_product_cards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # WB nmID товара
    title: Mapped[str | None] = mapped_column(String(500))  # imt_name
    brand: Mapped[str | None] = mapped_column(String(255))  # из cards/v4/detail
    subject: Mapped[str | None] = mapped_column(String(255))  # subj_name
    description: Mapped[str | None] = mapped_column(Text)  # description карточки
    contents: Mapped[str | None] = mapped_column(Text)  # комплектация (contents)

    # нормализованный список характеристик: [{"name": "Цвет", "value": "бежевый"}, ...]
    characteristics: Mapped[list | None] = mapped_column(JSONB)
    # URL больших фото: ["https://basket-XX.../images/big/1.webp", ...] (кап 10)
    photo_urls: Mapped[list | None] = mapped_column(JSONB)

    synced_at: Mapped[datetime | None] = mapped_column(DateTime)  # когда скачана карточка

    __table_args__ = (
        Index("uq_wb_product_cards_project_nm", "project_id", "nm_id", unique=True),
        Index("ix_wb_product_cards_nm_id", "nm_id"),
    )
