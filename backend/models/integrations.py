"""
Integration models: IntegrationKey, SyncLog, WbFunnelDaily, WbCostOverride, WbAdCampaign, WbAdCampaignDaily.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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


class IntegrationKey(Base, SoftDeleteMixin):
    """Encrypted API keys for external services (WB, OZON, etc.)."""

    __tablename__ = "integration_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    service: Mapped[str] = mapped_column(String(50), nullable=False)  # "wb", "ozon"
    label: Mapped[str | None] = mapped_column(String(200))  # user-friendly name
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet-encrypted
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Fulfillment: ключ, привязанный к конкретному складу (skladbot, migfull)
    warehouse_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouses.id"))
    config: Mapped[dict | None] = mapped_column(JSONB)  # customer_id, token_expires_at, ...

    __table_args__ = (UniqueConstraint("project_id", "service", "label", name="uq_integration_project_service_label"),)


class SyncLog(Base):
    """Log of integration sync operations."""

    __tablename__ = "sync_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    integration_id: Mapped[int] = mapped_column(Integer, ForeignKey("integration_keys.id"))
    service: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "sales", "payouts", "orders"
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")  # RUNNING, OK, ERROR
    rows_fetched: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_sync_log_integration_id", "integration_id"),
        Index("ix_sync_log_started_at", "started_at"),
    )


class WbFunnelDaily(Base):
    """Daily WB sales-funnel + advertising stats per nmId."""

    __tablename__ = "wb_funnel_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    date: Mapped[date] = mapped_column(Date, nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor_code: Mapped[str | None] = mapped_column(String(100))
    subject: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200))

    # Funnel
    open_card: Mapped[int] = mapped_column(Integer, default=0)
    add_to_cart: Mapped[int] = mapped_column(Integer, default=0)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    orders_sum_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    buyout_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    cart_to_order_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    add_to_cart_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    avg_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    stocks_wb: Mapped[int] = mapped_column(Integer, default=0)
    stocks_mp: Mapped[int] = mapped_column(Integer, default=0)

    # Advertising
    adv_views: Mapped[int] = mapped_column(Integer, default=0)
    adv_clicks: Mapped[int] = mapped_column(Integer, default=0)
    adv_sum: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    # Cost price (filled from last order or override)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    # Localization Index (WB v3 sales-funnel: localizationPercent, timeToReady)
    localization_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    time_to_ready_minutes: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("project_id", "date", "nm_id", name="uq_funnel_daily"),
        Index("ix_funnel_project_date", "project_id", "date"),
    )


class WbAdCampaign(Base):
    """WB advertising campaign metadata: name, type, status, budget, linked products."""

    __tablename__ = "wb_ad_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    name: Mapped[str | None] = mapped_column(String(500))
    campaign_type: Mapped[str | None] = mapped_column(String(20))  # cpm, cpc (модель оплаты WB)
    advert_type: Mapped[int | None] = mapped_column(Integer)  # числовой тип WB: 8=авто/рекомендации, 9=аукцион (для цветовой кодировки)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)  # дата создания кампании в WB (createTime) — для фильтра по дате добавления
    bid_mode: Mapped[str | None] = mapped_column(String(20))  # режим ставки: unified (единая) / manual (ручная)
    default_bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # ставка кампании ₽ по активной зоне (для инлайн-правки в списке); из nm_settings.bids_kopecks при синке
    status: Mapped[int] = mapped_column(Integer, default=9)  # 7=completed, 9=active, 11=paused
    budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # remaining budget (rubles)
    nm_ids: Mapped[list | None] = mapped_column(JSONB, default=list)  # linked product IDs
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "campaign_id", name="uq_ad_campaign_project"),
        Index("ix_ad_campaign_project", "project_id"),
    )


class WbAdCampaignEvent(Base):
    """History of ad campaign changes: budget and status."""

    __tablename__ = "wb_ad_campaign_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # budget_change, status_change
    old_value: Mapped[str | None] = mapped_column(String(50))
    new_value: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_ad_event_project_campaign", "project_id", "campaign_id"),
        Index("ix_ad_event_created", "created_at"),
    )


class WbAdCampaignDaily(Base):
    """Daily ad stats per campaign: views, clicks, spend."""

    __tablename__ = "wb_ad_campaign_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    date: Mapped[date] = mapped_column(Date, nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    __table_args__ = (
        UniqueConstraint("project_id", "campaign_id", "date", name="uq_ad_campaign_daily"),
        Index("ix_ad_campaign_daily_project_date", "project_id", "date"),
        Index("ix_ad_campaign_daily_campaign", "project_id", "campaign_id"),
    )


class WbAdCampaignSnapshot(Base):
    """Снимки накопительных ВНУТРИДНЕВНЫХ счётчиков кампании (показы/клики/расход).

    WB НЕ отдаёт внутридневную разбивку показы/клики — самая мелкая ось у кабинетного
    get-stats = DATE (сутки); dimension HOUR → 400 (проверено вживую 2026-07-11, см.
    reference-память wb-ad-cabinet-internal-api). Поэтому копим сами: scheduler-job
    каждые ~30 мин снимает накопительный «сегодняшний» счётчик из кабинетного
    campaigns-stats (cmp.wildberries.ru, сессия wb_portal_session), а дельта между
    соседними снимками одного stat_date = показы/клики/расход за интервал. Отсюда
    строится интрадей-график «место принятия решения» (стиль mkeeper). Реконструкция
    задним числом невозможна — только накопление вперёд.

    stat_date — МСК-дата, к которой относится счётчик (WB обнуляет его в полночь МСК).
    captured_at — момент снимка (naive UTC, как у WbAdCampaignEvent.created_at; читатель
    локализует pytz.UTC → МСК, зеркало get_hourly_spend).
    """

    __tablename__ = "wb_ad_campaign_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)  # МСК-день накопления
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)  # naive UTC
    views_cum: Mapped[int] = mapped_column(Integer, default=0)  # накопительно за stat_date
    clicks_cum: Mapped[int] = mapped_column(Integer, default=0)
    spend_cum: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    __table_args__ = (
        # покрывающий индекс под выборку интервалов: (проект, кампания, день) + порядок по времени
        Index(
            "ix_ad_snapshot_campaign_captured",
            "project_id",
            "campaign_id",
            "stat_date",
            "captured_at",
        ),
    )


class WbSearchPosition(Base):
    """Снимок ОРГАНИЧЕСКОЙ позиции товара по поисковой фразе (для кластеризатора).

    Источник — публичный поиск WB (search.wb.ru/.../search): фразу ищем постранично
    (100 товаров/стр.) и находим ранг nmId. Позиция — свойство пары (товар, фраза, регион,
    момент), от кампании НЕ зависит → ключ (project_id, nm_id, phrase), переиспользуется
    между кампаниями. «Позиция» на экране = последний снимок, «Была» = предыдущий.

    position=NULL — товар не найден в пределах проверенной глубины (depth позиций).
    captured_at — момент сбора (naive UTC).
    """

    __tablename__ = "wb_search_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    phrase: Mapped[str] = mapped_column(String(300), nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)  # 1-based ранг; NULL = не найден
    depth: Mapped[int] = mapped_column(Integer, default=0)  # сколько позиций проверено (глубина)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_search_pos_lookup", "project_id", "nm_id", "phrase", "captured_at"),
    )


class WbAdNmDaily(Base):
    """Посуточная РК-статистика в разбивке по товару: кампания × nmId × дата.

    Источник — WB /adv/v3/fullstats (кампания → days → apps → nms). WB отдаёт nm-разбивку
    в том же ответе, из которого мы берём итоги кампании, поэтому таблица наполняется
    без единого дополнительного запроса.

    Копим историю у себя: WB хранит статистику ограниченное время, а нам нужна глубина.

    ВНИМАНИЕ: orders/sum_price — заказы, АТРИБУТИРОВАННЫЕ рекламе, а не все заказы товара
    (те лежат в WbFunnelDaily по всем источникам трафика). Числа не взаимозаменяемы.
    """

    __tablename__ = "wb_ad_nm_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    atbs: Mapped[int] = mapped_column(Integer, default=0)  # корзины
    orders: Mapped[int] = mapped_column(Integer, default=0)  # заказы (атрибуция рекламы)
    shks: Mapped[int] = mapped_column(Integer, default=0)  # штуки
    orders_sum: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # WB sum_price

    __table_args__ = (
        UniqueConstraint("project_id", "campaign_id", "nm_id", "date", name="uq_ad_nm_daily"),
        # Страница кампании: метрики одного товара (или всех) за период
        Index("ix_ad_nm_daily_campaign_date", "project_id", "campaign_id", "date"),
        # Аналитика по товару поверх всех кампаний
        Index("ix_ad_nm_daily_nm_date", "project_id", "nm_id", "date"),
    )


class WbAdClusterBid(Base):
    """Паспорт ТЕКУЩЕЙ пофразовой CPM-ставки: когда её применили и на основании чего.

    Одна строка на (project, кампания, товар, фраза) — состояние, не журнал. При каждом
    применении ставки апсертим: если значение изменилось — обновляем ставку и `applied_at`
    (старт таймера «сбора статистики»); та же ставка повторно — таймер НЕ сбрасываем.
    Сброс ставки к кампании (bid≤0) удаляет строку. Показания читаются рядом с get-bids: если
    сохранённая `applied_bid` совпала с текущей ставкой в WB — фраза «стоит с applied_at».

    `basis_*` — точка отсчёта на момент применения: ДРР и оплаченная CPM (якорь рекомендации,
    см. clusterEff.recAnchor) + цель ДРР. Нужны, чтобы через неделю видеть, ОТ ЧЕГО считали.
    """

    __tablename__ = "wb_ad_cluster_bid"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    norm_query: Mapped[str] = mapped_column(Text, nullable=False)
    applied_bid: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # применённая ставка, ₽
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)  # старт таймера
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # recommendation | manual
    basis_drr: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)  # ДРР на момент, %
    basis_cpm: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)  # оплач. CPM на момент, ₽
    target_drr: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)  # цель ДРР на момент, %

    __table_args__ = (
        UniqueConstraint("project_id", "campaign_id", "nm_id", "norm_query", name="uq_ad_cluster_bid"),
        # Доклейка «стоит с» при чтении кластеров кампании (все фразы товара разом)
        Index("ix_ad_cluster_bid_campaign_nm", "project_id", "campaign_id", "nm_id"),
    )


class WbCostOverride(Base):
    """Manual cost price per nmId (used if no order data available)."""

    __tablename__ = "wb_cost_override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"))
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "nm_id", name="uq_cost_override_nm"),)


class WbPrice(Base):
    """Текущая цена витрины ВБ по nm_id (синк из API «Цены и скидки»).

    Зеркало: один UPSERT-ряд на (project_id, nm_id). `price` — цена витрины
    после seller-скидки (discountedPrice, то что видит покупатель ДО СПП);
    `base_price` — цена до скидки (price); `discount` — seller-скидка %.
    СПП тут НЕ хранится — его этот эндпоинт не отдаёт (берём из воронки).
    """

    __tablename__ = "wb_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor_code: Mapped[str | None] = mapped_column(String(100))
    base_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # price (до скидки)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # discountedPrice (витрина)
    discount: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # seller-скидка %
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", name="uq_wb_price_nm"),
        Index("ix_wb_prices_project", "project_id"),
    )


class WbSppObservation(Base):
    """Точка наблюдения «наша цена → СПП → цена клиента» по nm_id за день.

    Строится из двух источников:
      * `card` — снимок публичного card-API (цена клиента сейчас) × `wb_prices`
        (наша цена витрины). Даёт точку даже без единого заказа.
      * `orders` — ретро из `wb_orders` (поштучный `spp` + `price_with_disc`),
        90 дней истории «бесплатно».

    Один ряд = (проект, nm, день, источник, уровень нашей цены): цена в течение
    дня меняется редко, а СПП внутри дня гуляет по покупателям — храним медиану
    дня, а не каждое наблюдение.
    """

    __tablename__ = "wb_spp_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    observed_on: Mapped[date] = mapped_column(Date, nullable=False)  # МСК-день
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # card | orders

    seller_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # наша цена (до СПП)
    buyer_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # что платит клиент
    spp_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)  # % = 1 − buyer/seller
    obs_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # вес точки (заказов)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "project_id", "nm_id", "observed_on", "source", "seller_price", name="uq_spp_obs_point"
        ),
        Index("ix_spp_obs_project_nm_day", "project_id", "nm_id", "observed_on"),
        Index("ix_spp_obs_project_day", "project_id", "observed_on"),
    )


class WbWarehouseStock(Base):
    """Per-warehouse stock levels from WB API supplier/stocks."""

    __tablename__ = "wb_warehouse_stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor_code: Mapped[str | None] = mapped_column(String(100))
    subject: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200))
    warehouse_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    quantity_full: Mapped[int] = mapped_column(Integer, default=0)
    in_way_to_client: Mapped[int] = mapped_column(Integer, default=0)
    in_way_from_client: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "nm_id", "warehouse_name", name="uq_wh_stock_nm_wh"),
        Index("ix_wh_stock_project", "project_id"),
    )


class WbWarehouseRemains(Base):
    """Mirror of WB analytics report «Остатки на складах» (warehouse_remains API).

    Matches the seller cabinet numbers 1:1 — unlike statistics supplier/stocks
    it INCLUDES goods being accepted and in transit between WB warehouses.
    One row per (nm_id, barcode, warehouse_name). WB returns transit rows as
    pseudo-warehouses in the same list («В пути до получателей», «В пути
    возвраты на склад WB») plus the total row «Всего находится на складах»
    (= sum of real warehouses — exclude it when summing to avoid double count).
    Full replace per project on each sync.
    """

    __tablename__ = "wb_warehouse_remains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    barcode: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    vendor_code: Mapped[str | None] = mapped_column(String(100))
    brand: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(200))
    tech_size: Mapped[str | None] = mapped_column(String(50))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))  # литраж, не деньги
    warehouse_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        # project_id-first unique покрывает и фильтры по project_id (delete/exists/join),
        # и full-replace синк — отдельный индекс по project_id избыточен и грузит write.
        UniqueConstraint("project_id", "nm_id", "barcode", "warehouse_name", name="uq_wb_remains_nm_barcode_wh"),
    )


class WbStockSnapshot(Base):
    """Historical snapshot of warehouse stock at sync time."""

    __tablename__ = "wb_stock_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    warehouse_name: Mapped[str] = mapped_column(String(200), nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor_code: Mapped[str | None] = mapped_column(String(100))
    barcode: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    in_way_to_client: Mapped[int] = mapped_column(Integer, default=0)
    in_way_from_client: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_wb_stock_snap_project_date", "project_id", "synced_at"),
        Index("ix_wb_stock_snap_nm", "project_id", "nm_id", "synced_at"),
    )


class WbAdUpd(Base):
    """История фактических затрат на рекламу (WB /adv/v1/upd).

    Каждая строка — списание по кампании: сколько и когда WB списал за показы/клики.
    В отличие от wb_ad_campaign_daily (агрегат за день из fullstats), это первичные
    документы списаний с типом оплаты (Баланс/Счёт) и статусом кампании на момент списания.

    upd_time приходит с таймзоной (+03:00 МСК) — храним в UTC-naive, как остальные даты.
    Строки без upd_time WB иногда отдаёт (документный артефакт) — их пропускаем при синке.
    """

    __tablename__ = "wb_ad_upd"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    advert_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    upd_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # момент списания (UTC)
    upd_sum: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # сумма списания, ₽
    upd_num: Mapped[int] = mapped_column(Integer, default=0)  # номер документа WB
    camp_name: Mapped[str | None] = mapped_column(String(255))
    advert_type: Mapped[int | None] = mapped_column(Integer)  # числовой тип WB
    payment_type: Mapped[str | None] = mapped_column(String(50))  # "Баланс" / "Счет"
    advert_status: Mapped[int | None] = mapped_column(Integer)  # статус кампании на момент списания
    currency: Mapped[str | None] = mapped_column(String(10))

    __table_args__ = (
        # Идемпотентность заливки периода: одно списание = (кампания, момент, документ, сумма)
        UniqueConstraint("project_id", "advert_id", "upd_time", "upd_num", "upd_sum", name="uq_ad_upd"),
        Index("ix_ad_upd_project_time", "project_id", "upd_time"),
        Index("ix_ad_upd_advert", "project_id", "advert_id", "upd_time"),
    )


class WbAdSearchDaily(Base):
    """Посуточная статистика зоны ПОИСК: кампания × товар × дата (сумма поисковых кластеров).

    Источник — WB /adv/v1/normquery/stats (статистика кластеров с детализацией по дням).
    По каждому дню суммируем все нормализованные запросы (кластеры) — это метрики зоны
    «Поиск». Зона «Рекомендации» получается вычитанием: итог кампании (wb_ad_nm_daily)
    минус поиск. WB не отдаёт зоны по дням готовыми — это единственный источник разбивки.

    Только для CPM-кампаний (у CPC поиск = вся кампания, отдельная разбивка не нужна).
    """

    __tablename__ = "wb_ad_search_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)  # WB advertId
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    atbs: Mapped[int] = mapped_column(Integer, default=0)  # корзины
    orders: Mapped[int] = mapped_column(Integer, default=0)
    shks: Mapped[int] = mapped_column(Integer, default=0)  # штуки

    __table_args__ = (
        UniqueConstraint("project_id", "campaign_id", "nm_id", "date", name="uq_ad_search_daily"),
        Index("ix_ad_search_daily_campaign_date", "project_id", "campaign_id", "date"),
    )


class WbAdPayment(Base):
    """История пополнений счёта WB Продвижение (WB /adv/v1/payments).

    Пополнения баланса рекламного кабинета: когда и на сколько был пополнен счёт,
    каким способом (type) и с каким статусом. У WB есть свой уникальный id пополнения.
    """

    __tablename__ = "wb_ad_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # id пополнения в WB
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # дата пополнения
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # сумма, ₽
    payment_type: Mapped[int | None] = mapped_column(Integer)  # способ (WB type)
    status_id: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(10))
    card_status: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        UniqueConstraint("project_id", "wb_id", name="uq_ad_payment"),
        Index("ix_ad_payment_project_date", "project_id", "paid_at"),
    )
