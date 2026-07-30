# ruff: noqa: RUF001, RUF002, RUF003
"""
Service: WB FBS — поставки (`WB-GI-…`), контейнеры сборочных заданий.

Зеркало `GET /api/v3/supplies` + write-путь: создать → добавить задания →
передать → QR. Логика ровно та, которую диктует WB:

  • ГАБАРИТНЫЙ ЗАЛИПОН. Пустая поставка не имеет `cargoType`; первое
    добавленное задание фиксирует `cargoType` и `crossBorderType`, дальше WB
    принимает только такие же, а задания с РАЗНЫХ складов продавца в одну
    поставку нельзя вообще. Проверяем ЛОКАЛЬНО до вызова WB: 4XX стоит
    10 запросов бакета, слепой ретрай выжигает лимит в 30 раз быстрее.
  • Статусы заданий двигают методы поставок: добавили → `confirm`,
    передали → `complete`. Отдельных PATCH-ов у FBS нет.
  • Поставка закрывается автоматически при приёмке первого товара —
    доложить после этого нельзя, поэтому `done` из WB авторитетен.
  • QR (`/barcode`) доступен ТОЛЬКО после deliver, иначе WB отвечает
    409 `SupplyNotClosed`.
  • НЕСКОЛЬКО СКЛАДОВ = НЕСКОЛЬКО ПОСТАВОК. Прямое следствие первых двух
    правил: выделенные задания разбиваются на группы
    (склад продавца, cargoType, crossBorderType), и на каждую группу заводится
    своя поставка (`plan_supplies` — предпросмотр без единого запроса к WB,
    `create_supplies_bulk` — исполнение). Разбивать обязаны мы: пользователь,
    работающий с двух складов, иначе получал бы сырую 409 от WB.

`await db.commit()` до каждого похода в WB — транзакцию через внешний HTTP
не держим (pgbouncer-пул выедается `idle in transaction`).
"""

import asyncio
import logging
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import and_, cast, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.integrations.wb_fbs_api import WbFbsRateLimited, WbFbsWriteBlocked
from backend.models import (
    FBS_TERMINAL_STATUSES,
    FbsSupplierStatus,
    Nomenclature,
    WbFbsOrder,
    WbFbsSupply,
    WbFbsWarehouse,
)

# Прямо из подмодуля — как в `stock_service` / `orders_stats`: производный
# статус поставки в реэкспорт `backend.models` не вынесен.
from backend.models.wb_fbs import FbsSupplyStatus, supply_status
from backend.services.wb_fbs import orders_service
from backend.services.wb_fbs.client_factory import get_fbs_client
from backend.services.wb_fbs.contour import contour_condition, stamp_contour
from backend.services.wb_fbs.orders_service import (
    CACHE_ORDERS,
    _int_or_none,
    _order_to_dict,
    _parse_wb_datetime,
    _str_or_none,
    _transit_days,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.wb_fbs.supplies")

# ─── Константы ──────────────────────────────────────────────────────────────

_UPSERT_CHUNK = 500
#: WB принимает до 100 заданий за один `PATCH /supplies/{id}/orders`.
_ADD_ORDERS_MAX = 100
_LIST_MAX_LIMIT = 500
#: Префикс кэша чтения. В декораторе — строковым литералом (см. orders_service).
CACHE_SUPPLIES = "fbs:supplies"

#: Сколько «чужих» поставок за прогон досинкиваем составом через
#: `/order-ids` (по одному HTTP на поставку — держим в узде).
#:
#: 25, а не 10: закрытая поставка спрашивается ОДИН раз за всю жизнь, и на
#: десятке исторический хвост (52 поставки на проде) разбирался бы больше часа,
#: всё это время показывая «заданий 0». Джоб ходит раз в 15 минут — верхняя
#: нагрузка выходит 25 запросов на четверть часа, а недобранный остаток
#: пишется в лог (`_pull_missing_order_ids`): молча потолок не срезает.
_ORDER_IDS_FETCH_CAP = 25

#: Нейтральный элемент для мержа JSONB (`raw || excluded.raw`): NULL с одной
#: стороны обнулил бы весь результат, поэтому обе стороны страхуются coalesce'ом.
_EMPTY_JSONB = cast(literal("{}"), JSONB)

#: Ключ в `wb_fbs_supplies.raw`: сколько заданий у поставки ПО ДАННЫМ WB
#: (ответ `/order-ids`). Отдельной колонки нет намеренно — метка живёт в уже
#: существующей JSONB, как и метка контура (миграция не нужна).
#:
#: Зачем: наше зеркало заданий наполняется ТОЛЬКО из `GET /orders/new`, а
#: задание, положенное в поставку прямо в кабинете WB, «новым» больше не
#: приходит и в `wb_fbs_orders` не попадает НИКОГДА. Поэтому «в зеркале ноль
#: заданий» ≠ «поставка пуста»: без этой метки кабинетная поставка вечно
#: предлагалась бы к доклажу и вечно ловила 409 (цена 4XX — 10 запросов бакета).
WB_ORDERS_KEY = "_dds_wb_orders"

#: Из этих статусов задание ещё можно положить в поставку.
_ADDABLE_STATUSES: tuple[str, ...] = (
    FbsSupplierStatus.NEW.value,
    FbsSupplierStatus.CONFIRM.value,
)

#: Потолок разового плана — зеркалит `FbsSupplyBulkCreate.order_ids`.
_MAX_PLAN_ORDERS = 2000
#: Сколько активных поставок рассматриваем кандидатами на доклад.
_ACTIVE_SUPPLY_SCAN = 200
#: Потолок выдачи состава поставки.
_SUPPLY_ORDERS_LIMIT = 2000
#: Потолок групп листа подбора (позиция × статус). Реальная поставка WB столько
#: не набирает, но выборку без потолка держать нельзя.
_PICK_LIST_MAX_GROUPS = 5000
#: Потолок резолва «баркод строки листа → наши номенклатуры».
_PICK_NOM_LOOKUP_LIMIT = 5000
#: WB: имя поставки 1..128 символов.
_SUPPLY_NAME_MAX = 128

#: Почему задание не годится в НОВУЮ поставку. Текст видит пользователь.
_STATUS_BLOCK_REASON: dict[str, str] = {
    FbsSupplierStatus.CONFIRM.value: "уже добавлено в поставку в кабинете WB",
    FbsSupplierStatus.COMPLETE.value: "уже в доставке — поставка передана",
    FbsSupplierStatus.CANCEL.value: "отменено",
    FbsSupplierStatus.CANCEL_CARRIER.value: "отменено перевозчиком",
}
_MISSING_BLOCK_REASON = "не найдено в зеркале — синхронизируйте задания"


class FbsSupplyError(Exception):
    """Доменная ошибка поставок FBS — роутер отдаёт её как 400."""


def _chunks(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _wb_orders_count(raw: Any) -> int | None:
    """Сколько заданий у поставки по данным WB. None — состав ещё не проверяли."""
    if not isinstance(raw, dict):
        return None
    value = raw.get(WB_ORDERS_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ─── Нормализация payload'а WB ──────────────────────────────────────────────


def _supply_row(raw: dict, project_id: int, sync_ts: datetime) -> dict[str, Any] | None:
    """Payload WB → строка `wb_fbs_supplies`. None, если нет id поставки."""
    wb_supply_id = _str_or_none(raw.get("id"), 50)
    if not wb_supply_id:
        return None
    return {
        "project_id": project_id,
        "wb_supply_id": wb_supply_id,
        "name": _str_or_none(raw.get("name"), 128),
        "done": bool(raw.get("done", False)),
        "created_at_wb": _parse_wb_datetime(raw.get("createdAt")),
        "closed_at": _parse_wb_datetime(raw.get("closedAt")),
        "scan_dt": _parse_wb_datetime(raw.get("scanDt")),
        # Единственный признак отклонённой приёмки: своего поля статуса у
        # поставки нет, и без `rejectDt` отказ выглядел бы как «Передана».
        "reject_dt": _parse_wb_datetime(raw.get("rejectDt")),
        "cargo_type": _int_or_none(raw.get("cargoType")),
        "cross_border_type": _int_or_none(raw.get("crossBorderType")),
        "is_b2b": bool(raw.get("isB2b", False)),
        "is_pickup_point_shipment_allowed": bool(raw.get("isPickupPointShipmentAllowed", False)),
        "destination_office_id": _int_or_none(raw.get("destinationOfficeId")),
        "recommended_wh_id": _int_or_none(raw.get("recommendedWhId")),
        "orders_count": 0,  # пересчитывается из наших заданий (_recount_orders)
        # Метка контура — как у заданий: боевой экран не должен видеть поставки
        # песочницы, а план — предлагать доклад в них боевым токеном.
        "raw": stamp_contour(raw),
        "synced_at": sync_ts,
        "created_at": sync_ts,
        "updated_at": sync_ts,
    }


async def _upsert_supplies(db: AsyncSession, project_id: int, rows: list[dict]) -> int:
    """UPSERT по natural key `(project_id, wb_supply_id)` с дедупом в Python."""
    if not rows:
        return 0
    for chunk in _chunks(rows, _UPSERT_CHUNK):
        stmt = pg_insert(WbFbsSupply).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_wb_fbs_supply",
            set_={
                "name": func.coalesce(stmt.excluded.name, WbFbsSupply.name),
                "done": stmt.excluded.done,
                "created_at_wb": func.coalesce(stmt.excluded.created_at_wb, WbFbsSupply.created_at_wb),
                "closed_at": func.coalesce(stmt.excluded.closed_at, WbFbsSupply.closed_at),
                "scan_dt": func.coalesce(stmt.excluded.scan_dt, WbFbsSupply.scan_dt),
                # Отказ — необратимый факт: раз доехав, он не должен исчезать
                # от прогона, где WB отдал `rejectDt: null` (поля списочного
                # метода приходят непостоянно — та же страховка, что у closed_at).
                "reject_dt": func.coalesce(stmt.excluded.reject_dt, WbFbsSupply.reject_dt),
                # Списочный метод WB не отдаёт габариты — не затираем то,
                # что зафиксировал первый добавленный заказ.
                "cargo_type": func.coalesce(stmt.excluded.cargo_type, WbFbsSupply.cargo_type),
                "cross_border_type": func.coalesce(
                    stmt.excluded.cross_border_type, WbFbsSupply.cross_border_type
                ),
                "is_b2b": stmt.excluded.is_b2b,
                "is_pickup_point_shipment_allowed": stmt.excluded.is_pickup_point_shipment_allowed,
                "destination_office_id": func.coalesce(
                    stmt.excluded.destination_office_id, WbFbsSupply.destination_office_id
                ),
                "recommended_wh_id": func.coalesce(
                    stmt.excluded.recommended_wh_id, WbFbsSupply.recommended_wh_id
                ),
                # orders_count / wb_warehouse_id / qr_* — наши, не из списка WB.
                # `raw` МЕРЖИМ, а не перезаписываем: в нём живут не только поля
                # WB, но и наши метки — прежде всего `_dds_wb_orders` (id заданий
                # поставки, докладываемые отдельным запросом). Прямая перезапись
                # стирала их у ВСЕХ поставок каждые 15 минут, а восстановить их
                # успевало не больше `_ORDER_IDS_FETCH_CAP` штук за прогон:
                # остальные навсегда выпадали из кандидатов на дозаполнение, и
                # вместо переиспользования пустой поставки создавалась новая.
                # Порядок операндов важен: свежие поля WB перекрывают старые,
                # наши метки переживают синк, потому что в `excluded` их нет.
                "raw": func.coalesce(WbFbsSupply.raw, _EMPTY_JSONB).op("||")(
                    func.coalesce(stmt.excluded.raw, _EMPTY_JSONB)
                ),
                "synced_at": stmt.excluded.synced_at,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await db.execute(stmt)
    return len(rows)


async def _recount_orders(db: AsyncSession, project_id: int, wb_supply_id: str | None = None) -> None:
    """Пересчитать `orders_count` и склад поставки из наших заданий.

    Одним UPDATE с коррелированными подзапросами — никаких N+1 по поставкам.
    """
    count_sq = (
        select(func.count())
        .select_from(WbFbsOrder)
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.supply_id == WbFbsSupply.wb_supply_id,
        )
        .correlate(WbFbsSupply)
        .scalar_subquery()
    )
    warehouse_sq = (
        select(func.min(WbFbsOrder.wb_warehouse_id))
        .select_from(WbFbsOrder)
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.supply_id == WbFbsSupply.wb_supply_id,
        )
        .correlate(WbFbsSupply)
        .scalar_subquery()
    )
    stmt = update(WbFbsSupply).where(WbFbsSupply.project_id == project_id)
    if wb_supply_id:
        stmt = stmt.where(WbFbsSupply.wb_supply_id == wb_supply_id)
    await db.execute(
        stmt.values(
            orders_count=count_sq,
            wb_warehouse_id=func.coalesce(WbFbsSupply.wb_warehouse_id, warehouse_sq),
        )
    )


def _order_ids_candidates() -> Any:
    """Кого досинкиваем составом через `/order-ids`.

    Две РАЗНЫЕ ветки, потому что разная природа данных:

    • **активные** (`done = false`) с нулём заданий в зеркале — каждый прогон:
      в такую поставку докладывают прямо в кабинете, состав живой, и только
      свежая метка даёт право предложить её к доклажу (`_load_active_supply_fits`);
    • **закрытые** — РОВНО ОДИН раз, пока метки `raw[WB_ORDERS_KEY]` нет:
      закрытая поставка неизменна, повторный запрос — сожжённый запрос бакета.

    Раньше здесь стоял голый гейт `done == False`, и закрытые не спрашивались
    НИКОГДА: 52 поставки с прода вечно показывали «заданий 0», хотя в кабинете
    у них 22 / 11 / 8 / 7 / 9.
    """
    return and_(
        contour_condition(WbFbsSupply.raw),
        or_(
            and_(
                WbFbsSupply.done == False,  # noqa: E712
                WbFbsSupply.orders_count == 0,
            ),
            and_(
                WbFbsSupply.done == True,  # noqa: E712
                # `->> ключ IS NULL` вместо оператора `?`: покрывает и NULL-raw,
                # и отсутствие ключа, и не требует эскейпа плейсхолдера.
                WbFbsSupply.raw[WB_ORDERS_KEY].astext.is_(None),
            ),
        ),
    )


async def _pull_missing_order_ids(db: AsyncSession, project_id: int, client: Any) -> int:
    """Досинк состава поставок, набранных вне нашей системы.

    Наши поставки помечают задания сами (`add_orders`), поэтому в WB ходим
    только за теми, чей состав нам взяться неоткуда (`_order_ids_candidates`),
    и не больше `_ORDER_IDS_FETCH_CAP` штук за прогон — по одному HTTP на
    поставку. Недобранный хвост пишем в лог: молчащий потолок — это ровно то,
    из-за чего «заданий 0» держалось незамеченным.

    Фактическое число заданий из ответа WB кладём в `raw[WB_ORDERS_KEY]`, даже
    когда ни один id не нашёлся в нашем зеркале: «в зеркале пусто» и «в WB
    пусто» — РАЗНЫЕ факты, и только второй разрешает доклад в эту поставку
    (`_load_active_supply_fits`). Активной метку переписывает каждый синк — она
    всегда свежая, а непроверенная поставка кандидатом не считается.
    """
    candidates = _order_ids_candidates()
    result = await db.execute(
        select(WbFbsSupply.wb_supply_id, WbFbsSupply.raw)
        .where(WbFbsSupply.project_id == project_id, candidates)
        # Активные вперёд (false < true): их состав живой, а закрытые доберутся
        # следующими прогонами — они всё равно уже не изменятся.
        .order_by(
            WbFbsSupply.done.asc(),
            WbFbsSupply.created_at_wb.desc().nullslast(),
            WbFbsSupply.id.desc(),
        )
        .limit(_ORDER_IDS_FETCH_CAP)
    )
    targets: list[tuple[str, dict[str, Any]]] = [
        (row[0], dict(row[1]) if isinstance(row[1], dict) else {}) for row in result.all()
    ]
    if not targets:
        return 0

    if len(targets) == _ORDER_IDS_FETCH_CAP:
        total = await db.scalar(
            select(func.count())
            .select_from(WbFbsSupply)
            .where(WbFbsSupply.project_id == project_id, candidates)
        )
        leftover = max(0, int(total or 0) - len(targets))
        if leftover:
            logger.warning(
                "wb_fbs.supplies.order_ids cap reached project=%s fetched=%s pending=%s "
                "(доберём следующими прогонами)",
                project_id,
                len(targets),
                leftover,
            )

    await db.commit()  # не держим транзакцию через внешний HTTP

    linked = 0
    now = utcnow()
    for wb_supply_id, raw in targets:
        order_ids = await client.get_supply_order_ids(wb_supply_id)
        raw[WB_ORDERS_KEY] = len(order_ids)
        await db.execute(
            update(WbFbsSupply)
            .where(
                WbFbsSupply.project_id == project_id,
                WbFbsSupply.wb_supply_id == wb_supply_id,
            )
            .values(raw=raw, updated_at=now)
        )
        for chunk in _chunks(order_ids, _ADD_ORDERS_MAX):
            res = await db.execute(
                update(WbFbsOrder)
                .where(
                    WbFbsOrder.project_id == project_id,
                    WbFbsOrder.wb_order_id.in_(chunk),
                    WbFbsOrder.supply_id.is_(None),
                    contour_condition(WbFbsOrder.raw),
                )
                .values(supply_id=wb_supply_id, updated_at=now)
            )
            linked += res.rowcount or 0  # type: ignore[attr-defined]
        await db.commit()  # закрываем транзакцию ДО следующего похода в WB
    return linked


# ─── Синк ───────────────────────────────────────────────────────────────────


async def sync_supplies(db: AsyncSession, project_id: int) -> int:
    """Зеркалировать поставки WB в БД. Возвращает число обработанных строк."""
    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    raw_supplies = await client.list_supplies()

    sync_ts = utcnow()
    deduped: dict[str, dict] = {}
    for raw in raw_supplies:
        if not isinstance(raw, dict):
            continue
        wb_supply_id = _str_or_none(raw.get("id"), 50)
        if wb_supply_id:
            deduped[wb_supply_id] = raw  # дедуп ДО executemany (CardinalityViolation)

    rows = [row for raw in deduped.values() if (row := _supply_row(raw, project_id, sync_ts))]
    count = await _upsert_supplies(db, project_id, rows)
    await _recount_orders(db, project_id)
    await db.commit()

    linked = await _pull_missing_order_ids(db, project_id, client)
    if linked:
        await _recount_orders(db, project_id)
        await db.commit()

    await invalidate_cache(CACHE_SUPPLIES)
    if linked:
        await invalidate_cache(CACHE_ORDERS)
    logger.info("wb_fbs.supplies.sync project=%s upserted=%s linked_orders=%s", project_id, count, linked)
    return count


# ─── Чтение ─────────────────────────────────────────────────────────────────


def _supply_to_dict(supply: WbFbsSupply) -> dict[str, Any]:
    """Строка под `FbsSupplyOut` (контракт схем).

    Два числа заданий рядом и намеренно: `orders_count` — НАШЕ зеркало (на нём
    висят доклад и удаление), `wb_orders_count` — сколько их у WB. До бэкфилла
    истории первое почти везде 0, и подменять им второе значило бы врать
    ровно так же, как врал экран.

    `destination_office_name` здесь всегда None: справочник офисов — поход в WB,
    его резолвит `list_supplies` ОДНИМ запросом на страницу (см. `_office_names`).
    """
    return {
        "id": supply.id,
        "wb_supply_id": supply.wb_supply_id,
        "name": supply.name,
        "done": supply.done,
        "status": supply_status(supply.done, supply.scan_dt, supply.reject_dt),
        "created_at_wb": supply.created_at_wb,
        "closed_at": supply.closed_at,
        "scan_dt": supply.scan_dt,
        "reject_dt": supply.reject_dt,
        "cargo_type": supply.cargo_type,
        "cross_border_type": supply.cross_border_type,
        "is_b2b": supply.is_b2b,
        "destination_office_id": supply.destination_office_id,
        "destination_office_name": None,
        "wb_warehouse_id": supply.wb_warehouse_id,
        "orders_count": supply.orders_count,
        "wb_orders_count": _wb_orders_count(supply.raw),
        "qr_barcode": supply.qr_barcode,
    }


def _status_condition(status: str) -> Any:
    """Ярлык кабинета → условие WHERE. Зеркало `models.wb_fbs.supply_status`.

    Фильтруем в SQL, а не постфильтром по уже нарезанной странице: постфильтр
    отбрасывал бы строки ПОСЛЕ `limit/offset`, и вкладка «Отгрузите поставку»
    показывала бы то пусто, то половину при живых данных.
    """
    if status == FbsSupplyStatus.REJECTED.value:
        return WbFbsSupply.reject_dt.isnot(None)
    # Во всех остальных состояниях отказа нет — `rejectDt` перебивает всё.
    not_rejected = WbFbsSupply.reject_dt.is_(None)
    if status == FbsSupplyStatus.ACTIVE.value:
        return and_(not_rejected, WbFbsSupply.done == False)  # noqa: E712
    if status == FbsSupplyStatus.TO_SHIP.value:
        return and_(
            not_rejected,
            WbFbsSupply.done == True,  # noqa: E712
            WbFbsSupply.scan_dt.is_(None),
        )
    if status == FbsSupplyStatus.IN_DELIVERY.value:
        return and_(
            not_rejected,
            WbFbsSupply.done == True,  # noqa: E712
            WbFbsSupply.scan_dt.isnot(None),
        )
    # Молча снятый фильтр показал бы ВЕСЬ список под ярлыком вкладки — то же
    # враньё, что чиним. Роутер отсекает раньше (422), это defense in depth.
    raise FbsSupplyError(
        f"Неизвестный статус поставки «{status}». Допустимы: "
        + ", ".join(s.value for s in FbsSupplyStatus)
    )


async def _office_names(db: AsyncSession, project_id: int, office_ids: set[int]) -> dict[int, str]:
    """id пункта приёма WB → имя. ОДИН поход в справочник на всю страницу.

    Своей таблицы у офисов нет, источник — `GET /api/v3/offices` через
    `warehouse_service.list_offices` (кэш 10 минут). Резолв на каждой строке
    был бы N+1 по внешнему API с его лимитами.

    Справочник — УКРАШЕНИЕ строки, а не её суть: нет ключа интеграции, 429 или
    любой другой отказ WB не имеет права уронить список поставок, поэтому
    падение гасится в None с предупреждением в лог.
    """
    if not office_ids:
        return {}

    # Локальный импорт — как в соседних модулях домена (защита от цикла).
    from backend.services.wb_fbs import warehouse_service

    try:
        offices = await warehouse_service.list_offices(db, project_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("wb_fbs.supplies.offices lookup failed project=%s: %s", project_id, exc)
        return {}

    names: dict[int, str] = {}
    for office in offices:
        office_id = _int_or_none(office.get("id"))
        name = _str_or_none(office.get("name"), 200)
        if office_id and name:
            names[office_id] = name
    return names


@cached(prefix="fbs:supplies", ttl=60)
async def list_supplies(
    db: AsyncSession,
    project_id: int,
    *,
    done: bool | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Список поставок из зеркала. `done=False` — активные (можно доложить).

    `status` — точнее `done`: тот схлопывает в «закрыта» и «Отгрузите
    поставку», и «Поставка в обработке», и отклонённую приёмку, из-за чего наш
    экран расходился с кабинетом (52 «переданных» против «В доставке 44»).
    Оба фильтра оставлены и складываются друг с другом: `done` — часть
    прежнего контракта, на нём висят кнопки.

    Скоуп по контуру — как в списке заданий: боевой экран не должен показывать
    поставки песочницы (и наоборот).
    """
    limit = max(1, min(int(limit or 100), _LIST_MAX_LIMIT))
    offset = max(0, int(offset or 0))

    query = select(WbFbsSupply).where(
        WbFbsSupply.project_id == project_id,
        contour_condition(WbFbsSupply.raw),
    )
    if done is not None:
        query = query.where(WbFbsSupply.done == done)
    clean_status = (status or "").strip().lower()
    if clean_status:
        query = query.where(_status_condition(clean_status))
    result = await db.execute(
        query.order_by(WbFbsSupply.created_at_wb.desc().nullslast(), WbFbsSupply.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = [_supply_to_dict(supply) for supply in result.scalars().all()]

    # Строки уже материализованы в dict'ы: справочник коммитит сессию перед
    # HTTP (транзакцию через внешний вызов не держим), а после коммита
    # ORM-объекты протухают.
    office_ids = {r["destination_office_id"] for r in rows if r["destination_office_id"]}
    names = await _office_names(db, project_id, office_ids)
    if names:
        for row in rows:
            row["destination_office_name"] = names.get(row["destination_office_id"] or 0)
    return rows


async def _get_supply(db: AsyncSession, project_id: int, wb_supply_id: str) -> WbFbsSupply:
    result = await db.execute(
        select(WbFbsSupply).where(
            WbFbsSupply.project_id == project_id,
            WbFbsSupply.wb_supply_id == wb_supply_id,
            contour_condition(WbFbsSupply.raw),
        )
    )
    supply = result.scalar_one_or_none()
    if supply is None:
        raise FbsSupplyError(f"Поставка {wb_supply_id} не найдена в проекте (синкните список поставок)")
    return supply


# ─── Создание ───────────────────────────────────────────────────────────────


async def _register_created_supply(db: AsyncSession, project_id: int, wb_supply_id: str, name: str) -> None:
    """Завести зеркало только что созданной в WB поставки (без коммита).

    `raw` несёт метку контура (иначе поставка песочницы была бы неотличима от
    боевой) и подтверждённый нулевой состав: мы её только что завели, в WB она
    гарантированно пуста — значит доложить в неё можно без похода в `/order-ids`.
    """
    now = utcnow()
    stmt = pg_insert(WbFbsSupply).values(
        [
            {
                "project_id": project_id,
                "wb_supply_id": wb_supply_id,
                "name": name[:_SUPPLY_NAME_MAX],
                "done": False,
                "created_at_wb": now,
                "orders_count": 0,
                "raw": stamp_contour({WB_ORDERS_KEY: 0}),
                "synced_at": now,
                "created_at": now,
                "updated_at": now,
            }
        ]
    )
    await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_wb_fbs_supply",
            set_={"name": stmt.excluded.name, "synced_at": stmt.excluded.synced_at},
        )
    )


async def create_supply(db: AsyncSession, project_id: int, name: str) -> str:
    """Создать поставку в WB и завести её зеркало. Возвращает `WB-GI-…`."""
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > _SUPPLY_NAME_MAX:
        raise FbsSupplyError("Название поставки — от 1 до 128 символов")

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    wb_supply_id = await client.create_supply(clean_name)

    await _register_created_supply(db, project_id, wb_supply_id, clean_name)
    await db.commit()
    await invalidate_cache(CACHE_SUPPLIES)
    logger.info("wb_fbs.supplies.create project=%s supply=%s", project_id, wb_supply_id)
    return wb_supply_id


# ─── Добавление заданий (габаритный залипон) ────────────────────────────────


def _homogeneity_error(supply: WbFbsSupply, orders: list[WbFbsOrder]) -> str | None:
    """Локальная проверка «залипона» ДО вызова WB. None — всё однородно.

    WB фиксирует `cargoType`/`crossBorderType` поставки первым добавленным
    заданием и не принимает задания с другого склада продавца. Ловим это у
    себя: любой 4XX стоит 10 запросов бакета.
    """
    warehouses = {o.wb_warehouse_id for o in orders}
    if len(warehouses) > 1:
        listed = ", ".join(str(w) for w in sorted(warehouses, key=lambda x: (x is None, x)))
        return f"Задания с разных складов продавца WB ({listed}) нельзя класть в одну поставку"
    order_warehouse = next(iter(warehouses))
    if supply.wb_warehouse_id is not None and order_warehouse != supply.wb_warehouse_id:
        return (
            f"Поставка {supply.wb_supply_id} уже собирается со склада {supply.wb_warehouse_id}, "
            f"а задания — со склада {order_warehouse}"
        )

    # None и 0 — «габарит не указан», это одна и та же группа.
    cargo_types = {o.cargo_type or 0 for o in orders}
    if len(cargo_types) > 1:
        listed = ", ".join(str(c) for c in sorted(cargo_types))
        return f"Габаритный залипон: в поставке допустим один cargoType, а у заданий их несколько ({listed})"
    cross_types = {o.cross_border_type or 0 for o in orders}
    if len(cross_types) > 1:
        listed = ", ".join(str(c) for c in sorted(cross_types))
        return (
            "Габаритный залипон: в поставке допустим один crossBorderType, "
            f"а у заданий их несколько ({listed})"
        )

    order_cargo = next(iter(cargo_types))
    if supply.cargo_type is not None and (supply.cargo_type or 0) != order_cargo:
        return (
            f"Габаритный залипон: поставка {supply.wb_supply_id} зафиксирована на cargoType "
            f"{supply.cargo_type}, задания — {order_cargo or 'не указан'}"
        )
    order_cross = next(iter(cross_types))
    if supply.cross_border_type is not None and (supply.cross_border_type or 0) != order_cross:
        return (
            f"Габаритный залипон: поставка {supply.wb_supply_id} зафиксирована на crossBorderType "
            f"{supply.cross_border_type}, задания — {order_cross or 'не указан'}"
        )
    return None


async def _attach_orders_to_mirror(
    db: AsyncSession,
    project_id: int,
    wb_supply_id: str,
    ids: Sequence[int],
    *,
    wb_warehouse_id: int | None,
    cargo_type: int | None,
    cross_border_type: int | None,
) -> None:
    """Отразить в зеркале то, что WB уже принял: задания → `confirm`, габарит поставки.

    Без коммита и инвалидации — их делает вызывающий (он же решает, когда
    закрыть транзакцию перед следующим походом в WB).

    Переход `new → confirm`, сделанный НАШЕЙ кнопкой, фиксируется в журнале
    здесь же: синк статусов его не увидит (WB вернёт confirm, а строка уже
    confirm — диффа нет), и без записи таймлайн терял бы фазу «на сборке».
    """
    now = utcnow()
    # Снимок ДО перезаписи — база диффа журнала (уже-confirm событий не рождает).
    snapshot = await orders_service.order_status_snapshot(db, project_id, list(ids))
    await db.execute(
        update(WbFbsOrder)
        .where(WbFbsOrder.project_id == project_id, WbFbsOrder.wb_order_id.in_(list(ids)))
        .values(
            supply_id=wb_supply_id,
            supplier_status=FbsSupplierStatus.CONFIRM.value,
            synced_at=now,
            updated_at=now,
        )
    )
    await orders_service.record_order_events(
        db,
        project_id,
        [
            {
                "order_id": pk,
                "axis": orders_service.EVENT_AXIS_SUPPLIER,
                "old_value": old_sup,
                "new_value": FbsSupplierStatus.CONFIRM.value,
                "changed_at": now,
            }
            for pk, old_sup, _old_wb in snapshot.values()
            if old_sup != FbsSupplierStatus.CONFIRM.value
        ],
    )
    await db.execute(
        update(WbFbsSupply)
        .where(WbFbsSupply.project_id == project_id, WbFbsSupply.wb_supply_id == wb_supply_id)
        .values(
            # coalesce: габарит фиксирует ПЕРВОЕ задание и больше не меняется.
            wb_warehouse_id=func.coalesce(WbFbsSupply.wb_warehouse_id, wb_warehouse_id),
            cargo_type=func.coalesce(WbFbsSupply.cargo_type, cargo_type),
            cross_border_type=func.coalesce(WbFbsSupply.cross_border_type, cross_border_type),
            updated_at=now,
        )
    )
    await _recount_orders(db, project_id, wb_supply_id)


async def add_orders(db: AsyncSession, project_id: int, wb_supply_id: str, order_ids: list[int]) -> int:
    """Добавить задания в поставку (переводит их в `confirm`). Возвращает число заданий."""
    ids = list(dict.fromkeys(oid for oid in (_int_or_none(x) for x in order_ids or []) if oid))
    if not ids:
        raise FbsSupplyError("Не переданы сборочные задания")
    if len(ids) > _ADD_ORDERS_MAX:
        raise FbsSupplyError(f"WB принимает максимум {_ADD_ORDERS_MAX} заданий за раз, передано {len(ids)}")

    supply = await _get_supply(db, project_id, wb_supply_id)
    if supply.done:
        raise FbsSupplyError(f"Поставка {wb_supply_id} уже передана в доставку — доложить в неё нельзя")

    result = await db.execute(
        select(WbFbsOrder).where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.wb_order_id.in_(ids),
        )
    )
    orders = list(result.scalars().all())
    found = {o.wb_order_id for o in orders}
    missing = [oid for oid in ids if oid not in found]
    if missing:
        raise FbsSupplyError(f"Задания не найдены в проекте: {', '.join(str(m) for m in missing[:10])}")
    blocked = [o.wb_order_id for o in orders if o.supplier_status not in _ADDABLE_STATUSES]
    if blocked:
        raise FbsSupplyError(
            "В поставку можно класть только задания в статусах "
            f"{'/'.join(_ADDABLE_STATUSES)}. Не подходят: {', '.join(str(o) for o in sorted(blocked)[:10])}"
        )

    problem = _homogeneity_error(supply, orders)
    if problem:
        raise FbsSupplyError(problem)

    # Габарит читаем ДО коммита: после него ORM-объекты протухают (expire_on_commit).
    order_warehouse = orders[0].wb_warehouse_id
    order_cargo = orders[0].cargo_type
    order_cross = orders[0].cross_border_type

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    await client.add_orders_to_supply(wb_supply_id, ids)

    await _attach_orders_to_mirror(
        db,
        project_id,
        wb_supply_id,
        ids,
        wb_warehouse_id=order_warehouse,
        cargo_type=order_cargo,
        cross_border_type=order_cross,
    )
    await db.commit()
    await invalidate_cache(CACHE_ORDERS)
    await invalidate_cache(CACHE_SUPPLIES)
    logger.info("wb_fbs.supplies.add_orders project=%s supply=%s orders=%s", project_id, wb_supply_id, len(ids))
    return len(ids)


# ─── Передача, удаление, QR ─────────────────────────────────────────────────


async def deliver_supply(db: AsyncSession, project_id: int, wb_supply_id: str) -> None:
    """Передать поставку в доставку: задания → `complete`, товар списывается из ledger'а."""
    supply = await _get_supply(db, project_id, wb_supply_id)
    if supply.done:
        raise FbsSupplyError(f"Поставка {wb_supply_id} уже передана в доставку")

    result = await db.execute(
        select(func.count())
        .select_from(WbFbsOrder)
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.supply_id == wb_supply_id,
            WbFbsOrder.supplier_status.notin_(FBS_TERMINAL_STATUSES),
        )
    )
    if not (result.scalar() or 0):
        # Зеркало WB: пустую поставку он бы отбил 409 SupplyHasZeroOrders.
        raise FbsSupplyError(f"В поставке {wb_supply_id} нет активных заданий — передавать нечего")

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    await client.deliver_supply(wb_supply_id)

    now = utcnow()
    # Снимок ПОСЛЕ похода в WB и ДО bulk-UPDATE (одна транзакция с ним): дифф
    # для журнала переходов — каждое живое задание получает `old → complete`.
    pending_rows = (
        await db.execute(
            select(WbFbsOrder.id, WbFbsOrder.supplier_status).where(
                WbFbsOrder.project_id == project_id,
                WbFbsOrder.supply_id == wb_supply_id,
                WbFbsOrder.supplier_status.notin_(FBS_TERMINAL_STATUSES),
            )
        )
    ).all()
    await db.execute(
        update(WbFbsOrder)
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.supply_id == wb_supply_id,
            WbFbsOrder.supplier_status.notin_(FBS_TERMINAL_STATUSES),
        )
        .values(
            supplier_status=FbsSupplierStatus.COMPLETE.value,
            is_cancellable=False,
            synced_at=now,
            updated_at=now,
        )
    )
    await orders_service.record_order_events(
        db,
        project_id,
        [
            {
                "order_id": int(pk),
                "axis": orders_service.EVENT_AXIS_SUPPLIER,
                "old_value": old_sup,
                "new_value": FbsSupplierStatus.COMPLETE.value,
                "changed_at": now,
            }
            for pk, old_sup in pending_rows
            if old_sup != FbsSupplierStatus.COMPLETE.value
        ],
    )
    await db.execute(
        update(WbFbsSupply)
        .where(WbFbsSupply.project_id == project_id, WbFbsSupply.wb_supply_id == wb_supply_id)
        .values(done=True, closed_at=now, updated_at=now)
    )
    await db.commit()
    await invalidate_cache(CACHE_ORDERS)
    await invalidate_cache(CACHE_SUPPLIES)

    # Товар физически уехал — списываем из ledger'а. Падение списания не должно
    # отменять уже совершённый в WB deliver: логируем и оставляем джобу.
    try:
        await orders_service.writeoff_completed_orders(db, project_id)
    except Exception:
        logger.exception("wb_fbs.supplies.deliver writeoff failed project=%s supply=%s", project_id, wb_supply_id)


async def delete_supply(db: AsyncSession, project_id: int, wb_supply_id: str) -> None:
    """Удалить поставку. WB разрешает только активную и пустую."""
    supply = await _get_supply(db, project_id, wb_supply_id)
    if supply.done:
        raise FbsSupplyError(f"Поставка {wb_supply_id} уже передана — удалить нельзя")

    result = await db.execute(
        select(func.count())
        .select_from(WbFbsOrder)
        .where(WbFbsOrder.project_id == project_id, WbFbsOrder.supply_id == wb_supply_id)
    )
    orders_count = result.scalar() or 0
    if orders_count:
        raise FbsSupplyError(
            f"В поставке {wb_supply_id} ещё {orders_count} заданий — WB удаляет только пустую поставку"
        )

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    await client.delete_supply(wb_supply_id)

    # Зеркало без SoftDelete: у WB строки больше нет — и у нас не должно быть.
    await db.execute(
        delete(WbFbsSupply).where(
            WbFbsSupply.project_id == project_id,
            WbFbsSupply.wb_supply_id == wb_supply_id,
        )
    )
    await db.commit()
    await invalidate_cache(CACHE_SUPPLIES)
    logger.info("wb_fbs.supplies.delete project=%s supply=%s", project_id, wb_supply_id)


async def get_supply_barcode(
    db: AsyncSession, project_id: int, wb_supply_id: str, sticker_type: str = "png"
) -> dict:
    """QR поставки (`WB-GI-…`). Только после deliver — иначе WB 409 `SupplyNotClosed`."""
    supply = await _get_supply(db, project_id, wb_supply_id)
    if not supply.done:
        raise FbsSupplyError(
            f"QR поставки {wb_supply_id} доступен только после передачи в доставку (WB: SupplyNotClosed)"
        )

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    data = await client.get_supply_barcode(wb_supply_id, sticker_type=sticker_type)
    barcode = _str_or_none(data.get("barcode"), 60)
    if barcode:
        await db.execute(
            update(WbFbsSupply)
            .where(WbFbsSupply.project_id == project_id, WbFbsSupply.wb_supply_id == wb_supply_id)
            .values(qr_barcode=barcode, updated_at=utcnow())
        )
        await db.commit()
        await invalidate_cache(CACHE_SUPPLIES)
    return {"barcode": barcode, "file": data.get("file")}


# ─── Разбиение выделенных заданий на поставки ───────────────────────────────
#
# Единица работы в кабинете WB — ПОСТАВКА, а не задание, и WB запрещает мешать
# в одной поставке разные склады продавца и разные габариты. Значит при работе
# с несколькими складами пользователь ФИЗИЧЕСКИ обязан завести несколько
# поставок — разбивать выделенное должны мы, а не 409 от WB.


class _SupplyFit(NamedTuple):
    """Активная поставка как кандидат на доклад — с ФАКТИЧЕСКИМ габаритом.

    `orders_count == 0` — поставка ПОДТВЕРЖДЁННО пустая (WB вернул нулевой
    состав), габарита у неё нет, она подходит любой группе (в пределах уже
    зафиксированных полей). Поставка, чей состав мы не проверяли, кандидатом
    не становится вовсе — см. `_load_active_supply_fits`.
    """

    wb_supply_id: str
    wb_warehouse_id: int | None
    cargo_type: int | None
    cross_border_type: int | None
    orders_count: int


#: Группа = будущая поставка: (склад продавца, cargoType, crossBorderType).
_PlanKey = tuple[int | None, int, int]


def _plan_key(order: WbFbsOrder) -> _PlanKey:
    """Ключ группы. None и 0 — «габарит не указан», это одна и та же группа."""
    return (order.wb_warehouse_id, order.cargo_type or 0, order.cross_border_type or 0)


def _key_sort(key: _PlanKey) -> tuple:
    """Детерминированный порядок групп (склад без id — в конец)."""
    return (key[0] is None, key[0] or 0, key[1], key[2])


def _block_reason(order: WbFbsOrder) -> str | None:
    """Почему задание не годится в НОВУЮ поставку. None — годится."""
    if order.supply_id:
        return f"уже в поставке {order.supply_id}"
    if order.supplier_status != FbsSupplierStatus.NEW.value:
        return _STATUS_BLOCK_REASON.get(
            order.supplier_status,
            f"статус «{order.supplier_status}» — в поставку не принимается",
        )
    return None


def _norm_order_ids(order_ids: Sequence[Any] | None) -> list[int]:
    """Уникальные положительные id заданий в исходном порядке."""
    ids = list(dict.fromkeys(oid for oid in (_int_or_none(x) for x in order_ids or []) if oid))
    if not ids:
        raise FbsSupplyError("Не переданы сборочные задания")
    if len(ids) > _MAX_PLAN_ORDERS:
        raise FbsSupplyError(
            f"За раз можно разложить не больше {_MAX_PLAN_ORDERS} заданий, передано {len(ids)}"
        )
    return ids


async def _load_active_supply_fits(db: AsyncSession, project_id: int) -> list[_SupplyFit]:
    """Активные поставки + их фактический габарит, выведенный из НАШИХ заданий.

    Списочный метод WB габариты не отдаёт, поэтому `cargo_type` поставки часто
    NULL даже когда она давно не пуста. Достраиваем из состава одним
    агрегатом — иначе доклад в «пустую на вид» поставку словил бы 409.
    """
    result = await db.execute(
        select(WbFbsSupply)
        .where(
            WbFbsSupply.project_id == project_id,
            WbFbsSupply.done == False,  # noqa: E712
            contour_condition(WbFbsSupply.raw),
        )
        .order_by(WbFbsSupply.created_at_wb.desc().nullslast(), WbFbsSupply.id.desc())
        .limit(_ACTIVE_SUPPLY_SCAN)
    )
    supplies = list(result.scalars().all())
    if not supplies:
        return []

    agg_result = await db.execute(
        select(
            WbFbsOrder.supply_id,
            func.count(),
            func.min(WbFbsOrder.wb_warehouse_id),
            func.max(WbFbsOrder.wb_warehouse_id),
            func.min(func.coalesce(WbFbsOrder.cargo_type, 0)),
            func.max(func.coalesce(WbFbsOrder.cargo_type, 0)),
            func.min(func.coalesce(WbFbsOrder.cross_border_type, 0)),
            func.max(func.coalesce(WbFbsOrder.cross_border_type, 0)),
        )
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.supply_id.in_([s.wb_supply_id for s in supplies]),
        )
        .group_by(WbFbsOrder.supply_id)
    )
    stats = {row[0]: row[1:] for row in agg_result.all()}

    fits: list[_SupplyFit] = []
    for supply in supplies:
        row = stats.get(supply.wb_supply_id)
        if row is None:
            # «Нет заданий в зеркале» ≠ «поставка пуста»: состав, набранный
            # прямо в кабинете WB, к нам не приходит вовсе (`/orders/new` его
            # не отдаёт), а списочный метод габаритов не возвращает. Пустой
            # считаем только ПОДТВЕРЖДЁННО пустую (`/order-ids` вернул ноль) —
            # иначе кабинетная поставка вечно предлагалась бы к доклажу и
            # вечно ловила 409, и цикл не разрывался бы ничем.
            if _wb_orders_count(supply.raw) != 0:
                continue
            fits.append(
                _SupplyFit(
                    supply.wb_supply_id,
                    supply.wb_warehouse_id,
                    supply.cargo_type,
                    supply.cross_border_type,
                    0,
                )
            )
            continue
        count, wh_min, wh_max, cargo_min, cargo_max, cross_min, cross_max = row
        if wh_min != wh_max or cargo_min != cargo_max or cross_min != cross_max:
            # Состав уже неоднороден (ручные правки в кабинете) — доклад в такую
            # поставку гарантированно получит 409, кандидатом её не считаем.
            continue
        fits.append(
            _SupplyFit(
                supply.wb_supply_id,
                supply.wb_warehouse_id if supply.wb_warehouse_id is not None else wh_min,
                supply.cargo_type if supply.cargo_type is not None else cargo_min,
                supply.cross_border_type if supply.cross_border_type is not None else cross_min,
                int(count or 0),
            )
        )
    return fits


def _fit_rank(fit: _SupplyFit, key: _PlanKey) -> int | None:
    """Насколько поставка подходит группе: меньше — лучше. None — не подходит."""
    wb_warehouse_id, cargo, cross = key
    if fit.orders_count:
        # Непустая: габарит зафиксирован первым заданием, принимает только своё.
        if (
            fit.wb_warehouse_id == wb_warehouse_id
            and (fit.cargo_type or 0) == cargo
            and (fit.cross_border_type or 0) == cross
        ):
            return 0
        return None
    # Пустая поставка габарита не имеет — годится любой группе, чьи поля не
    # спорят с уже зафиксированными (склад мог проставить прошлый доклад).
    if fit.wb_warehouse_id is not None and fit.wb_warehouse_id != wb_warehouse_id:
        return None
    if fit.cargo_type is not None and (fit.cargo_type or 0) != cargo:
        return None
    if fit.cross_border_type is not None and (fit.cross_border_type or 0) != cross:
        return None
    return 1 if fit.wb_warehouse_id is not None else 2


async def _warehouse_names(
    db: AsyncSession, project_id: int, wb_warehouse_ids: set[int | None]
) -> dict[int, str]:
    """Имена складов продавца для показа в плане — одним запросом."""
    ids = sorted({int(w) for w in wb_warehouse_ids if w})
    if not ids:
        return {}
    result = await db.execute(
        select(WbFbsWarehouse.wb_warehouse_id, WbFbsWarehouse.name)
        .where(
            WbFbsWarehouse.project_id == project_id,
            WbFbsWarehouse.wb_warehouse_id.in_(ids),
        )
        .limit(len(ids))
    )
    return {int(row[0]): row[1] for row in result.all() if row[1]}


def _plan_group(
    key: _PlanKey,
    order_ids: Sequence[int],
    names: dict[int, str],
    *,
    existing_supply_id: str | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """Строка под `FbsSupplyPlanGroup`."""
    wb_warehouse_id, cargo, cross = key
    return {
        "wb_warehouse_id": wb_warehouse_id,
        "wb_warehouse_name": names.get(wb_warehouse_id) if wb_warehouse_id else None,
        # 0 — «габарит не указан»; наружу отдаём None, чтобы UI не рисовал ноль.
        "cargo_type": cargo or None,
        "cross_border_type": cross or None,
        "order_ids": list(order_ids),
        "orders_count": len(order_ids),
        "existing_supply_id": existing_supply_id,
        "blocked_reason": blocked_reason,
    }


async def plan_supplies(db: AsyncSession, project_id: int, order_ids: list[int]) -> dict:
    """Предпросмотр разбиения выделенных заданий на поставки (`FbsSupplyPlanOut`).

    В WB НЕ ходит вовсе — это чистый расчёт по нашему зеркалу: пользователь
    должен увидеть «получится 2 поставки» до единого запроса к WB.

    Группировка — по кортежу (склад продавца, cargoType, crossBorderType):
    ровно то, что WB запрещает мешать. Задание, уже привязанное к поставке или
    не в статусе `new`, в группу не попадает — оно уходит в отдельную строку с
    `blocked_reason`, чтобы из плана было видно, почему выделено 10, а поедет 7.
    """
    ids = _norm_order_ids(order_ids)

    result = await db.execute(
        select(WbFbsOrder)
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.wb_order_id.in_(ids),
            contour_condition(WbFbsOrder.raw),
        )
        .order_by(WbFbsOrder.wb_order_id)
        .limit(_MAX_PLAN_ORDERS)
    )
    orders = list(result.scalars().all())

    ok: dict[_PlanKey, list[int]] = {}
    blocked: dict[tuple[_PlanKey, str], list[int]] = {}
    for order in orders:
        key = _plan_key(order)
        reason = _block_reason(order)
        if reason:
            blocked.setdefault((key, reason), []).append(order.wb_order_id)
        else:
            ok.setdefault(key, []).append(order.wb_order_id)

    found = {o.wb_order_id for o in orders}
    missing = [oid for oid in ids if oid not in found]
    if missing:
        blocked.setdefault(((None, 0, 0), _MISSING_BLOCK_REASON), []).extend(missing)

    # Доклад вместо создания: одна активная поставка достаётся одной группе.
    fits = await _load_active_supply_fits(db, project_id) if ok else []
    used: set[str] = set()
    reuse: dict[_PlanKey, str] = {}
    for key in sorted(ok, key=_key_sort):
        best_rank: int | None = None
        best_id: str | None = None
        for fit in fits:
            if fit.wb_supply_id in used:
                continue
            rank = _fit_rank(fit, key)
            if rank is None or (best_rank is not None and rank >= best_rank):
                continue
            best_rank, best_id = rank, fit.wb_supply_id
        if best_id:
            used.add(best_id)
            reuse[key] = best_id

    names = await _warehouse_names(
        db, project_id, {k[0] for k in ok} | {k[0][0] for k in blocked}
    )

    groups = [
        _plan_group(key, ok[key], names, existing_supply_id=reuse.get(key))
        for key in sorted(ok, key=_key_sort)
    ]
    groups += [
        _plan_group(key, blocked[(key, reason)], names, blocked_reason=reason)
        for key, reason in sorted(blocked, key=lambda item: (_key_sort(item[0]), item[1]))
    ]
    return {
        "groups": groups,
        "total_orders": sum(g["orders_count"] for g in groups),
        "supplies_count": len(ok),
    }


def _group_label(group: dict[str, Any]) -> str:
    """Человекочитаемое имя склада группы — для имени поставки и текста ошибки."""
    if group.get("wb_warehouse_name"):
        return str(group["wb_warehouse_name"])
    if group.get("wb_warehouse_id"):
        return f"склад {group['wb_warehouse_id']}"
    return "склад не указан"


def _group_suffix(group: dict[str, Any]) -> str:
    """Различающий хвост имени для групп ОДНОГО склада.

    Группа плана — кортеж (склад, cargoType, crossBorderType), значит и суффикс
    обязан нести ОБА габаритных поля: без crossBorderType две группы одного
    склада с одинаковым cargoType получали одно и то же имя, и в кабинете WB
    висели две активные поставки, неразличимые для сборщика.
    """
    suffix = f"габарит {group.get('cargo_type') or 'н/у'}"
    cross = group.get("cross_border_type")
    if cross:
        # Значение, а не просто «трансгран.»: crossBorderType бывает не только 1,
        # а суффикс обязан быть уникальным на каждую группу склада.
        suffix += f" · трансгран. {cross}"
    return suffix


def _supply_name(prefix: str | None, label: str, suffix: str | None = None) -> str:
    """Имя поставки «префикс · склад [· габарит]», ужатое в 128 символов WB.

    Режем ПРЕФИКС, а не хвост: склад и габарит — единственное, что различает
    поставки одного прогона, обрезанный хвост сделал бы имена одинаковыми.
    """
    base = (prefix or "").strip() or "Поставка FBS"
    tail = " · ".join(p for p in (label, suffix) if p)
    if not tail:
        return base[:_SUPPLY_NAME_MAX]
    name = f"{base} · {tail}"
    if len(name) <= _SUPPLY_NAME_MAX:
        return name
    room = _SUPPLY_NAME_MAX - len(tail) - 3
    if room >= 1:
        return f"{base[:room].rstrip()} · {tail}"
    return tail[-_SUPPLY_NAME_MAX:]


async def _supplies_by_ids(db: AsyncSession, project_id: int, ids: Sequence[str]) -> dict[str, dict]:
    """Зеркала поставок по id — строками `FbsSupplyOut`."""
    unique = list(dict.fromkeys(ids))
    if not unique:
        return {}
    result = await db.execute(
        select(WbFbsSupply)
        .where(
            WbFbsSupply.project_id == project_id,
            WbFbsSupply.wb_supply_id.in_(unique),
        )
        .limit(len(unique))
    )
    return {s.wb_supply_id: _supply_to_dict(s) for s in result.scalars().all()}


async def create_supplies_bulk(
    db: AsyncSession,
    project_id: int,
    order_ids: list[int],
    *,
    name_prefix: str | None = None,
    reuse_existing: bool = True,
) -> dict:
    """Разложить задания по поставкам и залить их в WB (`FbsSupplyBulkOut`).

    На каждую группу плана — либо новая поставка, либо доклад в активную
    (`reuse_existing`). Задания уезжают чанками по 100 (потолок WB), и после
    КАЖДОГО принятого чанка зеркало обновляется и коммитится: пользователь
    видит результат сразу, не дожидаясь синка, а следующий поход в WB не
    держит открытую транзакцию.

    **Частичный успех — норма.** Сбой на одной группе (или на одном чанке)
    ложится в `errors`, остальные группы продолжают ехать: уже принятые WB
    задания откатывать нельзя, а «всё или ничего» здесь означало бы «ничего».
    Исключение — `WbFbsWriteBlocked` (режим `safe`) и `WbFbsRateLimited` (429):
    это отказ ВСЕГО прогона, а не одной группы, и роутер обязан отдать по ним
    409/429. Проглотить их в `errors` значило бы вернуть 200 на заблокированную
    запись и продолжать долбить исчерпанный бакет (каждый 4XX стоит 10 запросов).

    Провал доклада в ЧУЖУЮ поставку (её завели в кабинете WB, габарит там уже
    зафиксирован, а наше зеркало его не видит) не теряет группу: один раз
    откатываемся на создание своей поставки и везём в неё.

    Идемпотентность: повторный вызов с теми же id пустых поставок не плодит —
    задания уже получили `supply_id` и отсекаются планом как заблокированные.
    """
    plan = await plan_supplies(db, project_id, order_ids)
    groups = [g for g in plan["groups"] if not g["blocked_reason"] and g["order_ids"]]
    # Заблокированное — не ошибка выполнения, но пользователь обязан узнать,
    # почему выделил 10 заданий, а поехало 7.
    errors: list[str] = [
        f"Пропущено заданий: {g['orders_count']} — {g['blocked_reason']}"
        for g in plan["groups"]
        if g["blocked_reason"]
    ]
    if not groups:
        return {"created": [], "reused": [], "orders_attached": 0, "errors": errors}

    # Один склад в двух группах = разные габариты: без суффикса имена совпадут.
    per_warehouse: dict[int | None, int] = {}
    for group in groups:
        per_warehouse[group["wb_warehouse_id"]] = per_warehouse.get(group["wb_warehouse_id"], 0) + 1

    client = await get_fbs_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    async def _create_for(group: dict[str, Any], label: str) -> str:
        """Завести новую поставку под группу и отразить её в зеркале."""
        suffix = _group_suffix(group) if per_warehouse[group["wb_warehouse_id"]] > 1 else None
        name = _supply_name(name_prefix, label, suffix)
        new_id: str = await client.create_supply(name)
        await _register_created_supply(db, project_id, new_id, name)
        await db.commit()  # закрываем транзакцию ДО следующего HTTP
        return new_id

    async def _push(group: dict[str, Any], supply_id: str) -> tuple[int, Exception | None]:
        """Залить задания группы чанками. Возвращает (сколько уехало, чем упало)."""
        pushed = 0
        for chunk in _chunks(group["order_ids"], _ADD_ORDERS_MAX):
            try:
                await client.add_orders_to_supply(supply_id, chunk)
                await _attach_orders_to_mirror(
                    db,
                    project_id,
                    supply_id,
                    chunk,
                    wb_warehouse_id=group["wb_warehouse_id"],
                    cargo_type=group["cargo_type"],
                    cross_border_type=group["cross_border_type"],
                )
                await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await db.rollback()
                return pushed, exc
            pushed += len(chunk)
        return pushed, None

    created: list[str] = []
    reused: list[str] = []
    attached = 0
    for group in groups:
        label = _group_label(group)
        supply_id: str | None = group["existing_supply_id"] if reuse_existing else None
        is_new = False
        group_attached = 0
        try:
            if not supply_id:
                supply_id = await _create_for(group, label)
                is_new = True
            group_attached, failure = await _push(group, supply_id)
            if (
                failure is not None
                and not is_new
                and group_attached == 0
                # Гейт режима и 429 — не «поставка не приняла», а отказ всего
                # прогона: откат на создание ударил бы в ту же стену и сжёг
                # ещё один запрос из исчерпанного бакета.
                and not isinstance(failure, (WbFbsWriteBlocked, WbFbsRateLimited))
            ):
                # Доклад в поставку, заведённую в кабинете WB, отбит: её склад и
                # габарит зафиксированы там, а наше зеркало их не видит. Группу
                # не теряем — заводим свою и везём в неё. Ровно один откат на
                # группу, иначе цикл «предложили → 409 → предложили» не рвётся.
                errors.append(
                    f"{label}: доложить в {supply_id} не удалось ({failure}) — завели новую поставку"
                )
                supply_id = await _create_for(group, label)
                is_new = True
                group_attached, failure = await _push(group, supply_id)
            if failure is not None:
                raise failure
        except asyncio.CancelledError:
            raise
        except (WbFbsWriteBlocked, WbFbsRateLimited):
            # Гейт режима и исчерпанный бакет — отказ ВСЕГО прогона, а не одной
            # группы: следующая группа ударилась бы в ту же стену, а роутер
            # обязан отдать 409/429, а не 200 со строкой в `errors`.
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "wb_fbs.supplies.bulk group failed project=%s warehouse=%s supply=%s: %s",
                project_id,
                group["wb_warehouse_id"],
                supply_id,
                exc,
            )
            errors.append(f"{label}: {exc}")

        attached += group_attached
        if supply_id and is_new:
            # Созданную поставку показываем даже пустой: она реально есть в WB.
            created.append(supply_id)
        elif supply_id and group_attached:
            reused.append(supply_id)

    if created or attached:
        await invalidate_cache(CACHE_ORDERS)
        await invalidate_cache(CACHE_SUPPLIES)

    rows = await _supplies_by_ids(db, project_id, created + reused)
    logger.info(
        "wb_fbs.supplies.bulk project=%s groups=%s created=%s reused=%s orders=%s errors=%s",
        project_id,
        len(groups),
        len(created),
        len(reused),
        attached,
        len(errors),
    )
    return {
        "created": [rows[s] for s in created if s in rows],
        "reused": [rows[s] for s in reused if s in rows],
        "orders_attached": attached,
        "errors": errors,
    }


async def list_supply_orders(db: AsyncSession, project_id: int, wb_supply_id: str) -> list[dict]:
    """Состав поставки из НАШЕГО зеркала — строки `FbsOrderOut`.

    Не ходим в `/order-ids`: строка поставки раскрывается кликом, а лишний
    HTTP на каждый клик жёг бы бакет WB. Скоуп по контуру — как в списке
    заданий: боевой экран не должен показывать задания песочницы.

    Поставку резолвим ДО выборки состава (как deliver/delete/barcode): чужая
    или несуществующая — это 404, а не валидное «в поставке нет заданий».
    Иначе опечатка в id маскируется под пустое состояние.

    `transit_days` считается от якоря САМОЙ поставки (scan_dt → closed_at,
    фолбэк written_off_at внутри `_transit_days`) — она уже загружена, лишних
    запросов ноль; заполняется только для строк фазы «в пути».
    """
    supply_id = (wb_supply_id or "").strip()
    if not supply_id:
        raise FbsSupplyError("Не указана поставка")
    supply = await _get_supply(db, project_id, supply_id)
    result = await db.execute(
        select(WbFbsOrder)
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.supply_id == supply_id,
            contour_condition(WbFbsOrder.raw),
        )
        .order_by(WbFbsOrder.created_at_wb.desc().nullslast(), WbFbsOrder.id.desc())
        .limit(_SUPPLY_ORDERS_LIMIT)
    )
    # Фаза поставки одна на все строки: (якорь передачи, done, scan_dt) —
    # тот же кортеж, что собирает `_supply_meta_map` в списке заданий.
    meta = (supply.scan_dt or supply.closed_at, bool(supply.done), supply.scan_dt)
    meta_by_supply = {supply_id: meta}
    now = utcnow()
    return [
        _order_to_dict(
            order,
            transit_days=_transit_days(order, meta_by_supply, now),
            supply_meta=meta,
        )
        for order in result.scalars().all()
    ]


# ─── Лист подбора ───────────────────────────────────────────────────────────
#
# Документ для СБОРЩИКА: что и сколько физически снять со склада. В кабинете WB
# он висит кнопкой на экране поставки, у нас его не было вовсе. Ключевое
# отличие от состава поставки (`list_supply_orders`) — агрегат ПО ТОВАРУ, а не
# по заказам: WB количество не агрегирует (одно задание = одна единица), и
# 47 заданий одного артикула сборщик обязан видеть одной строкой «снять 47 шт»,
# а не листать три страницы одинаковых заданий.


def _pick_key(nomenclature_id: int | None, barcode: str | None, chrt_id: int | None) -> tuple[str, int, str]:
    """Ключ строки листа: чем схлопываем задания в позицию.

    БАРКОД В КЛЮЧЕ ВСЕГДА — и у сматченного задания тоже. Матчинг идёт по
    `chrtId` (`orders_service._resolve_nomenclature`), а пары
    (barcode → chrtId) many-to-one: разные баркоды одного chrt получают ОДИН
    `nomenclature_id`. Свёртка без баркода печатала их одной строкой с одним
    баркодом — сборщику «снять 4 шт по BCA…», хотя половина лежит под BCB…,
    а второй баркод в документе не появлялся вовсе. Позиция листа = то, что
    сборщик реально сканирует.

    Несматченное задание сворачивается по паре (chrtId, баркод) — такую
    позицию тоже надо найти на полке, терять её нельзя; маркер «не сматчено» —
    `nomenclature_id is None` в строке.
    """
    if nomenclature_id:
        return ("nom", int(nomenclature_id), barcode or "")
    return ("raw", int(chrt_id or 0), barcode or "")


async def _pick_list_groups(db: AsyncSession, project_id: int, wb_supply_id: str) -> list[Any]:
    """Задания поставки, свёрнутые СУБД в группы (позиция × статус). ОДИН запрос.

    Скоуп по контуру — как в списке заданий: боевой лист подбора не должен
    показывать задания песочницы.

    Деньги считаем по `sale_price` (с откатом на `price`): колонку «Цена, ₽»
    во ВСЕЙ FBS-выдаче рисует `sale_price`, и лист, сложенный по `price`,
    не сходился со списком заданий той же поставки — расхождение до ~40 %,
    хотя оба документа заявлены «для сверки при отгрузке». `price` у WB
    необязателен, поэтому фолбэк, а не голое поле.
    """
    result = await db.execute(
        select(
            WbFbsOrder.nomenclature_id,
            WbFbsOrder.barcode,
            WbFbsOrder.chrt_id,
            WbFbsOrder.nm_id,
            WbFbsOrder.article,
            WbFbsOrder.subject,
            WbFbsOrder.supplier_status,
            func.count().label("qty"),
            func.sum(orders_service.revenue_rub_expr()).label("amount"),
        )
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.supply_id == wb_supply_id,
            contour_condition(WbFbsOrder.raw),
        )
        .group_by(
            WbFbsOrder.nomenclature_id,
            WbFbsOrder.barcode,
            WbFbsOrder.chrt_id,
            WbFbsOrder.nm_id,
            WbFbsOrder.article,
            WbFbsOrder.subject,
            WbFbsOrder.supplier_status,
        )
        # Порядок групп фиксируем: свёртка берёт реквизиты у ПЕРВОЙ группы, где
        # они есть, и без ORDER BY лист мог бы печататься то с одним баркодом
        # позиции, то с другим.
        .order_by(
            WbFbsOrder.nomenclature_id.nullslast(),
            WbFbsOrder.chrt_id.nullslast(),
            WbFbsOrder.barcode.nullslast(),
            WbFbsOrder.supplier_status,
        )
        .limit(_PICK_LIST_MAX_GROUPS)
    )
    return list(result.all())


def _fold_pick_rows(groups: Sequence[Any]) -> tuple[list[dict[str, Any]], int]:
    """Группы СУБД → строки листа + общее число заданий поставки.

    Отменённые задания (`cancel` / `cancel_carrier`) в лист НЕ попадают —
    снимать со склада их не нужно, — но в `orders_count` шапки входят: там же
    цифра из списка поставок, и расхождение с `total_qty` честно показывает
    «часть заданий отменена», вместо молчаливого расхождения документов.
    """
    rows: dict[tuple[str, int, str], dict[str, Any]] = {}
    orders_total = 0
    for group in groups:
        qty = int(group.qty or 0)
        orders_total += qty
        if group.supplier_status in FBS_TERMINAL_STATUSES:
            continue
        key = _pick_key(group.nomenclature_id, group.barcode, group.chrt_id)
        row = rows.get(key)
        if row is None:
            row = {
                "nomenclature_id": _int_or_none(group.nomenclature_id),
                "barcode": None,
                "chrt_id": None,
                "nm_id": None,
                "article": None,
                "subject": None,
                "brand": None,
                "qty": 0,
                "amount": None,
                "stock_available": None,
            }
            rows[key] = row
        row["qty"] += qty
        if group.amount is not None:
            row["amount"] = (row["amount"] or Decimal("0")) + Decimal(group.amount)
        # Реквизиты берём у первой группы, где они есть: у заданий одной позиции
        # они одинаковы, но старые строки зеркала бывают неполными.
        for field, value in (
            ("barcode", _str_or_none(group.barcode, 50)),
            ("chrt_id", _int_or_none(group.chrt_id)),
            ("nm_id", _int_or_none(group.nm_id)),
            ("article", _str_or_none(group.article, 100)),
            ("subject", _str_or_none(group.subject, 200)),
        ):
            if row[field] is None and value is not None:
                row[field] = value
    return list(rows.values()), orders_total


async def _nom_ids_by_barcode(
    db: AsyncSession, project_id: int, barcodes: Iterable[str | None]
) -> dict[str, list[int]]:
    """Баркод → id ВСЕХ наших номенклатур с ним (их бывает несколько на chrtId).

    Нужен листу подбора: задание сматчено по `chrtId`, а физически сборщик
    снимает с полки то, что лежит под КОНКРЕТНЫМ баркодом строки, — остаток
    обязан считаться по этим номенклатурам, а не по одной «первой» у chrt.
    """
    codes = sorted({b.strip() for b in barcodes if b and b.strip()})
    if not codes:
        return {}
    out: dict[str, list[int]] = {}
    result = await db.execute(
        select(Nomenclature.id, Nomenclature.barcode)
        .where(Nomenclature.project_id == project_id, Nomenclature.barcode.in_(codes))
        .order_by(Nomenclature.id)
        .limit(_PICK_NOM_LOOKUP_LIMIT)
    )
    for nom_id, barcode in result.all():
        out.setdefault(barcode, []).append(int(nom_id))
    return out


async def _enrich_pick_rows(
    db: AsyncSession, project_id: int, rows: list[dict[str, Any]], warehouse_ids: list[int]
) -> None:
    """Дозаполнить строки из номенклатуры и физического остатка. Три запроса.

    Выборки номенклатуры и остатка — батчевые хелперы `stock_service`
    (локальный импорт, как в соседних модулях домена — защита от цикла).
    Вторую формулу остатка здесь НЕ заводим: цифра обязана браться из того же
    места, что и у трансляции.

    Остаток резолвим по БАРКОДУ строки, а не по её `nomenclature_id`: матчинг
    заданий идёт по `chrtId`, поэтому у двух баркодов одного chrt один и тот же
    `nomenclature_id`, и остаток одной номенклатуры показывался бы обеим
    строкам (и сразу краснел бы как нехватка). Фолбэк на сматченную
    номенклатуру — когда баркода в наших карточках нет.
    """
    nom_ids = sorted({int(r["nomenclature_id"]) for r in rows if r["nomenclature_id"]})
    if not nom_ids:
        return

    from backend.services.wb_fbs import stock_service

    by_barcode = await _nom_ids_by_barcode(
        db, project_id, (r["barcode"] for r in rows if r["nomenclature_id"])
    )
    all_ids = sorted(set(nom_ids) | {i for ids in by_barcode.values() for i in ids})

    noms = await stock_service._load_nomenclature(db, project_id, all_ids)
    quantities = (
        await stock_service._load_quantities(db, project_id, warehouse_ids, all_ids) if warehouse_ids else {}
    )
    stock: dict[int, int] = {}
    for (_wh_id, nom_id), qty in quantities.items():
        stock[nom_id] = stock.get(nom_id, 0) + qty

    for row in rows:
        nom_id = row["nomenclature_id"]
        if not nom_id:
            continue
        # Номенклатуры позиции = те, у кого ИМЕННО её баркод; иначе — сматченная.
        row_ids = by_barcode.get((row["barcode"] or "").strip()) or [int(nom_id)]
        nom = noms.get(row_ids[0]) or noms.get(int(nom_id))
        if nom is not None:
            row["barcode"] = row["barcode"] or nom.barcode
            row["chrt_id"] = row["chrt_id"] or _int_or_none(nom.chrt_id)
            row["nm_id"] = row["nm_id"] or _int_or_none(nom.article_wb)
            # Артикул продавца — то, чем товар подписан у НАС на полке.
            row["article"] = _str_or_none(nom.article_seller, 100) or row["article"]
            row["brand"] = _str_or_none(nom.brand, 100)
            row["subject"] = row["subject"] or _str_or_none(nom.subject, 200)
        if warehouse_ids:
            # Складов нет → остаток остаётся None («неизвестно»), а не 0:
            # ноль читался бы как «товара нет», хотя мы просто не знаем где смотреть.
            row["stock_available"] = sum(stock.get(i, 0) for i in row_ids)


def _pick_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    """Порядок строк листа: артикул, затем баркод — печать за печатью одинакова."""
    return ((row.get("article") or "").casefold(), row.get("barcode") or "")


async def pick_list(db: AsyncSession, project_id: int, wb_supply_id: str) -> dict:
    """Лист подбора поставки (`FbsPickListOut`): что и сколько снять со склада.

    Считается по НАШЕМУ зеркалу заданий, в WB не ходит — работает и в режиме
    `safe`. Одно задание = одна единица товара, поэтому `qty` позиции — это
    число её заданий, а `amount` — сумма их `sale_price` (то же поле, что в
    колонке «Цена, ₽» списков заданий, — иначе сверка при отгрузке не сходится).

    ПОЗИЦИЯ = (номенклатура, БАРКОД): матчинг заданий идёт по `chrtId`, а
    баркодов у одного chrt бывает несколько — свёртка без баркода печатала
    сборщику чужой баркод и остаток одной из номенклатур.

    **`stock_available` — СЫРОЙ физический остаток** `WarehouseStock.quantity`
    по нашим складам, привязанным к складу продавца этой поставки. Это НЕ
    FBS-расчёт: буферы, резервы активных заявок, черновики сборки и открытые
    задания здесь не вычитаются. Сборщику нужен ответ «лежит ли товар на
    полке», а не «сколько можно отдать витрине WB». `None` означает
    «неизвестно» — у поставки нет склада продавца, у склада нет привязок или
    задание не сматчено с нашей номенклатурой (у такой строки
    `nomenclature_id is None` — это и есть маркер несматченности).
    """
    supply_id = (wb_supply_id or "").strip()
    if not supply_id:
        raise FbsSupplyError("Не указана поставка")
    # Чужая или неизвестная поставка — доменная ошибка (роутер отдаст 400).
    supply = await _get_supply(db, project_id, supply_id)

    groups = await _pick_list_groups(db, project_id, supply_id)
    rows, orders_total = _fold_pick_rows(groups)

    # Локальный импорт — как в соседних модулях домена (защита от цикла).
    from backend.services.wb_fbs import warehouse_service

    warehouse_ids = (
        await warehouse_service.get_linked_warehouse_ids(db, project_id, supply.wb_warehouse_id)
        if supply.wb_warehouse_id
        else []
    )
    await _enrich_pick_rows(db, project_id, rows, warehouse_ids)
    rows.sort(key=_pick_sort_key)

    names = await _warehouse_names(db, project_id, {supply.wb_warehouse_id})
    amounts = [row["amount"] for row in rows if row["amount"] is not None]
    return {
        "wb_supply_id": supply.wb_supply_id,
        "supply_name": supply.name,
        "wb_warehouse_id": supply.wb_warehouse_id,
        "wb_warehouse_name": names.get(supply.wb_warehouse_id) if supply.wb_warehouse_id else None,
        "cargo_type": supply.cargo_type,
        "done": supply.done,
        "created_at_wb": supply.created_at_wb,
        "orders_count": orders_total,
        "positions_count": len(rows),
        "total_qty": sum(row["qty"] for row in rows),
        "total_amount": sum(amounts, Decimal("0")) if amounts else None,
        "rows": rows,
    }


__all__ = [
    "FbsSupplyError",
    "add_orders",
    "create_supplies_bulk",
    "create_supply",
    "deliver_supply",
    "delete_supply",
    "get_supply_barcode",
    "list_supplies",
    "list_supply_orders",
    "pick_list",
    "plan_supplies",
    "sync_supplies",
]
