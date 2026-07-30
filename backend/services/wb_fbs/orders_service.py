# ruff: noqa: RUF001, RUF002, RUF003
"""
Service: WB FBS — сборочные задания (заказы со склада продавца).

Зеркало Marketplace API v3: `GET /api/v3/orders/new` + `GET /api/v3/orders`
(период) → upsert по `(project_id, wb_order_id)`, `POST /api/v3/orders/status`
→ досинк статусов, стикеры, отмена и списание проданного из нашего
документного ledger'а.

Инварианты домена:
  • Одно задание WB = ОДНА единица товара (WB не агрегирует количество),
    поэтому списание всегда −1 на задание.
  • `GET /orders/new` отдаёт ТОЛЬКО задания, ещё не положенные в поставку:
    уехавшее в поставку исчезает оттуда навсегда. Одного этого метода мало —
    зеркало не видит ни истории, ни заданий, собранных между двумя опросами.
    Дыру закрывает периодный `GET /orders` (`backfill_orders_history` разово +
    `sync_orders_recent` в фоне).
  • 🔴 В payload'е периодного метода НЕТ `supplierStatus`, и всё, что он отдаёт,
    легло бы как `new` → `complete` → списание со склада продаж прошлых
    месяцев. Поэтому задания СТАРШЕ cutoff'а (момента, с которого домен вообще
    начал видеть заказы) вставляются сразу с `written_off_at`: физически они
    отгружены давно, и штатный `writeoff_completed_orders` их не тронет.
  • Цены приходят от WB В КОПЕЙКАХ (×100) — делим на 100 и кладём в Numeric(18,2).
  • `createdAt` — RFC3339 со смещением, а колонки БД naive → приводим к UTC
    и снимаем tzinfo (иначе asyncpg роняет TIMESTAMP WITHOUT TIME ZONE).
  • `supplier_status` двигают ТОЛЬКО WB-статусы и методы поставок: синк новых
    заданий не перетирает статус уже известного задания (иначе `complete`
    откатился бы в `new` и товар списался бы повторно).
  • Списание в ledger строго идемпотентно по `written_off_at`, не уводит
    остаток в минус и пишет `StockMovement` типа OUTBOUND.
  • Журнал переходов (`wb_fbs_order_events`): каждая мутация осей
    `supplier_status`/`wb_status` фиксирует переход В МОМЕНТ ОБНАРУЖЕНИЯ
    (дифф до/после), повторный синк без изменений событий не плодит — из
    журнала + точных дат строится таймлайн задания (`get_order_timeline`).
  • Зеркало заданий скоуплено по КОНТУРУ (`services/wb_fbs/contour.py`): задания
    песочницы помечаются в `raw._dds_contour` и не участвуют ни в списании из
    ledger'а, ни в вычетах остатка боевого контура. Гейт режима закрывает только
    запись В WB — запись в наши таблицы не гейтит ничто, а тестовое задание,
    попавшее в общее зеркало, списывало бы РЕАЛЬНЫЙ склад.
  • Транзакцию БД не держим через внешний HTTP (`await db.commit()` до похода
    в WB) — иначе серверные коннекты pgbouncer виснут `idle in transaction`.
"""

import logging
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, case, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.integrations.wb_fbs_api import WbFbsApiError, WbFbsClient
from backend.models import (
    FBS_IN_DELIVERY_STATUS,
    FBS_SORTED_STATUS,
    FBS_TERMINAL_STATUSES,
    FBS_WB_CANCELLED_STATUSES,
    FBS_WB_PRE_SORT_STATUSES,
    FBS_WB_SORTED_STATUSES,
    FbsSupplierStatus,
    Nomenclature,
    WbFbsOrder,
    WbFbsSupply,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.models.fulfillment import FulfillmentStock
from backend.models.warehouse import MovementType, StockMovement, Warehouse, WarehouseStock

from backend.models.wb_fbs import FBS_IN_DELIVERY_STUCK_STATUS, WbFbsOrderEvent
from backend.services.warehouse_stock_engine import _update_stock
from backend.services.wb_fbs.client_factory import get_fbs_client
from backend.services.wb_fbs.contour import contour_condition, is_sandbox_contour, stamp_contour
from backend.services.wb_fbs.locks import (
    WRITEOFF_LOCK_NAME,
    WRITEOFF_LOCK_TTL_SEC,
    acquire_lock,
    release_lock,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.wb_fbs.orders")

# ─── Константы ──────────────────────────────────────────────────────────────

#: Строк в одном multi-VALUES INSERT. 32 колонки × 500 ≈ 16k параметров —
#: с запасом под лимит asyncpg в 32767 параметров на statement.
_UPSERT_CHUNK = 500
#: WB принимает до 1000 id за один `POST /orders/status`.
_STATUS_CHUNK = 1000
#: Потолок заданий, опрашиваемых за один прогон синка статусов.
_STATUS_MAX_ORDERS = 10_000
#: WB печатает максимум 100 стикеров за запрос.
_STICKER_MAX = 100
_LIST_MAX_LIMIT = 500
#: Потолок заданий, списываемых за один прогон (хвост уедет следующим).
_WRITEOFF_MAX_ORDERS = 2000
_NOM_LOOKUP_LIMIT = 20_000

#: `GET /api/v3/orders` принимает окно НЕ БОЛЬШЕ 30 дней за запрос.
_ORDERS_WINDOW_DAYS = 30
#: Глубина истории у WB — 3 месяца; просить больше нечего.
_ORDERS_MAX_DEPTH_DAYS = 90
#: Дефолт регулярного догона: перекрывает любой разумный простой воркера.
_RECENT_DEFAULT_DAYS = 2
#: Страховка от бесконечной пагинации одного окна (1000 заданий на страницу).
_ORDERS_MAX_PAGES = 50

#: `reference_type` движения склада для FBS-продажи (String(30)).
#: ⚠️ Значение продублировано в `fulfillment_service._FBS_WRITEOFF_REF_TYPE`
#: (прямой импорт оттуда дал бы цикл: wb_fbs.stock_service уже импортирует
#: fulfillment_service) — равенство держит тест-связка в
#: tests/test_fulfillment_service.py.
_WRITEOFF_REF_TYPE = "FBS_ORDER"

#: «Зависло в пути на СЦ»: задание передано не меньше этого числа дней назад,
#: а сортировочный центр так и не принял (`in_delivery_condition`).
_TRANSIT_STUCK_DAYS = 2
#: Потолок окна «зависших»: у старых `complete` `wb_status` застывает — синк
#: статусов опрашивает не больше `_STATUS_MAX_ORDERS` НЕ-терминальных заданий
#: (см. docstring `warehouse_summary`), и без потолка счётчик копил бы мёртвые
#: строки, которые WB давно довёз, а зеркало об этом не узнало.
_TRANSIT_STUCK_MAX_DAYS = 30

#: Cap строк ответа `writeoff_issues` (агрегатов по товару); полный масштаб
#: проблемы виден по `total_orders`, который считается ДО среза.
_WRITEOFF_ISSUES_MAX_ROWS = 200

#: Причины незакрытого списания (`FbsWriteoffIssueRow.reason`), в порядке
#: приоритета классификации: нет карточки → нет привязки склада → нет остатка.
#: `queued` — пост-классификация ПОСЛЕ обогащения остатками: остатка хватает
#: на всё, задание просто ждёт ближайшего 5-минутного прогона списания.
#: Набор обязан совпадать со `schemas.wb_fbs.ALLOWED_WRITEOFF_REASONS`
#: (контракт с фронтом) — равенство держит тест-связка в
#: tests/test_wb_fbs_writeoff_issues.py::test_reason_codes_match_schema_contract.
_WRITEOFF_ISSUE_NO_CARD = "no_card"
_WRITEOFF_ISSUE_NO_LINK = "no_link"
_WRITEOFF_ISSUE_NO_STOCK = "no_stock"
_WRITEOFF_ISSUE_QUEUED = "queued"

#: ISO-4217 рубля. WB отдаёт код числом, колонка — String(8).
RUB_CURRENCY_CODE = "643"


def effective_status_expr() -> Any:
    """Статус задания с учётом ОБЕИХ осей WB — единственный источник для счётчиков.

    `supplierStatus` описывает действия продавца, `wbStatus` — судьбу заказа у WB,
    и они расходятся: покупатель отказался до сборки → `supplierStatus` навсегда
    `new`, `wbStatus` = `declined_by_client`. Голый `supplier_status` в таком
    случае врёт во все стороны сразу (см. `FBS_WB_CANCELLED_STATUSES`).

    Схлопываем WB-отмену в `cancel`: задание разом уходит из «Новых», приходит во
    вкладку «Отменено», перестаёт держать остаток и выпадает из опроса статусов.
    Сам `supplier_status` НЕ переписываем — это поле WB, и зеркало обязано
    оставаться верным источнику; расхождение видно в колонке «Статус WB».
    """
    return case(
        (
            and_(
                WbFbsOrder.wb_status.in_(FBS_WB_CANCELLED_STATUSES),
                WbFbsOrder.supplier_status.notin_(FBS_TERMINAL_STATUSES),
            ),
            FbsSupplierStatus.CANCEL.value,
        ),
        else_=WbFbsOrder.supplier_status,
    )


def alive_condition() -> Any:
    """«Задание ещё живое»: не отменено ни продавцом, ни на стороне WB."""
    return effective_status_expr().notin_(FBS_TERMINAL_STATUSES)


def in_delivery_condition() -> Any:
    """«Задание ЕЩЁ едет к СЦ и ещё НЕ отсортировано» — первая фаза после передачи.

    Одного `supplierStatus` тут мало: он застывает на `complete` в момент
    передачи поставки и остаётся таким навсегда — и через день, и через месяц
    после вручения. Ответ «что сейчас в пути» знает только `wbStatus`.

    🔴 БЕЛЫЙ список, не чёрный: `complete` И `wbStatus` ∈ до-сортировочным
    (`FBS_WB_PRE_SORT_STATUSES`) ЛИБО пуст. Прежний чёрный список «complete
    минус sorted минус sold/defect» возвращал любой НЕИЗВЕСТНЫЙ пост-сортировочный
    статус обратно в «едет к СЦ» — 168 заказов в `ready_for_pickup` (лежат в
    ПВЗ) через 2 дня зажигали «зависло» (прод 30.07.2026). У этой фазы (и у
    алярма `in_delivery_stuck` поверх неё) ложный пропуск дешевле ложной
    тревоги, поэтому новый статус WB по умолчанию НЕ считается «в пути».

    Отменённые сюда не попадают по построению: `effective_status_expr()` уже
    схлопнул WB-отмену в `cancel`, даже если продавец успел передать поставку.

    Пустой/NULL `wb_status` считаем «в пути»: так выглядят задания, записанные
    до появления колонки (канон домена), и их честнее показать в очереди, чем
    молча потерять.
    """
    return and_(
        effective_status_expr() == FbsSupplierStatus.COMPLETE.value,
        or_(
            WbFbsOrder.wb_status.is_(None),
            WbFbsOrder.wb_status == "",
            WbFbsOrder.wb_status.in_(FBS_WB_PRE_SORT_STATUSES),
        ),
    )


def sorted_condition() -> Any:
    """«Принято сортировочным центром WB» — вторая фаза, ещё не у покупателя.

    `FBS_WB_SORTED_STATUSES` включает и пост-сортировочные `ready_for_pickup` /
    `postponed_delivery`: заказ прошёл СЦ, дальше — зона логистики WB.
    """
    return and_(
        effective_status_expr() == FbsSupplierStatus.COMPLETE.value,
        WbFbsOrder.wb_status.in_(FBS_WB_SORTED_STATUSES),
    )


def transit_anchor_expr() -> Any:
    """Момент ПЕРЕДАЧИ задания в WB — точка отсчёта «сколько дней едет».

    Точнее всего его знает поставка: `scan_dt` (WB отсканировал QR — груз
    физически уехал) → `closed_at` (поставку закрыли кнопкой «Передать»).
    Фолбэк — `written_off_at` самого задания: списание из ledger'а происходит
    в момент передачи, поэтому по строкам без зеркала поставки (старые данные,
    поставка не синкнулась) метка списания — честное приближение.

    Требует LEFT OUTER JOIN на `WbFbsSupply` по `supply_id` в самом запросе.
    """
    return func.coalesce(WbFbsSupply.scan_dt, WbFbsSupply.closed_at, WbFbsOrder.written_off_at)


def _supply_join_condition(project_id: int) -> Any:
    """Условие LEFT OUTER JOIN зеркала поставок к заданиям (1:1 максимум).

    Скоуп по контуру — как у всех выборок поставок домена (`_get_supply`,
    `list_supplies`): якорь передачи боевого задания не должен браться из
    одноимённой поставки песочницы.
    """
    return and_(
        WbFbsSupply.project_id == project_id,
        WbFbsSupply.wb_supply_id == WbFbsOrder.supply_id,
        contour_condition(WbFbsSupply.raw),
    )


def in_delivery_stuck_condition(now: datetime) -> Any:
    """«Зависло в пути на СЦ»: передано давно, а сортировочный центр не принял.

    Подмножество `in_delivery_condition()` с окном по якорю передачи
    (`transit_anchor_expr`): якорь не свежее `_TRANSIT_STUCK_DAYS` дней (моложе —
    штатно едет) и не старше `_TRANSIT_STUCK_MAX_DAYS` (старше — почти наверняка
    застывший `wb_status`, а не живой груз; см. комментарий у констант).
    Задание без якоря (нет ни поставки, ни `written_off_at`) зависшим не
    считается: точку отсчёта взять неоткуда.

    Запрос обязан включать LEFT OUTER JOIN на `WbFbsSupply` (см.
    `_supply_join_condition`).
    """
    anchor = transit_anchor_expr()
    return and_(
        in_delivery_condition(),
        anchor >= now - timedelta(days=_TRANSIT_STUCK_MAX_DAYS),
        anchor <= now - timedelta(days=_TRANSIT_STUCK_DAYS),
    )


def revenue_rub_expr() -> Any:
    """Выручка задания В РУБЛЯХ — единственная валютно-корректная формула домена.

    🔴 `price` и `salePrice` приходят в валюте ПРОДАЖИ, а не продавца: WB торгует
    и в СНГ, поэтому в зеркале лежат заказы в сумах (860), тенге (398), белорусских
    рублях (933), драмах (51) и сомах (417). Складывать их с рублёвыми строками
    нельзя — узбекский заказ на 2 595 300 сумов (≈16.8 k ₽) прибавлял к выручке
    2 595 300 ₽ и один такой заказ перевешивал весь остальной период (прод 26.07:
    диваны показали 5.5 M ₽ вместо ~0.55 M).

    Пересчёт WB отдаёт сам — `convertedPrice` в `convertedCurrencyCode` (у нас
    всегда 643). Поэтому:
      • рубль (или пустой код у строк, записанных до появления валют) — прежняя
        семантика `salePrice` → `price`: `salePrice` учитывает скидку покупателя,
        а `convertedPrice` для рубля равен `price` бит-в-бит;
      • иная валюта — ТОЛЬКО `convertedPrice`; если WB его не прислал, строка даёт
        ноль. Занизить выручку лучше, чем сложить сумы с рублями.
    """
    is_rub = or_(
        WbFbsOrder.currency_code.is_(None),
        WbFbsOrder.currency_code == RUB_CURRENCY_CODE,
    )
    return case(
        (is_rub, func.coalesce(WbFbsOrder.sale_price, WbFbsOrder.price, 0)),
        else_=func.coalesce(WbFbsOrder.converted_price, 0),
    )

#: Префикс кэша чтения (зарегистрирован в `invalidate_project_reports`).
#: В самом декораторе префикс обязан стоять СТРОКОВЫМ литералом — гейт
#: `tests/test_conventions_sync.py` ищет его регуляркой по `@cached(prefix="…"`.
CACHE_ORDERS = "fbs:orders"

#: Стикер WB отдаёт только для заданий, уже лежащих в поставке.
_STICKER_STATUSES: tuple[str, ...] = (
    FbsSupplierStatus.CONFIRM.value,
    FbsSupplierStatus.COMPLETE.value,
)

_VALID_SUPPLIER_STATUSES: frozenset[str] = frozenset(s.value for s in FbsSupplierStatus)
_ALLOWED_STICKER_TYPES: frozenset[str] = frozenset({"svg", "zplv", "zplh", "png"})


class FbsOrderError(Exception):
    """Доменная ошибка сборочных заданий — роутер отдаёт её как 400."""


# ─── Журнал переходов статусов (`wb_fbs_order_events`) ──────────────────────
#
# WB истории статусов не отдаёт (только текущие значения), поэтому каждая
# мутация осей `supplier_status` / `wb_status` в зеркале фиксирует переход
# В МОМЕНТ ОБНАРУЖЕНИЯ: дифф «что было в строке» → «что реально записали».
# Идемпотентность бесплатна: old == new → ноль строк журнала.

#: Оси журнала — значения колонки `wb_fbs_order_events.axis`.
EVENT_AXIS_SUPPLIER = "supplier_status"
EVENT_AXIS_WB = "wb_status"

#: Потолок строк журнала в таймлайне (страховка от разросшейся истории).
_TIMELINE_EVENTS_MAX = 200
#: Строк в одном multi-VALUES INSERT событий: 7 колонок × 1000 = 7k параметров —
#: с запасом под лимит asyncpg 32767.
_EVENT_INSERT_CHUNK = 1000


async def order_status_snapshot(
    db: AsyncSession, project_id: int, wb_order_ids: Sequence[int]
) -> dict[int, tuple[int, str, str | None]]:
    """{wb_order_id: (pk, supplier_status, wb_status)} — снимок ДО перезаписи.

    База диффа журнала (паттерн `fulfillment_service._apply_requests`): берётся
    перед мутацией, после неё сравнивается с реально записанными значениями.
    Без учёта контура: ключ `(project_id, wb_order_id)` уникален, а событие
    принадлежит той же строке, которую мутация и перепишет.
    """
    snapshot: dict[int, tuple[int, str, str | None]] = {}
    ids = [oid for oid in wb_order_ids if oid]
    for chunk in _chunks(ids, _STATUS_CHUNK):
        result = await db.execute(
            select(
                WbFbsOrder.wb_order_id,
                WbFbsOrder.id,
                WbFbsOrder.supplier_status,
                WbFbsOrder.wb_status,
            ).where(
                WbFbsOrder.project_id == project_id,
                WbFbsOrder.wb_order_id.in_(chunk),
            )
        )
        for wb_order_id, pk, sup, wb in result.all():
            snapshot[int(wb_order_id)] = (int(pk), sup, wb)
    return snapshot


def _axis_transitions(
    old_sup: str,
    old_wb: str | None,
    new_sup: str | None,
    new_wb: str | None,
) -> list[tuple[str, str | None, str]]:
    """Реально сменившиеся оси: [(axis, old, new)].

    `None`/пустое НОВОЕ значение переходом не считается (`new_value` в журнале
    NOT NULL): «ось не пришла» и «ось очистили» неразличимы в payload'ах WB,
    и молчание не должно рождать строк.
    """
    out: list[tuple[str, str | None, str]] = []
    if new_sup and new_sup != old_sup:
        out.append((EVENT_AXIS_SUPPLIER, old_sup, new_sup))
    if new_wb and new_wb != (old_wb or None):
        out.append((EVENT_AXIS_WB, old_wb, new_wb))
    return out


async def record_order_events(db: AsyncSession, project_id: int, events: list[dict[str, Any]]) -> None:
    """Bulk-вставка строк журнала. Без commit — транзакцией управляет вызывающий.

    Ключи события: `order_id` (PK задания, не wb_order_id!), `axis`,
    `old_value`, `new_value`, `changed_at`. `project_id` проставляется здесь.
    """
    if not events:
        return
    rows = [{"project_id": project_id, **e} for e in events]
    for chunk in _chunks(rows, _EVENT_INSERT_CHUNK):
        await db.execute(pg_insert(WbFbsOrderEvent).values(chunk))


# ─── Нормализация payload'а WB ──────────────────────────────────────────────


def _chunks(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _int_or_none(value: Any) -> int | None:
    """WB иногда шлёт числа строками; мусор не должен ронять синк."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _str_or_none(value: Any, max_len: int) -> str | None:
    """Строка с обрезкой под ширину колонки — длинный ответ не валит транзакцию."""
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    return text_value[:max_len]


def _parse_wb_datetime(value: Any) -> datetime | None:
    """RFC3339 (`2026-07-24T12:00:00+03:00`) → naive UTC под DateTime-колонку."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_wb_date(value: Any) -> date | None:
    """`ddate` приходит как `YYYY-MM-DD` (или полноценный RFC3339)."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if "T" in raw:
        parsed = _parse_wb_datetime(raw)
        return parsed.date() if parsed else None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


def _kopecks_to_rub(value: Any) -> Decimal | None:
    """Цены WB — В КОПЕЙКАХ: 137900 → Decimal('1379.00')."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return (Decimal(str(value)) / Decimal(100)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _first_sku(raw: dict) -> str | None:
    """`skus` — список баркодов; для FBS всегда один значимый."""
    skus = raw.get("skus")
    if isinstance(skus, list):
        for sku in skus:
            value = _str_or_none(sku, 50)
            if value:
                return value
    return None


def _office_name(raw: dict) -> str | None:
    """WB отдаёт имена ПВЗ массивом `offices`."""
    offices = raw.get("offices")
    if isinstance(offices, list) and offices:
        return _str_or_none(offices[0], 200)
    return _str_or_none(raw.get("officeName"), 200)


async def _resolve_nomenclature(
    db: AsyncSession,
    project_id: int,
    chrt_ids: Iterable[int],
    barcodes: Iterable[str],
) -> tuple[dict[int, tuple[int, str | None]], dict[str, tuple[int, str | None]]]:
    """Одним запросом: chrt_id → (nomenclature_id, subject) и barcode → то же.

    Резолв по `chrtId` — основной (ключ Marketplace API), баркод — фолбэк.
    Пары (barcode → chrtId) many-to-one, поэтому по chrt_id берём первую
    строку по возрастанию id — детерминированно между прогонами.
    """
    chrt_list = sorted({c for c in chrt_ids if c})
    bc_list = sorted({b for b in barcodes if b})
    if not chrt_list and not bc_list:
        return {}, {}

    conditions = []
    if chrt_list:
        conditions.append(Nomenclature.chrt_id.in_(chrt_list))
    if bc_list:
        conditions.append(Nomenclature.barcode.in_(bc_list))

    result = await db.execute(
        select(Nomenclature.id, Nomenclature.barcode, Nomenclature.chrt_id, Nomenclature.subject)
        .where(Nomenclature.project_id == project_id, or_(*conditions))
        .order_by(Nomenclature.id)
        .limit(_NOM_LOOKUP_LIMIT)
    )
    by_chrt: dict[int, tuple[int, str | None]] = {}
    by_barcode: dict[str, tuple[int, str | None]] = {}
    for nom_id, barcode, chrt_id, subject in result.all():
        if chrt_id is not None and chrt_id not in by_chrt:
            by_chrt[chrt_id] = (nom_id, subject)
        if barcode and barcode not in by_barcode:
            by_barcode[barcode] = (nom_id, subject)
    return by_chrt, by_barcode


def _order_row(
    raw: dict,
    project_id: int,
    sync_ts: datetime,
    by_chrt: dict[int, tuple[int, str | None]],
    by_barcode: dict[str, tuple[int, str | None]],
    writeoff_before: datetime | None = None,
) -> dict[str, Any] | None:
    """Payload WB → строка `wb_fbs_orders`. None, если нет `id` задания.

    `writeoff_before` — граница «историческое / живое» для периодного метода
    (см. модульный docstring): задание, созданное РАНЬШЕ неё, вставляется уже
    списанным. Дата задания не распозналась — считаем историческим: не списать
    единицу дважды дешевле, чем вычесть со склада давнюю продажу.
    """
    wb_order_id = _int_or_none(raw.get("id"))
    if not wb_order_id:
        return None

    created_at_wb = _parse_wb_datetime(raw.get("createdAt"))
    is_history = writeoff_before is not None and (created_at_wb is None or created_at_wb < writeoff_before)

    chrt_id = _int_or_none(raw.get("chrtId"))
    barcode = _first_sku(raw)
    resolved = (by_chrt.get(chrt_id) if chrt_id else None) or (by_barcode.get(barcode) if barcode else None)
    nomenclature_id = resolved[0] if resolved else None
    subject = _str_or_none(resolved[1], 200) if resolved else None

    return {
        "project_id": project_id,
        "wb_order_id": wb_order_id,
        "rid": _str_or_none(raw.get("rid"), 120),
        "order_uid": _str_or_none(raw.get("orderUid"), 120),
        "created_at_wb": created_at_wb,
        "wb_warehouse_id": _int_or_none(raw.get("warehouseId")),
        "office_id": _int_or_none(raw.get("officeId")),
        "office_name": _office_name(raw),
        "nm_id": _int_or_none(raw.get("nmId")),
        "chrt_id": chrt_id,
        "barcode": barcode,
        "nomenclature_id": nomenclature_id,
        "article": _str_or_none(raw.get("article"), 100),
        "subject": subject,
        # ×100 → рубли
        "price": _kopecks_to_rub(raw.get("price")),
        "converted_price": _kopecks_to_rub(raw.get("convertedPrice")),
        "sale_price": _kopecks_to_rub(raw.get("salePrice")),
        "currency_code": _str_or_none(raw.get("currencyCode"), 8),
        "cargo_type": _int_or_none(raw.get("cargoType")),
        "cross_border_type": _int_or_none(raw.get("crossBorderType")),
        "is_zero_order": bool(raw.get("isZeroOrder", False)),
        "is_pickup_point_shipment_allowed": bool(raw.get("isPickupPointShipmentAllowed", False)),
        # Статусы — не из этого payload'а: при вставке дефолт `new`, при
        # конфликте не трогаются (см. _upsert_orders).
        "supplier_status": _supplier_status_or_new(raw),
        "wb_status": _str_or_none(raw.get("wbStatus"), 30),
        "is_cancellable": bool(raw.get("isCancellable", False)),
        "supply_id": _str_or_none(raw.get("supplyId"), 50),
        "ddate": _parse_wb_date(raw.get("ddate")),
        "seller_date": _parse_wb_datetime(raw.get("sellerDate")),
        "comment": _str_or_none(raw.get("comment"), 300),
        "address": raw.get("address") if isinstance(raw.get("address"), dict) else None,
        # Историческое задание вставляется УЖЕ списанным (в UPDATE поле не
        # входит — повторный прогон метку не трогает, см. _upsert_orders).
        "written_off_at": sync_ts if is_history else None,
        # Метка контура — единственный дискриминатор «песочница / боевой»
        # (колонки под него в модели нет, см. services/wb_fbs/contour.py).
        "raw": stamp_contour(raw),
        "synced_at": sync_ts,
        "created_at": sync_ts,
        "updated_at": sync_ts,
    }


def _supplier_status_or_new(raw: dict) -> str:
    status = _str_or_none(raw.get("supplierStatus"), 20)
    if status and status in _VALID_SUPPLIER_STATUSES:
        return status
    return FbsSupplierStatus.NEW.value


# ─── Синк заданий ───────────────────────────────────────────────────────────


async def _upsert_orders(
    db: AsyncSession,
    project_id: int,
    raw_orders: list[dict],
    *,
    writeoff_before: datetime | None = None,
) -> tuple[int, int]:
    """UPSERT заданий по natural key `(project_id, wb_order_id)` → (строк, помечено списанными).

    Дедуп ключей в Python ДО executemany — иначе PG роняет CardinalityViolation
    («ON CONFLICT не может обновить строку дважды»).

    `writeoff_before` (только периодный метод): задания старше этой границы
    вставляются с `written_off_at`. Во второй элемент кортежа попадают ТОЛЬКО
    реально вставленные — у известного задания метку не меняет ничто.

    Переходы статусов, честно принесённые payload'ом для СУЩЕСТВУЮЩИХ строк,
    фиксируются в журнале `wb_fbs_order_events` (см.
    `_record_upsert_transitions`); свежая вставка — в т.ч. историческая строка
    бэкфилла — событий не рождает.
    """
    deduped: dict[int, dict] = {}
    for raw in raw_orders:
        if not isinstance(raw, dict):
            continue
        wb_order_id = _int_or_none(raw.get("id"))
        if wb_order_id:
            deduped[wb_order_id] = raw  # последнее вхождение выигрывает
    if not deduped:
        return 0, 0

    # Порядок строк в multi-row UPSERT задаёт порядок, в котором PG берёт
    # блокировки. Писателей в эту таблицу ДВА и они пересекаются по заданиям:
    # `sync_new_orders` (очередь `/orders/new`, раз в 2 мин) и
    # `sync_orders_recent` (периодное окно, раз в 10 мин). WB отдаёт их ответы
    # в РАЗНОМ порядке, поэтому два одновременных прогона брали одни и те же
    # строки в противоположной последовательности → `DeadlockDetectedError`,
    # и периодный синк падал на проде каждый раз (0 успешных прогонов).
    # Сортировка по natural key даёт всем писателям один порядок захвата:
    # цикла ожидания не возникает даже при разных границах чанков.
    payloads = [deduped[key] for key in sorted(deduped)]
    by_chrt, by_barcode = await _resolve_nomenclature(
        db,
        project_id,
        (_int_or_none(p.get("chrtId")) or 0 for p in payloads),
        (_first_sku(p) or "" for p in payloads),
    )

    sync_ts = utcnow()
    rows = [
        row
        for p in payloads
        if (row := _order_row(p, project_id, sync_ts, by_chrt, by_barcode, writeoff_before))
    ]
    if not rows:
        return 0, 0

    # Снимок статусов ДО upsert — двойная служба: «кто уже известен» для
    # честного счётчика пометок и база диффа для журнала переходов.
    snapshot = await order_status_snapshot(db, project_id, [r["wb_order_id"] for r in rows])

    # Счётчик пометок обязан быть честным: на конфликте `written_off_at` не
    # обновляется, поэтому «помечено» = только те, кого мы реально вставили.
    marked_ids = [r["wb_order_id"] for r in rows if r["written_off_at"] is not None]
    marked = len([oid for oid in marked_ids if oid not in snapshot])

    for chunk in _chunks(rows, _UPSERT_CHUNK):
        stmt = pg_insert(WbFbsOrder).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_wb_fbs_order",
            set_={
                "rid": stmt.excluded.rid,
                "order_uid": stmt.excluded.order_uid,
                "created_at_wb": stmt.excluded.created_at_wb,
                "wb_warehouse_id": stmt.excluded.wb_warehouse_id,
                "office_id": stmt.excluded.office_id,
                "office_name": stmt.excluded.office_name,
                "nm_id": stmt.excluded.nm_id,
                "chrt_id": stmt.excluded.chrt_id,
                "barcode": stmt.excluded.barcode,
                # Резолв мог не удаться (карточки ещё нет) — не затираем найденное.
                "nomenclature_id": func.coalesce(stmt.excluded.nomenclature_id, WbFbsOrder.nomenclature_id),
                "article": stmt.excluded.article,
                "subject": func.coalesce(stmt.excluded.subject, WbFbsOrder.subject),
                "price": stmt.excluded.price,
                "converted_price": stmt.excluded.converted_price,
                "sale_price": stmt.excluded.sale_price,
                "currency_code": stmt.excluded.currency_code,
                "cargo_type": stmt.excluded.cargo_type,
                "cross_border_type": stmt.excluded.cross_border_type,
                "is_zero_order": stmt.excluded.is_zero_order,
                "is_pickup_point_shipment_allowed": stmt.excluded.is_pickup_point_shipment_allowed,
                # supplier_status / wb_status / is_cancellable / supply_id /
                # sticker_* / written_off_at НЕ трогаем: их владельцы —
                # sync_order_statuses, supplies_service и writeoff.
                "ddate": stmt.excluded.ddate,
                "seller_date": stmt.excluded.seller_date,
                "comment": stmt.excluded.comment,
                "address": stmt.excluded.address,
                "raw": stmt.excluded.raw,
                "synced_at": stmt.excluded.synced_at,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await db.execute(stmt)

    await _record_upsert_transitions(db, project_id, payloads, snapshot, sync_ts)

    await db.commit()
    await invalidate_cache(CACHE_ORDERS)
    return len(rows), marked


async def _record_upsert_transitions(
    db: AsyncSession,
    project_id: int,
    payloads: list[dict],
    snapshot: dict[int, tuple[int, str, str | None]],
    sync_ts: datetime,
) -> None:
    """Журнал переходов для upsert-пути: досылка честно принесённых осей + события.

    Upsert на конфликте оси статусов НЕ трогает (их владельцы — синк статусов
    и методы поставок), но payload иногда честно приносит ось (`supplierStatus`
    и/или `wbStatus`). Для СУЩЕСТВУЮЩИХ строк такая ось досылается отдельным
    UPDATE и переход фиксируется — иначе журнал разошёлся бы с зеркалом, а
    повторный синк того же payload'а плодил бы одинаковые события.

    Границы, которые держат старые инварианты:
      • фолбэк `new` (payload БЕЗ `supplierStatus` — оба боевых метода) сменой
        НЕ считается: «синк новых не откатывает статус известного задания»;
      • терминальные (`cancel`/`cancel_carrier`) здесь НЕ применяются: их обязан
        провести `_apply_statuses` — на нём висит возврат списанного
        (`_revert_writeoff_on_cancel`), а upsert, поставив `cancel` сам, вывел бы
        задание из опроса статусов до того, как возврат случился;
      • СВЕЖЕВСТАВЛЕННЫЕ строки (нет в снимке) событий не получают: их прошлое
        неизвестно, журнал начинается с текущего значения без строки — в том
        числе исторические строки бэкфилла.
    """
    events: list[dict[str, Any]] = []
    catch_up: dict[tuple[str | None, str | None], list[int]] = {}
    for p in payloads:
        wb_order_id = _int_or_none(p.get("id"))
        snap = snapshot.get(wb_order_id or 0)
        if snap is None or wb_order_id is None:
            continue
        pk, old_sup, old_wb = snap
        honest_sup = _str_or_none(p.get("supplierStatus"), 20)
        if honest_sup not in _VALID_SUPPLIER_STATUSES or honest_sup in FBS_TERMINAL_STATUSES:
            honest_sup = None
        honest_wb = _str_or_none(p.get("wbStatus"), 30)
        transitions = _axis_transitions(old_sup, old_wb, honest_sup, honest_wb)
        if not transitions:
            continue
        sup_new = next((n for a, _o, n in transitions if a == EVENT_AXIS_SUPPLIER), None)
        wb_new = next((n for a, _o, n in transitions if a == EVENT_AXIS_WB), None)
        catch_up.setdefault((sup_new, wb_new), []).append(wb_order_id)
        for axis, old, new in transitions:
            events.append(
                {"order_id": pk, "axis": axis, "old_value": old, "new_value": new, "changed_at": sync_ts}
            )

    for (sup_new, wb_new), ids in catch_up.items():
        values: dict[str, Any] = {"synced_at": sync_ts, "updated_at": sync_ts}
        if sup_new:
            values["supplier_status"] = sup_new
        if wb_new:
            values["wb_status"] = wb_new
        for chunk in _chunks(ids, _STATUS_CHUNK):
            await db.execute(
                update(WbFbsOrder)
                .where(WbFbsOrder.project_id == project_id, WbFbsOrder.wb_order_id.in_(chunk))
                .values(**values)
            )
    await record_order_events(db, project_id, events)


async def sync_new_orders(db: AsyncSession, project_id: int) -> int:
    """Забрать `GET /api/v3/orders/new` и зеркалировать в БД. Возвращает число строк."""
    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    raw_orders = await client.get_new_orders()
    if not raw_orders:
        return 0

    count, _marked = await _upsert_orders(db, project_id, raw_orders)
    logger.info("wb_fbs.orders.sync_new project=%s upserted=%s", project_id, count)
    return count


# ─── Периодный метод: бэкфилл истории и догон недавнего окна ────────────────


async def _orders_cutoff(db: AsyncSession, project_id: int) -> datetime:
    """Момент, с которого домен вообще начал видеть заказы этого проекта.

    Это `MIN(created_at)` зеркала — время ПЕРВОЙ вставки, а не дата заказа.
    Всё, что WB отдаёт за период раньше него и чего в зеркале нет, физически
    отгружено до нашего первого опроса: в `GET /orders/new` такое задание
    больше не приходило, иначе оно уже лежало бы у нас.

    Зеркало пустое → «сейчас»: истории мы не знаем вовсе, и вычитать со склада
    по ней нельзя ничего.
    """
    result = await db.execute(
        select(func.min(WbFbsOrder.created_at)).where(
            WbFbsOrder.project_id == project_id,
            contour_condition(WbFbsOrder.raw),
        )
    )
    return result.scalar() or utcnow()


def _order_windows(date_from: datetime, date_to: datetime) -> list[tuple[datetime, datetime]]:
    """Период → окна ≤30 дней встык (жёсткое ограничение `GET /api/v3/orders`)."""
    windows: list[tuple[datetime, datetime]] = []
    step = timedelta(days=_ORDERS_WINDOW_DAYS)
    start = date_from
    while start < date_to:
        end = min(start + step, date_to)
        windows.append((start, end))
        start = end
    return windows or [(date_from, date_to)]


async def _fetch_orders_window(client: WbFbsClient, start: datetime, end: datetime) -> list[dict]:
    """Одно окно периодного метода. Границы отдаём AWARE-UTC.

    `_unix()` в клиенте зовёт `.timestamp()`, а он трактует naive-дату как
    ЛОКАЛЬНОЕ время машины — на контейнере не с UTC окно уехало бы на часы.
    """
    return await client.get_orders(
        date_from=start.replace(tzinfo=timezone.utc),
        date_to=end.replace(tzinfo=timezone.utc),
        max_pages=_ORDERS_MAX_PAGES,
    )


async def backfill_orders_history(db: AsyncSession, project_id: int, days: int = 90) -> dict:
    """Разово подтянуть историю заданий за `days` дней (`GET /api/v3/orders`).

    Зачем: зеркало наполняется из `GET /orders/new`, а он отдаёт только то, что
    ещё не положено в поставку. Всё уехавшее в поставку исчезает оттуда
    навсегда — истории у нас не было вовсе.

    🔴 Историю вставляем СРАЗУ списанной (см. `_orders_cutoff`): в payload'е
    периодного метода нет `supplierStatus`, поэтому без этой защиты три месяца
    заданий легли бы как `new`, синк статусов перевёл бы их в `complete`, а
    `writeoff_completed_orders` вычел бы со склада товар, проданный весной.

    Отказ WB на очередном окне не откатывает уже записанное: прогон
    останавливается, отдаёт `ok=False` с причиной и частичные счётчики —
    следующий запуск доберёт остальное (UPSERT идемпотентен).
    """
    days = max(1, min(int(days or 0), _ORDERS_MAX_DEPTH_DAYS))
    cutoff = await _orders_cutoff(db, project_id)

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    now = utcnow()
    windows = _order_windows(now - timedelta(days=days), now)

    fetched = upserted = marked = done = 0
    ok = True
    message: str | None = None

    for start, end in windows:
        try:
            raw_orders = await _fetch_orders_window(client, start, end)
        except WbFbsApiError as err:
            ok = False
            message = f"Окно {start:%d.%m.%Y}–{end:%d.%m.%Y}: {err}"
            logger.warning(
                "wb_fbs.orders.backfill project=%s окно %s–%s оборвалось: %s",
                project_id,
                start,
                end,
                err,
            )
            break
        done += 1
        fetched += len(raw_orders)
        rows, newly_marked = await _upsert_orders(db, project_id, raw_orders, writeoff_before=cutoff)
        upserted += rows
        marked += newly_marked
        # Закрываем транзакцию ДО следующего похода в WB.
        await db.commit()
        logger.info(
            "wb_fbs.orders.backfill project=%s окно %s–%s: fetched=%s upserted=%s written_off=%s",
            project_id,
            start.date(),
            end.date(),
            len(raw_orders),
            rows,
            newly_marked,
        )

    if ok:
        message = (
            f"История за {days} дн.: получено {fetched}, записано {upserted}, "
            f"помечено списанными {marked}"
        )
    logger.info(
        "wb_fbs.orders.backfill project=%s готово ok=%s windows=%s/%s fetched=%s upserted=%s marked=%s",
        project_id,
        ok,
        done,
        len(windows),
        fetched,
        upserted,
        marked,
    )
    return {
        "ok": ok,
        "fetched": fetched,
        "upserted": upserted,
        "written_off_marked": marked,
        "windows": done,
        "message": message,
    }


async def sync_orders_recent(db: AsyncSession, project_id: int, days: int = _RECENT_DEFAULT_DAYS) -> int:
    """Догон недавнего окна периодным методом. Возвращает число заапсерченных строк.

    `GET /orders/new` теряет задание, собранное между двумя опросами: в поставку
    оно уехало, «новым» больше не приходит и в зеркало не попадает никогда.
    Окно в пару дней ловит такие задания и заодно чинит пропуски после простоя
    воркера.

    Граница исторического та же, что у бэкфилла: на ПЕРВОМ прогоне (зеркало
    пусто → cutoff = «сейчас») окно принесёт уже отгруженные задания, и
    списывать по ним реальный склад нельзя.
    """
    days = max(1, min(int(days or 0), _ORDERS_WINDOW_DAYS))
    cutoff = await _orders_cutoff(db, project_id)

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    now = utcnow()
    raw_orders = await _fetch_orders_window(client, now - timedelta(days=days), now)
    if not raw_orders:
        return 0

    count, marked = await _upsert_orders(db, project_id, raw_orders, writeoff_before=cutoff)
    logger.info(
        "wb_fbs.orders.sync_recent project=%s days=%s fetched=%s upserted=%s written_off=%s",
        project_id,
        days,
        len(raw_orders),
        count,
        marked,
    )
    return count


async def _apply_statuses(db: AsyncSession, project_id: int, items: list[dict]) -> int:
    """Записать статусы пачкой: группируем по одинаковой тройке значений.

    Параллельно ведём журнал переходов (`wb_fbs_order_events`): снимок осей ДО
    перезаписи (паттерн `FulfillmentStatusEvent`), после — событие на каждую
    реально сменившуюся ось. Повторный прогон с теми же статусами событий не
    плодит: old == new → ноль строк.
    """
    groups: dict[tuple[str, str | None, bool], list[int]] = {}
    incoming: dict[int, tuple[str, str | None]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        wb_order_id = _int_or_none(item.get("id"))
        status = _str_or_none(item.get("supplierStatus"), 20)
        if not wb_order_id or not status or status not in _VALID_SUPPLIER_STATUSES:
            continue
        wb_status = _str_or_none(item.get("wbStatus"), 30)
        key = (status, wb_status, bool(item.get("isCancellable", False)))
        groups.setdefault(key, []).append(wb_order_id)
        incoming[wb_order_id] = (status, wb_status)

    # Снимок ДО перезаписи — иначе дифф сравнивал бы новое с новым.
    snapshot = await order_status_snapshot(db, project_id, list(incoming))

    now = utcnow()
    updated = 0
    for (status, wb_status, is_cancellable), ids in groups.items():
        for chunk in _chunks(ids, _STATUS_CHUNK):
            result = await db.execute(
                update(WbFbsOrder)
                .where(
                    WbFbsOrder.project_id == project_id,
                    WbFbsOrder.wb_order_id.in_(chunk),
                )
                .values(
                    supplier_status=status,
                    wb_status=wb_status,
                    is_cancellable=is_cancellable,
                    synced_at=now,
                    updated_at=now,
                )
            )
            updated += result.rowcount or 0  # type: ignore[attr-defined]

    events: list[dict[str, Any]] = []
    for wb_order_id, (new_sup, new_wb) in incoming.items():
        snap = snapshot.get(wb_order_id)
        if snap is None:
            continue
        pk, old_sup, old_wb = snap
        for axis, old, new in _axis_transitions(old_sup, old_wb, new_sup, new_wb):
            events.append(
                {"order_id": pk, "axis": axis, "old_value": old, "new_value": new, "changed_at": now}
            )
    await record_order_events(db, project_id, events)

    await _revert_writeoff_on_cancel(db, project_id, groups)
    return updated


async def _writeoff_source_warehouses(
    db: AsyncSession, project_id: int, orders: list[WbFbsOrder]
) -> dict[int, int]:
    """{id задания: склад, с которого его списали} — по фактическим движениям.

    Единственный достоверный источник «откуда ушла единица»: в самом задании
    склада нет, а привязок у склада продавца может быть несколько. Берём
    расходное движение (`quantity < 0`) с нашей ссылкой на задание.
    """
    ids = [o.id for o in orders]
    if not ids:
        return {}
    out: dict[int, int] = {}
    for chunk in _chunks(ids, _STATUS_CHUNK):
        result = await db.execute(
            select(StockMovement.reference_id, StockMovement.warehouse_id)
            .where(
                StockMovement.project_id == project_id,
                StockMovement.reference_type == _WRITEOFF_REF_TYPE,
                StockMovement.reference_id.in_(chunk),
                StockMovement.quantity < 0,
            )
            .order_by(StockMovement.id)
        )
        # Последнее движение выигрывает: если задание списывали и возвращали
        # несколько раз, актуален самый свежий расход.
        for ref_id, warehouse_id in result.all():
            if ref_id is not None and warehouse_id is not None:
                out[int(ref_id)] = int(warehouse_id)
    return out


async def _revert_writeoff_on_cancel(
    db: AsyncSession, project_id: int, groups: dict[tuple[str, str | None, bool], list[int]]
) -> int:
    """Вернуть на склад единицы заданий, отменённых ПОСЛЕ передачи поставки.

    Статус `complete` не финальный: WB переводит уже переданное задание в
    `cancel_carrier` (отмена перевозчиком — ровно для этого статус и заведён),
    бывает и `cancel`. Списание при этом уже произошло, `written_off_at`
    проставлен, а движения OUTBOUND никто не сторнирует — и минус на складе
    остаётся навсегда. Задание вдобавок выпадает из `FBS_OPEN_STATUSES`, то есть
    перестаёт даже держать резерв: товар физически вернулся, а в учёте его нет.

    Возвращаем ровно то, что списывали: +1 INBOUND и сброс `written_off_at`,
    чтобы повторный проход не задвоил приход.
    """
    cancelled_ids = [
        wb_id for (status, _, _), ids in groups.items() if status in FBS_TERMINAL_STATUSES for wb_id in ids
    ]
    if not cancelled_ids:
        return 0

    reverted = 0
    for chunk in _chunks(cancelled_ids, _STATUS_CHUNK):
        result = await db.execute(
            select(WbFbsOrder).where(
                WbFbsOrder.project_id == project_id,
                WbFbsOrder.wb_order_id.in_(chunk),
                WbFbsOrder.written_off_at.is_not(None),
                contour_condition(WbFbsOrder.raw),
            )
        )
        orders = list(result.scalars().all())
        if not orders:
            continue
        links_by_wb, _qty, barcode_map = await _writeoff_context(db, project_id, orders)
        source_wh = await _writeoff_source_warehouses(db, project_id, orders)
        for order in orders:
            if not order.nomenclature_id:
                continue
            # Склад берём из ФАКТИЧЕСКОГО движения списания, а не «первый
            # привязанный»: у склада продавца привязок бывает несколько, списание
            # выбирает ту, где был остаток, и возврат «на первую» переложил бы
            # товар с одного нашего склада на другой — оба остатка становятся
            # неверными, а расхождение всплывает только при инвентаризации.
            target = source_wh.get(order.id)
            if target is None:
                # Движения нет (данные до этой правки) — падаем на прежнее
                # поведение, но только когда привязка ровно одна и выбора нет.
                candidates = links_by_wb.get(order.wb_warehouse_id or 0, [])
                if len(candidates) != 1:
                    logger.warning(
                        "wb_fbs.orders.revert project=%s задание %s: не нашли склад списания, "
                        "привязок %s — возврат пропущен",
                        project_id,
                        order.wb_order_id,
                        len(candidates),
                    )
                    continue
                target = candidates[0]
            barcode = order.barcode or barcode_map.get((target, order.nomenclature_id))
            if not barcode:
                continue
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=target,
                nomenclature_id=order.nomenclature_id,
                barcode=barcode,
                delta=1,
                movement_type=MovementType.INBOUND,
                reference_type=_WRITEOFF_REF_TYPE,
                reference_id=order.id,
                comment=f"Возврат отменённого FBS-задания {order.wb_order_id}",
            )
            order.written_off_at = None
            reverted += 1

    if reverted:
        await invalidate_cache(CACHE_ORDERS)
        await invalidate_cache("reports:warehouse_need")
        logger.warning(
            "wb_fbs.orders.statuses project=%s возвращено на склад %s отменённых после передачи заданий",
            project_id,
            reverted,
        )
    return reverted


async def sync_order_statuses(db: AsyncSession, project_id: int) -> int:
    """Досинк статусов НЕ-терминальных заданий чанками по 1000.

    Терминальные (`cancel`, `cancel_carrier`) не опрашиваем: WB их больше
    не меняет, а каждый лишний id съедает лимит запросов.

    Только задания ТЕКУЩЕГО контура: спрашивать песочницу про боевые id (и
    наоборот) — гарантированные 404 и сожжённый лимит.
    """
    result = await db.execute(
        select(WbFbsOrder.wb_order_id)
        .where(
            WbFbsOrder.project_id == project_id,
            alive_condition(),
            contour_condition(WbFbsOrder.raw),
        )
        .order_by(WbFbsOrder.wb_order_id.desc())
        .limit(_STATUS_MAX_ORDERS)
    )
    order_ids = [row[0] for row in result.all()]
    if not order_ids:
        return 0

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    updated = 0
    for chunk in _chunks(order_ids, _STATUS_CHUNK):
        items = await client.get_orders_status(chunk)
        updated += await _apply_statuses(db, project_id, items)
        await db.commit()  # закрываем транзакцию ДО следующего похода в WB

    if updated:
        await invalidate_cache(CACHE_ORDERS)
    logger.info("wb_fbs.orders.sync_statuses project=%s asked=%s updated=%s", project_id, len(order_ids), updated)
    return updated


# ─── Чтение ─────────────────────────────────────────────────────────────────


def _as_dt_from(value: Any) -> datetime | None:
    """`date`/`datetime`/ISO-строка → начало суток (нижняя граница фильтра)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    parsed = _parse_wb_datetime(str(value)) or _parse_wb_date(str(value))
    if isinstance(parsed, datetime):
        return parsed
    if isinstance(parsed, date):
        return datetime.combine(parsed, time.min)
    return None


def _as_dt_to(value: Any) -> datetime | None:
    """Верхняя граница: для календарной даты — начало СЛЕДУЮЩИХ суток (строгое <).

    🔴 Строка «2026-07-27» — это КАЛЕНДАРНАЯ дата, а не полночь. Раньше её
    первым перехватывал `_parse_wb_datetime` и возвращал `2026-07-27 00:00`
    as-is, из-за чего строгое `<` выкидывало последний день периода целиком —
    в отличие от того же значения, переданного объектом `date`. Роутер приводит
    query-параметр к `date` и на боевом пути это не стреляло, но любой прямой
    вызов со строкой молча терял сутки. Признак времени — `T` или `:`.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value + timedelta(days=1), time.min)
    text = str(value)
    if "T" not in text and ":" not in text:
        parsed_date = _parse_wb_date(text)
        return datetime.combine(parsed_date + timedelta(days=1), time.min) if parsed_date else None
    parsed = _parse_wb_datetime(text)
    if parsed is not None:
        return parsed
    parsed_date = _parse_wb_date(text)
    return datetime.combine(parsed_date + timedelta(days=1), time.min) if parsed_date else None


def _money(value: Decimal | None) -> str | None:
    """Деньги наружу — СТРОКОЙ.

    Ответ `list_orders` проходит через JSON-кэш (`@cached`), а его энкодер
    приводит `Decimal` к float — 1379.99 вернулся бы как 1379.9899999999998.
    Pydantic парсит строку в `Decimal` точно, и на выходе API ничего не меняется.
    """
    return None if value is None else str(value)


def _is_in_delivery_row(order: WbFbsOrder) -> bool:
    """Python-зеркало `in_delivery_condition()` для ОДНОЙ строки — бит-в-бит.

    Тот же БЕЛЫЙ список, что в SQL: `complete` И (`wb_status` пуст/NULL ИЛИ
    ∈ `FBS_WB_PRE_SORT_STATUSES`). WB-отмены (`FBS_WB_CANCELLED_STATUSES`) в
    SQL выбывают через `effective_status_expr()`; здесь их гасит сам белый
    список — они непустые и в до-сортировочные не входят. Неизвестный новый
    статус WB «в пути» НЕ считается (см. docstring SQL-версии).
    """
    if order.supplier_status != FbsSupplierStatus.COMPLETE.value:
        return False
    return not order.wb_status or order.wb_status in FBS_WB_PRE_SORT_STATUSES


async def _supply_anchor_map(
    db: AsyncSession, project_id: int, supply_ids: set[str]
) -> dict[str, datetime]:
    """{wb_supply_id: COALESCE(scan_dt, closed_at)} одним IN-запросом (без N+1).

    Скоуп по контуру — паритет с `_supply_join_condition`: якорь боевого
    задания не берётся из одноимённой поставки песочницы.
    """
    ids = sorted(s for s in supply_ids if s)
    if not ids:
        return {}
    result = await db.execute(
        select(
            WbFbsSupply.wb_supply_id,
            func.coalesce(WbFbsSupply.scan_dt, WbFbsSupply.closed_at),
        ).where(
            WbFbsSupply.project_id == project_id,
            WbFbsSupply.wb_supply_id.in_(ids),
            contour_condition(WbFbsSupply.raw),
        )
    )
    return {supply_id: anchor for supply_id, anchor in result.all() if anchor is not None}


def _transit_days(order: WbFbsOrder, anchor_by_supply: dict[str, datetime], now: datetime) -> int | None:
    """Сколько дней задание едет — только для фазы «в пути», иначе None.

    🔴 Считаем `int(total_seconds() // 86400)`, а НЕ `timedelta.days`: известная
    грабля проекта — int-усечение `days` опаздывает почти на сутки в сравнениях
    (learnings). Отрицательное (якорь «в будущем» из-за рассинхрона часов)
    клампим в 0.
    """
    if not _is_in_delivery_row(order):
        return None
    anchor = (anchor_by_supply.get(order.supply_id) if order.supply_id else None) or order.written_off_at
    if anchor is None:
        return None
    return max(0, int((now - anchor).total_seconds() // 86400))


def _order_to_dict(order: WbFbsOrder, *, transit_days: int | None = None) -> dict[str, Any]:
    """Строка под `FbsOrderOut` (контракт схем)."""
    return {
        "id": order.id,
        "wb_order_id": order.wb_order_id,
        "rid": order.rid,
        "created_at_wb": order.created_at_wb,
        "wb_warehouse_id": order.wb_warehouse_id,
        "office_name": order.office_name,
        "nm_id": order.nm_id,
        "chrt_id": order.chrt_id,
        "barcode": order.barcode,
        "article": order.article,
        "subject": order.subject,
        "price": _money(order.price),
        "sale_price": _money(order.sale_price),
        "currency_code": order.currency_code,
        "cargo_type": order.cargo_type,
        "is_zero_order": order.is_zero_order,
        "is_pickup_point_shipment_allowed": order.is_pickup_point_shipment_allowed,
        "supplier_status": order.supplier_status,
        "wb_status": order.wb_status,
        "is_cancellable": order.is_cancellable,
        "supply_id": order.supply_id,
        "sticker_barcode": order.sticker_barcode,
        "sticker_part_a": order.sticker_part_a,
        "sticker_part_b": order.sticker_part_b,
        "ddate": order.ddate,
        "comment": order.comment,
        "written_off_at": order.written_off_at,
        "synced_at": order.synced_at,
        "transit_days": transit_days,
    }


@cached(prefix="fbs:orders", ttl=60)  # литерал: гейт ищет префикс регуляркой
async def warehouse_summary(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: Any = None,
    date_to: Any = None,
) -> dict:
    """Очередь по складам продавца: сколько на каждой фазе (`FbsWarehouseSummaryOut`).

    ОДИН запрос с `GROUP BY wb_warehouse_id` вместо запроса на склад: карточек
    столько же, сколько складов, и прежняя схема «limit=1 на каждый» упиралась
    в 9 параллельных походов в БД ради девяти чисел.

    🔴 **Период применяется ТОЛЬКО к фазам доставки.** «Новые» и «На сборке» —
    это ОЧЕРЕДЬ, а не поток: задание, созданное два месяца назад и до сих пор
    не собранное, обязано быть в цифре сборщика, иначе он его просто не увидит.
    А `complete` копится вечно (WB его уже не двинет), и без окна «В доставке»
    показывает всё, что когда-либо уезжало, — цифра растёт и ничего не значит.

    Заодно окно чинит вторую беду: синк статусов опрашивает не больше
    `_STATUS_MAX_ORDERS` НЕ-терминальных заданий, поэтому у старых `complete`
    `wbStatus` со временем застывает — вне периода они висели бы «в доставке»
    вечно.

    `in_delivery_stuck` — БЕЗ периода (как очередь сборки): пока СЦ не принял,
    вопросы к нам, и зависшее обязано быть видно независимо от окна. Его
    собственное окно по якорю передачи (`in_delivery_stuck_condition`) не даёт
    счётчику копить мёртвые строки с застывшим `wb_status`. Ради якоря запрос
    несёт LEFT OUTER JOIN на зеркало поставок — 1:1 максимум (`uq_wb_fbs_supply`),
    счётчики не задваивает.
    """
    base = [WbFbsOrder.project_id == project_id, contour_condition(WbFbsOrder.raw)]
    period: list[Any] = []
    dt_from = _as_dt_from(date_from)
    if dt_from is not None:
        period.append(WbFbsOrder.created_at_wb >= dt_from)
    dt_to = _as_dt_to(date_to)
    if dt_to is not None:
        period.append(WbFbsOrder.created_at_wb < dt_to)

    now = utcnow()
    eff = effective_status_expr()
    in_period = and_(*period) if period else true()
    result = await db.execute(
        select(
            WbFbsOrder.wb_warehouse_id,
            func.count().filter(eff == FbsSupplierStatus.NEW.value),
            func.count().filter(eff == FbsSupplierStatus.CONFIRM.value),
            func.count().filter(and_(in_delivery_condition(), in_period)),
            func.count().filter(and_(sorted_condition(), in_period)),
            func.count().filter(in_delivery_stuck_condition(now)),
        )
        .select_from(WbFbsOrder)
        .outerjoin(WbFbsSupply, _supply_join_condition(project_id))
        .where(*base)
        .group_by(WbFbsOrder.wb_warehouse_id)
    )
    rows = [
        {
            "wb_warehouse_id": row[0],
            "new": int(row[1] or 0),
            "confirm": int(row[2] or 0),
            "in_delivery": int(row[3] or 0),
            "sorted": int(row[4] or 0),
            "in_delivery_stuck": int(row[5] or 0),
        }
        for row in result.all()
    ]
    totals = {
        key: sum(r[key] for r in rows)
        for key in ("new", "confirm", "in_delivery", "sorted", "in_delivery_stuck")
    }
    return {
        "date_from": dt_from.date() if dt_from else None,
        "date_to": (dt_to - timedelta(days=1)).date() if dt_to else None,
        "warehouses": rows,
        "totals": totals,
    }


# TTL короткий: задания живые, а любая наша мутация и так инвалидирует префикс.
@cached(prefix="fbs:orders", ttl=60)
async def list_orders(
    db: AsyncSession,
    project_id: int,
    *,
    status: str | None = None,
    supply_id: str | None = None,
    wb_warehouse_id: int | None = None,
    date_from: Any = None,
    date_to: Any = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Список заданий из зеркала + счётчики по статусам (для вкладок).

    `status_counts` считается по ТЕМ ЖЕ фильтрам, но без фильтра статуса —
    иначе вкладка показывала бы только собственный счётчик.

    `in_delivery_count` / `sorted_count` — две ФАЗЫ внутри `complete`: «ещё
    едет, не отсортировано» и «принято сортировочным центром». Отдельными
    полями, а НЕ ключами `status_counts`: сумма счётчиков — это `total` вкладки
    «Все», и синтетика внутри неё удвоила бы каждое переданное задание.

    `status=in_delivery` / `status=sorted` — те же псевдо-статусы в фильтре:
    цифра на карточке склада и выдача по клику обязаны совпадать до штуки.

    `status=in_delivery_stuck` — третий псевдо-статус: «передано ≥ N дней назад,
    СЦ так и не принял» (`in_delivery_stuck_condition`). 🔴 Этот фильтр (и его
    счётчик `in_delivery_stuck_count`) ИГНОРИРУЕТ период страницы: зависшее —
    ОЧЕРЕДЬ проблем, а не история; задание, переданное до начала окна, обязано
    остаться на виду, пока СЦ его не примет. Собственное окно по якорю передачи
    (2–30 дней) у фильтра есть всегда — см. комментарий у констант.

    Выдача скоуплена по контуру: боевой список не должен показывать задания
    песочницы (и наоборот) — цифры вкладок обязаны совпадать с тем, что
    участвует в остатке и списании.

    `transit_days` в строках считается для фазы «в пути» от якоря передачи
    (поставки страницы поднимаются одним IN-запросом, без N+1).
    """
    limit = max(1, min(int(limit or 100), _LIST_MAX_LIMIT))
    offset = max(0, int(offset or 0))
    now = utcnow()

    # scope — фильтры БЕЗ периода (их уважает и вкладка зависших), base — с окном дат.
    scope = [WbFbsOrder.project_id == project_id, contour_condition(WbFbsOrder.raw)]
    if supply_id:
        scope.append(WbFbsOrder.supply_id == supply_id)
    if wb_warehouse_id:
        scope.append(WbFbsOrder.wb_warehouse_id == wb_warehouse_id)
    base = list(scope)
    dt_from = _as_dt_from(date_from)
    if dt_from is not None:
        base.append(WbFbsOrder.created_at_wb >= dt_from)
    dt_to = _as_dt_to(date_to)
    if dt_to is not None:
        base.append(WbFbsOrder.created_at_wb < dt_to)

    # Выражение строим ОДИН раз и группируем по метке: два отдельных вызова
    # дают структурно равные, но разные объекты, и PG отказывается считать их
    # одним и тем же — «wb_status must appear in the GROUP BY clause».
    eff_status = effective_status_expr().label("eff")
    counts_result = await db.execute(
        select(
            eff_status,
            func.count(),
            func.count().filter(in_delivery_condition()),
            func.count().filter(sorted_condition()),
        )
        .where(*base)
        .group_by(eff_status)
    )
    counts_rows = counts_result.all()
    status_counts = {row[0]: int(row[1]) for row in counts_rows}
    in_delivery_count = sum(int(row[2] or 0) for row in counts_rows)
    sorted_count = sum(int(row[3] or 0) for row in counts_rows)

    # Счётчик зависших — БЕЗ периода (см. docstring); джойн поставок нужен ради
    # якоря передачи и не задваивает строки (1:1 максимум, `uq_wb_fbs_supply`).
    in_delivery_stuck_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(WbFbsOrder)
                .outerjoin(WbFbsSupply, _supply_join_condition(project_id))
                .where(*scope, in_delivery_stuck_condition(now))
            )
        ).scalar()
        or 0
    )

    stuck_filter = status == FBS_IN_DELIVERY_STUCK_STATUS
    conditions = list(scope) if stuck_filter else list(base)
    if stuck_filter:
        conditions.append(in_delivery_stuck_condition(now))
        total = in_delivery_stuck_count
    elif status == FBS_SORTED_STATUS:
        conditions.append(sorted_condition())
        total = sorted_count
    elif status == FBS_IN_DELIVERY_STATUS:
        conditions.append(in_delivery_condition())
        total = in_delivery_count
    elif status:
        conditions.append(effective_status_expr() == status)
        total = status_counts.get(status, 0)
    else:
        total = sum(status_counts.values())

    items_query = select(WbFbsOrder)
    if stuck_filter:
        items_query = items_query.outerjoin(WbFbsSupply, _supply_join_condition(project_id))
    items_result = await db.execute(
        items_query.where(*conditions)
        .order_by(WbFbsOrder.created_at_wb.desc().nullslast(), WbFbsOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    orders = list(items_result.scalars().all())

    # transit_days: якоря поставок страницы — одним запросом, без N+1.
    anchor_by_supply = await _supply_anchor_map(
        db, project_id, {o.supply_id for o in orders if o.supply_id and _is_in_delivery_row(o)}
    )
    items = [
        _order_to_dict(order, transit_days=_transit_days(order, anchor_by_supply, now))
        for order in orders
    ]
    return {
        "items": items,
        "total": total,
        "status_counts": status_counts,
        "in_delivery_count": in_delivery_count,
        "sorted_count": sorted_count,
        "in_delivery_stuck_count": in_delivery_stuck_count,
    }


# ─── Таймлайн задания ───────────────────────────────────────────────────────


async def get_order_timeline(db: AsyncSession, project_id: int, wb_order_id: int) -> dict:
    """Таймлайн «Статус заказа» (`FbsOrderTimelineOut`) — модалка как в кабинете WB.

    Два источника, оба наши (WB историю не отдаёт вовсе):
      • якоря (kind="anchor", approx=False) — синтетика из ТОЧНЫХ дат:
        `created` (задание оформлено, created_at_wb), `assembled` (поставка
        закрыта, closed_at), `scanned` (WB отсканировал QR, scan_dt),
        `written_off` (списано из ledger'а DDS);
      • журнал (kind="event", approx=True) — переходы осей из
        `wb_fbs_order_events`; их время — момент фиксации синком (точность =
        каденс, 5 мин). `supplier:complete` может дублировать якорь `assembled`
        по смыслу — оба отдаются, фронт решает сам.

    Сортировка по времени DESC (свежее сверху, как в кабинете) — контракт
    схемы, делается здесь. Журнал срезан `_TIMELINE_EVENTS_MAX` строками.
    """
    result = await db.execute(
        select(WbFbsOrder).where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.wb_order_id == wb_order_id,
            contour_condition(WbFbsOrder.raw),
        )
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise FbsOrderError(f"Сборочное задание {wb_order_id} не найдено в проекте")

    events: list[dict[str, Any]] = []

    def _anchor(code: str, at: datetime | None) -> None:
        if at is not None:
            events.append({"kind": "anchor", "code": code, "at": at, "approx": False})

    _anchor("created", order.created_at_wb)
    if order.supply_id:
        supply_row = (
            await db.execute(
                select(WbFbsSupply.closed_at, WbFbsSupply.scan_dt).where(
                    WbFbsSupply.project_id == project_id,
                    WbFbsSupply.wb_supply_id == order.supply_id,
                    contour_condition(WbFbsSupply.raw),
                )
            )
        ).first()
        if supply_row is not None:
            closed_at, scan_dt = supply_row
            _anchor("assembled", closed_at)
            _anchor("scanned", scan_dt)
    _anchor("written_off", order.written_off_at)

    journal = await db.execute(
        select(WbFbsOrderEvent.axis, WbFbsOrderEvent.new_value, WbFbsOrderEvent.changed_at)
        .where(
            WbFbsOrderEvent.project_id == project_id,
            WbFbsOrderEvent.order_id == order.id,
        )
        .order_by(WbFbsOrderEvent.changed_at.desc(), WbFbsOrderEvent.id.desc())
        .limit(_TIMELINE_EVENTS_MAX)
    )
    for axis, new_value, changed_at in journal.all():
        prefix = "supplier" if axis == EVENT_AXIS_SUPPLIER else "wb"
        events.append(
            {"kind": "event", "code": f"{prefix}:{new_value}", "at": changed_at, "approx": True}
        )

    # Стабильная сортировка: при равном времени порядок вставки сохраняется
    # (якоря хронологией, журнал свежее-первым) — выдача детерминирована.
    events.sort(key=lambda e: e["at"], reverse=True)
    return {
        "wb_order_id": order.wb_order_id,
        "article": order.article,
        "subject": order.subject,
        "nm_id": order.nm_id,
        "events": events,
    }


# ─── Стикеры ────────────────────────────────────────────────────────────────


async def get_stickers(
    db: AsyncSession,
    project_id: int,
    order_ids: list[int],
    sticker_type: str = "png",
    width: int = 58,
    height: int = 40,
) -> list[dict]:
    """Стикеры заданий: `partA`/`partB`/`barcode` кэшируем в БД, файл отдаём наружу.

    Файл (base64) в БД НЕ кладём: он большой, живёт один клик и легко
    перезапрашивается.
    """
    ids = list(dict.fromkeys(oid for oid in (_int_or_none(x) for x in order_ids or []) if oid))
    if not ids:
        raise FbsOrderError("Не переданы сборочные задания для печати стикеров")
    if len(ids) > _STICKER_MAX:
        raise FbsOrderError(f"WB печатает максимум {_STICKER_MAX} стикеров за раз, передано {len(ids)}")
    if sticker_type not in _ALLOWED_STICKER_TYPES:
        raise FbsOrderError(f"Неизвестный формат стикера «{sticker_type}»")

    result = await db.execute(
        select(WbFbsOrder.wb_order_id, WbFbsOrder.supplier_status).where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.wb_order_id.in_(ids),
        )
    )
    known = {row[0]: row[1] for row in result.all()}
    missing = [oid for oid in ids if oid not in known]
    if missing:
        raise FbsOrderError(f"Задания не найдены в проекте: {', '.join(str(m) for m in missing[:10])}")
    not_ready = [oid for oid, st in known.items() if st not in _STICKER_STATUSES]
    if not_ready:
        raise FbsOrderError(
            "Стикер доступен только для заданий, добавленных в поставку "
            f"(статусы {'/'.join(_STICKER_STATUSES)}). Не готовы: "
            f"{', '.join(str(o) for o in sorted(not_ready)[:10])}"
        )

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    raw_stickers = await client.get_stickers(ids, sticker_type=sticker_type, width=width, height=height)

    now = utcnow()
    out: list[dict] = []
    for item in raw_stickers:
        if not isinstance(item, dict):
            continue
        wb_order_id = _int_or_none(item.get("orderId"))
        if not wb_order_id:
            continue
        part_a = _str_or_none(item.get("partA"), 20)
        part_b = _str_or_none(item.get("partB"), 20)
        barcode = _str_or_none(item.get("barcode"), 60)
        await db.execute(
            update(WbFbsOrder)
            .where(WbFbsOrder.project_id == project_id, WbFbsOrder.wb_order_id == wb_order_id)
            .values(
                sticker_part_a=part_a,
                sticker_part_b=part_b,
                sticker_barcode=barcode,
                updated_at=now,
            )
        )
        out.append(
            {
                "order_id": wb_order_id,
                "part_a": part_a,
                "part_b": part_b,
                "barcode": barcode,
                "file": item.get("file"),
            }
        )

    await db.commit()
    await invalidate_cache(CACHE_ORDERS)
    return out


# ─── Отмена ─────────────────────────────────────────────────────────────────


async def cancel_order(db: AsyncSession, project_id: int, wb_order_id: int) -> None:
    """Отменить сборочное задание (`PATCH /orders/{id}/cancel`)."""
    result = await db.execute(
        select(WbFbsOrder.id, WbFbsOrder.supplier_status, WbFbsOrder.is_cancellable).where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.wb_order_id == wb_order_id,
        )
    )
    row = result.first()
    if row is None:
        raise FbsOrderError(f"Сборочное задание {wb_order_id} не найдено в проекте")
    order_pk, status, is_cancellable = row
    if status in FBS_TERMINAL_STATUSES:
        raise FbsOrderError(f"Задание {wb_order_id} уже отменено (статус «{status}»)")
    if not is_cancellable:
        raise FbsOrderError(
            f"WB не разрешает отмену задания {wb_order_id}: isCancellable = false "
            "(товар уже в пути или поставка передана)"
        )

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    await client.cancel_order(wb_order_id)

    now = utcnow()
    await db.execute(
        update(WbFbsOrder)
        .where(WbFbsOrder.project_id == project_id, WbFbsOrder.wb_order_id == wb_order_id)
        .values(
            supplier_status=FbsSupplierStatus.CANCEL.value,
            is_cancellable=False,
            synced_at=now,
            updated_at=now,
        )
    )
    # Переход в журнал: гард выше гарантирует old != cancel (терминальный — 409).
    await record_order_events(
        db,
        project_id,
        [
            {
                "order_id": int(order_pk),
                "axis": EVENT_AXIS_SUPPLIER,
                "old_value": status,
                "new_value": FbsSupplierStatus.CANCEL.value,
                "changed_at": now,
            }
        ],
    )
    await db.commit()
    await invalidate_cache(CACHE_ORDERS)


# ─── Списание проданного в ledger ───────────────────────────────────────────


def _pending_writeoff_conditions(project_id: int) -> list[Any]:
    """«Передано, но не списано»: complete + `written_off_at IS NULL`, текущий контур.

    Общая выборка списания (`_writeoff_locked`) и его диагностики
    (`writeoff_issues`) — обе стороны обязаны смотреть на одни и те же задания.
    """
    return [
        WbFbsOrder.project_id == project_id,
        WbFbsOrder.supplier_status == FbsSupplierStatus.COMPLETE.value,
        WbFbsOrder.written_off_at.is_(None),
        contour_condition(WbFbsOrder.raw),
    ]


def _active_links_subquery(project_id: int) -> Any:
    """Подзапрос «склады продавца с активной привязкой К ЖИВОМУ складу» (для IN/NOT IN).

    Мягко удалённый наш склад из привязки выпадает — канон домена
    (`warehouse_service.get_linked_warehouse_ids`, DOMAIN_WB_FBS «Мягко
    удалённый наш склад выпадает из привязок»). Без фильтра задание на склад
    продавца, чья единственная привязка мертва, проходило выборку списания и
    `_writeoff_locked` списывал В МЁРТВЫЙ остаток; в диагностике оно значилось
    `no_stock` с пустыми остатками. Теперь такие задания честно уходят в
    blocked/`no_link`.
    """
    return (
        select(WbFbsWarehouseLink.wb_warehouse_id)
        .join(Warehouse, Warehouse.id == WbFbsWarehouseLink.warehouse_id)
        .where(
            WbFbsWarehouseLink.project_id == project_id,
            WbFbsWarehouseLink.is_active == True,  # noqa: E712 — SQLAlchemy expression
            Warehouse.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
    )


async def _writeoff_context(
    db: AsyncSession, project_id: int, orders: list[WbFbsOrder]
) -> tuple[dict[int, list[int]], dict[tuple[int, int], int], dict[tuple[int, int], str]]:
    """Батч-контекст списания: привязки складов и текущие остатки. Без N+1.

    Мягко удалённые наши склады отфильтрованы (паритет с
    `_active_links_subquery`): у склада продавца с живой И мёртвой привязкой
    кандидатом списания остаётся только живая.
    """
    wb_wh_ids = sorted({o.wb_warehouse_id for o in orders if o.wb_warehouse_id})
    links_by_wb: dict[int, list[int]] = {}
    if wb_wh_ids:
        links_result = await db.execute(
            select(WbFbsWarehouseLink.wb_warehouse_id, WbFbsWarehouseLink.warehouse_id)
            .join(Warehouse, Warehouse.id == WbFbsWarehouseLink.warehouse_id)
            .where(
                WbFbsWarehouseLink.project_id == project_id,
                WbFbsWarehouseLink.is_active == True,  # noqa: E712
                WbFbsWarehouseLink.wb_warehouse_id.in_(wb_wh_ids),
                Warehouse.is_deleted == False,  # noqa: E712
            )
            .order_by(WbFbsWarehouseLink.id)
        )
        for wb_wh_id, warehouse_id in links_result.all():
            links_by_wb.setdefault(wb_wh_id, []).append(warehouse_id)

    nom_ids = sorted({o.nomenclature_id for o in orders if o.nomenclature_id})
    wh_ids = sorted({wid for wids in links_by_wb.values() for wid in wids})
    qty_map: dict[tuple[int, int], int] = {}
    barcode_map: dict[tuple[int, int], str] = {}
    if nom_ids and wh_ids:
        stock_result = await db.execute(
            select(
                WarehouseStock.warehouse_id,
                WarehouseStock.nomenclature_id,
                WarehouseStock.quantity,
                WarehouseStock.barcode,
            ).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(wh_ids),
                WarehouseStock.nomenclature_id.in_(nom_ids),
            )
        )
        for warehouse_id, nomenclature_id, quantity, barcode in stock_result.all():
            qty_map[(warehouse_id, nomenclature_id)] = int(quantity or 0)
            if barcode:
                barcode_map[(warehouse_id, nomenclature_id)] = barcode
    return links_by_wb, qty_map, barcode_map


async def writeoff_completed_orders(db: AsyncSession, project_id: int) -> int:
    """Списать из ledger'а задания в статусе `complete` (поставка передана).

    Строго идемпотентно: берём только `written_off_at IS NULL` и проставляем
    метку в той же транзакции, что и движение. Остаток в минус не уводим —
    при нехватке задание остаётся неотмеченным и уедет следующим прогоном,
    когда приход догонит.

    Ledger — общий на оба контура, а задания песочницы тестовые: в режиме
    `sandbox` не списываем ВООБЩЕ (ранний выход), а в боевом контуре
    отфильтровываем sandbox-строки, оставшиеся в зеркале от прошлых прогонов.
    Иначе «Передать» на вкладке поставок вычитал бы реальный склад по тестовым
    заданиям — гейт режима это не ловит, он закрывает только запись В WB.

    Прогон под распределённым локом: точек входа две — «Передать поставку»
    (api-контейнер) и джоб статусов (worker). Без лока оба читают одну и ту же
    пачку `written_off_at IS NULL`, оба считают `quantity − 1` в Python и пишут
    литерал: движений OUTBOUND два, а остаток уменьшился на единицу. Занят лок —
    выходим молча: соседний прогон уже делает ровно эту работу.
    """
    if is_sandbox_contour():
        logger.info(
            "wb_fbs.orders.writeoff project=%s пропуск: режим песочницы — "
            "ledger боевого контура не трогаем",
            project_id,
        )
        return 0

    token = await acquire_lock(WRITEOFF_LOCK_NAME, project_id, ttl=WRITEOFF_LOCK_TTL_SEC)
    if token is None:
        logger.info("wb_fbs.orders.writeoff project=%s пропуск: списание уже идёт", project_id)
        return 0
    try:
        return await _writeoff_locked(db, project_id)
    finally:
        await release_lock(WRITEOFF_LOCK_NAME, project_id, token)


async def _writeoff_locked(db: AsyncSession, project_id: int) -> int:
    """Тело списания под локом (см. `writeoff_completed_orders`).

    Привязки к мягко удалённым нашим складам НЕ считаются привязками
    (`_active_links_subquery` / `_writeoff_context`): списывать в мёртвый
    остаток нельзя — такие задания уходят в `blocked` и в диагностике
    (`writeoff_issues`) видны как `no_link`, а не как ложный `no_stock`.
    """
    # Задания, которые списать НЕЧЕМ, отсекаются в SQL, а не пропускаются в цикле.
    # Иначе они навсегда занимают голову очереди `LIMIT`: метку `written_off_at`
    # им никто не ставит, порядок по id — от старых к новым, и как только таких
    # накопится _WRITEOFF_MAX_ORDERS, каждый прогон выбирает ровно их, а НОВЫЕ
    # продажи перестают списываться со склада — молча, одним warning'ом в лог.
    # Две причины из трёх постоянные по своей природе: нет карточки товара и
    # нет привязки склада продавца к нашему.
    linked = _active_links_subquery(project_id)
    pending = _pending_writeoff_conditions(project_id)
    writable = [*pending, WbFbsOrder.nomenclature_id.is_not(None), WbFbsOrder.wb_warehouse_id.in_(linked)]

    result = await db.execute(
        select(WbFbsOrder)
        .where(*writable)
        # От НОВЫХ к старым: свежая продажа обязана списаться сегодня, даже если
        # в хвосте копится нерешаемое. При `id`-ASC хвост съедал бы весь лимит.
        .order_by(WbFbsOrder.id.desc())
        .limit(_WRITEOFF_MAX_ORDERS)
    )
    orders = list(result.scalars().all())

    # Сколько заданий списать нечем — цифра обязана быть видна: молчание об этом
    # и было тем, что превращало проблему в невидимую.
    blocked = int(
        (
            await db.execute(
                select(func.count()).select_from(WbFbsOrder).where(
                    *pending,
                    or_(
                        WbFbsOrder.nomenclature_id.is_(None),
                        WbFbsOrder.wb_warehouse_id.notin_(linked),
                    ),
                )
            )
        ).scalar()
        or 0
    )
    if blocked:
        logger.warning(
            "wb_fbs.orders.writeoff project=%s НЕ списываются %s заданий: нет карточки товара "
            "или склад продавца не привязан к нашему",
            project_id,
            blocked,
        )
    if not orders:
        return 0

    links_by_wb, qty_map, barcode_map = await _writeoff_context(db, project_id, orders)

    now = utcnow()
    written = 0
    skipped_no_stock = 0

    for order in orders:
        nom_id = order.nomenclature_id
        candidates = links_by_wb.get(order.wb_warehouse_id or 0, [])
        # Выборка уже отсекла задания без карточки и без привязки; здесь остаётся
        # гонка «настройку сняли между SELECT и этим моментом» — редко, но возможно.
        if nom_id is None or not candidates:
            skipped_no_stock += 1
            continue
        # Из нескольких привязок берём ту, где остаток реально есть.
        target = next((wid for wid in candidates if qty_map.get((wid, nom_id), 0) >= 1), None)
        if target is None:
            skipped_no_stock += 1
            continue
        barcode = order.barcode or barcode_map.get((target, nom_id))
        if not barcode:
            skipped_no_stock += 1
            continue

        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=target,
            nomenclature_id=nom_id,
            barcode=barcode,
            delta=-1,  # одно задание WB = одна единица товара
            movement_type=MovementType.OUTBOUND,
            reference_type=_WRITEOFF_REF_TYPE,
            reference_id=order.id,
            comment=f"FBS-задание {order.wb_order_id}",
        )
        qty_map[(target, nom_id)] = qty_map.get((target, nom_id), 0) - 1
        order.written_off_at = now
        written += 1

    await db.commit()
    if written:
        await invalidate_cache(CACHE_ORDERS)
        await invalidate_cache("reports:warehouse_need")
    if skipped_no_stock:
        logger.warning(
            "wb_fbs.orders.writeoff project=%s written=%s skipped: no_stock=%s",
            project_id,
            written,
            skipped_no_stock,
        )
    else:
        logger.info("wb_fbs.orders.writeoff project=%s written=%s", project_id, written)
    return written


# ─── Видимость незакрытых списаний ──────────────────────────────────────────


@cached(prefix="fbs:orders", ttl=60)  # литерал: гейт ищет префикс регуляркой
async def writeoff_issues(db: AsyncSession, project_id: int, *, kind: str = "writeoff_issues") -> dict:
    """Сводка «передано, но не списано» (`FbsWriteoffIssuesOut`) — агрегат по товару.

    Списание идемпотентно ретраится каждые 5 минут, но пока причина жива,
    задание молча висит `written_off_at IS NULL`, а единственным следом был
    warning в логе воркера (прод 29.07: 145 заданий). Ручка делает отказ видимым.

    Причина (`reason`) — по приоритету: `no_card` (нет карточки товара) →
    `no_link` (склад продавца не привязан к активному нашему ЖИВОМУ складу;
    NULL-склад — тоже сюда: `IN (подзапрос)` с NULL не матчится и в списание
    такое задание не попадает никогда; мягко удалённый склад из привязок
    выпадает — см. `_active_links_subquery`) → `no_stock` (всё привязано, но
    остатка не хватает — гвард «не в минус» держит задание в очереди).
    Пост-классификация после обогащения: `no_stock` при `our_qty >= stuck`
    (остатка хватает на все задания группы) — это `queued`, не проблема, а
    очередь до ближайшего прогона; частичный дефицит (1 ≤ our_qty < stuck)
    остаётся `no_stock` — он важнее.

    Агрегат по (склад продавца, товар, причина); строк наружу — не больше
    `_WRITEOFF_ISSUES_MAX_ROWS` (по убыванию `stuck`), срез виден флагом
    `truncated`; полный масштаб — в `total_orders`, он считается ДО среза.
    Обогащение — батчами, без N+1. `ff_loose` (россыпь зеркала ФФ по
    привязанным складам) отвечает на вопрос «товар физически есть у
    провайдера?»: None — зеркала у привязанных складов нет вовсе (сравнивать
    не с чем), число — сигнал, что наш ledger отстал.

    Кэш: ключ строится из имён аргументов, поэтому `kind` — дискриминатор
    (паттерн `warehouse_service._offices_cached`): без него ключ совпадал бы
    с `warehouse_summary(db, project_id)` под тем же префиксом. `project_id`
    в ключе есть всегда. Инвалидация — та же, что у списка заданий: все
    мутации заданий гасят `CACHE_ORDERS`.
    """
    pending = _pending_writeoff_conditions(project_id)
    linked = _active_links_subquery(project_id)

    reason = case(
        (WbFbsOrder.nomenclature_id.is_(None), _WRITEOFF_ISSUE_NO_CARD),
        (
            or_(
                WbFbsOrder.wb_warehouse_id.is_(None),
                WbFbsOrder.wb_warehouse_id.notin_(linked),
            ),
            _WRITEOFF_ISSUE_NO_LINK,
        ),
        else_=_WRITEOFF_ISSUE_NO_STOCK,
    ).label("reason")

    total_orders = int(
        (
            await db.execute(select(func.count()).select_from(WbFbsOrder).where(*pending))
        ).scalar()
        or 0
    )
    if not total_orders:
        return {"total_orders": 0, "rows": [], "truncated": False}

    grouped = await db.execute(
        select(
            WbFbsOrder.wb_warehouse_id,
            WbFbsOrder.nomenclature_id,
            reason,
            func.count().label("stuck"),
            func.min(WbFbsOrder.created_at_wb).label("oldest_at"),
            # Фолбэк для строк без карточки: артикул/ШК из самого задания.
            func.max(WbFbsOrder.article).label("order_article"),
            func.max(WbFbsOrder.barcode).label("order_barcode"),
        )
        .where(*pending)
        .group_by(WbFbsOrder.wb_warehouse_id, WbFbsOrder.nomenclature_id, reason)
        .order_by(func.count().desc(), func.min(WbFbsOrder.created_at_wb).asc().nullslast())
        # +1 строка сверх капа — дешёвый детектор среза для флага `truncated`.
        .limit(_WRITEOFF_ISSUES_MAX_ROWS + 1)
    )
    groups = grouped.all()
    truncated = len(groups) > _WRITEOFF_ISSUES_MAX_ROWS
    groups = groups[:_WRITEOFF_ISSUES_MAX_ROWS]
    if not groups:
        return {"total_orders": total_orders, "rows": [], "truncated": False}

    wb_wh_ids = sorted({g.wb_warehouse_id for g in groups if g.wb_warehouse_id})
    nom_ids = sorted({g.nomenclature_id for g in groups if g.nomenclature_id})

    # Имена складов продавца.
    wb_wh_names: dict[int, str | None] = {}
    if wb_wh_ids:
        names_result = await db.execute(
            select(WbFbsWarehouse.wb_warehouse_id, WbFbsWarehouse.name).where(
                WbFbsWarehouse.project_id == project_id,
                WbFbsWarehouse.wb_warehouse_id.in_(wb_wh_ids),
            )
        )
        wb_wh_names = {int(r[0]): r[1] for r in names_result.all()}

    # Активные привязки (живые склады): {wb_warehouse_id: [(warehouse_id, name)]}.
    links_by_wb: dict[int, list[tuple[int, str | None]]] = {}
    if wb_wh_ids:
        links_result = await db.execute(
            select(WbFbsWarehouseLink.wb_warehouse_id, WbFbsWarehouseLink.warehouse_id, Warehouse.name)
            .join(Warehouse, Warehouse.id == WbFbsWarehouseLink.warehouse_id)
            .where(
                WbFbsWarehouseLink.project_id == project_id,
                WbFbsWarehouseLink.is_active == True,  # noqa: E712 — SQLAlchemy expression
                WbFbsWarehouseLink.wb_warehouse_id.in_(wb_wh_ids),
                Warehouse.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
            )
            .order_by(WbFbsWarehouseLink.id)
        )
        for wb_wh_id, warehouse_id, wh_name in links_result.all():
            links_by_wb.setdefault(int(wb_wh_id), []).append((int(warehouse_id), wh_name))

    all_wh_ids = sorted({wid for pairs in links_by_wb.values() for wid, _ in pairs})

    # Остатки ledger'а по привязанным складам.
    qty_map: dict[tuple[int, int], tuple[int, int]] = {}
    if all_wh_ids and nom_ids:
        stock_result = await db.execute(
            select(
                WarehouseStock.warehouse_id,
                WarehouseStock.nomenclature_id,
                WarehouseStock.quantity,
                WarehouseStock.defect_quantity,
            ).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(all_wh_ids),
                WarehouseStock.nomenclature_id.in_(nom_ids),
            )
        )
        for wid, nid, qty, defect in stock_result.all():
            qty_map[(int(wid), int(nid))] = (int(qty or 0), int(defect or 0))

    # Зеркало ФФ: у каких привязанных складов оно вообще есть + россыпь по товару.
    mirror_wh: set[int] = set()
    loose_map: dict[tuple[int, int], int] = {}
    if all_wh_ids:
        mirror_result = await db.execute(
            select(FulfillmentStock.warehouse_id)
            .where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id.in_(all_wh_ids),
            )
            .distinct()
        )
        mirror_wh = {int(r[0]) for r in mirror_result.all()}
    if mirror_wh and nom_ids:
        loose_result = await db.execute(
            select(
                FulfillmentStock.warehouse_id,
                FulfillmentStock.nomenclature_id,
                func.sum(FulfillmentStock.qty_good * FulfillmentStock.units_per_box),
            )
            .where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id.in_(sorted(mirror_wh)),
                FulfillmentStock.nomenclature_id.in_(nom_ids),
                FulfillmentStock.base_barcode.is_(None),  # только россыпь
            )
            .group_by(FulfillmentStock.warehouse_id, FulfillmentStock.nomenclature_id)
        )
        for wid, nid, pieces in loose_result.all():
            loose_map[(int(wid), int(nid))] = int(pieces or 0)

    # Карточки товара: артикул/ШК.
    nom_info: dict[int, tuple[str | None, str | None]] = {}
    if nom_ids:
        nom_result = await db.execute(
            select(Nomenclature.id, Nomenclature.article_seller, Nomenclature.barcode).where(
                Nomenclature.project_id == project_id,
                Nomenclature.id.in_(nom_ids),
            )
        )
        nom_info = {int(r[0]): (r[1], r[2]) for r in nom_result.all()}

    rows: list[dict[str, Any]] = []
    for g in groups:
        pairs = links_by_wb.get(g.wb_warehouse_id or 0, [])
        first_wh_id, first_wh_name = pairs[0] if pairs else (None, None)
        our_qty = our_defect = 0
        ff_loose: int | None = None
        if g.nomenclature_id is not None and pairs:
            for wid, _name in pairs:
                q, d = qty_map.get((wid, g.nomenclature_id), (0, 0))
                our_qty += q
                our_defect += d
            if any(wid in mirror_wh for wid, _name in pairs):
                ff_loose = sum(loose_map.get((wid, g.nomenclature_id), 0) for wid, _name in pairs)
        article, barcode = nom_info.get(g.nomenclature_id or 0, (None, None))
        stuck = int(g.stuck or 0)
        # Пост-классификация: остатка хватает на ВСЕ задания группы → это не
        # алярм «нечем списать», а очередь до ближайшего 5-минутного прогона.
        # Частичный дефицит (1 ≤ our_qty < stuck) важнее — остаётся no_stock.
        reason_code = g.reason
        if reason_code == _WRITEOFF_ISSUE_NO_STOCK and our_qty >= stuck:
            reason_code = _WRITEOFF_ISSUE_QUEUED
        rows.append(
            {
                "wb_warehouse_id": g.wb_warehouse_id,
                "wb_warehouse_name": wb_wh_names.get(g.wb_warehouse_id or 0),
                "warehouse_id": first_wh_id,
                "warehouse_name": first_wh_name,
                "nomenclature_id": g.nomenclature_id,
                "article": article or g.order_article,
                "barcode": barcode or g.order_barcode,
                "stuck": stuck,
                "oldest_at": g.oldest_at,
                "our_qty": our_qty,
                "our_defect": our_defect,
                "ff_loose": ff_loose,
                "reason": reason_code,
            }
        )
    return {"total_orders": total_orders, "rows": rows, "truncated": truncated}


__all__ = [
    "FbsOrderError",
    "backfill_orders_history",
    "cancel_order",
    "get_order_timeline",
    "get_stickers",
    "list_orders",
    "order_status_snapshot",
    "record_order_events",
    "sync_new_orders",
    "sync_order_statuses",
    "sync_orders_recent",
    "writeoff_completed_orders",
    "writeoff_issues",
]
