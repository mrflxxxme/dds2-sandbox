# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Product Knowledge Base — база знаний товаров для ИИ-автоответов.

Эталонные пары «типичный вопрос → правильный ответ» по конкретному nm_id.
Источники: ручной ввод продавца (source=manual) и импорт из архива уже
отвеченных вопросов WB (source=import, `question_hash` = md5 нормализованного
текста вопроса — гард от дублей при повторном импорте).

ИИ-агент отвечает СТРОГО из этих записей: если подходящего факта нет —
черновик помечается needs_info и уходит продавцу на ручную доработку.
Записи enabled=False исключаются из подбора (мягкое отключение без удаления).
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin

# Источники записи базы знаний
KB_SOURCES = ("manual", "import", "card")

# Темы эвристической классификации типичных вопросов (см. reply_service.classify_kb_topic)
KB_TOPICS = ("Размер", "Доставка", "Качество", "Состав", "Цвет", "Комплект", "Гарантия", "Прочее")


class WBProductKB(Base, TimestampMixin):
    """Одна запись базы знаний по товару (nm_id): тема → эталонный ответ."""

    __tablename__ = "wb_product_kb"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # WB nmID товара
    topic: Mapped[str] = mapped_column(String(100), nullable=False)  # тема/типичный вопрос
    question_example: Mapped[str | None] = mapped_column(Text)  # пример формулировки покупателя
    answer: Mapped[str] = mapped_column(Text, nullable=False)  # эталонный ответ продавца

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual|import|card
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # md5 нормализованного текста вопроса — дедуп импорта (только для source=import)
    question_hash: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_wb_product_kb_project_nm_enabled", "project_id", "nm_id", "enabled"),
        Index("ix_wb_product_kb_nm_id", "nm_id"),
        # дедуп-гард импорта: один (nm_id, хэш вопроса) — одна запись
        Index(
            "uq_wb_product_kb_project_nm_qhash",
            "project_id",
            "nm_id",
            "question_hash",
            unique=True,
            postgresql_where=text("question_hash IS NOT NULL"),
        ),
    )
