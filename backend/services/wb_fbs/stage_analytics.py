# ruff: noqa: RUF001, RUF002, RUF003
"""
Аналитика ЭТАПОВ жизненного цикла FBS-задания: сколько времени заказ проводит
на каждом шаге пути «оформлен → собран → передан → отсортирован → выдан».

Отвечает на три вопроса: **сколько** длится каждый этап (медиана и хвост p90),
**у какого склада** он длится дольше и **как это менялось** по периодам.

## Откуда берётся время

WB истории статусов не отдаёт вовсе (только текущие значения обеих осей), и
это определяет всю конструкцию: у вех РАЗНАЯ точность, и смешивать их молча
нельзя. Источников два.

  • **Точные вехи (`PRECISION_EXACT`)** — реальные даты, приходящие от WB или
    порождённые нашей же операцией: `WbFbsOrder.created_at_wb` (заказ оформлен),
    `WbFbsSupply.closed_at` (поставку закрыли кнопкой «Передать»),
    `WbFbsSupply.scan_dt` (WB отсканировал QR — груз уехал),
    `WbFbsOrder.written_off_at` (списание из нашего ledger'а). Эти этапы
    считаются по ВСЕЙ истории зеркала и не зависят от каденса синка.

  • **Приблизительные вехи (`PRECISION_APPROX`)** — журнал `wb_fbs_order_events`:
    переход фиксируется в момент ОБНАРУЖЕНИЯ синком, точность = каденс опроса
    (5 мин). 🔴 Журнал начинается с деплоя и историю НЕ восстанавливает
    (докстринг `WbFbsOrderEvent`): у заданий, приехавших бэкфиллом, поздних вех
    нет и не будет. Поэтому у каждого этапа отдаётся `precision` и собственный
    `count` — этап без данных обязан честно показывать ноль, а не подмешиваться
    к соседям и не изображать «мгновенно».

  🔴 Вехи поставки (`closed_at`/`scan_dt`) берутся ТОЛЬКО у заданий, реально с
  ней уехавших (`supplier_status ∈ complete, cancel_carrier`) — тот же гейт, что
  у якорей таймлайна. Отмена перевозчиком случается ПОСЛЕ передачи, а заданию,
  отменённому ДО неё, даты поставки приписали бы чужой путь.

## Как этап попадает в период

Этап засчитывается в тот день, когда он **ЗАВЕРШИЛСЯ** (МСК-сутки веха-«до»).
Это единственная разбивка без систематического перекоса: группируй мы по дате
ЗАКАЗА, свежие сутки состояли бы сплошь из недоехавших заданий, у которых
поздние этапы ещё не закрыты, и «Ожидание на ПВЗ» за вчера выглядело бы
неправдоподобно быстрым (в выборку попали бы только самые быстрые). Отсюда же
формулировка на экране: «за неделю X мы отгружали в среднем за Y часов».

## Границы и мусор

  • Длительность < 0 (веха-«до» раньше вехи-«от») отбрасывается: это рассинхрон
    источников разной точности (журнал ±5 мин против точного `scan_dt`), а не
    факт. Отрицательное медиане навредило бы сильнее пропуска.
  • Длительность > `_MAX_STAGE_DAYS` тоже отбрасывается и считается отдельно
    (`dropped`): у заданий, выпавших из окна опроса статусов, `wb_status`
    застывает — «сортировка длиной 200 дней» это протухшая ось, а не логистика.
    Молчаливое усечение здесь недопустимо, поэтому число выброшенных видно.

Очередь (`queue`) — про НАСТОЯЩЕЕ, а не про историю: сколько заданий прямо
сейчас висит на каждом этапе и как давно. Она намеренно ИГНОРИРУЕТ период
(зависшее — очередь проблем, не история). Её фазы — РАЗБИЕНИЕ тех же множеств,
что считает вкладка «Заказы»: `to_ship` ∪ `in_transit` == `in_delivery_condition()`,
`sorted` ∪ `ready` == `sorted_condition()`. Оба равенства закреплены тестами —
разойдись они, две цифры об одном и том же на соседних экранах читались бы как
ошибка расчёта.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import pytz
from sqlalchemy import Date, Float, and_, case, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_fbs import (
    FBS_TERMINAL_STATUSES,
    FBS_WB_PRE_SORT_STATUSES,
    FBS_WB_SORTED_STATUSES,
    FbsSupplierStatus,
    WbFbsOrder,
    WbFbsOrderEvent,
    WbFbsOrderHistory,
    WbFbsSupply,
    WbFbsWarehouse,
)
from backend.services.wb_fbs import order_history, orders_stats
from backend.services.wb_fbs.contour import contour_condition
from backend.services.wb_fbs.orders_service import (
    EVENT_AXIS_SUPPLIER,
    EVENT_AXIS_WB,
    effective_status_expr,
    transit_anchor_expr,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

MSK = pytz.timezone("Europe/Moscow")

#: Потолок правдоподобной длительности этапа. Всё, что дольше — застывший
#: `wb_status` задания, выпавшего из окна опроса (`_STATUS_MAX_ORDERS`),
#: а не реальная логистика. Считается отдельно в `dropped`.
_MAX_STAGE_DAYS = 60

#: Границы автоматического выбора шага динамики. Дневной шаг на трёх месяцах
#: даёт 90 точек шума; месячный на двух неделях — одну точку.
_BUCKET_DAY_MAX = 45
_BUCKET_WEEK_MAX = 200

#: Потолок справочника имён складов продавца — их единицы, это защита от
#: вырождения, а не рабочее ограничение (правило проекта: `.all()` с лимитом).
_MAX_WAREHOUSES = 500

BUCKET_DAY = "day"
BUCKET_WEEK = "week"
BUCKET_MONTH = "month"
ALLOWED_BUCKETS = (BUCKET_DAY, BUCKET_WEEK, BUCKET_MONTH)

#: Точность ОСНОВНОГО источника вехи. `exact` — история кабинета и точные даты
#: WB/наших операций; `approx` остаётся у этапов, чей источник только журнал.
#: Сколько замеров реально опёрлось на журнал — в `approx_count` строки этапа.
PRECISION_EXACT = "exact"
PRECISION_APPROX = "approx"

#: Зона ответственности. До приёмки на СЦ вопросы к нам, после — к логистике WB;
#: разделение прямо из канона домена (`in_delivery_condition` / `sorted_condition`).
ZONE_US = "us"
ZONE_WB = "wb"
ZONE_TOTAL = "total"

#: `wbStatus`, означающий «выдано покупателю». Намеренно БЕЗ `defect`
#: (он в `FBS_WB_DELIVERED_STATUSES` рядом с `sold`): брак до покупателя не
#: доехал, и в «Ожидании на ПВЗ» его длительность мерила бы другой процесс.
_WB_STATUS_SOLD = "sold"
_WB_STATUS_SORTED = "sorted"
_WB_STATUS_READY = "ready_for_pickup"


@dataclass(frozen=True)
class StageDef:
    """Определение этапа: пара вех, зона ответственности и точность.

    `summary` помечает СВОДНЫЕ этапы, перекрывающие соседние (`to_handover`
    покрывает `to_confirm` + `assembly`). Их нельзя складывать с обычными в
    одну сумму — фронт показывает их отдельной группой.
    """

    key: str
    title: str
    #: Короткое имя для узкой колонки таблицы. Обязано нести ОБЕ границы этапа
    #: («Уехал → принял СЦ»): односторонняя формулировка вроде «До сортировки»
    #: не отвечает на вопрос «от чего», и столбец приходится расшифровывать
    #: подсказкой. Полное название и объяснение остаются в `title`/`hint`.
    short: str
    zone: str
    src: str
    dst: str
    precision: str
    summary: bool = False
    hint: str = ""


#: Этапы пути. Порядок = порядок на экране (он же хронологический).
STAGES: tuple[StageDef, ...] = (
    StageDef(
        key="to_confirm",
        title="Заказ → в поставке",
        short="Заказ → в поставку",
        zone=ZONE_US,
        src="t_created",
        dst="t_confirm",
        precision=PRECISION_EXACT,
        hint="От оформления заказа до добавления в поставку. 🔴 Здесь же лежит ФАКТИЧЕСКАЯ сборка: WB ставит метку «Продавец собирает заказ» в момент добавления в поставку, а не в момент начала сборки.",
    ),
    StageDef(
        key="assembly",
        title="Поставка: набор → закрытие",
        short="В поставке → закрыта",
        zone=ZONE_US,
        src="t_confirm",
        dst="t_closed",
        precision=PRECISION_EXACT,
        hint="От добавления в поставку до её закрытия кнопкой «Передать» — метка «Продавец собрал заказ» совпадает с closed_at поставки до минуты. Это НЕ время сборки: у нас поставку закрывают почти сразу (медиана 1–2 мин), сборка прошла раньше.",
    ),
    StageDef(
        key="handover_scan",
        title="Отметка ФФ → груз реально уехал",
        short="Закрыта → уехал",
        zone=ZONE_US,
        src="t_closed",
        dst="t_scan",
        precision=PRECISION_EXACT,
        hint="🔴 Зона ответственности ФУЛФИЛМЕНТА. Поставку закрыли кнопкой «Передать» — "
        "и ждём, пока WB отсканирует QR и физически заберёт груз. Закрытие поставки НЕ равно "
        "отгрузке: ФФ переводит задания «в доставку» заранее (Газпром Москва — медиана 62 ч "
        "между отметкой и сканом против 2.5 ч у Мигфула).",
    ),
    StageDef(
        key="to_sorted",
        title="Скан → сортировка",
        short="Уехал → принял СЦ",
        zone=ZONE_WB,
        src="t_scan",
        dst="t_sorted",
        precision=PRECISION_EXACT,
        hint="Груз уехал — ждём приёмки на сортировочном центре WB.",
    ),
    StageDef(
        key="to_pickup",
        title="Сортировка → готов к вручению",
        short="СЦ → готов к выдаче",
        zone=ZONE_WB,
        src="t_sorted",
        dst="t_ready",
        precision=PRECISION_EXACT,
        hint="Доставка от СЦ до пункта выдачи (или до передачи курьеру).",
    ),
    StageDef(
        key="at_pickup",
        title="Ожидание вручения",
        short="Готов → получен",
        zone=ZONE_WB,
        src="t_ready",
        dst="t_sold",
        precision=PRECISION_EXACT,
        hint="Ждёт покупателя: лежит в ПВЗ либо едет с курьером.",
    ),
    StageDef(
        key="our_zone",
        title="Наша зона: заказ → груз уехал",
        short="Итого наша зона",
        zone=ZONE_US,
        src="t_created",
        dst="t_scan",
        precision=PRECISION_EXACT,
        summary=True,
        hint="Главная цифра нашей ответственности: от оформления заказа до скана QR — момента, "
        "когда WB физически забрал груз. 🔴 Закрывается именно СКАНОМ, а не закрытием поставки: "
        "фулфилмент переводит задания «в доставку» заранее, и по закрытию поставки наша скорость "
        "выглядела бы лучше, чем есть.",
    ),
    StageDef(
        key="to_handover",
        title="Заказ → отметка ФФ о передаче",
        short="Заказ → отметка ФФ",
        zone=ZONE_US,
        src="t_created",
        dst="t_closed",
        precision=PRECISION_EXACT,
        summary=True,
        hint="⚠️ Меряет КЛИК фулфилмента, а не отгрузку: поставку помечают переданной раньше, "
        "чем WB забирает груз. Для оценки собственной скорости берите «Наша зона: заказ → "
        "груз уехал» — он закрывается сканом QR, то есть реальным фактом.",
    ),
    # 🔴 Этапа «заказ → списание со склада» здесь НЕТ намеренно, хотя веха
    # `written_off_at` точная. Бэкфилл истории вставляет строки УЖЕ списанными с
    # меткой = момент своего прогона (`orders_service._order_row`:
    # `"written_off_at": sync_ts if is_history else None`), поэтому у трёх
    # месяцев истории этот «этап» меряет не логистику, а «когда мы нажали
    # Бэкфилл»: на проде — кластер 390 строк в одном часе с медианой 42 ч и
    # максимумом 2136 ч против обычных 5–20 ч. Отличить бэкфилленную строку от
    # живой надёжного признака нет, а состояние списаний и без того показывает
    # отдельная панель `GET /orders/writeoff-issues`. Колонка `t_writeoff` в CTE
    # остаётся — она нужна `_left_us_expr` как фолбэк якоря передачи.
    StageDef(
        key="wb_zone",
        title="Зона WB: уехал → готов к вручению",
        short="Итого зона WB",
        zone=ZONE_WB,
        src="t_scan",
        dst="t_ready",
        precision=PRECISION_EXACT,
        summary=True,
        hint="Логистика Wildberries: от момента, когда груз забрали, до готовности к вручению "
        "покупателю. Влиять на это можно только выбором склада отгрузки (через длину маршрута).",
    ),
    StageDef(
        key="total",
        title="Итого: заказ → выдача",
        short="Заказ → получен",
        zone=ZONE_TOTAL,
        src="t_created",
        dst="t_sold",
        precision=PRECISION_EXACT,
        summary=True,
        hint="Весь путь от оформления до получения покупателем.",
    ),
)

STAGE_BY_KEY: dict[str, StageDef] = {s.key: s for s in STAGES}

#: Этапы очереди «где сейчас висит» — те же фазы, что на вкладке «Заказы».
QUEUE_NEW = "new"
QUEUE_CONFIRM = "confirm"
QUEUE_TO_SHIP = "to_ship"
#: 🔴 НЕ `in_delivery`: имя псевдо-статуса вкладки «Заказы» занято другим
#: множеством. Там `in_delivery` = всё переданное до сортировки, здесь оно
#: разбито по факту скана QR на `to_ship` (груз ещё у нас) и `in_transit`
#: (уже у перевозчика). Совпадает СУММА двух фаз — инвариант закреплён тестом;
#: одинаковое имя у разных множеств читалось бы как расхождение счётчиков.
QUEUE_IN_TRANSIT = "in_transit"
QUEUE_SORTED = "sorted"
QUEUE_READY = "ready"

_QUEUE_TITLES: dict[str, str] = {
    QUEUE_NEW: "Новые — ждут сборки",
    QUEUE_CONFIRM: "В сборке (в поставке)",
    QUEUE_TO_SHIP: "Собрано — ждёт отгрузки",
    QUEUE_IN_TRANSIT: "Едет к сортировочному центру",
    QUEUE_SORTED: "Отсортировано — везут клиенту",
    QUEUE_READY: "Лежит на ПВЗ",
}

#: Порядок очереди — хронологический, как у этапов.
QUEUE_ORDER: tuple[str, ...] = (
    QUEUE_NEW,
    QUEUE_CONFIRM,
    QUEUE_TO_SHIP,
    QUEUE_IN_TRANSIT,
    QUEUE_SORTED,
    QUEUE_READY,
)


#: Границы периода — ОБЩИЕ с блоком статистики заказов. Импорт, а не копия:
#: два блока на одном экране обязаны понимать «30 дней» одинаково, а дрейф двух
#: одинаковых реализаций в соседних модулях не поймал бы ни один тест.
resolve_period = orders_stats.resolve_period


def resolve_bucket(d_from: date, d_to: date, bucket: str | None = None) -> str:
    """Шаг динамики: явный или подобранный по длине периода."""
    if bucket in ALLOWED_BUCKETS:
        return str(bucket)
    span = (d_to - d_from).days + 1
    if span <= _BUCKET_DAY_MAX:
        return BUCKET_DAY
    if span <= _BUCKET_WEEK_MAX:
        return BUCKET_WEEK
    return BUCKET_MONTH


def _utc_bounds(d_from: date, d_to: date) -> tuple[datetime, datetime]:
    """МСК-сутки [d_from; d_to] → полуинтервал в наивном UTC."""
    start = MSK.localize(datetime.combine(d_from, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
    end = MSK.localize(datetime.combine(d_to + timedelta(days=1), time.min))
    return start, end.astimezone(pytz.UTC).replace(tzinfo=None)


def _msk(col: Any) -> Any:
    """Наивно-UTC метку → московское время.

    Два вызова `timezone` обязательны и не взаимозаменяемы: первый навешивает
    на наивную метку зону UTC, второй переводит её в московскую. Один вызов на
    `timestamp without time zone` означает в Postgres ОБРАТНОЕ преобразование
    и сдвинул бы сутки на три часа в другую сторону (та же грабля, что в
    `orders_stats._msk_day_expr`).
    """
    return func.timezone("Europe/Moscow", func.timezone("UTC", col))


def _bucket_expr(col: Any, bucket: str) -> Any:
    """МСК-бакет вехи: сутки / начало недели / начало месяца."""
    if bucket == BUCKET_DAY:
        return cast(func.date(_msk(col)), Date)
    return cast(func.date_trunc(bucket, _msk(col)), Date)


def _hours_expr(src: Any, dst: Any) -> Any:
    """Длительность этапа в часах (float)."""
    return cast(func.extract("epoch", dst - src) / 3600.0, Float)


def _history_milestone(milestone: str) -> Any:
    """Момент вехи из ИСТОРИИ КАБИНЕТА — первое вхождение подходящего имени.

    Подстроки берутся из `order_history.needles_for`, а не пишутся здесь второй
    раз: правило распознавания статуса обязано быть одно на весь домен, иначе
    новое имя WB применится в записи и не применится в аналитике.

    Цепочка повторяется на каждом плече (несколько «Отсортирован» в разных
    городах), поэтому веха — `min(at)`: первый скан, а не последний.
    """
    needles = order_history.needles_for(milestone)
    match = or_(*[func.lower(WbFbsOrderHistory.name).like(f"%{n}%") for n in needles])
    return func.min(WbFbsOrderHistory.at).filter(match)


def _milestones(project_id: int, wb_warehouse_id: int | None) -> Any:
    """CTE «вехи задания»: по строке на задание со всеми известными моментами.

    🔴 Источников вехи ТРИ, и приоритет между ними — про точность, а не про
    свежесть:
      1. **История кабинета** (`WbFbsOrderHistory`) — точное время от WB, есть
         задним числом. Главный источник поздних вех.
      2. **Точные даты зеркала** — `created_at_wb`, `closed_at`/`scan_dt` поставки.
      3. **Журнал переходов** (`WbFbsOrderEvent`) — фолбэк ± каденс синка, для
         заданий, у которых история ещё не догнана.

    Флаги `x_*` запоминают, досталась ли веха из точного источника: по ним
    считается `approx_count` этапа, и экран честно показывает, сколько замеров
    опёрлось на журнал.

    Журнал схлопывается в первое появление значения (`min(changed_at) FILTER`):
    повторный вход в статус — возврат/переоткрытие, а не новый замер.

    Вехи поставки закрыты гейтом `supplier_status ∈ (complete, cancel_carrier)`
    — канон якорей домена (см. модуль-докстринг).
    """
    history = (
        select(
            WbFbsOrderHistory.order_id.label("order_id"),
            _history_milestone(order_history.MILESTONE_CONFIRM).label("h_confirm"),
            _history_milestone(order_history.MILESTONE_ASSEMBLED).label("h_assembled"),
            _history_milestone(order_history.MILESTONE_SORTED).label("h_sorted"),
            _history_milestone(order_history.MILESTONE_READY).label("h_ready"),
            _history_milestone(order_history.MILESTONE_SOLD).label("h_sold"),
        )
        .where(WbFbsOrderHistory.project_id == project_id)
        .group_by(WbFbsOrderHistory.order_id)
        .subquery("hist")
    )
    events = (
        select(
            WbFbsOrderEvent.order_id.label("order_id"),
            func.min(WbFbsOrderEvent.changed_at)
            .filter(
                and_(
                    WbFbsOrderEvent.axis == EVENT_AXIS_SUPPLIER,
                    WbFbsOrderEvent.new_value == FbsSupplierStatus.CONFIRM.value,
                )
            )
            .label("t_confirm"),
            func.min(WbFbsOrderEvent.changed_at)
            .filter(
                and_(
                    WbFbsOrderEvent.axis == EVENT_AXIS_WB,
                    WbFbsOrderEvent.new_value == _WB_STATUS_SORTED,
                )
            )
            .label("t_sorted"),
            func.min(WbFbsOrderEvent.changed_at)
            .filter(
                and_(
                    WbFbsOrderEvent.axis == EVENT_AXIS_WB,
                    WbFbsOrderEvent.new_value == _WB_STATUS_READY,
                )
            )
            .label("t_ready"),
            func.min(WbFbsOrderEvent.changed_at)
            .filter(
                and_(
                    WbFbsOrderEvent.axis == EVENT_AXIS_WB,
                    WbFbsOrderEvent.new_value == _WB_STATUS_SOLD,
                )
            )
            .label("t_sold"),
        )
        .where(
            WbFbsOrderEvent.project_id == project_id,
            # Белый список целевых переходов. Семантически тождественно (группа,
            # у которой все FILTER дали NULL, после LEFT JOIN неотличима от
            # отсутствующей строки), но режет скан журнала: в него пишутся ещё
            # complete/waiting/new/cancel, которые этапам не нужны.
            or_(
                and_(
                    WbFbsOrderEvent.axis == EVENT_AXIS_SUPPLIER,
                    WbFbsOrderEvent.new_value == FbsSupplierStatus.CONFIRM.value,
                ),
                and_(
                    WbFbsOrderEvent.axis == EVENT_AXIS_WB,
                    WbFbsOrderEvent.new_value.in_(
                        (_WB_STATUS_SORTED, _WB_STATUS_READY, _WB_STATUS_SOLD)
                    ),
                ),
            ),
        )
        .group_by(WbFbsOrderEvent.order_id)
        .subquery("ev")
    )

    # Гейт вех поставки: только у заданий, реально уехавших с ней.
    with_supply = WbFbsOrder.supplier_status.in_(
        (FbsSupplierStatus.COMPLETE.value, FbsSupplierStatus.CANCEL_CARRIER.value)
    )

    where: list[Any] = [
        WbFbsOrder.project_id == project_id,
        contour_condition(WbFbsOrder.raw),
        WbFbsOrder.created_at_wb.isnot(None),
    ]
    if wb_warehouse_id:
        where.append(WbFbsOrder.wb_warehouse_id == int(wb_warehouse_id))

    return (
        select(
            WbFbsOrder.id.label("order_id"),
            WbFbsOrder.wb_warehouse_id.label("wb_warehouse_id"),
            effective_status_expr().label("eff_status"),
            WbFbsOrder.wb_status.label("wb_status"),
            WbFbsOrder.created_at_wb.label("t_created"),
            func.coalesce(history.c.h_confirm, events.c.t_confirm).label("t_confirm"),
            func.coalesce(
                history.c.h_assembled,
                case((with_supply, WbFbsSupply.closed_at), else_=None),
            ).label("t_closed"),
            case((with_supply, WbFbsSupply.scan_dt), else_=None).label("t_scan"),
            # 🔴 Отдельная колонка «закрытие ПОСТАВКИ» — без подмеса истории.
            # `t_closed` выше коалесцирует историю кабинета, и после первого же
            # догона он заполняется у заданий БЕЗ строки поставки. Граница зон
            # (`_left_us_expr`) различает источники по NULL, и на общей колонке
            # 326 прод-заданий уехали бы обратно в «ждёт отгрузки», пока вкладка
            # «Заказы» считает их зависшими в пути.
            case((with_supply, WbFbsSupply.closed_at), else_=None).label("t_closed_supply"),
            func.coalesce(history.c.h_sorted, events.c.t_sorted).label("t_sorted"),
            func.coalesce(history.c.h_ready, events.c.t_ready).label("t_ready"),
            func.coalesce(history.c.h_sold, events.c.t_sold).label("t_sold"),
            WbFbsOrder.written_off_at.label("t_writeoff"),
            transit_anchor_expr().label("transit_anchor"),
            # Досталась ли веха из ТОЧНОГО источника. У `t_created`/`t_closed`/
            # `t_scan` ответ всегда «да» (дата WB или наша операция), поэтому
            # флагов у них нет — считаются точными по построению.
            history.c.h_confirm.isnot(None).label("x_confirm"),
            history.c.h_sorted.isnot(None).label("x_sorted"),
            history.c.h_ready.isnot(None).label("x_ready"),
            history.c.h_sold.isnot(None).label("x_sold"),
        )
        .select_from(WbFbsOrder)
        .join(
            WbFbsSupply,
            and_(
                WbFbsSupply.project_id == project_id,
                WbFbsSupply.wb_supply_id == WbFbsOrder.supply_id,
                contour_condition(WbFbsSupply.raw),
            ),
            isouter=True,
        )
        .join(events, events.c.order_id == WbFbsOrder.id, isouter=True)
        .join(history, history.c.order_id == WbFbsOrder.id, isouter=True)
        .where(*where)
        .cte("milestones")
    )


#: Веха → колонка-флаг «досталась из точного источника». Вехи, которых здесь
#: нет, точны по построению: дата WB (`t_created`) или наша операция
#: (`t_closed`/`t_scan` поставки).
_EXACT_FLAG: dict[str, str] = {
    "t_confirm": "x_confirm",
    "t_sorted": "x_sorted",
    "t_ready": "x_ready",
    "t_sold": "x_sold",
}


def _exact_expr(ms: Any, stage: StageDef) -> Any:
    """Обе вехи этапа взяты из точного источника (истории кабинета или дат WB)."""
    flags = [ms.c[_EXACT_FLAG[key]] for key in (stage.src, stage.dst) if key in _EXACT_FLAG]
    if not flags:
        return literal(True)
    return and_(*flags) if len(flags) > 1 else flags[0]


def _valid(src: Any, dst: Any) -> Any:
    """Пара вех годна для замера: обе есть, порядок не нарушен, длительность в пределах."""
    return and_(
        src.isnot(None),
        dst.isnot(None),
        dst >= src,
        dst <= src + timedelta(days=_MAX_STAGE_DAYS),
    )


def _dropped(src: Any, dst: Any) -> Any:
    """Пара вех есть, но замер выброшен (порядок или потолок) — считаем отдельно."""
    return and_(
        src.isnot(None),
        dst.isnot(None),
        or_(dst < src, dst > src + timedelta(days=_MAX_STAGE_DAYS)),
    )


def _in_period(col: Any, start: datetime, end: datetime) -> Any:
    """Веха-«до» попала в период: этап засчитывается по дате ЗАВЕРШЕНИЯ."""
    return and_(col >= start, col < end)


def _metrics(hours: Any) -> tuple[Any, Any, Any]:
    """Медиана, p90 и среднее длительности."""
    return (
        func.percentile_cont(0.5).within_group(hours.asc()),
        func.percentile_cont(0.9).within_group(hours.asc()),
        func.avg(hours),
    )


def _f(value: Any) -> float | None:
    """Число из БД → округлённые часы (или None, если замеров не было)."""
    return None if value is None else round(float(value), 2)


async def _stage_totals(
    db: AsyncSession, ms: Any, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Сводка по каждому этапу за период — одним проходом (FILTER на агрегатах)."""
    columns: list[Any] = []
    for stage in STAGES:
        src, dst = ms.c[stage.src], ms.c[stage.dst]
        hours = _hours_expr(src, dst)
        good = and_(_valid(src, dst), _in_period(dst, start, end))
        p50, p90, avg = _metrics(hours)
        columns.extend(
            [
                func.count().filter(good),
                p50.filter(good),
                p90.filter(good),
                avg.filter(good),
                func.min(hours).filter(good),
                func.max(hours).filter(good),
                func.count().filter(and_(_dropped(src, dst), _in_period(dst, start, end))),
                # Сколько замеров опёрлось на журнал вместо истории кабинета.
                func.count().filter(and_(good, ~_exact_expr(ms, stage))),
            ]
        )

    row = (await db.execute(select(*columns).select_from(ms))).one()
    width = 8
    out: list[dict[str, Any]] = []
    for idx, stage in enumerate(STAGES):
        count, p50, p90, avg, low, high, dropped, approx = row[idx * width : idx * width + width]
        out.append(
            {
                "key": stage.key,
                "title": stage.title,
                "short": stage.short,
                "zone": stage.zone,
                "precision": stage.precision,
                "summary": stage.summary,
                "hint": stage.hint,
                "count": int(count or 0),
                "median_hours": _f(p50),
                "p90_hours": _f(p90),
                "avg_hours": _f(avg),
                "min_hours": _f(low),
                "max_hours": _f(high),
                "dropped": int(dropped or 0),
                "approx_count": int(approx or 0),
            }
        )
    return out


def _long_query(ms: Any, label: Any, start: datetime, end: datetime) -> Any:
    """UNION ALL по этапам в «длинном» виде: (stage, label, count, p50, p90, avg).

    Длинная форма вместо девяти троек колонок: разрез по складам и динамика
    отличаются только группирующим ярлыком, а фронту так проще фильтровать.
    """
    parts = []
    for stage in STAGES:
        src, dst = ms.c[stage.src], ms.c[stage.dst]
        hours = _hours_expr(src, dst)
        p50, p90, avg = _metrics(hours)
        parts.append(
            select(
                literal(stage.key).label("stage"),
                label.label("label"),
                func.count().label("cnt"),
                p50.label("p50"),
                p90.label("p90"),
                avg.label("avg"),
            )
            .select_from(ms)
            .where(_valid(src, dst), _in_period(dst, start, end))
            .group_by(label)
        )
    return union_all(*parts)


async def _by_warehouse(
    db: AsyncSession, project_id: int, ms: Any, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Разрез по складу продавца: у кого какой этап длиннее.

    Склад — тот, С КОТОРОГО собирают (`WbFbsOrder.wb_warehouse_id`, склад
    продавца в WB). Наш складской id тут намеренно не используется: привязка
    N:1 (`WbFbsWarehouseLink`), и одному складу WB может соответствовать
    несколько наших — разрез по ним не был бы разбиением.
    """
    rows = (await db.execute(_long_query(ms, ms.c.wb_warehouse_id, start, end))).all()
    if not rows:
        return []

    name_rows = (
        await db.execute(
            select(WbFbsWarehouse.wb_warehouse_id, WbFbsWarehouse.name)
            .where(WbFbsWarehouse.project_id == project_id)
            .limit(_MAX_WAREHOUSES)
        )
    ).all()
    names: dict[int, str | None] = {int(wid): name for wid, name in name_rows}
    out: list[dict[str, Any]] = []
    for stage_key, wh_id, count, p50, p90, avg in rows:
        out.append(
            {
                "stage": stage_key,
                "wb_warehouse_id": int(wh_id) if wh_id is not None else None,
                "warehouse_name": names.get(wh_id) or (f"Склад {wh_id}" if wh_id else "Без склада"),
                "count": int(count or 0),
                "median_hours": _f(p50),
                "p90_hours": _f(p90),
                "avg_hours": _f(avg),
            }
        )
    # UNION ALL из девяти GROUP BY порядка не гарантирует: без сортировки строки
    # таблицы складов гуляли бы между одинаковыми запросами.
    out.sort(key=lambda r: (str(r["warehouse_name"]), str(r["stage"])))
    return out


async def _dynamics(
    db: AsyncSession, ms: Any, start: datetime, end: datetime, bucket: str
) -> list[dict[str, Any]]:
    """Динамика: медиана длительности этапа по бакетам даты ЗАВЕРШЕНИЯ."""
    parts = []
    for stage in STAGES:
        src, dst = ms.c[stage.src], ms.c[stage.dst]
        hours = _hours_expr(src, dst)
        p50, p90, avg = _metrics(hours)
        label = _bucket_expr(dst, bucket)
        parts.append(
            select(
                literal(stage.key).label("stage"),
                label.label("bucket"),
                func.count().label("cnt"),
                p50.label("p50"),
                p90.label("p90"),
                avg.label("avg"),
            )
            .select_from(ms)
            .where(_valid(src, dst), _in_period(dst, start, end))
            .group_by(label)
        )

    rows = (await db.execute(union_all(*parts))).all()
    return [
        {
            "stage": stage_key,
            "period_start": bucket_date,
            "count": int(count or 0),
            "median_hours": _f(p50),
            "p90_hours": _f(p90),
            "avg_hours": _f(avg),
        }
        for stage_key, bucket_date, count, p50, p90, avg in sorted(
            rows, key=lambda r: (r[0], r[1])
        )
    ]


def _queue_anchor(ms: Any, phase: str) -> Any:
    """Точка отсчёта возраста для фазы очереди.

    До передачи считаем от оформления заказа (это и есть «сколько клиент ждёт»),
    после — от якоря передачи `transit_anchor_expr`, того же, которым живёт
    «зависло в пути» на вкладке «Заказы». У поздних фаз, где журнал уже наполнен,
    он уточняет отсчёт до момента входа В ЭТУ фазу.
    """
    if phase in (QUEUE_NEW, QUEUE_CONFIRM):
        return ms.c.t_created
    if phase == QUEUE_TO_SHIP:
        return func.coalesce(ms.c.t_closed, ms.c.t_created)
    if phase == QUEUE_IN_TRANSIT:
        return func.coalesce(ms.c.transit_anchor, ms.c.t_created)
    if phase == QUEUE_SORTED:
        return func.coalesce(ms.c.t_sorted, ms.c.transit_anchor, ms.c.t_created)
    return func.coalesce(ms.c.t_ready, ms.c.t_sorted, ms.c.transit_anchor, ms.c.t_created)


def _left_us_expr(ms: Any) -> Any:
    """«Груз физически уже не у нас» — граница `to_ship` / `in_transit`.

    Скан QR знает поставка, но её зеркало бывает неполным, а `in_delivery_stuck`
    на вкладке «Заказы» отсчитывает передачу от
    `coalesce(scan_dt, closed_at, written_off_at)`. Повторяем ту же логику по
    смыслу, а не буквально:
      • есть `scan_dt` — уехало точно;
      • поставки в зеркале НЕТ вовсе, но есть наше списание — списание делается
        в момент передачи, значит уехало (326 таких заданий на проде; сырой
        `t_scan IS NULL` держал бы их в «ждёт отгрузки», пока соседний экран
        считает их зависшими в пути);
      • поставка есть, `closed_at` есть, скана нет — честное «ждёт отгрузки»,
        и оно остаётся у нас.
    """
    return or_(
        ms.c.t_scan.isnot(None),
        and_(ms.c.t_closed_supply.is_(None), ms.c.t_writeoff.isnot(None)),
    )


def _queue_condition(ms: Any, phase: str) -> Any:
    """Множество фазы очереди — разбиение тех же множеств, что на «Заказах».

    Предикаты `orders_service` написаны против колонок `WbFbsOrder`/`WbFbsSupply`,
    а очередь считается по CTE вех, поэтому здесь их зеркало на колонках CTE.
    🔴 Зеркало обязано повторять белые списки бит-в-бит, иначе счётчик очереди
    разойдётся с карточкой склада на соседнем экране. Два инварианта закреплены
    тестами:
        `to_ship` ∪ `in_transit` == `in_delivery_condition()`
        `sorted`  ∪ `ready`      == `sorted_condition()`
    Поэтому «отсортировано» тянет ВЕСЬ `FBS_WB_SORTED_STATUSES` минус
    `ready_for_pickup`, а не литерал `sorted`: `postponed_delivery` (заведён в
    белый список после инцидента 30.07) иначе не попадал бы НИ В ОДНУ фазу и
    исчезал бы с экрана молча.
    """
    complete = FbsSupplierStatus.COMPLETE.value
    pre_sort_or_empty = or_(
        ms.c.wb_status.is_(None),
        ms.c.wb_status == "",
        ms.c.wb_status.in_(FBS_WB_PRE_SORT_STATUSES),
    )
    alive = ms.c.eff_status.notin_(FBS_TERMINAL_STATUSES)
    sorted_not_ready = tuple(s for s in FBS_WB_SORTED_STATUSES if s != _WB_STATUS_READY)

    if phase == QUEUE_NEW:
        return and_(alive, ms.c.eff_status == FbsSupplierStatus.NEW.value)
    if phase == QUEUE_CONFIRM:
        return and_(alive, ms.c.eff_status == FbsSupplierStatus.CONFIRM.value)
    if phase == QUEUE_TO_SHIP:
        return and_(ms.c.eff_status == complete, ~_left_us_expr(ms), pre_sort_or_empty)
    if phase == QUEUE_IN_TRANSIT:
        return and_(ms.c.eff_status == complete, _left_us_expr(ms), pre_sort_or_empty)
    if phase == QUEUE_SORTED:
        return and_(ms.c.eff_status == complete, ms.c.wb_status.in_(sorted_not_ready))
    return and_(ms.c.eff_status == complete, ms.c.wb_status == _WB_STATUS_READY)


async def _queue(db: AsyncSession, ms: Any) -> list[dict[str, Any]]:
    """Что висит СЕЙЧАС и как давно. Период намеренно не применяется."""
    now = utcnow()
    columns: list[Any] = []
    for phase in QUEUE_ORDER:
        anchor = _queue_anchor(ms, phase)
        age = _hours_expr(anchor, literal(now, type_=WbFbsOrder.created_at_wb.type))
        # Якорь не бывает NULL по построению: все ветки `_queue_anchor`
        # коалесцируют в `t_created`, а CTE требует `created_at_wb IS NOT NULL`.
        cond = _queue_condition(ms, phase)
        columns.extend(
            [
                func.count().filter(cond),
                func.percentile_cont(0.5).within_group(age.asc()).filter(cond),
                func.max(age).filter(cond),
            ]
        )

    row = (await db.execute(select(*columns).select_from(ms))).one()
    out: list[dict[str, Any]] = []
    for idx, phase in enumerate(QUEUE_ORDER):
        count, median, oldest = row[idx * 3 : idx * 3 + 3]
        out.append(
            {
                "phase": phase,
                "title": _QUEUE_TITLES[phase],
                "count": int(count or 0),
                "median_age_hours": _f(median),
                "max_age_hours": _f(oldest),
            }
        )
    return out


async def _journal_coverage(db: AsyncSession, project_id: int) -> dict[str, Any]:
    """Покрытие журнала переходов: с какого момента поздние этапы вообще считаются.

    Без этой подписи экран врёт умолчанием: пустой «Ожидание на ПВЗ» читается
    как «мгновенно», хотя на деле это «истории ещё нет». Журнал начинается с
    деплоя и прошлое не восстанавливает (см. модуль-докстринг).
    """
    row = (
        await db.execute(
            select(
                func.count(),
                func.min(WbFbsOrderEvent.changed_at),
                func.count(func.distinct(WbFbsOrderEvent.order_id)),
            ).where(WbFbsOrderEvent.project_id == project_id)
        )
    ).one()
    events, since, orders = row
    return {
        "events": int(events or 0),
        "since": since,
        "orders": int(orders or 0),
    }


async def stage_analytics(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: Any = None,
    date_to: Any = None,
    wb_warehouse_id: int | None = None,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Аналитика этапов FBS за период (`FbsStageAnalyticsOut`).

    Запросы идут последовательно и намеренно не распараллелены: одна сессия
    SQLAlchemy не потокобезопасна, а `asyncio.gather` по ней даёт
    `InterfaceError: another operation is in progress`.
    """
    d_from, d_to = resolve_period(date_from, date_to)
    step = resolve_bucket(d_from, d_to, bucket)
    start, end = _utc_bounds(d_from, d_to)
    ms = _milestones(project_id, wb_warehouse_id)

    stages = await _stage_totals(db, ms, start, end)
    result = {
        "date_from": d_from,
        "date_to": d_to,
        "bucket": step,
        "wb_warehouse_id": wb_warehouse_id,
        "stages": stages,
        "by_warehouse": await _by_warehouse(db, project_id, ms, start, end),
        "dynamics": await _dynamics(db, ms, start, end, step),
        "queue": await _queue(db, ms),
        "journal": await _journal_coverage(db, project_id),
        # Покрытие догона истории кабинета — подпись «на чём посчитано».
        "history": await order_history.history_coverage(db, project_id),
    }
    logger.info(
        "FBS stage analytics: project=%s %s..%s wh=%s bucket=%s measured=%s",
        project_id,
        d_from,
        d_to,
        wb_warehouse_id or "все",
        step,
        {s["key"]: s["count"] for s in stages if s["count"]},
    )
    return result
