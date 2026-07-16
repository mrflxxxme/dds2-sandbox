"""АБ-тесты главного фото карточки WB.

Механика: варианты главного фото крутятся на карточке по «кругам» — круг закрывается
по истечении round_minutes (смена гарантирована по времени) либо ДОСРОЧНО, когда
активное фото набрало views_per_round рекламных показов. Метрики круга — дельты накопительных счётчиков «за сегодня»
(реклама: /adv/v3/fullstats по nmId; органика: воронка v3), стиль WbAdCampaignSnapshot /
get_intraday_metrics: WB внутридневную разбивку не отдаёт, копим сами вперёд.

Тест завершается, когда каждый активный вариант набрал target_views показов
(или по предохранителю max_days), после чего на карточку возвращается исходное фото.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin
from backend.utils.time import utcnow


class AbPhotoTest(Base, SoftDeleteMixin):
    """Тест: артикул × рекламная кампания × набор вариантов фото."""

    __tablename__ = "ab_photo_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    # draft | running | paused | finished | error
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    pause_reason: Mapped[str | None] = mapped_column(Text)  # почему встал (внешняя правка карточки и т.п.)

    # Параметры (дефолты — решения юзера 13.07.2026)
    views_per_round: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    round_minutes: Mapped[int] = mapped_column(Integer, default=45, nullable=False)  # время круга (смена гарантирована)
    target_views: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)  # на каждый вариант
    max_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)  # предохранитель

    # Снимок карточки на момент старта: URL исходного главного фото + весь массив photos
    original_photo_url: Mapped[str | None] = mapped_column(Text)
    original_media: Mapped[dict | None] = mapped_column(JSONB)

    # Текущее состояние ротации. active_variant_id/winner_variant_id — без FK:
    # циклическая ссылка тест↔вариант, целостность держит сервис.
    active_variant_id: Mapped[int | None] = mapped_column(Integer)
    # Последние накопительные счётчики «за сегодня» для дельт:
    # {"stat_date": "YYYY-MM-DD", "adv": {views, clicks, atbs, orders, spend, orders_sum},
    #  "organic": {open, cart, orders}}
    last_stat: Mapped[dict | None] = mapped_column(JSONB)

    winner_variant_id: Mapped[int | None] = mapped_column(Integer)
    winner_applied_at: Mapped[datetime | None] = mapped_column(DateTime)

    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ab_photo_tests_project_status", "project_id", "status"),
        Index("ix_ab_photo_tests_project_nm", "project_id", "nm_id"),
    )


class AbPhotoVariant(Base):
    """Вариант главного фото. position 0 = контроль (исходное фото карточки).

    Байты фото лежат в MinIO под image_key (в т.ч. для контроля — скачанный оригинал,
    чтобы ротация и возврат исходного шли одним путём: media/file с X-Photo-Number=1).
    excluded_at — исключён из ротации (минусик); можно вернуть (сбросить в NULL).
    """

    __tablename__ = "ab_photo_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    test_id: Mapped[int] = mapped_column(Integer, ForeignKey("ab_photo_tests.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_control: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    image_key: Mapped[str] = mapped_column(Text, nullable=False)  # ключ в MinIO
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("test_id", "position", name="uq_ab_photo_variant_position"),
        Index("ix_ab_photo_variants_test", "project_id", "test_id"),
    )


class AbPhotoRound(Base):
    """Круг ротации: интервал, в котором на карточке стояло одно фото.

    Метрики — суммы дельт накопительных счётчиков за время круга. adv-заказы —
    атрибуция рекламы (fullstats), organic_* — вся воронка товара (все источники).
    ended_at IS NULL — текущий (открытый) круг.
    """

    __tablename__ = "ab_photo_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    test_id: Mapped[int] = mapped_column(Integer, ForeignKey("ab_photo_tests.id"), nullable=False)
    variant_id: Mapped[int] = mapped_column(Integer, ForeignKey("ab_photo_variants.id"), nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)

    apply_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # фото применилось
    flags: Mapped[dict | None] = mapped_column(JSONB)  # «грязь»: campaign_paused, apply_error…

    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    atbs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    orders_sum: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    organic_open: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    organic_cart: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    organic_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("test_id", "round_no", name="uq_ab_photo_round_no"),
        Index("ix_ab_photo_rounds_test", "project_id", "test_id", "round_no"),
        Index("ix_ab_photo_rounds_variant", "project_id", "variant_id"),
    )
