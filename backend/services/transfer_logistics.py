# ruff: noqa: RUF001, RUF002, RUF003
"""
Отчёт «Логистика переездов» — стоимость перевозки между НАШИМИ складами.

Источник — заборы переездов: `outbound_shipments` с непустым `stock_transfer_id`
(их создаёт `warehouse_outbound.send_transfer`), сджойненные с самим
перемещением. Сток эти заборы не двигают — они носители логистики и денег.

Почему отдельный отчёт, а не разрез логистической аналитики сборок
(`services/assembly/analytics.py`): та построена на INNER JOIN по
`AssemblyRequest` (переезды туда физически не проходят — у их заборов
`assembly_request_id IS NULL`), и её медианы/прогноз описывают маршруты НА
МАРКЕТПЛЕЙС. Переезд «наш склад → наш склад» — другой рынок и другая цена;
подмешав его, мы испортили бы и ₽/паллета, и прогнозную модель.

₽/паллета считается ТОЛЬКО по паллетным переездам (`shipped_as_boxes=False`).
`pallets_count` у перемещения — поле с ДВУМЯ единицами измерения (как у заявки
на сборку): при `shipped_as_boxes=True` там короба. Сложить их в одну сумму —
получить бессмысленное «₽ за штуко-паллето-короб», поэтому коробочный объём
живёт отдельным `total_boxes`, а в `total_pallets`/`cost_per_pallet` не входит.
"""

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.counterparty import Counterparty
from backend.models.payment_request import (
    PaymentRequest,
    PaymentRequestShipment,
    PaymentRequestStatus,
)
from backend.models.warehouse import (
    OutboundShipment,
    StockTransfer,
    StockTransferItem,
    Warehouse,
)
from backend.schemas.warehouse import (
    TransferLogisticsCarrier,
    TransferLogisticsPeriod,
    TransferLogisticsReport,
    TransferLogisticsRoute,
    TransferLogisticsRow,
    TransferLogisticsSummary,
)
from backend.services.assembly.analytics import _period_bucket

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
#: Потолок строк ДЕТАЛИЗАЦИИ — её читает человек, а не машина.
#: 🔴 На деньги НЕ распространяется: `summary` и разрезы считаются отдельным
#: агрегатным запросом БЕЗ лимита (`_aggregate_rows`). Раньше лимит стоял на
#: базовой выборке, из которой считалось всё, и с 1001-го забора отчёт молча
#: показывал бы деньги только по 1000 свежайшим — заниженная сумма в денежном
#: отчёте неотличима от «мало возили». Если строк больше лимита, об этом честно
#: говорит `summary.rows_truncated`.
_ROWS_LIMIT = 1000
#: Заявка на оплату считается «закрывающей деньги» в этих статусах. DRAFT/
#: PENDING_REVIEW — ещё не решение, REJECTED/CANCELLED — отказ.
_PAID_REQUEST_STATUSES = (
    PaymentRequestStatus.APPROVED.value,
    PaymentRequestStatus.DRAFT_CREATED.value,
    PaymentRequestStatus.PAID.value,
)


def _per_unit(cost: Decimal, units: int) -> Decimal | None:
    """₽ на единицу с округлением до копейки. Ноль единиц → None (не делим на ноль)."""
    if units <= 0:
        return None
    return (cost / Decimal(units)).quantize(Decimal("0.01"))


def _avg(cost: Decimal, count: int) -> Decimal | None:
    if count <= 0:
        return None
    return (cost / Decimal(count)).quantize(Decimal("0.01"))


def _base_filters(
    project_id: int,
    *,
    date_from: date | None,
    date_to: date | None,
    from_warehouse_id: int | None,
    to_warehouse_id: int | None,
    counterparty_id: int | None,
) -> list[ColumnElement[bool]]:
    """Условия выборки заборов переездов. Период — по `shipped_date` забора.

    Один источник истины для ОБОИХ запросов (агрегат и детализация): разъехавшись,
    они дали бы сводку и строки по разным множествам — худший вид расхождения,
    потому что каждая половина по отдельности выглядит правдоподобно.
    """
    filters: list[ColumnElement[bool]] = [
        OutboundShipment.project_id == project_id,
        OutboundShipment.is_deleted == False,  # noqa: E712
        OutboundShipment.stock_transfer_id.isnot(None),
        StockTransfer.project_id == project_id,
        StockTransfer.is_deleted == False,  # noqa: E712
    ]
    if date_from is not None:
        filters.append(OutboundShipment.shipped_date >= date_from)
    if date_to is not None:
        filters.append(OutboundShipment.shipped_date <= date_to)
    if from_warehouse_id is not None:
        filters.append(StockTransfer.from_warehouse_id == from_warehouse_id)
    if to_warehouse_id is not None:
        filters.append(StockTransfer.to_warehouse_id == to_warehouse_id)
    if counterparty_id is not None:
        filters.append(OutboundShipment.counterparty_id == counterparty_id)
    return filters


def _paid_condition(project_id: int) -> ColumnElement[bool]:
    """Забор считается оплаченным: авто-матч выписки ИЛИ заявка в решённом статусе.

    EXISTS, а не JOIN: забор может входить в несколько заявок на оплату, и join
    размножил бы строку — сумма `pickup_cost` посчиталась бы дважды.
    """
    return or_(
        OutboundShipment.matched_transaction_id.isnot(None),
        (
            select(1)
            .select_from(PaymentRequestShipment)
            .join(
                PaymentRequest,
                PaymentRequest.id == PaymentRequestShipment.payment_request_id,
            )
            .where(
                PaymentRequestShipment.project_id == project_id,
                PaymentRequestShipment.outbound_shipment_id == OutboundShipment.id,
                PaymentRequest.is_deleted == False,  # noqa: E712
                PaymentRequest.status.in_(_PAID_REQUEST_STATUSES),
            )
            .exists()
        ),
    )


def _aggregate_query(project_id: int, filters: list[ColumnElement[bool]]) -> Select:
    """Деньги и объёмы, сгруппированные по (маршрут, перевозчик, дата забора).

    БЕЗ лимита — это источник `summary` и всех разрезов. Группировка по датам, а
    не по периодам: бакет периода считает `_period_bucket` в Python, и повторять
    его логику в SQL (день / понедельник недели / месяц) значило бы завести
    второе определение периода, которое рано или поздно разъедется с первым.
    Число групп ограничено числом различных сочетаний, а не числом заборов.

    Штуки — коррелированным подзапросом, а НЕ join'ом на позиции: join размножил
    бы забор на число строк состава и завысил бы `total_cost` кратно.
    """
    cost = func.coalesce(OutboundShipment.pickup_cost, _ZERO)
    unit_count = func.coalesce(StockTransfer.pallets_count, 0)
    is_boxes = StockTransfer.shipped_as_boxes
    units = (
        select(func.coalesce(func.sum(StockTransferItem.quantity), 0))
        .where(
            StockTransferItem.project_id == project_id,
            StockTransferItem.transfer_id == StockTransfer.id,
        )
        .scalar_subquery()
    )
    return (
        select(
            StockTransfer.from_warehouse_id,
            StockTransfer.to_warehouse_id,
            OutboundShipment.counterparty_id,
            OutboundShipment.shipped_date,
            func.count().label("cnt"),
            func.coalesce(func.sum(cost), _ZERO).label("cost"),
            func.coalesce(func.sum(units), 0).label("units"),
            func.coalesce(
                func.sum(case((is_boxes, 0), else_=unit_count)), 0
            ).label("pallets"),
            func.coalesce(
                func.sum(case((is_boxes, unit_count), else_=0)), 0
            ).label("boxes"),
            # Деньги ПАЛЛЕТНЫХ переездов — знаменатель и числитель ₽/паллета
            # обязаны описывать одно множество, иначе коробочные задерут метрику.
            func.coalesce(
                func.sum(case((~is_boxes & (unit_count > 0), cost), else_=_ZERO)), _ZERO
            ).label("pallet_cost"),
            func.coalesce(
                func.sum(case((_paid_condition(project_id), cost), else_=_ZERO)), _ZERO
            ).label("paid_cost"),
        )
        .join(StockTransfer, StockTransfer.id == OutboundShipment.stock_transfer_id)
        .where(*filters)
        .group_by(
            StockTransfer.from_warehouse_id,
            StockTransfer.to_warehouse_id,
            OutboundShipment.counterparty_id,
            OutboundShipment.shipped_date,
        )
    )


def _detail_query(project_id: int, filters: list[ColumnElement[bool]]) -> Select:
    """Построчная детализация — ТОЛЬКО нужные колонки и ТОЛЬКО она под лимитом.

    Раньше здесь был `select(OutboundShipment, StockTransfer)` — фактический
    `SELECT *` по двум широким таблицам (у `outbound_shipments` 30+ колонок) ради
    семи полей, плюс до 2000 ORM-объектов в identity map на каждый рендер.
    """
    return (
        select(
            # Метки обязательны: `id`/`number` есть у обеих таблиц, без них
            # доступ по имени неоднозначен, а по индексу — нечитаем и ломается
            # от любой вставки колонки в середину.
            OutboundShipment.id.label("shipment_id"),
            OutboundShipment.number.label("shipment_number"),
            OutboundShipment.shipped_date.label("shipped_date"),
            OutboundShipment.vehicle_info.label("vehicle_info"),
            OutboundShipment.counterparty_id.label("counterparty_id"),
            OutboundShipment.pickup_cost.label("pickup_cost"),
            OutboundShipment.matched_transaction_id.label("matched_transaction_id"),
            StockTransfer.id.label("transfer_id"),
            StockTransfer.number.label("transfer_number"),
            StockTransfer.status.label("transfer_status"),
            StockTransfer.from_warehouse_id.label("from_warehouse_id"),
            StockTransfer.to_warehouse_id.label("to_warehouse_id"),
            StockTransfer.pallets_count.label("pallets_count"),
            StockTransfer.shipped_as_boxes.label("shipped_as_boxes"),
        )
        .join(StockTransfer, StockTransfer.id == OutboundShipment.stock_transfer_id)
        .where(*filters)
        .order_by(
            OutboundShipment.shipped_date.desc().nullslast(), OutboundShipment.id.desc()
        )
        .limit(_ROWS_LIMIT)
    )


async def get_transfer_logistics_report(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    group_by: str = "month",
    from_warehouse_id: int | None = None,
    to_warehouse_id: int | None = None,
    counterparty_id: int | None = None,
    summary_only: bool = False,
) -> TransferLogisticsReport:
    """Сводка + разрезы (маршрут / перевозчик / период) + детализация по заборам.

    Оплаченность: у забора есть `matched_transaction_id` (авто-матч выписки) ИЛИ
    он входит в заявку на оплату в решённом статусе (APPROVED/DRAFT_CREATED/PAID).
    Черновик заявки и отказ оплатой не считаются.

    🔴 Деньги (`summary`, `by_route`, `by_carrier`, `by_period`) считает АГРЕГАТНЫЙ
    запрос без лимита — они всегда полные. Лимит (`_ROWS_LIMIT`) стоит только на
    построчной детализации; если строк больше, `summary.rows_truncated=True`.

    Запросов — шесть независимо от объёма: агрегат, имена складов, перевозчики,
    детализация, состав пачкой, оплаты пачкой. N+1 нет ни одного.
    При `summary_only=True` — три (последние три не выполняются).
    """
    filters = _base_filters(
        project_id,
        date_from=date_from,
        date_to=date_to,
        from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id,
        counterparty_id=counterparty_id,
    )

    agg_rows = (await db.execute(_aggregate_query(project_id, filters))).all()
    if not agg_rows:
        return TransferLogisticsReport(
            summary=TransferLogisticsSummary(
                transfers_count=0,
                total_cost=_ZERO,
                avg_cost=None,
                total_units=0,
                cost_per_unit=None,
                paid_cost=_ZERO,
                unpaid_cost=_ZERO,
                total_pallets=0,
                cost_per_pallet=None,
                total_boxes=0,
                rows_truncated=False,
            )
        )

    # Имена складов и перевозчиков берём из АГРЕГАТА, а не из детализации:
    # разрезы строятся по полному множеству, и склад/перевозчик, не попавший в
    # усечённую 1000 строк, иначе остался бы в отчёте как «#12».
    wh_ids = {r.from_warehouse_id for r in agg_rows} | {r.to_warehouse_id for r in agg_rows}
    wh_names = {
        int(wid): name
        for wid, name in (
            await db.execute(
                select(Warehouse.id, Warehouse.name).where(
                    Warehouse.project_id == project_id,
                    Warehouse.id.in_(wh_ids),
                )
            )
        ).all()
    }

    # Перевозчики — одной пачкой.
    cp_ids = {r.counterparty_id for r in agg_rows if r.counterparty_id}
    cp_names: dict[int, str] = {}
    if cp_ids:
        cp_names = {
            int(cid): name
            for cid, name in (
                await db.execute(
                    select(Counterparty.id, Counterparty.name).where(
                        Counterparty.project_id == project_id,
                        Counterparty.id.in_(cp_ids),
                    )
                )
            ).all()
        }

    def _new_acc() -> dict:
        # pallet_cost — доля стоимости, относящаяся к ПАЛЛЕТНЫМ переездам:
        # ₽/паллета обязана делить сопоставимые деньги на сопоставимые паллеты,
        # иначе коробочные переезды задерут числитель без вклада в знаменатель.
        return {"count": 0, "cost": _ZERO, "units": 0, "pallets": 0, "pallet_cost": _ZERO}

    by_route: dict[tuple[int, int], dict] = defaultdict(_new_acc)
    by_carrier: dict[int | None, dict] = defaultdict(_new_acc)
    by_period: dict[str, dict] = defaultdict(_new_acc)

    total_count = 0
    total_cost = _ZERO
    total_units = 0
    paid_cost = _ZERO
    total_pallets = 0
    total_boxes = 0
    pallet_cost = _ZERO

    # Свод по АГРЕГАТУ (полное множество, без лимита). Бакет периода считает
    # `_period_bucket` здесь, в Python — единственное определение периода в коде.
    for r in agg_rows:
        total_count += r.cnt
        total_cost += r.cost
        total_units += r.units
        total_pallets += r.pallets
        total_boxes += r.boxes
        pallet_cost += r.pallet_cost
        paid_cost += r.paid_cost

        route = by_route[(r.from_warehouse_id, r.to_warehouse_id)]
        carrier = by_carrier[r.counterparty_id]
        # Забор без даты отгрузки в динамику не попадает (бакет «—» смешал бы
        # разные месяцы в одну точку графика), но в маршрут и перевозчика — да.
        period = by_period[_period_bucket(r.shipped_date, group_by)] if r.shipped_date else None
        for acc in (route, carrier, period):
            if acc is None:
                continue
            acc["count"] += r.cnt
            acc["cost"] += r.cost
            acc["units"] += r.units
            acc["pallets"] += r.pallets
            acc["pallet_cost"] += r.pallet_cost

    unpaid_cost = total_cost - paid_cost

    # Детализация — отдельным проходом по усечённой выборке. `summary_only`
    # обрывает здесь: вкладке «Перемещения» нужны четыре числа из сводки, а не
    # тысяча строк с составом, перевозчиками и заявками на оплату.
    rows: list[TransferLogisticsRow] = []
    pairs: list = []
    units_by_transfer: dict[int, int] = {}
    skus_by_transfer: dict[int, int] = {}
    request_by_shipment: dict[int, tuple[str, str]] = {}
    if not summary_only:
        pairs = list((await db.execute(_detail_query(project_id, filters))).all())
        transfer_ids = [r.transfer_id for r in pairs]
        shipment_ids = [r.shipment_id for r in pairs]

        # Состав перемещений — одной пачкой (штуки и число SKU на переезд).
        comp_rows = (
            await db.execute(
                select(
                    StockTransferItem.transfer_id,
                    func.coalesce(func.sum(StockTransferItem.quantity), 0),
                    func.count(func.distinct(StockTransferItem.nomenclature_id)),
                )
                .where(
                    StockTransferItem.project_id == project_id,
                    StockTransferItem.transfer_id.in_(transfer_ids),
                )
                .group_by(StockTransferItem.transfer_id)
            )
        ).all()
        units_by_transfer = {int(tid): int(qty or 0) for tid, qty, _ in comp_rows}
        skus_by_transfer = {int(tid): int(cnt or 0) for tid, _, cnt in comp_rows}

        # Заявки на оплату по этим заборам — одной пачкой (N заборов → 1 заявка).
        pay_rows = (
            await db.execute(
                select(
                    PaymentRequestShipment.outbound_shipment_id,
                    PaymentRequest.number,
                    PaymentRequest.status,
                )
                .join(
                    PaymentRequest,
                    PaymentRequest.id == PaymentRequestShipment.payment_request_id,
                )
                .where(
                    PaymentRequestShipment.project_id == project_id,
                    PaymentRequestShipment.outbound_shipment_id.in_(shipment_ids),
                    PaymentRequest.is_deleted == False,  # noqa: E712
                )
                .order_by(PaymentRequest.id.asc())  # asc → в карте останется самая свежая
            )
        ).all()
        request_by_shipment = {int(sid): (number, status) for sid, number, status in pay_rows}

    for shipment in pairs:
        transfer_id = shipment.transfer_id
        units = units_by_transfer.get(transfer_id, 0)
        pay = request_by_shipment.get(shipment.shipment_id)
        is_paid = shipment.matched_transaction_id is not None or (
            pay is not None and pay[1] in _PAID_REQUEST_STATUSES
        )
        as_boxes = bool(shipment.shipped_as_boxes)

        rows.append(
            TransferLogisticsRow(
                transfer_id=transfer_id,
                transfer_number=shipment.transfer_number,
                shipment_id=shipment.shipment_id,
                shipment_number=shipment.shipment_number,
                shipped_date=shipment.shipped_date,
                from_warehouse=wh_names.get(
                    shipment.from_warehouse_id, f"#{shipment.from_warehouse_id}"
                ),
                to_warehouse=wh_names.get(
                    shipment.to_warehouse_id, f"#{shipment.to_warehouse_id}"
                ),
                vehicle_info=shipment.vehicle_info,
                counterparty_name=(
                    cp_names.get(shipment.counterparty_id) if shipment.counterparty_id else None
                ),
                pickup_cost=shipment.pickup_cost,
                units_total=units,
                sku_count=skus_by_transfer.get(transfer_id, 0),
                transfer_status=shipment.transfer_status,
                payment_request_number=pay[0] if pay else None,
                is_paid=is_paid,
                pallets_count=shipment.pallets_count,
                shipped_as_boxes=as_boxes,
            )
        )

    return TransferLogisticsReport(
        summary=TransferLogisticsSummary(
            transfers_count=total_count,
            total_cost=total_cost,
            avg_cost=_avg(total_cost, total_count),
            total_units=total_units,
            cost_per_unit=_per_unit(total_cost, total_units),
            paid_cost=paid_cost,
            unpaid_cost=unpaid_cost,
            total_pallets=total_pallets,
            cost_per_pallet=_per_unit(pallet_cost, total_pallets),
            total_boxes=total_boxes,
            # Строк больше потолка — сводка полная, детализация нет. UI обязан
            # сказать это вслух, иначе «показано 1000» читается как «всего 1000».
            # При summary_only детализации нет ПО ЗАПРОСУ, а не по усечению —
            # флаг остаётся False, чтобы не показать ложную тревогу.
            rows_truncated=(
                not summary_only and len(rows) >= _ROWS_LIMIT and total_count > len(rows)
            ),
        ),
        by_route=[
            TransferLogisticsRoute(
                from_warehouse_id=src,
                from_warehouse=wh_names.get(src, f"#{src}"),
                to_warehouse_id=dst,
                to_warehouse=wh_names.get(dst, f"#{dst}"),
                transfers_count=acc["count"],
                total_cost=acc["cost"],
                avg_cost=_avg(acc["cost"], acc["count"]),
                total_units=acc["units"],
                cost_per_unit=_per_unit(acc["cost"], acc["units"]),
                total_pallets=acc["pallets"],
                cost_per_pallet=_per_unit(acc["pallet_cost"], acc["pallets"]),
            )
            for (src, dst), acc in sorted(by_route.items(), key=lambda kv: -kv[1]["cost"])
        ],
        by_carrier=[
            TransferLogisticsCarrier(
                counterparty_id=cid,
                counterparty_name=cp_names.get(cid) if cid else None,
                transfers_count=acc["count"],
                total_cost=acc["cost"],
                avg_cost=_avg(acc["cost"], acc["count"]),
            )
            for cid, acc in sorted(by_carrier.items(), key=lambda kv: -kv[1]["cost"])
        ],
        by_period=[
            TransferLogisticsPeriod(
                period=key,
                transfers_count=acc["count"],
                total_cost=acc["cost"],
                total_units=acc["units"],
                cost_per_unit=_per_unit(acc["cost"], acc["units"]),
                total_pallets=acc["pallets"],
                cost_per_pallet=_per_unit(acc["pallet_cost"], acc["pallets"]),
            )
            for key, acc in sorted(by_period.items())
        ],
        rows=rows,
    )
