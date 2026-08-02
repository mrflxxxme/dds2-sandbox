# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Complaint Agents — ИИ-агенты подготовки жалоб на отзывы по правилам.

Агент фильтрует отзывы (предмет/бренд/артикул/звёзды) и по «Правилам для жалобы»
(специфика НАШЕГО товара) решает через LLM, есть ли основание, и готовит текст.
LLM — сменный провайдер (OpenAI-совместимый: DeepSeek/GigaChat/OpenRouter или Claude).
Подготовка ≠ отправка: жалобы кладутся в учёт (`wb_feedback_complaints`), подаёт продавец.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin

# Провайдеры LLM: openai_compatible (DeepSeek/GigaChat/OpenRouter/Groq) или наш Claude
LLM_PROVIDERS = ("openai_compatible", "claude")


class WBComplaintAgent(Base, TimestampMixin):
    """ИИ-агент подготовки жалоб на отзывы (по правилам, на нашем каталоге)."""

    __tablename__ = "wb_complaint_agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ─── Фильтры отзывов (из нашего каталога) ───────────────────────────────
    subject: Mapped[str | None] = mapped_column(String(200))  # предмет; None = все
    brand: Mapped[str | None] = mapped_column(String(200))  # бренд; None = все
    nm_ids: Mapped[str | None] = mapped_column(Text)  # артикулы через запятую; None = все
    star_levels: Mapped[str] = mapped_column(String(16), nullable=False, default="1,2,3")

    # ─── Правила и примеры (специфика товара) ───────────────────────────────
    rules: Mapped[str] = mapped_column(Text, nullable=False)  # «Правила для жалобы»
    examples: Mapped[str | None] = mapped_column(Text)  # примеры жалоб (few-shot)

    # ─── LLM-провайдер (сменный) ────────────────────────────────────────────
    llm_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="openai_compatible")
    llm_model: Mapped[str] = mapped_column(String(64), nullable=False, default="deepseek-chat")
    llm_base_url: Mapped[str | None] = mapped_column(String(200))  # для openai_compatible

    last_run_at: Mapped[datetime | None] = mapped_column()
