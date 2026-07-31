# ruff: noqa: RUF001, RUF002, RUF003
"""
География доставки FBS: куда едут заказы, как быстро и что съедает время.

Аналитика этапов (`stage_analytics`) отвечает «сколько длится шаг». Этот модуль
отвечает на три соседних вопроса, которые без географии не разрешимы:
**куда** едет (округ покупателя), **каким маршрутом** (узлы СЦ) и **что
конкретно тормозит** (перевалки и удержание на узлах).

## Источники и что каждый умеет

  • **Округ/регион покупателя** — сшивка с зеркалом статистики WB по
    `WbFbsOrder.rid == WbOrder.srid` (индекс `uq_wb_orders_project_srid`).
    🔴 Покрытие НЕПОЛНОЕ (на проде ~74%): не всякое задание доезжает до отчёта
    статистики. Доля сшитых отдаётся в `coverage` — без неё «мало заказов
    в Сибири» не отличить от «Сибирь не сшилась».

  • **Узлы маршрута** — `WbFbsOrderHistory.place`. 🔴 Последний узел — это
    сортировочный центр, а НЕ город покупателя, и у курьерской ветки его нет
    вовсе. Поэтому направление берётся только из зеркала статистики, а `place`
    работает исключительно как узел маршрута.

  • **Склад отгрузки** — `WbFbsOrder.wb_warehouse_id`. `office_name` задания
    для этого НЕ годится: это пункт приёма, куда мы сдаём поставку (у одного
    склада он один), а не адрес доставки.

## Две ловушки, заложенные в расчёт

  🔴 **Выкуп нельзя считать на всей выборке.** Почти всё свежее ещё в пути:
  на проде в ЦФО 27 выкупов на 1295 заказов — не потому что не выкупают, а
  потому что не доехали. Доля отказов считается только по СОЗРЕВШЕЙ когорте
  (`_MATURITY_DAYS` от даты заказа), и размер этой когорты отдаётся отдельным
  полем `matured` — иначе получилась бы классическая ошибка выжившего.

  🔴 **«0 часов удержания» ≠ «прошёл мгновенно».** У части узлов в истории
  одно событие на заказ, и держали там груз или нет — неизвестно. Такие узлы
  помечены `measured = 0` и обязаны показываться прочерком, а не нулём.

Период применяется по дате ЗАВЕРШЕНИЯ пути (готовность к вручению) — тот же
канон, что у этапов: группировка по дате заказа дала бы систематический
перекос из недоехавших.
"""

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Float, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_order import WbOrder
from backend.models.wb_fbs import (
    FBS_WB_CLIENT_CANCEL_STATUSES,
    WbFbsOrder,
    WbFbsOrderHistory,
    WbFbsWarehouse,
)
from backend.services.wb_fbs import order_history
from backend.services.wb_fbs.contour import contour_condition
from backend.services.wb_fbs.stage_analytics import (
    _MAX_STAGE_DAYS,
    _utc_bounds,
    resolve_period,
)

logger = logging.getLogger(__name__)

#: Сколько дней заказу нужно, чтобы его судьба определилась. Раньше этого срока
#: он почти наверняка ещё едет, и доля отказов по нему ничего не значит.
_MATURITY_DAYS = 14

#: Потолки строк разрезов — защита от вырождения справочника, не ограничение.
_MAX_DIRECTIONS = 200
_MAX_NODES = 200
_MAX_MATRIX = 500

#: Узел с меньшим числом проходов — статистический шум, в таблицу не берём.
_MIN_NODE_PASSES = 20

_NO_REGION = "Не сшито со статистикой"

#: Псевдо-направление «итого по складу». 🔴 Обязательно: клетки по округам
#: строятся ТОЛЬКО на сшитых заданиях, и склад, у которого все завершённые пути
#: не сшились, исчезал из матрицы целиком (ловилось живьём на «Газпром Москва»).
#: Итог считается по ВСЕМ путям, поэтому строка склада не пропадает никогда.
_ALL_DIRECTIONS = "Все направления"


def _milestone(needles: tuple[str, ...]) -> Any:
    """`min(at)` по строкам истории, чьё имя подходит под подстроки вехи."""
    match = or_(*[func.lower(WbFbsOrderHistory.name).like(f"%{n}%") for n in needles])
    return func.min(WbFbsOrderHistory.at).filter(match)


def _paths(project_id: int) -> Any:
    """CTE «путь задания»: вехи пути и число перевалок.

    Перевалки — `count(distinct place)`: узел, а не событие. Цепочка на одном
    узле повторяется («Отсортирован» дважды подряд), и счёт по событиям
    завышал бы длину маршрута вдвое.
    """
    return (
        select(
            WbFbsOrderHistory.order_id.label("order_id"),
            _milestone(order_history.needles_for(order_history.MILESTONE_SORTED)).label("t_sorted"),
            _milestone(order_history.needles_for(order_history.MILESTONE_READY)).label("t_ready"),
            func.count(func.distinct(WbFbsOrderHistory.place))
            .filter(func.coalesce(WbFbsOrderHistory.place, "") != "")
            .label("hops"),
        )
        .where(WbFbsOrderHistory.project_id == project_id)
        .group_by(WbFbsOrderHistory.order_id)
        .cte("paths")
    )


def _days_expr(paths: Any) -> Any:
    """Длительность пути «первая сортировка → готовность к вручению», в днях."""
    return cast(func.extract("epoch", paths.c.t_ready - paths.c.t_sorted) / 86400.0, Float)


def _measurable(paths: Any, start: Any, end: Any) -> Any:
    """Путь пройден целиком, длительность правдоподобна и завершился в периоде."""
    return and_(
        paths.c.t_sorted.isnot(None),
        paths.c.t_ready.isnot(None),
        paths.c.t_ready >= paths.c.t_sorted,
        paths.c.t_ready <= paths.c.t_sorted + timedelta(days=_MAX_STAGE_DAYS),
        paths.c.t_ready >= start,
        paths.c.t_ready < end,
    )


def _base_join(project_id: int, paths: Any, wb_warehouse_id: int | None) -> Any:
    """Задания текущего контура + путь + округ покупателя из зеркала статистики."""
    query = (
        select(WbFbsOrder, paths, WbOrder.oblast_okrug_name, WbOrder.region_name)
        .select_from(WbFbsOrder)
        .join(paths, paths.c.order_id == WbFbsOrder.id, isouter=True)
        .join(
            WbOrder,
            and_(WbOrder.project_id == project_id, WbOrder.srid == WbFbsOrder.rid),
            isouter=True,
        )
        .where(WbFbsOrder.project_id == project_id, contour_condition(WbFbsOrder.raw))
    )
    if wb_warehouse_id:
        query = query.where(WbFbsOrder.wb_warehouse_id == int(wb_warehouse_id))
    return query


def _f(value: Any, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


async def _directions(
    db: AsyncSession, project_id: int, paths: Any, start: Any, end: Any, wh: int | None, now: Any
) -> list[dict[str, Any]]:
    """Разрез по округу покупателя: скорость, перевалки, SLA и отказы.

    В строке уживаются ДВЕ разные выборки, и это осознанно:
      • скорость и перевалки — по заданиям, чей путь завершился в периоде;
      • отказы — по СОЗРЕВШЕЙ когорте (заказ старше `_MATURITY_DAYS`).
    Поэтому у каждой свой счётчик (`orders` и `matured`): одна цифра на две
    разные популяции читалась бы как ошибка.
    """
    label = func.coalesce(func.nullif(WbOrder.oblast_okrug_name, ""), _NO_REGION)
    days = _days_expr(paths)
    good = _measurable(paths, start, end)
    matured = WbFbsOrder.created_at_wb < now - timedelta(days=_MATURITY_DAYS)
    refused = and_(matured, WbFbsOrder.wb_status.in_(FBS_WB_CLIENT_CANCEL_STATUSES))
    in_time = and_(
        good,
        WbFbsOrder.delivery_date_plan.isnot(None),
        paths.c.t_ready <= WbFbsOrder.delivery_date_plan,
    )
    has_plan = and_(good, WbFbsOrder.delivery_date_plan.isnot(None))

    query = (
        _base_join(project_id, paths, wh)
        .with_only_columns(
            label.label("okrug"),
            func.count().filter(good).label("orders"),
            func.percentile_cont(0.5).within_group(days.asc()).filter(good).label("p50"),
            func.percentile_cont(0.9).within_group(days.asc()).filter(good).label("p90"),
            func.avg(paths.c.hops).filter(good).label("hops"),
            func.count().filter(matured).label("matured"),
            func.count().filter(refused).label("refused"),
            func.count().filter(has_plan).label("sla_total"),
            func.count().filter(in_time).label("sla_in_time"),
        )
        .group_by(label)
        .order_by(func.count().filter(good).desc())
        .limit(_MAX_DIRECTIONS)
    )
    rows = (await db.execute(query)).all()
    return [
        {
            "label": str(okrug),
            "orders": int(orders or 0),
            "median_days": _f(p50, 1),
            "p90_days": _f(p90, 1),
            "avg_hops": _f(hops, 1),
            "matured": int(mat or 0),
            "refused": int(ref or 0),
            "sla_total": int(sla_total or 0),
            "sla_in_time": int(sla_ok or 0),
        }
        for okrug, orders, p50, p90, hops, mat, ref, sla_total, sla_ok in rows
        if (orders or 0) or (mat or 0)
    ]


async def _matrix(
    db: AsyncSession, project_id: int, paths: Any, start: Any, end: Any, wh: int | None
) -> list[dict[str, Any]]:
    """Матрица «склад отгрузки × округ назначения» — откуда куда возить.

    Единственный разрез, который прямо отвечает на операционный вопрос: у
    какого склада какое направление получается быстрым. Сравнивать склады
    БЕЗ этого разреза нельзя — разная география доставки даёт разницу вдвое
    при одинаковой работе склада.
    """
    okrug = func.coalesce(func.nullif(WbOrder.oblast_okrug_name, ""), _NO_REGION)
    days = _days_expr(paths)
    good = _measurable(paths, start, end)

    query = (
        _base_join(project_id, paths, wh)
        .with_only_columns(
            WbFbsOrder.wb_warehouse_id.label("wh"),
            okrug.label("okrug"),
            func.count().label("orders"),
            func.percentile_cont(0.5).within_group(days.asc()).label("p50"),
        )
        # 🔴 Псевдо-округ «не сшито» из матрицы ИСКЛЮЧЁН: это не направление, а
        # признак пробела в зеркале статистики. Столбцом он выглядел как ещё
        # одна география и мешал единственному честному сравнению — внутри
        # столбца. Полноту «куда едет» закрывает блок `destinations`, у него
        # покрытие 100%.
        .where(good, WbOrder.oblast_okrug_name.isnot(None))
        .group_by(WbFbsOrder.wb_warehouse_id, okrug)
        .order_by(func.count().desc())
        .limit(_MAX_MATRIX)
    )
    per_okrug = (await db.execute(query)).all()

    # Итог по складу — по ВСЕМ завершённым путям, включая несшитые.
    totals = (
        await db.execute(
            _base_join(project_id, paths, wh)
            .with_only_columns(
                WbFbsOrder.wb_warehouse_id.label("wh"),
                func.count().label("orders"),
                func.percentile_cont(0.5).within_group(days.asc()).label("p50"),
            )
            .where(good)
            .group_by(WbFbsOrder.wb_warehouse_id)
            .limit(_MAX_MATRIX)
        )
    ).all()
    rows = [(wh_id, _ALL_DIRECTIONS, n, p50) for wh_id, n, p50 in totals] + list(per_okrug)
    if not rows:
        return []

    name_rows = (
        await db.execute(
            select(WbFbsWarehouse.wb_warehouse_id, WbFbsWarehouse.name)
            .where(WbFbsWarehouse.project_id == project_id)
            .limit(_MAX_MATRIX)
        )
    ).all()
    names: dict[int, str | None] = {int(wid): nm for wid, nm in name_rows}
    return [
        {
            "wb_warehouse_id": int(wh_id) if wh_id is not None else None,
            "warehouse_name": names.get(wh_id) or (f"Склад {wh_id}" if wh_id else "Без склада"),
            "label": str(ok),
            "orders": int(n or 0),
            "median_days": _f(p50, 1),
        }
        for wh_id, ok, n, p50 in rows
    ]


async def _destinations(
    db: AsyncSession, project_id: int, paths: Any, start: Any, end: Any, wh: int | None
) -> list[dict[str, Any]]:
    """Города назначения — ПОСЛЕДНИЙ узел маршрута перед готовностью к вручению.

    Зачем при наличии округов: сшивка с зеркалом статистики покрывает не всё
    (отменённые заказы в отчёт продаж не попадают вовсе, свежие отстают на
    сутки), и «Не сшито» съедало пятую часть выборки. Последний узел известен
    для ВСЕХ заданий с завершённым путём — 226 из 226 на живых данных.

    🔴 Это узел WB (СЦ/ПВЗ), а НЕ город покупателя: «Вёшки» и «Внуково Почта
    России» — это точки сети, а не адрес. Для вопроса «куда физически едет
    груз» этого достаточно, для демографии покупателя — нет, и подпись на
    экране обязана эту разницу называть.
    """
    last_node = (
        select(
            WbFbsOrderHistory.order_id.label("order_id"),
            WbFbsOrderHistory.place.label("place"),
            func.row_number()
            .over(
                partition_by=WbFbsOrderHistory.order_id,
                order_by=WbFbsOrderHistory.at.desc(),
            )
            .label("rn"),
        )
        .join(paths, paths.c.order_id == WbFbsOrderHistory.order_id)
        .where(
            WbFbsOrderHistory.project_id == project_id,
            func.coalesce(WbFbsOrderHistory.place, "") != "",
            WbFbsOrderHistory.at <= paths.c.t_ready,
        )
        .subquery("ln")
    )
    days = _days_expr(paths)
    rows = (
        await db.execute(
            _base_join(project_id, paths, wh)
            .join(
                last_node,
                and_(last_node.c.order_id == WbFbsOrder.id, last_node.c.rn == 1),
            )
            .with_only_columns(
                last_node.c.place.label("place"),
                func.count().label("orders"),
                func.percentile_cont(0.5).within_group(days.asc()).label("p50"),
                func.percentile_cont(0.9).within_group(days.asc()).label("p90"),
            )
            .where(_measurable(paths, start, end))
            .group_by(last_node.c.place)
            .order_by(func.count().desc())
            .limit(_MAX_DIRECTIONS)
        )
    ).all()
    return [
        {
            "label": str(place),
            "orders": int(n or 0),
            "median_days": _f(p50, 1),
            "p90_days": _f(p90, 1),
        }
        for place, n, p50, p90 in rows
    ]


async def _nodes(db: AsyncSession, project_id: int, start: Any, end: Any) -> list[dict[str, Any]]:
    """Узлы маршрута: сколько заказов прошло и сколько узел держит груз.

    Удержание — от первого события узла до последнего по этому же заказу.
    🔴 Если событие на узле ОДНО, удержание неизвестно (не ноль!): такие
    проходы считаются отдельно (`measured`), и при `measured = 0` длительность
    отдаётся `None`, чтобы экран нарисовал прочерк, а не «мгновенно».
    """
    legs = (
        select(
            WbFbsOrderHistory.place.label("place"),
            WbFbsOrderHistory.order_id.label("order_id"),
            func.min(WbFbsOrderHistory.at).label("arrived"),
            func.max(WbFbsOrderHistory.at).label("left_at"),
            func.count().label("events"),
        )
        .where(
            WbFbsOrderHistory.project_id == project_id,
            func.coalesce(WbFbsOrderHistory.place, "") != "",
            WbFbsOrderHistory.at >= start,
            WbFbsOrderHistory.at < end,
        )
        .group_by(WbFbsOrderHistory.place, WbFbsOrderHistory.order_id)
        .subquery("legs")
    )
    hold = cast(func.extract("epoch", legs.c.left_at - legs.c.arrived) / 3600.0, Float)
    measurable = legs.c.events > 1

    rows = (
        await db.execute(
            select(
                legs.c.place,
                func.count().label("passes"),
                func.count().filter(measurable).label("measured"),
                func.percentile_cont(0.5).within_group(hold.asc()).filter(measurable).label("p50"),
                func.percentile_cont(0.9).within_group(hold.asc()).filter(measurable).label("p90"),
            )
            .group_by(legs.c.place)
            .having(func.count() >= _MIN_NODE_PASSES)
            .order_by(func.count().desc())
            .limit(_MAX_NODES)
        )
    ).all()
    return [
        {
            "label": str(place),
            "passes": int(passes or 0),
            "measured": int(measured or 0),
            "median_hours": _f(p50, 1) if measured else None,
            "p90_hours": _f(p90, 1) if measured else None,
        }
        for place, passes, measured, p50, p90 in rows
    ]


async def _hops(
    db: AsyncSession, project_id: int, paths: Any, start: Any, end: Any, wh: int | None
) -> list[dict[str, Any]]:
    """Перевалки против времени — главный управляемый рычаг маршрута.

    На проде связь почти линейная: 1 узел ≈ 0.7 дня, 4 узла ≈ 3.5 дня, то есть
    каждая лишняя перевалка добавляет около суток. Это единственная метрика
    раздела, на которую можно повлиять выбором склада отгрузки.
    """
    days = _days_expr(paths)
    good = _measurable(paths, start, end)
    rows = (
        await db.execute(
            _base_join(project_id, paths, wh)
            .with_only_columns(
                paths.c.hops.label("hops"),
                func.count().label("orders"),
                func.percentile_cont(0.5).within_group(days.asc()).label("p50"),
            )
            .where(good)
            .group_by(paths.c.hops)
            .order_by(paths.c.hops)
        )
    ).all()
    return [
        {"hops": int(h or 0), "orders": int(n or 0), "median_days": _f(p50, 1)}
        for h, n, p50 in rows
    ]


async def _coverage(db: AsyncSession, project_id: int, wh: int | None) -> dict[str, Any]:
    """Доля заданий, сшитых с зеркалом статистики (иначе округ неизвестен).

    Без этой подписи «мало заказов в Сибири» не отличить от «Сибирь не сшилась».
    """
    query = select(
        func.count(),
        func.count().filter(WbOrder.srid.isnot(None)),
    ).select_from(WbFbsOrder).join(
        WbOrder,
        and_(WbOrder.project_id == project_id, WbOrder.srid == WbFbsOrder.rid),
        isouter=True,
    ).where(WbFbsOrder.project_id == project_id, contour_condition(WbFbsOrder.raw))
    if wh:
        query = query.where(WbFbsOrder.wb_warehouse_id == int(wh))
    total, matched = (await db.execute(query)).one()
    return {"orders_total": int(total or 0), "orders_matched": int(matched or 0)}


async def geo_analytics(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: Any = None,
    date_to: Any = None,
    wb_warehouse_id: int | None = None,
) -> dict[str, Any]:
    """География доставки FBS (`FbsGeoAnalyticsOut`).

    Запросы идут последовательно и намеренно не распараллелены: одна сессия
    SQLAlchemy не потокобезопасна (`InterfaceError: another operation is in
    progress`).
    """
    d_from, d_to = resolve_period(date_from, date_to)
    start, end = _utc_bounds(d_from, d_to)
    paths = _paths(project_id)
    now = func.timezone("UTC", func.now())

    directions = await _directions(db, project_id, paths, start, end, wb_warehouse_id, now)
    nodes = await _nodes(db, project_id, start, end)
    result: dict[str, Any] = {
        "date_from": d_from,
        "date_to": d_to,
        "wb_warehouse_id": wb_warehouse_id,
        "maturity_days": _MATURITY_DAYS,
        "directions": directions,
        "matrix": await _matrix(db, project_id, paths, start, end, wb_warehouse_id),
        "destinations": await _destinations(db, project_id, paths, start, end, wb_warehouse_id),
        "nodes": nodes,
        "hops": await _hops(db, project_id, paths, start, end, wb_warehouse_id),
        "coverage": await _coverage(db, project_id, wb_warehouse_id),
    }
    logger.info(
        "FBS geo analytics: project=%s %s..%s wh=%s направлений=%s узлов=%s",
        project_id,
        d_from,
        d_to,
        wb_warehouse_id or "все",
        len(directions),
        len(nodes),
    )
    return result
