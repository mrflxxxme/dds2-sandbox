# ruff: noqa: RUF001, RUF002, RUF003
"""
Service: WB FBS — сборочные задания (заказы со склада продавца).

Зеркало Marketplace API v3: `GET /api/v3/orders/new` → upsert по
`(project_id, wb_order_id)`, `POST /api/v3/orders/status` → досинк статусов,
стикеры, отмена и списание проданного из нашего документного ledger'а.

Инварианты домена:
  • Одно задание WB = ОДНА единица товара (WB не агрегирует количество),
    поэтому списание всегда −1 на задание.
  • Цены приходят от WB В КОПЕЙКАХ (×100) — делим на 100 и кладём в Numeric(18,2).
  • `createdAt` — RFC3339 со смещением, а колонки БД naive → приводим к UTC
    и снимаем tzinfo (иначе asyncpg роняет TIMESTAMP WITHOUT TIME ZONE).
  • `supplier_status` двигают ТОЛЬКО WB-статусы и методы поставок: синк новых
    заданий не перетирает статус уже известного задания (иначе `complete`
    откатился бы в `new` и товар списался бы повторно).
  • Списание в ledger строго идемпотентно по `written_off_at`, не уводит
    остаток в минус и пишет `StockMovement` типа OUTBOUND.
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

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models import (
    FBS_TERMINAL_STATUSES,
    FbsSupplierStatus,
    Nomenclature,
    WbFbsOrder,
    WbFbsWarehouseLink,
)
from backend.models.warehouse import MovementType, StockMovement, WarehouseStock
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

#: `reference_type` движения склада для FBS-продажи (String(30)).
_WRITEOFF_REF_TYPE = "FBS_ORDER"

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
) -> dict[str, Any] | None:
    """Payload WB → строка `wb_fbs_orders`. None, если нет `id` задания."""
    wb_order_id = _int_or_none(raw.get("id"))
    if not wb_order_id:
        return None

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
        "created_at_wb": _parse_wb_datetime(raw.get("createdAt")),
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


async def _upsert_orders(db: AsyncSession, project_id: int, raw_orders: list[dict]) -> int:
    """UPSERT заданий по natural key `(project_id, wb_order_id)`.

    Дедуп ключей в Python ДО executemany — иначе PG роняет CardinalityViolation
    («ON CONFLICT не может обновить строку дважды»).
    """
    deduped: dict[int, dict] = {}
    for raw in raw_orders:
        if not isinstance(raw, dict):
            continue
        wb_order_id = _int_or_none(raw.get("id"))
        if wb_order_id:
            deduped[wb_order_id] = raw  # последнее вхождение выигрывает
    if not deduped:
        return 0

    payloads = list(deduped.values())
    by_chrt, by_barcode = await _resolve_nomenclature(
        db,
        project_id,
        (_int_or_none(p.get("chrtId")) or 0 for p in payloads),
        (_first_sku(p) or "" for p in payloads),
    )

    sync_ts = utcnow()
    rows = [row for p in payloads if (row := _order_row(p, project_id, sync_ts, by_chrt, by_barcode))]
    if not rows:
        return 0

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

    await db.commit()
    await invalidate_cache(CACHE_ORDERS)
    return len(rows)


async def sync_new_orders(db: AsyncSession, project_id: int) -> int:
    """Забрать `GET /api/v3/orders/new` и зеркалировать в БД. Возвращает число строк."""
    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    raw_orders = await client.get_new_orders()
    if not raw_orders:
        return 0

    count = await _upsert_orders(db, project_id, raw_orders)
    logger.info("wb_fbs.orders.sync_new project=%s upserted=%s", project_id, count)
    return count


async def _apply_statuses(db: AsyncSession, project_id: int, items: list[dict]) -> int:
    """Записать статусы пачкой: группируем по одинаковой тройке значений."""
    groups: dict[tuple[str, str | None, bool], list[int]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        wb_order_id = _int_or_none(item.get("id"))
        status = _str_or_none(item.get("supplierStatus"), 20)
        if not wb_order_id or not status or status not in _VALID_SUPPLIER_STATUSES:
            continue
        key = (status, _str_or_none(item.get("wbStatus"), 30), bool(item.get("isCancellable", False)))
        groups.setdefault(key, []).append(wb_order_id)

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
            WbFbsOrder.supplier_status.notin_(FBS_TERMINAL_STATUSES),
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
    """Верхняя граница: для календарной даты — начало СЛЕДУЮЩИХ суток (строгое <)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value + timedelta(days=1), time.min)
    parsed = _parse_wb_datetime(str(value))
    if parsed is not None:
        return parsed
    parsed_date = _parse_wb_date(str(value))
    return datetime.combine(parsed_date + timedelta(days=1), time.min) if parsed_date else None


def _money(value: Decimal | None) -> str | None:
    """Деньги наружу — СТРОКОЙ.

    Ответ `list_orders` проходит через JSON-кэш (`@cached`), а его энкодер
    приводит `Decimal` к float — 1379.99 вернулся бы как 1379.9899999999998.
    Pydantic парсит строку в `Decimal` точно, и на выходе API ничего не меняется.
    """
    return None if value is None else str(value)


def _order_to_dict(order: WbFbsOrder) -> dict[str, Any]:
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

    Выдача скоуплена по контуру: боевой список не должен показывать задания
    песочницы (и наоборот) — цифры вкладок обязаны совпадать с тем, что
    участвует в остатке и списании.
    """
    limit = max(1, min(int(limit or 100), _LIST_MAX_LIMIT))
    offset = max(0, int(offset or 0))

    base = [WbFbsOrder.project_id == project_id, contour_condition(WbFbsOrder.raw)]
    if supply_id:
        base.append(WbFbsOrder.supply_id == supply_id)
    if wb_warehouse_id:
        base.append(WbFbsOrder.wb_warehouse_id == wb_warehouse_id)
    dt_from = _as_dt_from(date_from)
    if dt_from is not None:
        base.append(WbFbsOrder.created_at_wb >= dt_from)
    dt_to = _as_dt_to(date_to)
    if dt_to is not None:
        base.append(WbFbsOrder.created_at_wb < dt_to)

    counts_result = await db.execute(
        select(WbFbsOrder.supplier_status, func.count())
        .where(*base)
        .group_by(WbFbsOrder.supplier_status)
    )
    status_counts = {row[0]: int(row[1]) for row in counts_result.all()}

    conditions = list(base)
    if status:
        conditions.append(WbFbsOrder.supplier_status == status)
        total = status_counts.get(status, 0)
    else:
        total = sum(status_counts.values())

    items_result = await db.execute(
        select(WbFbsOrder)
        .where(*conditions)
        .order_by(WbFbsOrder.created_at_wb.desc().nullslast(), WbFbsOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [_order_to_dict(order) for order in items_result.scalars().all()]
    return {"items": items, "total": total, "status_counts": status_counts}


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
        select(WbFbsOrder.supplier_status, WbFbsOrder.is_cancellable).where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.wb_order_id == wb_order_id,
        )
    )
    row = result.first()
    if row is None:
        raise FbsOrderError(f"Сборочное задание {wb_order_id} не найдено в проекте")
    status, is_cancellable = row
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
    await db.commit()
    await invalidate_cache(CACHE_ORDERS)


# ─── Списание проданного в ledger ───────────────────────────────────────────


async def _writeoff_context(
    db: AsyncSession, project_id: int, orders: list[WbFbsOrder]
) -> tuple[dict[int, list[int]], dict[tuple[int, int], int], dict[tuple[int, int], str]]:
    """Батч-контекст списания: привязки складов и текущие остатки. Без N+1."""
    wb_wh_ids = sorted({o.wb_warehouse_id for o in orders if o.wb_warehouse_id})
    links_by_wb: dict[int, list[int]] = {}
    if wb_wh_ids:
        links_result = await db.execute(
            select(WbFbsWarehouseLink.wb_warehouse_id, WbFbsWarehouseLink.warehouse_id)
            .where(
                WbFbsWarehouseLink.project_id == project_id,
                WbFbsWarehouseLink.is_active == True,  # noqa: E712
                WbFbsWarehouseLink.wb_warehouse_id.in_(wb_wh_ids),
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
    """Тело списания под локом (см. `writeoff_completed_orders`)."""
    # Задания, которые списать НЕЧЕМ, отсекаются в SQL, а не пропускаются в цикле.
    # Иначе они навсегда занимают голову очереди `LIMIT`: метку `written_off_at`
    # им никто не ставит, порядок по id — от старых к новым, и как только таких
    # накопится _WRITEOFF_MAX_ORDERS, каждый прогон выбирает ровно их, а НОВЫЕ
    # продажи перестают списываться со склада — молча, одним warning'ом в лог.
    # Две причины из трёх постоянные по своей природе: нет карточки товара и
    # нет привязки склада продавца к нашему.
    linked = select(WbFbsWarehouseLink.wb_warehouse_id).where(
        WbFbsWarehouseLink.project_id == project_id,
        WbFbsWarehouseLink.is_active == True,  # noqa: E712 — SQLAlchemy expression
    )
    pending = [
        WbFbsOrder.project_id == project_id,
        WbFbsOrder.supplier_status == FbsSupplierStatus.COMPLETE.value,
        WbFbsOrder.written_off_at.is_(None),
        contour_condition(WbFbsOrder.raw),
    ]
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


__all__ = [
    "FbsOrderError",
    "cancel_order",
    "get_stickers",
    "list_orders",
    "sync_new_orders",
    "sync_order_statuses",
    "writeoff_completed_orders",
]
