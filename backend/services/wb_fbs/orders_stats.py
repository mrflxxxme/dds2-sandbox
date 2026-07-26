# ruff: noqa: RUF001, RUF002, RUF003
"""
Статистика заказов FBS за период: выручка, разрезы и доля в общем объёме.

Отвечает на вопрос «сколько мы продали со своего склада и что именно»:
штуки и рубли за период, разбивка по дням, брендам, предметам и под-категориям,
плюс сопоставление с воронкой продаж.

Что здесь важно и легко потерять:

  • **Одно задание = одна единица товара** (инвариант домена, см. `WbFbsOrder`),
    поэтому «штук» — это `COUNT(*)`, отдельного поля количества нет.

  • **Цена позиции — общая формула `orders_service.revenue_rub_expr()`**, она же
    в листе подбора (`supplies_service._pick_list_groups`): расхождение между
    двумя экранами по одной и той же сумме читалось бы как ошибка расчёта.
    🔴 Наивная сумма `salePrice`/`price` НЕВЕРНА — эти поля в валюте ПРОДАЖИ, а
    WB торгует и в СНГ: узбекский заказ прибавлял к выручке 2.6 M «рублей».
    Для не-рублёвых строк берётся `convertedPrice` (пересчёт самого WB).
    Копейки WB делит на 100 синк заданий, так что в базе всё уже в единицах.

  • **Сутки считаются по МСК, а не по UTC.** `created_at_wb` хранится наивным
    UTC, воронка WB отчитывается московскими сутками. Без приведения заказ,
    сделанный в 01:00 МСК, попал бы во вчерашний день, и дневная разбивка не
    сошлась бы с воронкой ни на границах периода, ни в итогах.

  • **Отменённые НЕ вычитаются из основной цифры.** Воронка WB отмены тоже не
    вычитает, а весь смысл блока — сравнение с ней. Отменённые показываются
    отдельной строкой, чтобы «живой» объём был виден рядом.

  • **Доля FBS считается от воронки как от ПОЛНОГО объёма.** Отчёт воронки —
    карточный: он не делит продажи по схеме (в коде синка нет ни одного фильтра
    по FBO/FBS, а рядом лежат `stocks.mp` и `timeToReady`, которые существуют
    только для FBS). То есть знаменатель включает и FBO, и FBS — доля честная.
    Если WB когда-нибудь начнёт отдавать воронку без FBS, доля станет завышенной,
    и это придётся ловить здесь.

  • Выдача скоуплена по контуру (`contour_condition`): задания песочницы не
    должны попадать в боевые цифры выручки.
"""

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytz
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.integrations import WbFunnelDaily
from backend.models.refs import ProductSubcategory, ProductSubcategoryMap
from backend.models.wb_fbs import FBS_TERMINAL_STATUSES, WbFbsOrder
from backend.services.wb_fbs.contour import contour_condition
from backend.services.wb_fbs.orders_service import revenue_rub_expr

logger = logging.getLogger(__name__)

MSK = pytz.timezone("Europe/Moscow")

#: Период по умолчанию — как в воронке продаж (последние 30 дней).
_DEFAULT_DAYS = 30
#: Потолок глубины периода: 400 дней с запасом перекрывают «год к году».
_MAX_DAYS = 400
#: Потолок строк любого разреза. Брендов и предметов десятки; потолок — защита
#: от вырождения (мусорный справочник), а не рабочее ограничение.
_MAX_GROUPS = 500

#: Ярлыки пустых значений — те же, что в воронке, иначе один и тот же товар
#: назывался бы на двух экранах по-разному.
_NO_BRAND = "Без бренда"
_NO_SUBJECT = "Без категории"
_NO_SUBCATEGORY = "Без под-категории"


def _revenue_expr() -> Any:
    """Выручка строки в рублях — общая формула домена (см. `revenue_rub_expr`).

    Своей копии тут быть не должно: `price`/`salePrice` лежат в валюте ПРОДАЖИ,
    и наивная сумма подмешивала сумы и тенге к рублям.
    """
    return revenue_rub_expr()


def _msk_day_expr() -> Any:
    """МСК-дата задания из наивно-UTC `created_at_wb`.

    Два вызова `timezone` обязательны и не взаимозаменяемы: первый навешивает на
    наивную метку зону UTC, второй переводит её в московскую. Один вызов на
    `timestamp without time zone` в Postgres означает ОБРАТНОЕ преобразование
    (из зоны в UTC) и сдвинул бы сутки на три часа в другую сторону.
    """
    return func.date(func.timezone("Europe/Moscow", func.timezone("UTC", WbFbsOrder.created_at_wb)))


def resolve_period(date_from: Any = None, date_to: Any = None) -> tuple[date, date]:
    """Календарные МСК-границы периода: что не задано — последние 30 дней.

    Верхняя граница включительна (в неё входит сегодня целиком), период
    подрезается до `_MAX_DAYS` — запрос «с 1970 года» иначе положил бы базу.
    """
    today = datetime.now(MSK).date()
    d_to = _as_date(date_to) or today
    d_from = _as_date(date_from) or (d_to - timedelta(days=_DEFAULT_DAYS - 1))
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    if (d_to - d_from).days > _MAX_DAYS:
        d_from = d_to - timedelta(days=_MAX_DAYS)
    return d_from, d_to


def _as_date(value: Any) -> date | None:
    """`date` / `datetime` / ISO-строка → календарная дата."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _utc_bounds(d_from: date, d_to: date) -> tuple[datetime, datetime]:
    """МСК-сутки [d_from; d_to] → полуинтервал в наивном UTC для сравнения с колонкой."""
    start = MSK.localize(datetime.combine(d_from, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
    end = MSK.localize(datetime.combine(d_to + timedelta(days=1), time.min))
    return start, end.astimezone(pytz.UTC).replace(tzinfo=None)


def _base_where(project_id: int, d_from: date, d_to: date, wb_warehouse_id: int | None) -> list[Any]:
    """Общий фильтр всех запросов статистики: проект, контур, период, склад."""
    start, end = _utc_bounds(d_from, d_to)
    where: list[Any] = [
        WbFbsOrder.project_id == project_id,
        contour_condition(WbFbsOrder.raw),
        WbFbsOrder.created_at_wb >= start,
        WbFbsOrder.created_at_wb < end,
    ]
    if wb_warehouse_id:
        where.append(WbFbsOrder.wb_warehouse_id == int(wb_warehouse_id))
    return where


def _grouped(label: Any, where: list[Any]) -> Select:
    """Заготовка разреза: ярлык → (штук, рублей), по убыванию суммы."""
    revenue = func.coalesce(func.sum(_revenue_expr()), 0).label("orders_sum")
    return (
        select(label.label("label"), func.count().label("orders_count"), revenue)
        .where(*where)
        .group_by(label)
        .order_by(revenue.desc())
        .limit(_MAX_GROUPS)
    )


def _rows(result: Any) -> list[dict[str, Any]]:
    """Строки разреза в контракт схемы (`FbsOrderStatRow`)."""
    return [
        {
            "label": str(label),
            "orders_count": int(count or 0),
            "orders_sum": Decimal(str(total or 0)),
        }
        for label, count, total in result.all()
    ]


async def _totals(db: AsyncSession, where: list[Any]) -> dict[str, Any]:
    """Итоги периода: всего, из них отменено (в штуках и рублях)."""
    cancelled = WbFbsOrder.supplier_status.in_(FBS_TERMINAL_STATUSES)
    result = await db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(_revenue_expr()), 0),
            func.count().filter(cancelled),
            func.coalesce(func.sum(_revenue_expr()).filter(cancelled), 0),
        ).where(*where)
    )
    count, total, cancel_count, cancel_sum = result.one()
    return {
        "orders_count": int(count or 0),
        "orders_sum": Decimal(str(total or 0)),
        "cancelled_count": int(cancel_count or 0),
        "cancelled_sum": Decimal(str(cancel_sum or 0)),
    }


async def _by_day(db: AsyncSession, where: list[Any]) -> list[dict[str, Any]]:
    """Разбивка по МСК-суткам — по возрастанию даты (это график, а не топ)."""
    day = _msk_day_expr()
    revenue = func.coalesce(func.sum(_revenue_expr()), 0)
    result = await db.execute(
        select(day.label("day"), func.count(), revenue)
        .where(*where)
        .group_by(day)
        .order_by(day)
        .limit(_MAX_DAYS + 1)
    )
    return [
        {"day": d, "orders_count": int(count or 0), "orders_sum": Decimal(str(total or 0))}
        for d, count, total in result.all()
    ]


async def _by_status(db: AsyncSession, where: list[Any]) -> list[dict[str, Any]]:
    """Разрез по статусу продавца. Ярлыки — на фронте (`SUPPLIER_STATUS_LABEL`)."""
    result = await db.execute(_grouped(func.coalesce(WbFbsOrder.supplier_status, "—"), where))
    return _rows(result)


async def _by_brand(db: AsyncSession, where: list[Any]) -> list[dict[str, Any]]:
    """Разрез по бренду. Бренда в задании нет — берём из номенклатуры по FK."""
    label = func.coalesce(func.nullif(Nomenclature.brand, ""), _NO_BRAND)
    query = _grouped(label, where).join(
        Nomenclature, Nomenclature.id == WbFbsOrder.nomenclature_id, isouter=True
    )
    return _rows(await db.execute(query))


async def _by_subject(db: AsyncSession, where: list[Any]) -> list[dict[str, Any]]:
    """Разрез по предмету: снимок в задании, при пустом — из номенклатуры.

    Снимок первичен намеренно: он зафиксирован на момент заказа, а карточку в
    номенклатуре могли позже переименовать — тогда прошлые продажи «переехали»
    бы в другую категорию задним числом.
    """
    label = func.coalesce(
        func.nullif(WbFbsOrder.subject, ""), func.nullif(Nomenclature.subject, ""), _NO_SUBJECT
    )
    query = _grouped(label, where).join(
        Nomenclature, Nomenclature.id == WbFbsOrder.nomenclature_id, isouter=True
    )
    return _rows(await db.execute(query))


async def _by_subcategory(db: AsyncSession, project_id: int, where: list[Any]) -> list[dict[str, Any]]:
    """Разрез по под-категории. Карта привязана к `nm_id`, не к id номенклатуры.

    `ProductSubcategory` — SoftDelete-модель: удалённые под-категории обязаны
    выпадать в «Без под-категории», а не тянуть за собой мёртвый ярлык.
    """
    label = func.coalesce(ProductSubcategory.name, _NO_SUBCATEGORY)
    query = (
        _grouped(label, where)
        .join(
            ProductSubcategoryMap,
            (ProductSubcategoryMap.nm_id == WbFbsOrder.nm_id)
            & (ProductSubcategoryMap.project_id == project_id),
            isouter=True,
        )
        .join(
            ProductSubcategory,
            (ProductSubcategory.id == ProductSubcategoryMap.subcategory_id)
            & (ProductSubcategory.is_deleted == False),  # noqa: E712 — SQLAlchemy expression
            isouter=True,
        )
    )
    return _rows(await db.execute(query))


async def _funnel_totals(
    db: AsyncSession, project_id: int, d_from: date, d_to: date, wb_warehouse_id: int | None
) -> dict[str, Any]:
    """Знаменатель доли FBS: объём заказов проекта по воронке WB.

    `wb_funnel_daily.date` — уже московские сутки (так отчитывается WB), поэтому
    границы календарные, без пересчёта зон.

    ГЛАВНОЕ здесь — считать долю за ОДИНАКОВЫЕ дни с обеих сторон. Воронка
    отстаёт: её наполняет свой синк, и последние дни периода в ней обычно ещё
    пустые. Дели мы полный период FBS на неполный период воронки — доля вылезала
    бы кратно завышенной и молча (локально: 30 дней заданий против 5 дней
    воронки давали «2 % по деньгам» вместо честных долей процента).
    Поэтому: находим реально покрытый воронкой отрезок внутри периода и
    пересчитываем цифры FBS РОВНО за него.
    """
    coverage = await db.execute(
        select(
            func.min(WbFunnelDaily.date),
            func.max(WbFunnelDaily.date),
            func.coalesce(func.sum(WbFunnelDaily.orders_count), 0),
            func.coalesce(func.sum(WbFunnelDaily.orders_sum_rub), 0),
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= d_from,
            WbFunnelDaily.date <= d_to,
        )
    )
    cov_from, cov_to, count, total = coverage.one()
    count = int(count or 0)
    # Пустая воронка — это «синк не проходил», а не «продаж не было». Доля,
    # посчитанная от нуля, показала бы «100 % FBS» на отсутствующих данных.
    if not count or cov_from is None or cov_to is None:
        return {
            "orders_count": 0,
            "orders_sum": Decimal("0"),
            "has_data": False,
            "covered_from": None,
            "covered_to": None,
            "full_period": False,
            "fbs_orders_count": 0,
            "fbs_orders_sum": Decimal("0"),
        }

    fbs = await _totals(db, _base_where(project_id, cov_from, cov_to, wb_warehouse_id))
    return {
        "orders_count": count,
        "orders_sum": Decimal(str(total or 0)),
        "has_data": True,
        "covered_from": cov_from,
        "covered_to": cov_to,
        "full_period": cov_from <= d_from and cov_to >= d_to,
        # Сопоставимые цифры: те же дни, тот же склад, что и у знаменателя.
        "fbs_orders_count": fbs["orders_count"],
        "fbs_orders_sum": fbs["orders_sum"],
    }


async def orders_stats(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: Any = None,
    date_to: Any = None,
    wb_warehouse_id: int | None = None,
) -> dict[str, Any]:
    """Сводка заказов FBS за период (`FbsOrderStatsOut`).

    Запросы идут последовательно и намеренно не распараллелены: одна сессия
    SQLAlchemy не потокобезопасна, а `asyncio.gather` по ней даёт
    `InterfaceError: another operation is in progress`.
    """
    d_from, d_to = resolve_period(date_from, date_to)
    where = _base_where(project_id, d_from, d_to, wb_warehouse_id)

    totals = await _totals(db, where)
    stats: dict[str, Any] = {
        "date_from": d_from,
        "date_to": d_to,
        "wb_warehouse_id": wb_warehouse_id,
        **totals,
        "by_day": await _by_day(db, where),
        "by_status": await _by_status(db, where),
        "by_brand": await _by_brand(db, where),
        "by_subject": await _by_subject(db, where),
        "by_subcategory": await _by_subcategory(db, project_id, where),
        # Знаменатель — весь проект: воронка не знает про склады продавца, и
        # фильтровать её по `wb_warehouse_id` нечем. При выбранном складе доля
        # показывает вклад ИМЕННО этого склада в общий объём — так и подписано.
        "funnel": await _funnel_totals(db, project_id, d_from, d_to, wb_warehouse_id),
    }
    logger.info(
        "FBS orders stats: project=%s %s..%s wh=%s orders=%s sum=%s",
        project_id,
        d_from,
        d_to,
        wb_warehouse_id or "все",
        stats["orders_count"],
        stats["orders_sum"],
    )
    return stats
