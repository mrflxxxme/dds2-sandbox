"""Обзор «сырых данных»: что мы тянем из внешних API и копим у себя.

Одна декларативная таблица источников (`RAW_SOURCES`) отвечает на два вопроса:
  1. Что вообще накоплено — сколько строк, за какой период (`get_sources_overview`).
  2. Как это принудительно дозагрузить — адаптер поверх уже существующих
     синков/бэкфиллов (`start_refresh`), без дублирования их логики.

Прогресс дозагрузки живёт в памяти процесса (как у ad_nm_stats): переживать
рестарт ему не нужно, при перезапуске задача просто считается неактивной.
"""

import asyncio
import logging
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Coroutine

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.models.integrations import (
    WbAdCampaign,
    WbAdCampaignDaily,
    WbAdNmDaily,
    WbAdPayment,
    WbAdSearchDaily,
    WbAdUpd,
    WbFunnelDaily,
    WbPrice,
    WbWarehouseStock,
)
from backend.models.wb_finance import WbFinanceRow
from backend.models.wb_order import WbOrder
from backend.models.wb_returns import WbGoodsReturn
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

# key → прогресс последней дозагрузки этого источника в этом проекте
_REFRESH: dict[tuple[int, str], dict[str, Any]] = {}

RefreshFn = Callable[[int, date | None, date | None], Coroutine[Any, Any, Any]]


@dataclass(frozen=True)
class RawSource:
    """Один источник сырых данных."""

    key: str
    title: str
    group: str            # раздел в UI: Реклама / Продажи / Склад / Финансы
    table: str            # имя таблицы в БД
    model: Any            # ORM-класс
    date_field: str       # колонка «за какую дату этот факт»
    description: str      # что именно копим и зачем
    source: str           # откуда: endpoint внешнего API
    schedule: str         # как часто тянет планировщик
    refresh: str | None = None      # ключ адаптера дозагрузки (None — только по расписанию)
    refresh_hint: str | None = None  # чего ждать от кнопки
    ranged: bool = False            # адаптер принимает период
    # Колонка «когда данные обновлялись у нас» для индикатора свежести, если отличается от
    # date_field (пример: у кампаний date_field=created_at — дата ПОСЛЕДНЕЙ СОЗДАННОЙ кампании,
    # а не последнего синка → свежесть краснела при живом синке)
    freshness_field: str | None = None
    # Человеческие названия колонок для просмотра содержимого; чего нет — назовём автоматически
    labels: dict[str, str] = dc_field(default_factory=dict)


# ─── Адаптеры дозагрузки ────────────────────────────────────────────────────
# Каждый сам открывает сессию: выполняется в фоне, вне HTTP-запроса.

async def _refresh_funnel(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.funnel.sync import run_funnel_sync

    d_to = date_to or utcnow().date()
    d_from = date_from or (d_to - timedelta(days=29))
    async with AsyncSessionLocal() as db:
        return await run_funnel_sync(db, project_id, d_from.isoformat(), d_to.isoformat())


async def _refresh_ad_campaigns(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.funnel.ad_campaigns_service import sync_ad_campaigns

    async with AsyncSessionLocal() as db:
        return await sync_ad_campaigns(db, project_id)


async def _refresh_ad_daily(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    """Полный ре-синк рекламной статистики по кампаниям за весь диапазон."""
    from backend.services import funnel as funnel_service

    return await funnel_service.batch_resync_ads(project_id)


async def _refresh_ad_nm(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.funnel.ad_nm_stats import run_ad_nm_backfill_bg

    return await run_ad_nm_backfill_bg(project_id, date_from, date_to)


async def _refresh_prices(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.pricing.sync import sync_wb_prices

    async with AsyncSessionLocal() as db:
        log = await sync_wb_prices(db, project_id)
        return {"rows": log.rows_fetched}


async def _refresh_stocks(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.funnel.wb_api_client import fetch_warehouse_stocks, get_wb_key
    from backend.services.stock_analytics_service import sync_warehouse_stocks

    async with AsyncSessionLocal() as db:
        api_key = await get_wb_key(db, project_id, "wb")
        if not api_key:
            raise ValueError("Не настроен API-ключ WB")
        items = await fetch_warehouse_stocks(api_key)
        return {"rows": await sync_warehouse_stocks(db, project_id, items)}


async def _refresh_orders(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.wb_orders_sync_service import sync_wb_orders

    # У WB-заказов нет произвольного окна — только «за последние N дней»
    days_back = (utcnow().date() - date_from).days if date_from else 30
    async with AsyncSessionLocal() as db:
        return await sync_wb_orders(db, project_id, days_back=max(1, min(days_back, 90)))


async def _refresh_finance(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.wb_finance_sync import sync_wb_finance

    d_to = date_to or utcnow().date()
    d_from = date_from or (d_to - timedelta(days=30))
    async with AsyncSessionLocal() as db:
        return await sync_wb_finance(db, project_id, d_from, d_to)


async def _refresh_ad_search(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    """Догрузить посуточную статистику зоны Поиск по всем CPM-кампаниям проекта.

    Батч: все (advertId,nmId) пары идут в normquery/stats пачками по 100 —
    запросов ~десяток вместо «кампания-на-запрос», обходим rate-limit WB.
    """
    from sqlalchemy import select as _select

    from backend.models.integrations import WbAdCampaign
    from backend.services.funnel.ad_search_stats import sync_ad_search_daily_bulk

    d_to = date_to or utcnow().date()
    d_from = date_from or (d_to - timedelta(days=29))
    async with AsyncSessionLocal() as db:
        cids = (await db.execute(
            _select(WbAdCampaign.campaign_id).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_type == "cpm",
            ).limit(2000)
        )).scalars().all()
        return await sync_ad_search_daily_bulk(db, project_id, list(cids), d_from.isoformat(), d_to.isoformat())


async def _refresh_ad_upd(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.funnel.ad_finance_sync import sync_ad_upd

    d_to = date_to or utcnow().date()
    d_from = date_from or (d_to - timedelta(days=29))
    async with AsyncSessionLocal() as db:
        return await sync_ad_upd(db, project_id, d_from.isoformat(), d_to.isoformat())


async def _refresh_ad_payments(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.funnel.ad_finance_sync import sync_ad_payments

    # Пополнения редки — по умолчанию тянем за год, чтобы кнопка что-то показала
    d_to = date_to or utcnow().date()
    d_from = date_from or (d_to - timedelta(days=365))
    async with AsyncSessionLocal() as db:
        return await sync_ad_payments(db, project_id, d_from.isoformat(), d_to.isoformat())


async def _refresh_returns(project_id: int, date_from: date | None, date_to: date | None) -> Any:
    from backend.services.funnel.wb_api_client import get_wb_key
    from backend.services.wb_returns_service import sync_project_returns

    async with AsyncSessionLocal() as db:
        api_key = await get_wb_key(db, project_id, "wb_analytics") or await get_wb_key(db, project_id, "wb")
        if not api_key:
            raise ValueError("Не настроен API-ключ WB")
        return await sync_project_returns(db, project_id, api_key, date_from=date_from, date_to=date_to)


REFRESH_ADAPTERS: dict[str, RefreshFn] = {
    "funnel": _refresh_funnel,
    "ad_campaigns": _refresh_ad_campaigns,
    "ad_daily": _refresh_ad_daily,
    "ad_nm": _refresh_ad_nm,
    "ad_upd": _refresh_ad_upd,
    "ad_payments": _refresh_ad_payments,
    "ad_search": _refresh_ad_search,
    "prices": _refresh_prices,
    "stocks": _refresh_stocks,
    "orders": _refresh_orders,
    "finance": _refresh_finance,
    "returns": _refresh_returns,
}


RAW_SOURCES: list[RawSource] = [
    RawSource(
        key="ad_campaigns", title="Рекламные кампании", group="Реклама",
        table="wb_ad_campaigns", model=WbAdCampaign, date_field="created_at",
        freshness_field="updated_at",
        description="Метаданные кампаний: название, тип, статус, остаток бюджета, товары.",
        source="advert-api · /api/advert/v2/adverts, /adv/v1/budget",
        schedule="Каждые 30 минут (бюджеты — каждые 10)",
        refresh="ad_campaigns", refresh_hint="Перечитать список кампаний и бюджеты",
        labels={
            "campaign_id": "ID кампании", "name": "Название", "status": "Статус",
            "campaign_type": "Оплата", "advert_type": "Тип WB", "bid_mode": "Режим ставки",
            "budget": "Остаток бюджета, ₽", "nm_ids": "Товары (nmID)",
            "created_at": "Создана в WB", "updated_at": "Обновлено у нас",
        },
    ),
    RawSource(
        key="ad_daily", title="Реклама по дням", group="Реклама",
        table="wb_ad_campaign_daily", model=WbAdCampaignDaily, date_field="date",
        description="Показы, клики и расход по кампании за каждый день.",
        source="advert-api · /adv/v3/fullstats",
        schedule="Вместе с синком воронки",
        refresh="ad_daily", refresh_hint="Полный ре-синк всей истории (долго)",
        labels={
            "campaign_id": "ID кампании", "date": "Дата", "views": "Показы",
            "clicks": "Клики", "spend": "Затраты, ₽",
        },
    ),
    RawSource(
        key="ad_nm", title="Реклама по товарам", group="Реклама",
        table="wb_ad_nm_daily", model=WbAdNmDaily, date_field="date",
        description="Та же статистика, но в разбивке кампания × товар × день. WB её вечно не хранит — копим сами.",
        source="advert-api · /adv/v3/fullstats (разбивка nms)",
        schedule="Раз в сутки, 04:20",
        refresh="ad_nm", refresh_hint="Догрузить историю окнами по 31 дню", ranged=True,
        labels={
            "campaign_id": "ID кампании", "nm_id": "Номенклатура", "date": "Дата",
            "views": "Показы", "clicks": "Клики", "spend": "Затраты, ₽",
            "atbs": "Положили в корзину", "orders": "Заказали, шт",
            "shks": "Штуки", "orders_sum": "Заказали на сумму, ₽",
        },
    ),
    RawSource(
        key="ad_search", title="Реклама: зона Поиск", group="Реклама",
        table="wb_ad_search_daily", model=WbAdSearchDaily, date_field="date",
        description="Посуточная статистика зоны «Поиск» (сумма поисковых кластеров). Нужна для разбивки метрик кампании по зонам показов.",
        source="advert-api · /adv/v1/normquery/stats",
        schedule="Раз в сутки, 04:35 (+ по кнопке)",
        refresh="ad_search", refresh_hint="Загрузить статистику поиска по CPM-кампаниям за период", ranged=True,
        labels={
            "campaign_id": "ID кампании", "nm_id": "Номенклатура", "date": "Дата",
            "views": "Показы", "clicks": "Клики", "spend": "Затраты, ₽",
            "atbs": "Корзины", "orders": "Заказы", "shks": "Штуки",
        },
    ),
    RawSource(
        key="ad_upd", title="История затрат", group="Реклама",
        table="wb_ad_upd", model=WbAdUpd, date_field="upd_time",
        description="Первичные списания за рекламу: сколько и когда WB списал по каждой кампании, каким типом оплаты.",
        source="advert-api · /adv/v1/upd",
        schedule="Раз в сутки, 04:25 (+ по кнопке)",
        refresh="ad_upd", refresh_hint="Загрузить историю затрат за период", ranged=True,
        labels={
            "advert_id": "ID кампании", "camp_name": "Кампания", "upd_time": "Время списания",
            "upd_sum": "Сумма, ₽", "upd_num": "Номер документа", "advert_type": "Тип WB",
            "payment_type": "Тип оплаты", "advert_status": "Статус кампании", "currency": "Валюта",
        },
    ),
    RawSource(
        key="ad_payments", title="Пополнения счёта", group="Реклама",
        table="wb_ad_payments", model=WbAdPayment, date_field="paid_at",
        description="История пополнений счёта WB Продвижение: когда, на сколько и каким способом.",
        source="advert-api · /adv/v1/payments",
        schedule="Раз в сутки, 04:45 (+ по кнопке)",
        refresh="ad_payments", refresh_hint="Загрузить историю пополнений за период", ranged=True,
        labels={
            "wb_id": "ID пополнения", "paid_at": "Дата", "amount": "Сумма, ₽",
            "payment_type": "Способ", "status_id": "Статус", "currency": "Валюта", "card_status": "Статус карты",
        },
    ),
    RawSource(
        key="funnel", title="Воронка продаж", group="Продажи",
        table="wb_funnel_daily", model=WbFunnelDaily, date_field="date",
        description="Суточная воронка по товару: переходы, корзины, заказы, выкупы, остатки.",
        source="seller-analytics-api · /api/analytics/v3/sales-funnel/products",
        schedule="Ежечасно (последние 2 дня) + ночью за прошлые",
        refresh="funnel", refresh_hint="Перезалить воронку за период", ranged=True,
        labels={
            "date": "Дата", "nm_id": "Номенклатура", "vendor_code": "Артикул продавца",
            "subject": "Предмет", "brand": "Бренд",
            "open_card": "Переходы в карточку", "add_to_cart": "Положили в корзину",
            "orders_count": "Заказали, шт", "orders_sum_rub": "Заказали на сумму, ₽",
            "buyout_percent": "Процент выкупа", "cart_to_order_pct": "Конверсия в заказ, %",
            "add_to_cart_pct": "Конверсия в корзину, %", "avg_price": "Средняя цена, ₽",
            "stocks_wb": "Остаток на WB", "stocks_mp": "Остаток на складе продавца",
            "adv_views": "Показы рекламы", "adv_clicks": "Клики по рекламе", "adv_sum": "Затраты на рекламу, ₽",
            "cost_price": "Себестоимость, ₽", "localization_percent": "Локализация, %",
            "time_to_ready_minutes": "Время до готовности, мин",
        },
    ),
    RawSource(
        key="orders", title="Заказы", group="Продажи",
        table="wb_orders", model=WbOrder, date_field="order_date",
        freshness_field="synced_at",  # заказы всегда «по вчера» (cutoff) — свежесть по времени синка
        description="Заказы поштучно, с городом и складом. Основа индекса локализации.",
        source="statistics-api · /api/v1/supplier/orders",
        schedule="3 раза в сутки",
        refresh="orders", refresh_hint="Перечитать заказы за период", ranged=True,
        labels={
            "srid": "ID заказа (srid)", "g_number": "Номер заказа", "sticker": "Стикер",
            "income_id": "Поставка", "order_date": "Дата заказа", "last_change_date": "Изменён",
            "nm_id": "Артикул WB", "barcode": "Баркод", "vendor_code": "Артикул продавца",
            "subject": "Предмет", "brand": "Бренд", "category": "Категория", "tech_size": "Размер",
            "warehouse_name": "Склад", "warehouse_type": "Тип склада",
            "country_name": "Страна", "oblast_okrug_name": "Округ", "region_name": "Регион",
            "total_price": "Цена до скидок, ₽", "discount_percent": "Скидка, %", "spp": "СПП, %",
            "finished_price": "Цена покупателя, ₽", "price_with_disc": "Цена со скидкой, ₽",
            "is_cancel": "Отменён", "cancel_date": "Дата отмены",
            "is_supply": "Догрузка", "is_realization": "Реализация", "synced_at": "Синк у нас",
        },
    ),
    RawSource(
        key="prices", title="Цены витрины", group="Продажи",
        table="wb_prices", model=WbPrice, date_field="synced_at",
        description="Текущая цена и скидка по каждому товару — зеркало витрины.",
        source="discounts-prices-api",
        schedule="Дважды в сутки",
        refresh="prices", refresh_hint="Перечитать цены сейчас",
    ),
    RawSource(
        key="returns", title="Возвраты", group="Продажи",
        table="wb_goods_returns", model=WbGoodsReturn, date_field="order_dt",
        freshness_field="synced_at",  # дата возврата отстаёт от синка — свежесть по времени синка
        description="Возвраты покупателей на ПВЗ.",
        source="seller-analytics-api · /api/v1/analytics/goods-return",
        schedule="Дважды в сутки",
        refresh="returns", refresh_hint="Перечитать возвраты за период", ranged=True,
    ),
    RawSource(
        key="stocks", title="Остатки на складах WB", group="Склад",
        table="wb_warehouse_stocks", model=WbWarehouseStock, date_field="updated_at",
        description="Текущий остаток по каждому товару и складу WB.",
        source="statistics-api · /api/v1/supplier/stocks",
        schedule="Ежедневно + автоматически, если данные старше часа",
        refresh="stocks", refresh_hint="Перечитать остатки сейчас",
    ),
    RawSource(
        key="finance", title="Финансовый отчёт", group="Финансы",
        table="wb_finance_rows", model=WbFinanceRow, date_field="rr_dt",
        description="Строки отчёта о реализации: выручка, комиссия, логистика, штрафы.",
        source="statistics-api · /api/v5/supplier/report",
        schedule="Еженедельно + ежедневный догон",
        refresh="finance", refresh_hint="Перезалить отчёт за период", ranged=True,
    ),
]

SOURCES_BY_KEY: dict[str, RawSource] = {s.key: s for s in RAW_SOURCES}


def _progress_key(project_id: int, key: str) -> tuple[int, str]:
    return (project_id, key)


def get_refresh_progress(project_id: int) -> dict[str, dict[str, Any]]:
    """Прогресс дозагрузок этого проекта: {source_key: {...}}."""
    return {k[1]: v for k, v in _REFRESH.items() if k[0] == project_id}


# Технические сообщения адаптеров/WB → человекочитаемые для UI дозагрузки
_KEY_MISSING_MARKERS = ("no wb api key", "api key not found", "api ключ wb не найден", "no_api_key")


def _humanize_refresh_error(msg: str) -> str:
    if any(m in msg.lower() for m in _KEY_MISSING_MARKERS):
        return "Нет активного WB-ключа с нужным доступом (напр. «Статистика») — проверьте интеграции проекта"
    return msg


def _adapter_error(result: Any) -> str | None:
    """Ошибка, которую адаптер вернул СЛОВАРЁМ (без исключения). None — успех.

    Адаптеры сигналят по-разному: часть кладёт status='error' (+errors[]/error),
    sync_ad_campaigns — error='no_api_key', заказы — errors[] без status.
    ВАЖНО: при status='ok' непустой errors[] = частичные сбои (напр. funnel
    отдаёт errors[:5]) — весь refresh НЕ роняем. Без status: errors[] считаем
    провалом только если ничего не залито и не получено.
    """
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    if status == "error":
        errs = result.get("errors") or []
        raw = result.get("error") or (errs[0] if errs else None) or "источник вернул ошибку"
        return _humanize_refresh_error(str(raw))
    if status == "ok":
        return None
    if result.get("error"):
        return _humanize_refresh_error(str(result["error"]))
    errs = result.get("errors")
    if errs:
        touched = (result.get("inserted") or 0) + (result.get("updated") or 0) + (result.get("rows") or 0)
        if not touched and not result.get("total_fetched"):
            return _humanize_refresh_error(str(errs[0]))
    return None


async def _run_refresh(project_id: int, key: str, date_from: date | None, date_to: date | None) -> None:
    pk = _progress_key(project_id, key)
    _REFRESH[pk] = {"status": "running", "started_at": utcnow().isoformat(), "finished_at": None, "error": None}
    try:
        result = await REFRESH_ADAPTERS[key](project_id, date_from, date_to)
        # Адаптер мог не бросить исключение, но вернуть ошибку словарём (напр. нет
        # WB-ключа с нужным scope) — иначе UI показывал бы «загружено» при пустой заливке
        adapter_err = _adapter_error(result)
        _REFRESH[pk] = {
            "status": "error" if adapter_err else "ok",
            "started_at": _REFRESH[pk]["started_at"],
            "finished_at": utcnow().isoformat(), "error": adapter_err,
            "result": result if isinstance(result, dict) else None,
        }
        if adapter_err:
            logger.warning("Raw refresh %s for project %s returned error: %s", key, project_id, adapter_err)
        else:
            logger.info("Raw refresh %s done for project %s", key, project_id)
    except asyncio.CancelledError:
        _REFRESH[pk] = {**_REFRESH[pk], "status": "error", "finished_at": utcnow().isoformat(), "error": "Отменено"}
        raise
    except Exception as e:
        logger.warning("Raw refresh %s failed for project %s: %s", key, project_id, e)
        _REFRESH[pk] = {
            "status": "error", "started_at": _REFRESH[pk]["started_at"],
            "finished_at": utcnow().isoformat(), "error": str(e)[:300],
        }


async def start_refresh(project_id: int, key: str, date_from: date | None, date_to: date | None) -> dict:
    """Запустить принудительную дозагрузку источника в фоне."""
    src = SOURCES_BY_KEY.get(key)
    if src is None or src.refresh is None:
        return {"status": "unsupported", "error": "Для этого источника дозагрузка недоступна"}
    if get_refresh_progress(project_id).get(key, {}).get("status") == "running":
        return {"status": "already_running"}

    # Период имеет смысл не для всех источников — у «цен»/«остатков» это срез «сейчас»
    if not src.ranged:
        date_from = date_to = None

    asyncio.create_task(_run_refresh(project_id, key, date_from, date_to))  # noqa: RUF006
    return {"status": "started"}


async def _source_stats(db: AsyncSession, project_id: int, src: RawSource) -> dict[str, Any]:
    """Сколько строк накоплено и за какой период."""
    col = getattr(src.model, src.date_field)
    fresh_col = getattr(src.model, src.freshness_field) if src.freshness_field else None
    selects = [func.count(), func.min(col), func.max(col)]
    if fresh_col is not None:
        selects.append(func.max(fresh_col))
    row = (
        await db.execute(select(*selects).where(src.model.project_id == project_id))
    ).one()
    rows, first, last = int(row[0]), row[1], row[2]
    fresh = row[3] if fresh_col is not None else last
    return {
        "rows": rows,
        "first_date": first.isoformat() if isinstance(first, (date, datetime)) else None,
        "last_date": last.isoformat() if isinstance(last, (date, datetime)) else None,
        "fresh_at": fresh.isoformat() if isinstance(fresh, (date, datetime)) else None,
    }


# Служебные колонки не показываем: id — суррогатный ключ, project_id — и так фиксирован
HIDDEN_COLUMNS = {"id", "project_id"}
MAX_ROWS_PER_PAGE = 200


def _column_type(col: Any) -> str:
    """Тип для фронта: как форматировать и выравнивать значение.

    Идентификаторы (campaign_id, nm_id, srid) — числа по типу колонки, но разделители
    разрядов в них бессмысленны: «37 158 056» не читается как ID. Отдаём их типом "id".
    """
    if col.name.endswith("_id") or col.name in {"srid", "nm_ids"}:
        return "json" if col.name.endswith("s") else "id"

    py = getattr(col.type, "python_type", None)
    try:
        t = py if py is None else py()
    except Exception:
        t = None
    name = col.type.__class__.__name__.lower()
    if "date" in name and "time" not in name:
        return "date"
    if "datetime" in name or "timestamp" in name:
        return "datetime"
    if "bool" in name:
        return "bool"
    if "numeric" in name or "float" in name or "integer" in name or "bigint" in name:
        return "number"
    if isinstance(t, (list, dict)) or "json" in name:
        return "json"
    return "string"


def _auto_label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def source_columns(src: RawSource) -> list[dict[str, str]]:
    """Колонки таблицы источника — прямо из ORM-модели."""
    return [
        {"key": c.name, "label": src.labels.get(c.name) or _auto_label(c.name), "type": _column_type(c)}
        for c in src.model.__table__.columns
        if c.name not in HIDDEN_COLUMNS
    ]


def _serialize(value: Any) -> Any:
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


async def get_source_rows(
    db: AsyncSession, project_id: int, key: str,
    limit: int = 50, offset: int = 0,
    date_from: date | None = None, date_to: date | None = None,
    sort_by: str | None = None, sort_dir: str = "desc",
) -> dict:
    """Содержимое таблицы источника: страница строк + общее число под текущим фильтром."""
    src = SOURCES_BY_KEY.get(key)
    if src is None:
        raise ValueError(f"Неизвестный источник: {key}")

    columns = source_columns(src)
    limit = max(1, min(limit, MAX_ROWS_PER_PAGE))

    conds = [src.model.project_id == project_id]
    date_col = getattr(src.model, src.date_field)
    is_datetime = _column_type(src.model.__table__.columns[src.date_field]) == "datetime"
    if date_from:
        conds.append(date_col >= date_from)
    if date_to:
        # У datetime-колонки «по 10 июля» обязано включать весь день, а не только полночь
        conds.append(date_col < date_to + timedelta(days=1) if is_datetime else date_col <= date_to)

    # Сортировка только по существующей колонке — имя в SQL не подставляем из ввода
    allowed = {c["key"] for c in columns}
    sort_col = getattr(src.model, sort_by) if sort_by in allowed else date_col
    order = sort_col.desc() if sort_dir != "asc" else sort_col.asc()

    total = (await db.execute(select(func.count()).select_from(src.model).where(*conds))).scalar_one()
    result = await db.execute(select(src.model).where(*conds).order_by(order).limit(limit).offset(max(0, offset)))
    rows = [
        {c["key"]: _serialize(getattr(obj, c["key"])) for c in columns}
        for obj in result.scalars().all()
    ]
    return {
        "key": src.key, "title": src.title, "table": src.table, "date_field": src.date_field,
        "description": src.description, "source": src.source,
        "columns": columns, "rows": rows, "total": int(total),
        "limit": limit, "offset": max(0, offset),
    }


async def get_sources_overview(db: AsyncSession, project_id: int) -> dict:
    """Все источники сырых данных с накопленными объёмами и прогрессом дозагрузки."""
    progress = get_refresh_progress(project_id)
    sources = []
    for src in RAW_SOURCES:
        try:
            stats = await _source_stats(db, project_id, src)
        except Exception as e:  # таблица есть, но что-то не так — не роняем всю страницу
            logger.warning("Raw stats failed for %s: %s", src.key, e)
            stats = {"rows": None, "first_date": None, "last_date": None, "fresh_at": None}
        sources.append({
            "key": src.key, "title": src.title, "group": src.group, "table": src.table,
            "date_field": src.date_field, "description": src.description,
            "source": src.source, "schedule": src.schedule,
            "refreshable": src.refresh is not None, "refresh_hint": src.refresh_hint,
            "ranged": src.ranged,
            **stats,
            "progress": progress.get(src.key),
        })
    return {"sources": sources, "groups": list(dict.fromkeys(s.group for s in RAW_SOURCES))}
