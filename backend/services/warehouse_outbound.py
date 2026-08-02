"""
Warehouse outbound — shipments and stock transfers.
"""

import asyncio
import logging
from datetime import date

from sqlalchemy import and_, func, inspect as sa_inspect, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload

from backend.cache import invalidate_cache
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.counterparty import Counterparty
from backend.models.fulfillment import FfRequestKind, FulfillmentRequest
from backend.models.gazelka import GazelkaOrder, GazelkaOrderStatus
from backend.models.warehouse import (
    TRANSFER_EDITABLE_STATUSES,
    TRANSFER_TRANSITIONS,
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    StockMovement,
    StockTransfer,
    StockTransferItem,
    StockTransferStatusHistory,
    TransferStatus,
    Warehouse,
    WarehouseStock,
    WarehouseType,
)
from backend.schemas.warehouse import (
    AssemblyToTransfer,
    AssemblyToTransferResult,
    TransferAssignVehicle,
)
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcodes_batch,
    _update_stock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

#: Терминальные статусы заявки: резерв не держат, отгрузить нельзя. Конвертация
#: их НЕ трогает — история заявки должна остаться честной. Все прочие статусы
#: при конвертации отменяются (иначе фантомный резерв + двойная отгрузка).
_TERMINAL_ASSEMBLY_STATUSES = frozenset(
    {AssemblyStatus.CLOSED, AssemblyStatus.RETURNED, AssemblyStatus.CANCELLED}
)

#: Статусы заявки, из которых ФФ уже собрал груз: конвертация в переезд обязана
#: унаследовать готовность, а не гонять человека по ступеням заново (кейс юзера:
#: ASM-726/ASM-727 в READY). Всё остальное (PENDING/IN_PROGRESS/PRE_DISTRIBUTED)
#: → переезд рождается в PENDING.
_ASSEMBLY_READY_FOR_TRANSFER = frozenset(
    {
        AssemblyStatus.READY,
        AssemblyStatus.VEHICLE_ASSIGNED,
        AssemblyStatus.SHIPPED,
        AssemblyStatus.DELIVERED,
        AssemblyStatus.RETURNED,
        AssemblyStatus.CLOSED,
    }
)

#: Статусы ДО отгрузки: сток ещё не двигался ни на одном складе. Отсюда переезд
#: можно отменить (`cancel_transfer`) и здесь же он живёт в срезе Листа логиста.
TRANSFER_PRE_SHIP_STATUSES = frozenset(
    {
        TransferStatus.PENDING,
        TransferStatus.IN_PROGRESS,
        TransferStatus.READY,
        TransferStatus.VEHICLE_ASSIGNED,
    }
)


def _check_transfer_transition(current: TransferStatus, target: TransferStatus) -> None:
    """Валидация перехода переезда по `TRANSFER_TRANSITIONS` — зеркало
    `assembly.status._check_transition`.

    Единая точка вместо россыпи `if status != ...`: таблица переходов живёт в
    модели, а сервисы только спрашивают её. Текст ошибки одинаков у всех
    ступеней, поэтому роутер маппит его одним `_transfer_error`.
    """
    allowed = TRANSFER_TRANSITIONS.get(current, set())
    if target not in allowed:
        names = ", ".join(sorted(s.value for s in allowed)) or "нет переходов"
        raise ValueError(
            f"Переезд: переход {current.value} → {target.value} запрещён. Разрешено: {names}"
        )


def _log_transfer_status_change(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    old_status: str | None,
    new_status: str,
    changed_by: str = "user",
    comment: str | None = None,
) -> None:
    """Запись перехода в историю статусов переезда — зеркало `_log_status_change`.

    Без commit: строка ложится в ТУ ЖЕ транзакцию, что и сам переход, иначе
    между «статус сменился» и «история записалась» было бы окно, в котором
    падение процесса оставило бы переход без автора.

    `changed_by`: `user` — кнопка, `ff_sync` — сигнал провайдера, `system` —
    авто-переход (конвертация заявки).
    """
    db.add(
        StockTransferStatusHistory(
            project_id=project_id,
            stock_transfer_id=transfer_id,
            old_status=old_status,
            new_status=new_status,
            changed_at=utcnow(),
            changed_by=changed_by,
            comment=comment,
        )
    )


# ─── Outbound Shipments (Отгрузка) ────────────────────────────────────────


async def list_shipments(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    include_defect: bool = False,
) -> list:
    """List shipments for a warehouse. By default excludes defect writeoff shipments.

    Заборы ПЕРЕЕЗДОВ (`stock_transfer_id IS NOT NULL`) в этот список не входят:
    это не отгрузка со склада, а логистический носитель перемещения (сток он не
    двигает). Вкладка «Отгрузки» карточки склада — вход в карточку с кнопками
    «Отменить/Доставлено», а над забором переезда они запрещены; плюс счётчик
    отгрузок склада не должен раздуваться переездами.
    """
    query = (
        select(OutboundShipment)
        .options(selectinload(OutboundShipment.items))
        .where(
            OutboundShipment.project_id == project_id,
            OutboundShipment.warehouse_id == warehouse_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
            OutboundShipment.stock_transfer_id.is_(None),
        )
    )
    if not include_defect:
        query = query.where(OutboundShipment.is_defect.is_(False))
    query = query.order_by(OutboundShipment.id.desc()).limit(500)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment | None:
    """Get shipment with items."""
    result = await db.execute(
        select(OutboundShipment).where(
            OutboundShipment.id == shipment_id,
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
    )
    shipment = result.scalar_one_or_none()
    if shipment:
        await db.refresh(shipment, ["items"])
    return shipment


async def create_shipment(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> OutboundShipment:
    """Create outbound shipment. Only from FULFILLMENT warehouses."""
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")
    if wh.warehouse_type != WarehouseType.FULFILLMENT:
        raise ValueError("Shipments can only be created from FULFILLMENT warehouses")

    number = await _next_number(db, project_id, "OUT", OutboundShipment)

    shipment = OutboundShipment(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=OutboundStatus.DRAFT,
        destination=payload.get("destination"),
        comment=payload.get("comment"),
    )
    db.add(shipment)
    await db.flush()

    items_data = payload.get("items", [])
    barcode_map = await _resolve_barcodes_batch(db, project_id, [d["barcode"] for d in items_data])
    for item_data in items_data:
        nom = barcode_map[item_data["barcode"]]
        item = OutboundShipmentItem(
            project_id=project_id,
            shipment_id=shipment.id,
            nomenclature_id=nom.id,
            barcode=item_data["barcode"],
            quantity=item_data["quantity"],
        )
        db.add(item)

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def ship_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """
    Ship: DRAFT → SHIPPED.
    Checks stock >= qty, then stock -= qty for each item.
    """
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.DRAFT:
        raise ValueError(f"Cannot ship in status {shipment.status}")

    # Забор переезда создаётся сразу SHIPPED и стока не двигает — сюда попасть
    # не должен (гард на случай ручной правки статуса в БД).
    if shipment.stock_transfer_id is not None:
        raise ValueError("Забор переезда отгружается вместе с перемещением, а не здесь")

    if not shipment.items:
        raise ValueError("Cannot ship with no items")

    for item in shipment.items:
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=shipment.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=-item.quantity,
            movement_type=MovementType.OUTBOUND,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
        )

    shipment.status = OutboundStatus.SHIPPED
    shipment.shipped_date = date.today()

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def deliver_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """Mark shipment as delivered: SHIPPED → DELIVERED."""
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.SHIPPED:
        raise ValueError(f"Cannot deliver in status {shipment.status}")

    # Доставку переезда фиксирует приёмка перемещения (complete_transfer /
    # receive_transfer_fact) — она же приходует сток. Отдельный «Доставлено» у
    # забора развёл бы два источника истины о статусе одной перевозки.
    if shipment.stock_transfer_id is not None:
        raise ValueError("Доставка переезда отмечается приёмкой перемещения, а не здесь")

    shipment.status = OutboundStatus.DELIVERED

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def cancel_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """
    Cancel shipped shipment: SHIPPED → CANCELLED.
    Returns stock for each item.
    """
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.SHIPPED:
        raise ValueError(f"Can only cancel SHIPPED shipments, got {shipment.status}")

    # 🔴 Забор ПЕРЕЕЗДА стока не двигал (списание за самим перемещением,
    # TRANSFER_OUT/reference_type='TRANSFER') — «отмена» вернула бы +qty из
    # ниоткуда, то есть фантомный остаток. Переезд отменяется/принимается
    # своими ручками перемещения, а не через отгрузку.
    if shipment.stock_transfer_id is not None:
        raise ValueError(
            "Это забор переезда — сток он не списывал, отменять здесь нечего. "
            "Работайте с самим перемещением."
        )

    # For defect writeoff shipments — return defect_quantity, not good stock.
    for item in shipment.items:
        if shipment.is_defect:
            delta = 0
            defect_delta = item.quantity
        else:
            delta = item.quantity
            defect_delta = 0
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=shipment.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=delta,
            defect_delta=defect_delta,
            movement_type=MovementType.OUTBOUND_CANCEL,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
            comment=f"Cancel shipment {shipment.number}",
        )

    shipment.status = OutboundStatus.CANCELLED

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


# ─── Stock Transfers (Перемещение) ─────────────────────────────────────────


async def _attach_transfer_labels(
    db: AsyncSession,
    project_id: int,
    transfers: list,
    *,
    with_totals: bool = False,
    with_ff_links: bool = False,
) -> None:
    """Проставить читаемые подписи на объекты перемещений: имена складов
    обоих концов маршрута, имя перевозчика и (опционально) итоги состава.

    Не relationship и не N+1: по одной выборке на справочник, дальше присвоение
    немапленых атрибутов (их читает `StockTransferSchema` через from_attributes).
    Без этого карточка переезда показывала «Контрагент #12», а строка списка —
    «TR-21» без «откуда → куда»; догружать справочники с фронта ради двух
    подписей было лишним раундтрипом.

    `with_totals=True` — для СПИСКА: считает `units_total`/`sku_count` одним
    GROUP BY вместо загрузки состава. Полный состав в списке никому не нужен, а
    весит он много: 500 переездов × ~53 позиции ≈ 3.4 МБ и 23 500
    pydantic-моделей на каждый заход.

    `with_ff_links=True` — связанные заявки ФФ обеих сторон. Раньше только для
    карточки; теперь и для списка (строка переезда стоит в одном рабочем списке
    с заявками и рисует бейдж «ФФ: PVB-…»), поэтому выборка БАТЧЕВАЯ: поштучный
    вызов дал бы N+1 ровно в том месте, ради которого этот хелпер написан.

    `received_units` считается ВСЕГДА (и в списке, и в карточке) — «принято X
    из Y» у SHIPPED-переезда: приём бывает порционным, и без этой цифры
    «доехало всё» не отличить от «доехала половина». Это один GROUP BY по
    журналу движений на всю пачку, а не N запросов.

    `via_gazelka` и забор (`pickup_shipment_id` / `_number`) — тоже ВСЕГДА и тоже
    по одной выборке на пачку. Первый гасит кнопку «Назначить машину» (её ведёт
    агрегатор), второй ведёт из карточки переезда в «Оплаты»: без него строка
    молчала бы о том, есть ли у перевозки денежный документ вообще.
    """
    if not transfers:
        return
    wh_ids = {t.from_warehouse_id for t in transfers} | {t.to_warehouse_id for t in transfers}
    names = {
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
    cp_ids = {t.counterparty_id for t in transfers if t.counterparty_id}
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

    totals: dict[int, tuple[int, int]] = {}
    if with_totals:
        totals = {
            int(tid): (int(qty or 0), int(skus or 0))
            for tid, qty, skus in (
                await db.execute(
                    select(
                        StockTransferItem.transfer_id,
                        func.coalesce(func.sum(StockTransferItem.quantity), 0),
                        func.count(func.distinct(StockTransferItem.nomenclature_id)),
                    )
                    .where(
                        StockTransferItem.project_id == project_id,
                        StockTransferItem.transfer_id.in_([t.id for t in transfers]),
                    )
                    .group_by(StockTransferItem.transfer_id)
                )
            ).all()
        }

    # Принято получателем — по журналу движений, одним GROUP BY на пачку.
    # Фильтра по складу нет намеренно: пары (reference_type, movement_type) ниже
    # однозначно принадлежат складу-получателю, а reference_id адресует переезд.
    received: dict[int, int] = (
        {
            int(ref_id): int(qty or 0)
            for ref_id, qty in (
                await db.execute(
                    select(
                        StockMovement.reference_id,
                        func.coalesce(
                            func.sum(StockMovement.quantity + StockMovement.defect_delta), 0
                        ),
                    )
                    .where(
                        StockMovement.project_id == project_id,
                        StockMovement.reference_id.in_([t.id for t in transfers]),
                        # НЕТТО, как в `_transfer_received_map`: возврат откатывает
                        # приход, и без его вычета бейдж «принято X из Y» после
                        # переотправки показывал бы прошлый круг — «доехало всё» на
                        # переезде, который едет заново.
                        # Пара (reference_type, movement_type) здесь ОБЯЗАНА быть
                        # явной: выборка идёт пачкой по многим переездам и фильтра
                        # по складу не имеет, а `TRANSFER` + `TRANSFER_OUT` — это
                        # списание на складе-ИСТОЧНИКЕ (`send_transfer`). Возьми
                        # типы плоским списком — и оно вычлось бы из принятого,
                        # уведя прогресс в минус на всю отгрузку.
                        or_(
                            and_(
                                StockMovement.reference_type == "TRANSFER",
                                StockMovement.movement_type.in_(
                                    [
                                        MovementType.TRANSFER_IN.value,
                                        MovementType.DEFECT_TRANSFER_IN.value,
                                    ]
                                ),
                            ),
                            and_(
                                StockMovement.reference_type == "TRANSFER_RETURN",
                                StockMovement.movement_type.in_(
                                    [
                                        MovementType.TRANSFER_OUT.value,
                                        MovementType.DEFECT_TRANSFER_OUT.value,
                                    ]
                                ),
                            ),
                        ),
                    )
                    .group_by(StockMovement.reference_id)
                )
            ).all()
        }
    )

    links_map: dict[int, list[dict]] = {}
    if with_ff_links:
        links_map = await _transfer_ff_links_batch(db, project_id, list(transfers))

    # Переезды под Газелькой — одним IN на всю пачку (поштучный вопрос дал бы
    # ровно тот N+1, ради отсутствия которого написан весь этот хелпер).
    gazelka_ids = await _gazelka_led_transfer_ids(db, project_id, [t.id for t in transfers])

    # Забор переезда: id + номер. Заборов у переезда столько, сколько было
    # ПОПЫТОК отправки (переотправка после возврата заводит свой документ),
    # поэтому сортируем по `attempt_no` и оставляем последний: карточка ведёт на
    # актуальный рейс, а не на случайный из истории. Порядок задан явно — без
    # ORDER BY «победитель» плоского dict зависел бы от плана запроса.
    pickups: dict[int, tuple[int, str]] = {
        int(tid): (int(sid), number)
        for sid, number, tid in (
            await db.execute(
                select(
                    OutboundShipment.id,
                    OutboundShipment.number,
                    OutboundShipment.stock_transfer_id,
                )
                .where(
                    OutboundShipment.project_id == project_id,
                    OutboundShipment.stock_transfer_id.in_([t.id for t in transfers]),
                    OutboundShipment.is_deleted == False,  # noqa: E712
                )
                .order_by(OutboundShipment.attempt_no.asc(), OutboundShipment.id.asc())
            )
        ).all()
    }

    for t in transfers:
        t.from_warehouse_name = names.get(t.from_warehouse_id)
        t.to_warehouse_name = names.get(t.to_warehouse_id)
        t.counterparty_name = cp_names.get(t.counterparty_id) if t.counterparty_id else None
        t.received_units = received.get(t.id, 0)
        t.via_gazelka = t.id in gazelka_ids
        pickup = pickups.get(t.id)
        t.pickup_shipment_id = pickup[0] if pickup else None
        t.pickup_shipment_number = pickup[1] if pickup else None
        # Без `with_ff_links` атрибут НЕ ставим вовсе — даже пустым списком.
        # `from_attributes` возьмёт дефолт схемы (`[]`), а безусловное `= []`
        # было бы хуже отсутствия: в одной сессии (тесты, батч-задачи) список
        # затирал бы связки, уже собранные карточкой того же переезда.
        if with_ff_links:
            t.ff_links = links_map.get(t.id, [])
        if with_totals:
            units, skus = totals.get(t.id, (0, 0))
        else:
            # Деталка: состав уже загружен — считаем по нему, лишнего запроса не
            # нужно. Безусловное обнуление здесь показывало «0 SKU / 0 шт» в
            # шапке карточки под полной таблицей состава.
            # `unloaded` — защита от ленивой подгрузки в async-сессии (взорвалась
            # бы MissingGreenlet на путях, где состав не грузили).
            items = [] if "items" in sa_inspect(t).unloaded else list(t.items or [])
            units = sum(int(i.quantity or 0) for i in items)
            skus = len({i.nomenclature_id for i in items})
        t.units_total = units
        t.sku_count = skus


async def list_transfers(
    db: AsyncSession,
    project_id: int,
    in_transit_only: bool = False,
    warehouse_id: int | None = None,
    *,
    status: str | None = None,
    status_in: list[str] | None = None,
    has_vehicle: bool | None = None,
    has_pickup_cost: bool | None = None,
    converted_from_assembly_id: int | None = None,
) -> list:
    """List transfers. Optionally filter only SHIPPED (в пути) and/or by warehouse (source OR destination).

    `in_transit_only` — «едет прямо сейчас», то есть SHIPPED: сток уже списан с
    источника и висит транзитом на получателе. DELIVERED/RETURNED сюда не
    попадают — груз доехал.

    `status` — точный статус (Лист логиста берёт срез READY: переезды, которые
    ещё можно посадить на машину). `status_in` — несколько статусов ОДНИМ
    запросом: срез «уехало, а денег нет» смотрит сразу SHIPPED+DELIVERED+CLOSED,
    и поштучный `status` стоил бы трёх round-trip'ов. Комбинируются как И —
    задавать оба разом бессмысленно, но и не запрещено.

    `has_vehicle` — назначена ли машина; признак назначения — `vehicle_assigned_at`
    (статус VEHICLE_ASSIGNED говорит о том же, но у конвертированных из заявки
    переездов машина может быть унаследована раньше ступени).

    `has_pickup_cost` — есть ли ДЕНЬГИ на перевозке. `False` ловит ровно тот
    случай, ради которого написан ретро-ввод логистики: переезд уехал, а в
    «Оплатах» его нет. Ноль трактуем как «нет суммы»: `pickup_cost = 0` в этом
    срезе означает не «везли бесплатно», а «поле не заполнили», и прятать такие
    строки от логиста нельзя.

    `converted_from_assembly_id` — переезды, сделанные из конкретной заявки
    (карточка заявки показывает «уже переделана в TR-хх», не ловя 400). Фильтры
    опциональны и комбинируются.

    🔴 СОСТАВ (`items`) В СПИСКЕ НЕ ОТДАЁТСЯ — `noload` вместо `selectinload`, а
    вместо него `units_total`/`sku_count` одним GROUP BY. Потребителям списка
    нужны только эти два числа, а полный состав на потолке в 500 переездов — это
    ~3.4 МБ и 23 500 pydantic-моделей на запрос. За составом — в `get_transfer`.
    """
    query = (
        select(StockTransfer)
        # noload, а не отсутствие опции: без неё pydantic дёрнет `.items` при
        # сериализации и упадёт lazy-load'ом в async-контексте (MissingGreenlet).
        # Оговорка: noload действует на объекты, которых ещё нет в identity map
        # сессии. В проде это всегда так (сессия своя на HTTP-запрос), но в
        # тестах с общей сессией уже загруженный переезд вернётся С составом —
        # это артефакт переиспользования сессии, а не поведение эндпоинта.
        .options(noload(StockTransfer.items))
        .where(
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    if in_transit_only:
        query = query.where(StockTransfer.status == TransferStatus.SHIPPED)
    if status is not None:
        query = query.where(StockTransfer.status == status)
    if status_in:
        query = query.where(StockTransfer.status.in_(status_in))
    if has_vehicle is True:
        query = query.where(StockTransfer.vehicle_assigned_at.isnot(None))
    elif has_vehicle is False:
        query = query.where(StockTransfer.vehicle_assigned_at.is_(None))
    if has_pickup_cost is True:
        query = query.where(
            StockTransfer.pickup_cost.isnot(None), StockTransfer.pickup_cost > 0
        )
    elif has_pickup_cost is False:
        query = query.where(
            or_(StockTransfer.pickup_cost.is_(None), StockTransfer.pickup_cost == 0)
        )
    if converted_from_assembly_id is not None:
        query = query.where(
            StockTransfer.converted_from_assembly_id == converted_from_assembly_id
        )
    if warehouse_id is not None:
        query = query.where(
            or_(
                StockTransfer.from_warehouse_id == warehouse_id,
                StockTransfer.to_warehouse_id == warehouse_id,
            )
        )
    query = query.order_by(StockTransfer.id.desc()).limit(500)
    result = await db.execute(query)
    rows = list(result.scalars().all())
    # ff_links и в СПИСКЕ: переезды стоят в одном рабочем списке с заявками на
    # сборку, и строка обязана нести бейдж «ФФ: PVB-…» без догрузки. Это ОДНА
    # дополнительная выборка на весь список (батч), а не N — см.
    # `list_transfer_ff_links_batch`.
    await _attach_transfer_labels(db, project_id, rows, with_totals=True, with_ff_links=True)
    return rows


async def get_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer | None:
    """Карточка перемещения — С полным составом (в отличие от списка).

    Состав грузим ЯВНЫМ selectinload + populate_existing, а не `refresh` после
    выборки: список рядом просит `noload`, и объект мог остаться в identity map
    сессии с пустой коллекцией — `refresh` её в этом случае не восстанавливает,
    и карточка молча показала бы переезд без позиций.
    """
    result = await db.execute(
        select(StockTransfer)
        .options(selectinload(StockTransfer.items))
        .execution_options(populate_existing=True)
        .where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    transfer = result.scalar_one_or_none()
    if transfer:
        await _attach_transfer_labels(db, project_id, [transfer], with_ff_links=True)
    return transfer


async def _transfer_ff_links_batch(
    db: AsyncSession, project_id: int, transfers: list
) -> dict[int, list[dict]]:
    """Связки ФФ ПАЧКИ переездов (обе стороны) одной выборкой — см. fulfillment_service.

    Импорт локальный: `fulfillment_service` тянет `assembly.crud` → фасад
    `warehouse_service` → этот модуль, то есть на уровне модуля это цикл (та же
    причина, что у локального импорта резолверов перевозчика ниже).
    """
    from backend.services.fulfillment_service import list_transfer_ff_links_batch

    return await list_transfer_ff_links_batch(db, project_id, transfers)


async def get_transfer_ff_candidates(
    db: AsyncSession, project_id: int, transfer_id: int, side: str
) -> list[dict]:
    """Заявки ФФ, которые можно привязать к переезду с указанной стороны.

    Связка ФФ ↔ переезд существует с обеих сторон (см. `link_request`), но до
    сих пор заводилась только «от заявки ФФ»: пользователь открывал заявку и
    искал наш документ. С карточки переезда обратного пути не было — фронт
    вместо этого тянул ДВА полных списка заявок ФФ по обоим складам и искал
    связку в них.

    ValueError — переезда нет или сторона неизвестна.
    """
    from backend.services.fulfillment_service import list_transfer_ff_candidates

    result = await db.execute(
        select(StockTransfer)
        .options(noload(StockTransfer.items))
        .where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    transfer = result.scalar_one_or_none()
    if not transfer:
        raise ValueError("Перемещение не найдено")
    return await list_transfer_ff_candidates(db, project_id, transfer, side)


async def create_transfer(db: AsyncSession, project_id: int, payload: dict) -> StockTransfer:
    """Create stock transfer between two warehouses."""
    from_wh = await get_warehouse(db, project_id, payload["from_warehouse_id"])
    to_wh = await get_warehouse(db, project_id, payload["to_warehouse_id"])
    if not from_wh:
        raise ValueError("Source warehouse not found")
    if not to_wh:
        raise ValueError("Destination warehouse not found")
    if from_wh.id == to_wh.id:
        raise ValueError("Cannot transfer to the same warehouse")

    number = await _next_number(db, project_id, "TR", StockTransfer)

    transfer = StockTransfer(
        project_id=project_id,
        from_warehouse_id=from_wh.id,
        to_warehouse_id=to_wh.id,
        number=number,
        status=TransferStatus.PENDING,
        comment=payload.get("comment"),
        is_defect=payload.get("is_defect", False),
        defect_reason=payload.get("defect_reason"),
        pallets_count=payload.get("pallets_count"),
        pallet_weight_kg=payload.get("pallet_weight_kg"),
        shipped_as_boxes=payload.get("shipped_as_boxes", False) or False,
    )
    db.add(transfer)
    await db.flush()

    items_data = payload.get("items", [])
    barcode_map = await _resolve_barcodes_batch(db, project_id, [d["barcode"] for d in items_data])
    for item_data in items_data:
        nom = barcode_map[item_data["barcode"]]
        item = StockTransferItem(
            project_id=project_id,
            transfer_id=transfer.id,
            nomenclature_id=nom.id,
            barcode=item_data["barcode"],
            quantity=item_data["quantity"],
        )
        db.add(item)

    await db.commit()
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def _get_transfer_locked(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer | None:
    """Get transfer with row lock (FOR UPDATE) — для мутаций статуса, против гонки send/cancel."""
    result = await db.execute(
        select(StockTransfer)
        .where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
        .with_for_update()
    )
    transfer = result.scalar_one_or_none()
    if transfer:
        await db.refresh(transfer, ["items"])
    return transfer


async def _guard_reroute_ff_links(
    db: AsyncSession, project_id: int, transfer: StockTransfer, from_id: int, to_id: int
) -> None:
    """Смена маршрута черновика не должна осиротить уже заведённые связки ФФ.

    `link_request` вяжет отгрузочное зеркало (kind=assembly) к складу ЗАБОРА, а
    приёмочное (kind=inbound) — к складу ПОЛУЧАТЕЛЯ, и сверяет это явно. Если
    после правки маршрута склад связки перестаёт быть концом маршрута, связь
    становится такой, какую `link_request` создать бы не дал, — и это не
    косметика: авто-приём факта (`_collect_transfer_fact_candidates`) отбирает
    приёмки ПО СКЛАДУ ЗАЯВКИ, а джойн на перемещение конец маршрута не сверяет.
    Факт чужого склада поехал бы на `to_warehouse_id` этого переезда, то есть
    сток приходовался бы не туда. Дешевле потребовать отвязать вручную.
    """
    rows = (
        await db.execute(
            select(
                FulfillmentRequest.number,
                FulfillmentRequest.external_id,
                FulfillmentRequest.kind,
                FulfillmentRequest.warehouse_id,
            )
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.stock_transfer_id == transfer.id,
            )
            .limit(50)
        )
    ).all()
    broken = [
        r.number or r.external_id
        for r in rows
        if r.warehouse_id != (from_id if r.kind == FfRequestKind.ASSEMBLY.value else to_id)
    ]
    if broken:
        raise ValueError(
            "Маршрут не изменить: с переездом связаны заявки ФФ другого склада — "
            f"{', '.join(broken)}. Сначала отвяжите их на карточке переезда."
        )


async def update_transfer(
    db: AsyncSession, project_id: int, transfer_id: int, payload: dict
) -> StockTransfer:
    """Правка перемещения — в `TRANSFER_EDITABLE_STATUSES` (PENDING/IN_PROGRESS/READY).

    Во всех трёх сток ещё не двигался: маршрут, состав, транспортная единица и
    брак — это пока просто намерение, и менять их безопасно. VEHICLE_ASSIGNED
    сюда НЕ входит осознанно: машина уже посчитана под конкретный объём и
    маршрут, и правка состава задним числом разошлась бы с тем, за что логист
    договорился платить (снять машину — `unassign_vehicle_transfer`, вернёт в
    READY). После `send_transfer` списание проведено (`TRANSFER_OUT` со склада
    забора + транзит на получателе) и снято снимком в забор (`OutboundShipment`
    с логистикой и деньгами) — правка состава разъехалась бы с движениями, а
    правка маршрута оставила бы транзит висеть на складе, куда груз уже не едет.

    `payload` — только ЯВНО переданные поля (`exclude_unset` в роутере).
    `items` — полная замена состава; отсутствует — состав не трогаем.

    Валидация ВСЯ идёт до первой мутации: иначе отказ на резолве баркода
    оставил бы в сессии половину применённых полей и удалённые позиции, а
    роутер, поймав ValueError, ответил бы 400 «ничего не изменилось».
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    current = TransferStatus(transfer.status)
    if current not in TRANSFER_EDITABLE_STATUSES:
        editable = ", ".join(sorted(s.value for s in TRANSFER_EDITABLE_STATUSES))
        raise ValueError(
            f"Переезд в статусе {current.value} не правится — сток списан со склада забора "
            "и висит транзитом на получателе, а логистика зафиксирована в заборе. "
            f"Править маршрут и состав можно в статусах: {editable}."
        )

    # ── Валидация (без мутаций) ───────────────────────────────────────────
    from_id = payload.get("from_warehouse_id") or transfer.from_warehouse_id
    to_id = payload.get("to_warehouse_id") or transfer.to_warehouse_id
    if from_id != transfer.from_warehouse_id and not await get_warehouse(db, project_id, from_id):
        raise ValueError("Склад забора не найден")
    if to_id != transfer.to_warehouse_id and not await get_warehouse(db, project_id, to_id):
        raise ValueError("Склад получателя не найден")
    if from_id == to_id:
        raise ValueError("Склад забора и склад получателя совпадают — переезда не получится")

    rerouted = (from_id, to_id) != (transfer.from_warehouse_id, transfer.to_warehouse_id)
    if rerouted:
        await _guard_reroute_ff_links(db, project_id, transfer, from_id, to_id)

    items_data = payload.get("items")
    barcode_map: dict = {}
    if items_data is not None:
        if not items_data:
            raise ValueError(
                "Состав перемещения пуст — переезд без позиций всё равно не отправить. "
                "Оставьте хотя бы одну строку или удалите черновик."
            )
        barcode_map = await _resolve_barcodes_batch(
            db, project_id, [d["barcode"] for d in items_data]
        )

    # ── Мутации ───────────────────────────────────────────────────────────
    transfer.from_warehouse_id = from_id
    transfer.to_warehouse_id = to_id
    for field in (
        "comment",
        "is_defect",
        "defect_reason",
        "pallets_count",
        "pallet_weight_kg",
        "shipped_as_boxes",
    ):
        if field in payload:
            setattr(transfer, field, payload[field])

    if items_data is not None:
        for old_item in list(transfer.items):
            await db.delete(old_item)  # no-soft-delete-check: у StockTransferItem нет SoftDeleteMixin
        await db.flush()
        for item_data in items_data:
            nom = barcode_map[item_data["barcode"]]
            db.add(
                StockTransferItem(
                    project_id=project_id,
                    transfer_id=transfer.id,
                    nomenclature_id=nom.id,
                    barcode=item_data["barcode"],
                    quantity=item_data["quantity"],
                )
            )

    await db.commit()
    # Симметрично `unassign_vehicle_transfer` — единственная мутация черновика,
    # которая кэш сбрасывает. Сток черновик не двигает, поэтому reports:balance /
    # warehouse_need здесь ни при чём; ручная правка редка, и один SCAN на неё
    # не жалко (в отличие от bulk-назначения машины, где сброс вынесен).
    await invalidate_cache("reports:assembly_link_anomalies")
    # Отдаём карточку целиком (состав + подписи + ff_links) тем же путём, что GET:
    # после сохранения фронт не должен перезапрашивать переезд отдельно.
    return await get_transfer(db, project_id, transfer_id) or transfer


async def cancel_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> None:
    """Отмена переезда: статус CANCELLED + soft-delete. Только ДО отгрузки.

    Статус ставим ЯВНО, а не полагаемся на один soft-delete: отменённый переезд
    обязан быть отличим от «удалили черновик по ошибке» в истории и в отчётах,
    которые читают статус, а не флаг удаления (`TRANSFER_TRANSITIONS` знает
    CANCELLED терминальным).

    Из SHIPPED/DELIVERED отмены НЕТ, хотя таблица переходов её формально
    допускает: сток уже уехал со склада забора, и «отмена» оставила бы единицы
    списанными в никуда. Обратный ход после отгрузки — только `return_transfer`
    (он сток возвращает).
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    current = TransferStatus(transfer.status)
    if current not in TRANSFER_PRE_SHIP_STATUSES:
        raise ValueError(
            f"Переезд в статусе {current.value} не отменить — сток уже уехал со склада забора. "
            "Оформите возврат («Вернуть на склад»)."
        )
    _check_transfer_transition(current, TransferStatus.CANCELLED)

    transfer.status = TransferStatus.CANCELLED
    _log_transfer_status_change(
        db, project_id, transfer.id, current.value, TransferStatus.CANCELLED.value
    )
    transfer.soft_delete()
    await db.commit()


def _transfer_has_logistics(transfer: StockTransfer) -> bool:
    """Оформлена ли на переезде логистика — ОДИН источник истины для гейта
    «Отправить» (`send_transfer`) и для создания забора (`_create_transfer_pickup`).

    Достаточно ЛЮБОГО признака: машина, водитель, перевозчик, стоимость, даты
    забора/доставки или отметка «логистику оказывает склад забора». Требовать
    именно госномер нельзя — живые сценарии «везёт сам склад» и «машины нет, но
    стоимость известна» его не дают, а выдуманный номер хуже пустого.

    `pickup_cost` сверяем с None, а не на истинность: 0 ₽ — это осознанное
    «везли бесплатно» (оформление есть), а не пустое поле.

    🔴 ДОСЛОВНОЕ зеркало `transferHasLogistics`
    (frontend-react/src/lib/transfer.ts). Разъедутся — кнопка начнёт врать: фронт
    покажет «Отправить» там, где сервер ответит 400, либо спрячет её у переезда,
    который сервер отправить готов.
    """
    return any(
        (
            transfer.vehicle_assigned_at,
            transfer.vehicle_info,
            transfer.vehicle_brand,
            transfer.driver_phone,
            transfer.counterparty_id,
            transfer.pickup_cost is not None,
            transfer.pickup_date,
            transfer.delivery_date,
            transfer.logistics_by_warehouse,
        )
    )


#: Из каких статусов работает «Отправить». READY входит СВЕРХ
#: `TRANSFER_TRANSITIONS` — осознанный карв-аут, зеркало `allow_gazelka_ready` у
#: заявки: у заявки машина обязательна (её везут на маркетплейс по пропуску), а
#: переезд между нашими складами возит и сам склад, без госномера.
#: 🔴 Но карв-аут пускает не ЛЮБОЙ READY: без оформленной логистики отправка
#: отбивается (см. гейт в `send_transfer`). Голый READY уезжал, списывая сток,
#: а забор при этом не создавался — переезд проходил мимо оплат и отчёта
#: логистики, и добрать его задним числом было нечем (TR-32).
#: АВТО-отгрузка по сигналу ФФ карв-аутом НЕ пользуется — она строго из
#: VEHICLE_ASSIGNED (см. `_collect_transfer_ship_candidates`).
_TRANSFER_SEND_FROM = frozenset({TransferStatus.READY, TransferStatus.VEHICLE_ASSIGNED})


async def send_transfer(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    *,
    allowed_from: frozenset[TransferStatus] | None = None,
    allow_no_logistics: bool = False,
) -> StockTransfer:
    """
    Отправить переезд: READY | VEHICLE_ASSIGNED → SHIPPED.
    source.stock -= qty, target.in_transit += qty. Единственное списание переезда.

    `allowed_from` сужает вход — зеркало `allow_gazelka_ready` у `ship_request`.
    Нужен АВТО-отгрузке по сигналу ФФ: она отбирает кандидатов под транзакцией
    синка (`status == VEHICLE_ASSIGNED`), а зовёт нас уже ПОСЛЕ её commit, между
    чем лежат десятки секунд HTTP к провайдеру. В этом окне логист успевает снять
    машину (переезд уходит в READY, логистика вычищена) или отредактировать
    состав — и без сужения мы бы отгрузили по общему карв-ауту: сток списан,
    забор создаётся без перевозчика и без суммы, откат только возвратом.
    Проверка идёт ПОД row-lock, то есть по свежему статусу, а не по снимку.

    `allow_no_logistics` — явное «везём без оформления» для формы «создать и
    увезти» на карточке склада, где кладовщик задним числом фиксирует уже
    состоявшуюся внутреннюю переброску. ЕДИНСТВЕННЫЙ путь отправить READY без
    логистики; Лист логиста флаг не шлёт — оттуда переезд обязан доехать до
    оплат.

    🔴 Гейт логистики НЕ зависит от `allowed_from`. Раньше он стоял под
    `allowed_from is None` — на том основании, что авто-путь и так сужен до
    VEHICLE_ASSIGNED, где машина есть по определению. Допущение сломала Газелька:
    её синк зовёт нас с `{READY, VEHICLE_ASSIGNED}`, а реквизиты агрегатора могли
    и не примениться (у портального маршрута бывает одна марка ТС без госномера и
    без организации перевозчика — `_transfer_vehicle_payload` штатно возвращает
    None). Голый READY проезжал мимо гейта: сток списан, а забор — носитель денег —
    не рождён, то есть ровно инцидент TR-32, только молча и от агрегатора. Теперь
    такой такт синка отбивается ValueError и переезд ждёт реквизитов следующего.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")

    current = TransferStatus(transfer.status)
    if allowed_from is not None and current not in allowed_from:
        raise ValueError(
            f"Переезд больше не в статусе «{'», «'.join(s.value for s in allowed_from)}» "
            f"(сейчас {current.value}) — авто-отправка отменена"
        )
    if current not in _TRANSFER_SEND_FROM:
        # Всегда бросает: READY/VEHICLE_ASSIGNED отсеяны выше, а из прочих
        # статусов SHIPPED в таблице переходов нет.
        _check_transfer_transition(current, TransferStatus.SHIPPED)

    # Канон юзера 01.08.2026: «сначала же назначить машину». Отбиваем ДО единой
    # проводки — иначе сток списан, а забор (носитель денег) не родился.
    # 🔴 Перекладка ВНУТРИ одного склада исключена (канон юзера 02.08.2026):
    # источник и получатель совпадают, товар за ворота не выехал — везти его
    # некому и платить не за что. Брак признаком НЕ считается: переезд
    # «склад → склад» остаётся обычной перевозкой, даже когда едет брак.
    # Зеркало фронтового `isInternalTransfer` (frontend-react/src/lib/transfer.ts).
    _is_internal = transfer.from_warehouse_id == transfer.to_warehouse_id
    if (
        current is TransferStatus.READY
        and not allow_no_logistics
        and not _is_internal
        and not _transfer_has_logistics(transfer)
    ):
        raise ValueError(
            "Назначьте машину (или укажите перевозчика/стоимость) — "
            "иначе переезд уедет мимо оплат и отчёта логистики"
        )

    if not transfer.items:
        raise ValueError("Cannot send transfer with no items")

    is_defect = transfer.is_defect

    for item in transfer.items:
        if is_defect:
            # Deduct from source defect stock
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.from_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=0,
                defect_delta=-item.quantity,
                movement_type=MovementType.DEFECT_TRANSFER_OUT,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )
        else:
            # Deduct from source warehouse (normal)
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.from_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=-item.quantity,
                movement_type=MovementType.TRANSFER_OUT,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )

        # Mark as in_transit on destination
        result = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                WarehouseStock.nomenclature_id == item.nomenclature_id,
            )
        )
        target_stock = result.scalar_one_or_none()
        if target_stock is None:
            target_stock = WarehouseStock(
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                quantity=0,
                in_transit=0,
                defect_quantity=0,
                defect_in_transit=0,
            )
            db.add(target_stock)
            await db.flush()

        if is_defect:
            target_stock.defect_in_transit += item.quantity
        else:
            target_stock.in_transit += item.quantity
        target_stock.updated_at = utcnow()

    transfer.status = TransferStatus.SHIPPED
    # Веха «уехал» — зеркало AssemblyRequest.shipped_at. Из статуса её не
    # вывести: он хранит только ТЕКУЩЕЕ состояние, а после DELIVERED/RETURNED
    # дата отгрузки нужна сводному списку и отчётам логистики.
    transfer.shipped_at = utcnow()
    _log_transfer_status_change(
        db, project_id, transfer.id, current.value, TransferStatus.SHIPPED.value
    )

    number = transfer.number  # после rollback атрибуты протухнут (lazy-load в async = взрыв)
    try:
        # Отправка — всегда НОВАЯ попытка: сюда с живым забором приходят только
        # после возврата, и снимок оплаченного первого круга трогать нельзя.
        await _create_transfer_pickup(db, project_id, transfer, new_attempt=True)
        await db.commit()
    except IntegrityError as e:
        # Партиальный уникальный uq_outbound_shipments_stock_transfer
        # (`stock_transfer_id, attempt_no`) ловит гонку двойного клика
        # «Отправить»: обе транзакции читают одну и ту же последнюю попытку до
        # коммита друг друга и метят один номер. Без перехвата логист получал бы
        # голый 500.
        await db.rollback()
        raise ValueError(f"Забор переезда {number} уже создан — обновите страницу") from e
    # Сток уехал со склада-источника и повис транзитом на получателе — отчётные
    # кэши обязаны это увидеть сразу (iron rule 7). ff_billing:invoices — из-за
    # забора переезда: сверка счетов ФФ читает отгрузки склада по перевозчику.
    # Параллельно: каждый сброс — SCAN по кейспейсу Redis (~22 мс), три подряд
    # добавляли бы к отправке две трети лишней десятой секунды на ровном месте.
    await asyncio.gather(
        invalidate_cache("reports:balance"),
        invalidate_cache("reports:warehouse_need"),
        invalidate_cache("ff_billing:invoices"),
    )
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def _create_transfer_pickup(
    db: AsyncSession, project_id: int, transfer: StockTransfer, *, new_attempt: bool = False
) -> OutboundShipment | None:
    """Забор переезда: OutboundShipment как НОСИТЕЛЬ ЛОГИСТИКИ И ДЕНЕГ.

    🔴 ГЛАВНОЕ: забор НЕ создаёт НИ ОДНОГО `StockMovement` и НЕ трогает
    `WarehouseStock`. Списание уже сделал сам `send_transfer`
    (`MovementType.TRANSFER_OUT`, `reference_type='TRANSFER'`); второй проход
    через `_update_stock` списал бы те же единицы дважды. Позиции забора
    (`OutboundShipmentItem`) заводим только ради читаемости истории отгрузок —
    это данные, а не проводки.

    Забор создаётся ТОЛЬКО когда на перемещении есть логистика
    (`_transfer_has_logistics`). Обоснование: смысл забора — «одна машина, один
    документ на оплату» (заявка на оплату + связка с выпиской). Внутренняя
    переброска между складами, которую никто не везёт и никто не оплачивает,
    породила бы пустой забор без перевозчика и без суммы — мусор в листе оплаты
    и в сверке счетов ФФ. Вызывающий коммитит сам.

    🔴 ОДИН ЗАБОР НА ПОПЫТКУ ОТПРАВКИ (`attempt_no`), как у заявки на сборку:
    партиальный уникальный `uq_outbound_shipments_stock_transfer` держит пару
    `(stock_transfer_id, attempt_no)`.

      * `new_attempt=True` (зовёт `send_transfer`) — ВСЕГДА новый документ.
        Попасть сюда с уже существующим забором можно ровно одним путём:
        RETURNED → READY → «Отправить» (переотправка после возврата, таблица
        переходов её разрешает, а `return_transfer` забор намеренно не удаляет —
        «перевозка состоялась и оплачена»). Раньше здесь стоял upsert, и второй
        круг ПЕРЕЗАПИСЫВАЛ снимок первого: 30 000 ₽ июньского рейса вместе с его
        `shipped_date` исчезали из отчёта «Логистика переездов», две реальные
        перевозки схлопывались в одну, а оплаченная заявка (`PaymentRequestShipment`)
        и матч с выпиской оставались висеть на документе с чужой суммой — и
        второй перевозчик уже не мог попасть в лист оплат (забор помечен занятым).
      * `new_attempt=False` (ретро-оформление `set_transfer_logistics`) —
        обновляет снимок и позиции ПОСЛЕДНЕЙ попытки: его зовут повторно и
        осознанно, дописывая перевозчика и стоимость уже уехавшему переезду.

    🔴 ИНВАРИАНТЫ ЗАПИСИ (нарушение = переезд протекает в чужие отчёты):
      - `assembly_request_id` ОСТАЁТСЯ None, даже когда перемещение сделано из
        заявки (`converted_from_assembly_id`). Логистические отчёты
        (`assembly/analytics.py::_logistics_base_filters`) отсекают переезды
        ровно по пустому `assembly_request_id` и идут INNER JOIN по
        AssemblyRequest — проставим, и переезд проедет в ₽/паллета, в аналитику
        перевозок и в прогноз стоимости. Связь с заявкой живёт на самом
        перемещении (`StockTransfer.converted_from_assembly_id`).
      - `AssemblyRequest.outbound_shipment_id` НЕ трогаем: цепочка попыток
        отгрузки заявки показала бы переезд как попытку сдачи на WB.
      - Ни одного `_update_stock(..., reference_type="SHIPMENT")`.

    Денежный контур требует, чтобы забор нёс `counterparty_id`, `pickup_cost`,
    `destination` (из него `payment_request_service._build_purpose` строит
    назначение платежа) и статус SHIPPED — иначе он выпадет из авто-матча
    выписки и из строк LOGISTICS счёта ФФ.
    """
    if not _transfer_has_logistics(transfer):
        return None

    dest_name = (
        await db.execute(
            select(Warehouse.name).where(
                Warehouse.id == transfer.to_warehouse_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()

    # Снимок логистики: на самом перемещении эти поля ещё могут быть
    # перезаписаны, у забора они зафиксированы. Один словарь на INSERT и UPDATE —
    # разъехавшись, ветки дали бы заборы с разным набором заполненных полей, и
    # ретро-оформленный переезд выпал бы из сверки счетов ФФ на ровном месте.
    snapshot = {
        "destination": dest_name,
        # Ось периода в отчёте «Переезды» и в сверке — день, когда груз РЕАЛЬНО
        # уехал, а не день нажатия кнопки. `date.today()` здесь свалил бы
        # ретро-оформление TR-1…TR-31 в текущий месяц и нарисовал бы пик
        # расходов, которого не было. Порядок падения: фактическая отгрузка →
        # согласованная дата забора → сегодня (переезд ещё не уехал).
        "shipped_date": (
            transfer.shipped_at.date()
            if transfer.shipped_at
            else (transfer.pickup_date or date.today())
        ),
        "pickup_cost": transfer.pickup_cost,
        "vehicle_info": transfer.vehicle_info,
        "vehicle_brand": transfer.vehicle_brand,
        "driver_phone": transfer.driver_phone,
        "counterparty_id": transfer.counterparty_id,
        "pickup_date": transfer.pickup_date,
        "pickup_time_slot": transfer.pickup_time_slot,
        "delivery_date": transfer.delivery_date,
        # Транспортная единица переезда: на неё смотрит назначение платежа
        # (`payment_request_service._build_purpose`) и отчёты.
        "pallets_count": transfer.pallets_count,
        "pallet_weight_kg": transfer.pallet_weight_kg,
        "shipped_as_boxes": transfer.shipped_as_boxes,
    }

    # Последняя попытка отправки этого переезда — она же кандидат на обновление
    # (ретро-путь) и база для номера следующей (переотправка после возврата).
    last = (
        (
            await db.execute(
                select(OutboundShipment)
                .where(
                    OutboundShipment.project_id == project_id,
                    OutboundShipment.stock_transfer_id == transfer.id,
                    OutboundShipment.is_deleted == False,  # noqa: E712
                )
                .order_by(OutboundShipment.attempt_no.desc(), OutboundShipment.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    shipment = None if new_attempt else last
    if shipment is not None:
        for field, value in snapshot.items():
            setattr(shipment, field, value)
        # Склад забора мог смениться: правка маршрута разрешена до отгрузки, а
        # ретро-оформление приходит на документ с уже актуальным источником.
        shipment.warehouse_id = transfer.from_warehouse_id
        # Позиции переписываем целиком, а не доливаем: между кругами
        # (RETURNED → READY) состав переезда правится, и уцелевшие строки
        # прошлого круга сделали бы историю отгрузки суммой двух поездок.
        # `status` НЕ трогаем: у забора переезда он всегда SHIPPED (deliver/cancel
        # на нём запрещены), а переписать его значило бы затереть ручную правку.
        old_items = (
            (
                await db.execute(
                    select(OutboundShipmentItem).where(
                        OutboundShipmentItem.project_id == project_id,
                        OutboundShipmentItem.shipment_id == shipment.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for old in old_items:
            await db.delete(old)  # no-soft-delete-check: у OutboundShipmentItem нет SoftDeleteMixin
        await db.flush()
    else:
        shipment = OutboundShipment(
            project_id=project_id,
            warehouse_id=transfer.from_warehouse_id,
            number=await _next_number(db, project_id, "OUT", OutboundShipment),
            status=OutboundStatus.SHIPPED,
            stock_transfer_id=transfer.id,
            attempt_no=(last.attempt_no + 1) if last is not None else 1,
            **snapshot,
        )
        db.add(shipment)
        await db.flush()

    for item in transfer.items:
        db.add(
            OutboundShipmentItem(
                project_id=project_id,
                shipment_id=shipment.id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                quantity=item.quantity,
            )
        )
    return shipment


async def _transfer_received_map(
    db: AsyncSession, project_id: int, transfer: StockTransfer
) -> dict[int, int]:
    """{nomenclature_id: сколько СЕЙЧАС лежит у получателя по этому переезду}.

    Источник истины — журнал движений, а не поля документа: порционный авто-приём
    ФФ (`receive_transfer_fact`) добирает факт несколькими синками, и «сколько уже
    принято» выводится только из движений.

    Сумма НЕТТО, и это принципиально. Возврат (`return_transfer`) откатывает
    зачисленное движением `TRANSFER_OUT` с `reference_type='TRANSFER_RETURN'` —
    отдельным типом, чтобы производная осталась честной. Считай мы только
    приходные движения, карта после возврата НАВСЕГДА помнила бы принятое в
    прошлом круге, а бюджет прихода считается как «план − принято». Переотправка
    (RETURNED → READY → SHIPPED, таблица её разрешает и докстринга `return_transfer`
    обещает) тогда списывала бы сток с источника и не зачисляла никому: единицы
    повисали бы вечным фантомным транзитом, переезд рапортовал бы DELIVERED, а
    бейдж «принято 10 из 10» это подтверждал. Второй возврат по такому переезду
    пытался бы снять с получателя устаревшее количество — то есть списать чужие
    единицы, если они на складе есть.

    `TRANSFER_OUT` с `reference_type='TRANSFER'` на складе-ПОЛУЧАТЕЛЕ не бывает
    (списание живёт на источнике), поэтому фильтр по складу не даёт вычесть лишнего.
    """
    res = await db.execute(
        select(
            StockMovement.nomenclature_id,
            func.coalesce(func.sum(StockMovement.quantity + StockMovement.defect_delta), 0),
        )
        .where(
            StockMovement.project_id == project_id,
            StockMovement.warehouse_id == transfer.to_warehouse_id,
            StockMovement.reference_type.in_(("TRANSFER", "TRANSFER_RETURN")),
            StockMovement.reference_id == transfer.id,
            StockMovement.movement_type.in_(
                [
                    MovementType.TRANSFER_IN.value,
                    MovementType.DEFECT_TRANSFER_IN.value,
                    MovementType.TRANSFER_OUT.value,
                    MovementType.DEFECT_TRANSFER_OUT.value,
                ]
            ),
        )
        .group_by(StockMovement.nomenclature_id)
    )
    # Отрицательный нетто невозможен, но кламп страхует от порчи данных руками:
    # «принято минус пять» уронило бы бюджет прихода в сторону переприёма.
    return {row[0]: max(0, int(row[1] or 0)) for row in res.all()}


async def complete_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer:
    """
    Принять переезд получателем: SHIPPED → DELIVERED.
    target.stock += qty, target.in_transit -= qty. Единственный приход переезда.

    🔴 Приходуем ОСТАТОК плана, а не план целиком. Тот же переезд мог уже
    частично приехать через авто-приём по факту ФФ (`receive_transfer_fact`
    работает в SHIPPED и порционно добирает дельту), и ручное «Принять» поверх
    него зачислило бы принятые единицы ВТОРОЙ раз — при этом транзит ушёл бы в
    ноль по `max(0, ...)` и расхождение не всплыло бы нигде.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")

    current = TransferStatus(transfer.status)
    _check_transfer_transition(current, TransferStatus.DELIVERED)

    is_defect = transfer.is_defect
    # Бюджет прихода по номенклатуре: план минус уже принятое. Считаем по
    # номенклатуре (а не по строке состава): дубли строк одного ШК в составе
    # законны, и каждая из них не должна получить полный «остаток» целиком.
    received = await _transfer_received_map(db, project_id, transfer)
    budget: dict[int, int] = {}
    for item in transfer.items:
        budget[item.nomenclature_id] = budget.get(item.nomenclature_id, 0) + item.quantity
    for nom_id, planned in budget.items():
        budget[nom_id] = max(planned - received.get(nom_id, 0), 0)

    for item in transfer.items:
        take = min(item.quantity, budget.get(item.nomenclature_id, 0))
        budget[item.nomenclature_id] = budget.get(item.nomenclature_id, 0) - take
        if take <= 0:
            continue
        if is_defect:
            # Add to destination defect stock
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=0,
                defect_delta=take,
                movement_type=MovementType.DEFECT_TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )
        else:
            # Add to destination stock (normal)
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=take,
                movement_type=MovementType.TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )

        # Decrease in_transit
        result = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                WarehouseStock.nomenclature_id == item.nomenclature_id,
            )
        )
        target_stock = result.scalar_one_or_none()
        if target_stock:
            if is_defect:
                target_stock.defect_in_transit = max(0, target_stock.defect_in_transit - take)
            else:
                target_stock.in_transit = max(0, target_stock.in_transit - take)
            target_stock.updated_at = utcnow()

    # Наследуем ручную кратность коробов на склад-получатель (товар переехал —
    # «шт/короб» те же; раньше кратность заносили заново руками). Best-effort:
    # сбой наследования не валит приёмку перемещения.
    if not is_defect:
        try:
            from backend.services import box_multiplicity_service

            await box_multiplicity_service.inherit_on_transfer(
                db,
                project_id,
                transfer.from_warehouse_id,
                transfer.to_warehouse_id,
                [i.barcode for i in transfer.items],
                transfer.number,
            )
        except Exception:
            logger.warning("transfer box-qty inherit failed for %s", transfer.number, exc_info=True)

    transfer.status = TransferStatus.DELIVERED
    _log_transfer_status_change(
        db, project_id, transfer.id, current.value, TransferStatus.DELIVERED.value
    )

    await db.commit()
    # Приход зачислен на склад-получатель — отчётные кэши обязаны увидеть это
    # сразу (iron rule 7). Раньше сбрасывался только warehouse_need и только при
    # унаследованной кратности, из-за чего расхождение остатков висело до TTL.
    await asyncio.gather(
        invalidate_cache("reports:balance"),
        invalidate_cache("reports:warehouse_need"),
    )
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def receive_transfer_fact(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    accepted_by_barcode: dict[str, int],
    *,
    defect_by_barcode: dict[str, int] | None = None,
    note: str | None = None,
    mark_ff_request_applied: int | None = None,
) -> dict:
    """Порционный приём перемещения ПО ФАКТУ (авто-приём по связанной ФФ-приёмке).

    ФФ принимает наш TR своими приёмками (возможно, порциями — кейс
    PVB-0000121 → TR-21) и отчитывается фактами. Здесь: TRANSFER_IN на факт
    (годное + брак отдельно), транзит уменьшается на принятое, сверх плана
    не приходуем (излишек — предмет сверки зеркала, не транзита). Transfer
    закрывается ТОЛЬКО когда суммарный факт покрыл весь план; недобор остаётся
    видимым транзитом до следующих порций или ручного закрытия. Идемпотентно:
    «уже принятое» выводится из движений TRANSFER_IN/DEFECT_TRANSFER_IN этого
    transfer на складе-назначении, повторный вызов доберёт максимум остаток.

    mark_ff_request_applied — id ФФ-заявки: если что-то принято (или план
    покрыт), её transfer_fact_applied_at ставится В ТОЙ ЖЕ транзакции, что и
    движения. Иначе между «приход закоммичен» и «маркер закоммичен» было бы
    окно, в котором падение процесса заставило бы следующий синк применить
    тот же факт повторно (частичный факт добрал бы фантомный остаток).
    """
    defect_by_barcode = defect_by_barcode or {}
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    current = TransferStatus(transfer.status)
    if current is not TransferStatus.SHIPPED:
        raise ValueError(
            f"Факт приёмки принимается только у отправленного переезда (сейчас {current.value})"
        )

    is_defect = transfer.is_defect

    # Уже принято по этому transfer на складе-назначении (порционные приёмы).
    received_map = await _transfer_received_map(db, project_id, transfer)

    # План по номенклатуре: дубли строк одного ШК схлопываем ДО цикла — иначе
    # каждая строка получила бы полный факт и «приём по факту» превратился бы
    # в «приём по плану» (create_transfer дубли не запрещает).
    plan_map: dict[int, tuple[str, int]] = {}
    for item in transfer.items:
        bc, qty = plan_map.get(item.nomenclature_id, (item.barcode, 0))
        plan_map[item.nomenclature_id] = (bc, qty + item.quantity)
    plan_barcodes = {bc for bc, _ in plan_map.values()}
    # ШК факта, которых нет в составе перемещения, — сигнал кривой связки или
    # чужого товара в приёмке; вызывающий логирует, при пустом приёме не ставит маркер.
    unmatched = sorted((set(accepted_by_barcode) | set(defect_by_barcode)) - plan_barcodes)

    received_good = 0
    received_defect = 0
    remaining_total = 0
    for nomenclature_id, (barcode, plan_qty) in plan_map.items():
        already = received_map.get(nomenclature_id, 0)
        remaining = max(plan_qty - already, 0)
        fact_good = max(int(accepted_by_barcode.get(barcode, 0)), 0)
        fact_defect = max(int(defect_by_barcode.get(barcode, 0)), 0)
        take_good = min(fact_good, remaining)
        take_defect = min(fact_defect, remaining - take_good)

        # Перемещение брака: весь факт приходуется браком же.
        if is_defect:
            take_defect, take_good = take_good + take_defect, 0

        if take_good > 0:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=nomenclature_id,
                barcode=barcode,
                delta=take_good,
                movement_type=MovementType.TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
                comment=note,
            )
        if take_defect > 0:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=nomenclature_id,
                barcode=barcode,
                delta=0,
                defect_delta=take_defect,
                movement_type=MovementType.DEFECT_TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
                comment=note,
            )

        taken = take_good + take_defect
        if taken > 0:
            result = await db.execute(
                select(WarehouseStock).where(
                    WarehouseStock.project_id == project_id,
                    WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                    WarehouseStock.nomenclature_id == nomenclature_id,
                )
            )
            target_stock = result.scalar_one_or_none()
            if target_stock:
                if is_defect:
                    target_stock.defect_in_transit = max(0, target_stock.defect_in_transit - taken)
                else:
                    target_stock.in_transit = max(0, target_stock.in_transit - taken)
                target_stock.updated_at = utcnow()

        received_good += take_good
        received_defect += take_defect
        remaining_total += remaining - taken

    completed = remaining_total == 0
    if completed:
        transfer.status = TransferStatus.DELIVERED
        _log_transfer_status_change(
            db,
            project_id,
            transfer.id,
            current.value,
            TransferStatus.DELIVERED.value,
            changed_by="ff_sync",
            comment=f"Приём по факту ФФ{f' ({note})' if note else ''}",
        )
    if note:
        stamp = f"{note}: принято {received_good} годн."
        if received_defect:
            stamp += f" + {received_defect} брак"
        if remaining_total:
            stamp += f", в пути осталось {remaining_total}"
        transfer.comment = f"{transfer.comment}\n{stamp}" if transfer.comment else stamp

    # Полное покрытие = фактическое завершение перемещения: наследуем ручную
    # кратность коробов на склад-получатель, как ручной complete_transfer —
    # авто-приём для migfull-складов ЗАМЕНЯЕТ ручное закрытие, без наследования
    # SKU без кратности выпадал бы из отгрузки в предбронь (грабля TR-20).
    inherited = 0
    if completed and not is_defect:
        try:
            from backend.services import box_multiplicity_service

            inherited = await box_multiplicity_service.inherit_on_transfer(
                db,
                project_id,
                transfer.from_warehouse_id,
                transfer.to_warehouse_id,
                [bc for bc, _ in plan_map.values()],
                transfer.number,
            )
        except Exception:
            logger.warning("transfer box-qty inherit failed for %s", transfer.number, exc_info=True)

    marker_set = False
    if mark_ff_request_applied is not None and (received_good + received_defect > 0 or completed):
        from backend.models.fulfillment import FulfillmentRequest

        req_row = await db.get(FulfillmentRequest, mark_ff_request_applied)
        if req_row is not None and req_row.project_id == project_id:
            req_row.transfer_fact_applied_at = utcnow()
            marker_set = True

    await db.commit()
    # Сток изменился — сбрасываем отчётные кэши (iron rule 7): расхождение
    # остатков и потребность обязаны увидеть приход сразу, а не через TTL.
    if received_good or received_defect:
        await invalidate_cache("reports:balance")
        await invalidate_cache("reports:assembly_link_anomalies")
        await invalidate_cache("reports:warehouse_need")
    elif inherited:
        await invalidate_cache("reports:warehouse_need")
    await db.refresh(transfer, ["items"])
    return {
        "received_good": received_good,
        "received_defect": received_defect,
        "remaining_total": remaining_total,
        "completed": completed,
        "unmatched": unmatched,
        "applied_marker": marker_set,
    }


# ─── Переезд: машина и конвертация заявки в перемещение ────────────────────


async def _gazelka_led_transfer_ids(
    db: AsyncSession, project_id: int, transfer_ids: list[int]
) -> set[int]:
    """id переездов из списка, логистику которых ВЕДЁТ Газелька (заказ SENT/MATCHED).

    Зеркало `assembly.status._gazelka_linked_ids` — тот же вопрос, но по второй
    ссылке заказа (`stock_transfer_id`): агрегатор возит теперь и переезды между
    нашими складами. Ограничено IN (ids) → без `.limit()`. Project-scoped.
    """
    if not transfer_ids:
        return set()
    rows = (
        (
            await db.execute(
                select(GazelkaOrder.stock_transfer_id).where(
                    GazelkaOrder.project_id == project_id,
                    GazelkaOrder.stock_transfer_id.in_(transfer_ids),
                    GazelkaOrder.status.in_(
                        [GazelkaOrderStatus.SENT, GazelkaOrderStatus.MATCHED]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return {tid for tid in rows if tid is not None}


async def _deny_if_gazelka_leads(
    db: AsyncSession, project_id: int, transfers: list[StockTransfer]
) -> None:
    """Ручная машина на переезде, который ведёт агрегатор, — запрещена.

    Зеркало гарда заявки (`assembly.status.assign_vehicle`): реквизиты приедут от
    Газельки собственным синком, и наш госномер поверх них разошёлся бы с тем,
    что реально подадут под погрузку — а снимок логистики уезжает в забор, то
    есть в деньги. Жёсткая гарантия для ВСЕХ путей (одиночный, bulk, снятие);
    фронт лишь дублирует дизейблом кнопки.
    """
    led = await _gazelka_led_transfer_ids(db, project_id, [t.id for t in transfers])
    if led:
        blocked = ", ".join(t.number for t in transfers if t.id in led)
        raise ValueError(
            "Логистику этого переезда ведёт Газелька — машину назначает агрегатор "
            f"({blocked})."
        )


async def assign_vehicle_transfer(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    payload: TransferAssignVehicle,
) -> StockTransfer:
    """Назначить машину на перемещение — зеркало `assembly.status.assign_vehicle`.

    Осознанное отличие от заявки на сборку (см. модель StockTransfer): НЕТ
    WB-пропуска (`sync_pass_from_vehicle` / `try_autopush_pass_by_assembly`) —
    переезд едет между НАШИМИ складами, на маркетплейс он не заезжает и пропуск
    ему не нужен. Гард Газельки, наоборот, ЕСТЬ: агрегатор возит и переезды.

    READY → VEHICLE_ASSIGNED, как у заявки. ПЕРЕназначение внутри
    VEHICLE_ASSIGNED разрешено (в отличие от заявки): переезд возит наёмная
    машина без пропуска и без брони на маркетплейсе — поменять госномер или
    водителя за день до забора это норма, а не повод снимать машину и назначать
    заново. Статус при переназначении не меняется, в историю такой вызов не
    пишется (переход-то не состоялся).

    До READY назначать нельзя: пока ФФ не собрал груз, объём и вес неизвестны, а
    именно из них считается стоимость забора. После отгрузки — тоже: снимок
    логистики уехал в забор (`_create_transfer_pickup`), и правка задним числом
    разошлась бы со снимком и с листом оплаты.

    Перевозчик: при `logistics_by_warehouse=True` — контрагент склада-ИСТОЧНИКА
    (`Warehouse.counterparty_id` склада `from_warehouse_id`), иначе резолв по
    `carrier_inn`/`carrier_name` тем же резолвером, что у заявки.
    """
    transfer = await _apply_vehicle_to_transfer(db, project_id, transfer_id, payload)
    await db.commit()
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def _write_transfer_logistics(
    db: AsyncSession,
    project_id: int,
    transfer: StockTransfer,
    payload: TransferAssignVehicle,
    *,
    restamp_assigned_at: bool,
    partial: bool = False,
) -> None:
    """Присвоить переезду блок логистики — ОБЩЕЕ тело назначения машины
    (`_apply_vehicle_to_transfer`) и ретро-оформления уехавшего
    (`set_transfer_logistics`). Без commit, без смены статуса, без движения стока.

    Вынесено, а не скопировано: копия разъехалась бы на первом же новом поле, а
    цена расхождения — снимок в заборе, то есть деньги, не сходящиеся с карточкой.

    `restamp_assigned_at=False` (ретро-путь): `vehicle_assigned_at` ставится
    только когда он пуст. У уже уехавшего переезда это исторический момент
    посадки на машину, и затирать его сегодняшним временем нельзя — по нему
    считается, сколько документ ждал транспорт.

    `partial=True` (путь СИНКА Газельки) — пустое значение в теле означает «не
    знаю», а не «сбрось». Ручные формы шлют ПОЛНЫЙ снимок карточки, и для них
    безусловное присваивание единственно верно. Но тем же телом ходит агрегатор,
    который отдаёт лишь то, что заполнено в кабинете: тарифа у плана может ещё не
    быть, интервал забора и «логистику оказывает склад» он не знает вовсе — и
    безусловная запись стирала бы согласованные логистом 22 000 ₽ и окно погрузки,
    а следом уносила бы пустой снимок в забор, то есть в «Оплаты». Дословное
    зеркало `assembly.status.apply_gazelka_logistics`, где каждое поле пишется
    построчным `if value:`.
    """
    # Резолверы перевозчика переиспользуем из assembly-сервиса (единая логика
    # upsert-а контрагента и bump-а OTHER→CARRIER). Импорт локальный: модуль
    # assembly.status тянет фасад warehouse_service → этот модуль (цикл).
    from backend.services.assembly.status import _resolve_carrier, _warehouse_counterparty_id

    if payload.logistics_by_warehouse:
        cp_id = await _warehouse_counterparty_id(db, project_id, transfer.from_warehouse_id)
        if cp_id is None:
            raise ValueError(
                "У склада забора не указан контрагент — заполните в справочнике складов."
            )
    else:
        cp_id = await _resolve_carrier(db, project_id, payload.carrier_inn, payload.carrier_name)

    if not partial:
        transfer.vehicle_info = payload.vehicle_info
        transfer.vehicle_brand = payload.vehicle_brand
        transfer.driver_phone = payload.driver_phone
        transfer.pickup_date = payload.pickup_date
        transfer.pickup_time_slot = payload.pickup_time_slot
        transfer.pickup_cost = payload.pickup_cost
        transfer.delivery_date = payload.delivery_date
        transfer.logistics_by_warehouse = payload.logistics_by_warehouse
    else:
        # Агрегатор — источник истины только по тому, что у него ЗАПОЛНЕНО.
        # `logistics_by_warehouse` он не присылает никогда, поэтому поднимаем
        # флаг, но не опускаем: `False` здесь — «не знаю», а не «склад не везёт».
        for field in (
            "vehicle_info", "vehicle_brand", "driver_phone",
            "pickup_date", "pickup_time_slot", "pickup_cost", "delivery_date",
        ):
            value = getattr(payload, field)
            if value is not None and value != "":
                setattr(transfer, field, value)
        if payload.logistics_by_warehouse:
            transfer.logistics_by_warehouse = True
    if payload.driver_first_name is not None:
        transfer.driver_first_name = payload.driver_first_name
    if payload.driver_last_name is not None:
        transfer.driver_last_name = payload.driver_last_name
    if cp_id is not None:
        transfer.counterparty_id = cp_id
    # Транспортная единица трёхзначна: None = «не уточнял» и НЕ затирает уже
    # заданное (в т.ч. унаследованное от заявки при конвертации).
    if payload.pallets_count is not None:
        transfer.pallets_count = payload.pallets_count
    if payload.pallet_weight_kg is not None:
        transfer.pallet_weight_kg = payload.pallet_weight_kg
    if payload.shipped_as_boxes is not None:
        transfer.shipped_as_boxes = payload.shipped_as_boxes
    if restamp_assigned_at or transfer.vehicle_assigned_at is None:
        transfer.vehicle_assigned_at = utcnow()


async def _apply_vehicle_to_transfer(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    payload: TransferAssignVehicle,
    *,
    check_gazelka: bool = True,
    partial: bool = False,
) -> StockTransfer:
    """Мутация назначения БЕЗ commit — общее тело одиночного и bulk-пути.

    Вынесено ради атомарности bulk: commit на каждом id означал бы, что отказ на
    третьем переезде оставит первые два уже назначенными, а логист получит 400 и
    решит, что не назначилось ничего.

    `check_gazelka=False` — только для `apply_gazelka_transfer_logistics`: гард
    отбивает РУЧНУЮ машину поверх агрегатора, а сам агрегатор своими же
    реквизитами обязан пройти.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    if check_gazelka:
        await _deny_if_gazelka_leads(db, project_id, [transfer])
    current = TransferStatus(transfer.status)
    # Переназначение внутри VEHICLE_ASSIGNED — не переход, таблицу не спрашиваем.
    if current is not TransferStatus.VEHICLE_ASSIGNED:
        _check_transfer_transition(current, TransferStatus.VEHICLE_ASSIGNED)

    await _write_transfer_logistics(
        db, project_id, transfer, payload, restamp_assigned_at=True, partial=partial
    )
    if current is not TransferStatus.VEHICLE_ASSIGNED:
        transfer.status = TransferStatus.VEHICLE_ASSIGNED
        _log_transfer_status_change(
            db,
            project_id,
            transfer.id,
            current.value,
            TransferStatus.VEHICLE_ASSIGNED.value,
            comment=payload.vehicle_info,
        )
    # Кэш здесь НЕ сбрасываем намеренно: единственный кандидат —
    # `reports:assembly_link_anomalies`, но `assembly/link_anomalies.py` не
    # обращается к StockTransfer ни разу, то есть назначение машины на переезд
    # физически не может изменить этот отчёт. А сброс не бесплатен: это SCAN по
    # всему кейспейсу Redis (~22 мс на 39k ключей) — внутри bulk-цикла на 50
    # переездов набегала секунда впустую плюс 50 холодных пересчётов отчёта.
    return transfer


async def assign_vehicle_transfer_bulk(
    db: AsyncSession,
    project_id: int,
    ids: list[int],
    payload: TransferAssignVehicle,
) -> list[StockTransfer]:
    """Одна машина на N переездов (Лист логиста: три переезда «транзит Питер»
    едут одной газелью).

    ДЕЙСТВИТЕЛЬНО атомарен: одна транзакция на весь список, отказ на любом id
    откатывает ВСЁ. Раньше здесь был цикл по `assign_vehicle_transfer`, который
    коммитит каждый id, — докстринга обещала «первый отказ роняет весь вызов», а
    на деле отказ на третьем оставлял первые два назначенными, и логист, получив
    400, был уверен, что не назначилось ничего. Побочно: откатывается и
    контрагент, который `_resolve_carrier` мог создать под этот батч.

    Дубли id схлопываются: переназначение внутри VEHICLE_ASSIGNED разрешено,
    поэтому повторный id прошёл бы гейт заново и отработал бы второй круг
    впустую (лишняя строка истории на ровном месте). Порядок сохраняем
    (`dict.fromkeys`) — он определяет, на каком элементе вызов упадёт, а значит
    и текст ошибки логисту.

    Гард Газельки работает и здесь — он живёт внутри `_apply_vehicle_to_transfer`,
    то есть один переезд под агрегатором роняет ВЕСЬ батч (и правильно: логист
    выделил пачку, часть которой везёт не он).
    """
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        return []
    transfers: list[StockTransfer] = []
    try:
        for tid in unique_ids:
            transfers.append(await _apply_vehicle_to_transfer(db, project_id, tid, payload))
    except ValueError as e:
        # Явный rollback, а не расчёт на teardown сессии: роутер ловит ValueError
        # и отвечает 400, сессия живёт дальше (в тестах — тем более), и остаться
        # в оборванной транзакции с частичными мутациями нельзя.
        await db.rollback()
        raise ValueError(f"Переезд #{tid}: {e} Ничего не назначено — вызов атомарный.") from None
    await db.commit()
    for transfer in transfers:
        await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, transfers)
    return transfers


async def unassign_vehicle_transfer(
    db: AsyncSession, project_id: int, transfer_id: int
) -> StockTransfer:
    """Снять машину с переезда: VEHICLE_ASSIGNED → READY (зеркало `unassign_vehicle`).

    Чистит ТОЛЬКО логистику (машина, водитель, перевозчик, даты, стоимость).
    Транспортная единица (`pallets_count` / `pallet_weight_kg` /
    `shipped_as_boxes`) НЕ трогается: это свойство ГРУЗА, а не машины — груз
    остаётся тем же, меняется только тот, кто его повезёт.

    После отгрузки снять нельзя: забор уже создан со снимком логистики и связан
    с деньгами (заявка на оплату, выписка) — «снятие» оставило бы забор с
    суммой, у которой нет владельца на перемещении. Гарантирует это сама
    таблица переходов: из SHIPPED в READY хода нет.

    Гард Газельки — тот же, что у назначения: реквизиты поставил агрегатор,
    и «снять машину» у себя, не отменив заказ у него, значит развести карточку с
    тем, что реально приедет под погрузку.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    await _deny_if_gazelka_leads(db, project_id, [transfer])
    current = TransferStatus(transfer.status)
    if current is not TransferStatus.VEHICLE_ASSIGNED:
        raise ValueError(
            f"На переезде в статусе {current.value} машина не назначена — снимать нечего."
        )
    _check_transfer_transition(current, TransferStatus.READY)

    transfer.status = TransferStatus.READY
    _log_transfer_status_change(
        db,
        project_id,
        transfer.id,
        current.value,
        TransferStatus.READY.value,
        comment="Отмена назначения машины",
    )
    transfer.vehicle_info = None
    transfer.vehicle_brand = None
    transfer.driver_first_name = None
    transfer.driver_last_name = None
    transfer.driver_phone = None
    transfer.counterparty_id = None
    transfer.logistics_by_warehouse = False
    transfer.pickup_date = None
    transfer.pickup_time_slot = None
    transfer.pickup_cost = None
    transfer.delivery_date = None
    transfer.vehicle_assigned_at = None

    await db.commit()
    await invalidate_cache("reports:assembly_link_anomalies")
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def apply_gazelka_transfer_logistics(
    db: AsyncSession, project_id: int, transfer_id: int, payload: TransferAssignVehicle
) -> StockTransfer | None:
    """Машина от агрегатора: обходит гард via_gazelka (его поставил сам агрегатор).

    Точка входа для синка Газельки: портал вернул госномер, водителя и цену —
    переезд обязан их принять, хотя РУЧНОЕ назначение на нём уже запрещено
    (`_deny_if_gazelka_leads`). Всё остальное — как у обычного назначения:
    READY → VEHICLE_ASSIGNED со строкой в истории, переназначение внутри
    VEHICLE_ASSIGNED без перехода, тот же резолв перевозчика. Реквизиты кладём
    неразрушающе (`partial=True`): пустое поле кабинета не стирает ручной ввод.

    🔴 Вне READY/VEHICLE_ASSIGNED — NO-OP (`None`), дословно как
    `assembly.status.apply_gazelka_logistics`. Заказ живёт в кабинете и после
    отгрузки, синк дёргает его каждые 15 минут, и попытка «назначить машину» на
    SHIPPED роняла бы ValueError из таблицы переходов. Он не локальный: вылетал
    из `_reconcile_active_transfer` и уносил с собой ВСЁ, что стоит ниже, — в
    первую очередь бэкфилл тарифа. Итог: тариф, появившийся у портала после
    отгрузки, не доезжал до забора никогда, а лог каждые 15 минут писал
    `reconcile_failed`.
    """
    # Статус читаем ОТДЕЛЬНЫМ скаляром, а не через `_get_transfer_locked`:
    # взятый ради no-op `FOR UPDATE` держал бы строку до чужого коммита, а снять
    # его rollback'ом нельзя — тот экспайрит ORM-объекты вызывающего. Гонка тут
    # безобидна: реальную проверку перехода всё равно делает `_apply_vehicle_to_transfer`
    # уже под локом.
    status = (
        await db.execute(
            select(StockTransfer.status).where(
                StockTransfer.id == transfer_id,
                StockTransfer.project_id == project_id,
                StockTransfer.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if status is None or TransferStatus(status) not in (
        TransferStatus.READY,
        TransferStatus.VEHICLE_ASSIGNED,
    ):
        return None

    transfer = await _apply_vehicle_to_transfer(
        db, project_id, transfer_id, payload, check_gazelka=False, partial=True
    )
    await db.commit()
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


# ─── Переезд: логистика ЗАДНИМ ЧИСЛОМ (уже уехавшие) ──────────────────────

#: Статусы, в которых перевозчика и стоимость дописывают уже уехавшему переезду.
#: CANCELLED сюда не входит: отменённый переезд никто не вёз, платить не за что.
#: 🔴 Зеркало `TRANSFER_LOGISTICS_EDITABLE_STATUSES` (frontend/src/lib/transfer.ts).
_TRANSFER_LOGISTICS_RETRO_STATUSES = frozenset(
    {
        TransferStatus.SHIPPED,
        TransferStatus.DELIVERED,
        TransferStatus.RETURNED,
        TransferStatus.CLOSED,
    }
)


async def _apply_retro_logistics(
    db: AsyncSession, project_id: int, transfer_id: int, payload: TransferAssignVehicle
) -> StockTransfer:
    """Мутация ретро-оформления БЕЗ commit — общее тело одиночного и bulk-пути
    (зеркало `_apply_vehicle_to_transfer`, вынесено ради атомарности bulk)."""
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    current = TransferStatus(transfer.status)
    if current not in _TRANSFER_LOGISTICS_RETRO_STATUSES:
        raise ValueError(
            f"Перевозчика и стоимость дописывают только уехавшему переезду "
            f"(сейчас {current.value}) — до отгрузки логистика оформляется "
            f"назначением машины"
        )
    await _write_transfer_logistics(
        db, project_id, transfer, payload, restamp_assigned_at=False
    )
    # Забор идемпотентен: повторный вызов обновит снимок, а не заведёт второй
    # документ на ту же перевозку (и вторую заявку на оплату следом).
    await _create_transfer_pickup(db, project_id, transfer)
    return transfer


async def set_transfer_logistics(
    db: AsyncSession, project_id: int, transfer_id: int, payload: TransferAssignVehicle
) -> StockTransfer:
    """Перевозчик и стоимость на УЖЕ УЕХАВШЕМ переезде (SHIPPED / DELIVERED /
    RETURNED / CLOSED) — единственный способ дать деньги старым переездам.

    🔴 Это НЕ `assign_vehicle_transfer`: статус НЕ меняется, строка в
    `stock_transfer_status_history` НЕ пишется (перехода-то нет), сток НЕ
    двигается. Смысл вызова один — родить забор (`OutboundShipment`), носитель
    денег: через него переезд попадает в «Оплаты», в сверку счетов ФФ и в отчёт
    «Логистика переездов». TR-1…TR-31 уехали до появления машины на перемещении:
    у них пусто и counterparty_id, и pickup_cost, заборов ноль, и оживить их
    иначе нечем — назначение машины из этих статусов таблица переходов запрещает
    (и правильно: снимок логистики уже уехал бы в забор).

    `vehicle_assigned_at` ставится только если пуст — у уехавшего переезда это
    исторический момент, а не «когда мы про него вспомнили».
    """
    transfer = await _apply_retro_logistics(db, project_id, transfer_id, payload)
    number = transfer.number  # после rollback атрибуты протухнут (lazy-load в async = взрыв)
    try:
        await db.commit()
    except IntegrityError as e:
        # Тот же партиальный уникальный, что и у отправки: два параллельных
        # ретро-оформления одного переезда читают «забора нет» до коммита
        # друг друга.
        await db.rollback()
        raise ValueError(f"Забор переезда {number} уже создан — обновите страницу") from e
    # Забор появился/изменился — сверка счетов ФФ читает отгрузки склада по
    # перевозчику (iron rule 7). Отчётные кэши стока НЕ трогаем: движений нет.
    await invalidate_cache("ff_billing:invoices")
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def set_transfer_logistics_bulk(
    db: AsyncSession, project_id: int, ids: list[int], payload: TransferAssignVehicle
) -> list[StockTransfer]:
    """Одна машина на N уехавших переездов — оптовое оживление старых записей.

    Атомарен ровно как `assign_vehicle_transfer_bulk`: одна транзакция на весь
    список, отказ на любом id откатывает ВСЁ (включая контрагента, которого
    `_resolve_carrier` мог создать под этот батч). Дубли id схлопываются с
    сохранением порядка — он определяет, на каком элементе вызов упадёт.
    """
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        return []
    transfers: list[StockTransfer] = []
    try:
        for tid in unique_ids:
            transfers.append(await _apply_retro_logistics(db, project_id, tid, payload))
    except ValueError as e:
        await db.rollback()
        raise ValueError(f"Переезд #{tid}: {e} Ничего не записано — вызов атомарный.") from None
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(
            "Забор одного из переездов уже создан — обновите страницу. "
            "Ничего не записано — вызов атомарный."
        ) from e
    await invalidate_cache("ff_billing:invoices")
    for transfer in transfers:
        await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, transfers)
    return transfers


# ─── Переезд: ручные ступени (переезд без связки с ФФ) ─────────────────────
#
# Переезд между складами БЕЗ интеграции (транзитный склад, свой склад) синк
# никуда не двигает — ступени проставляет человек этими тремя ручками. Зеркало
# `assembly.status.mark_ready` / `return_to_warehouse` / `close_request`.


async def mark_transfer_ready(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    changed_by: str = "user",
) -> StockTransfer:
    """PENDING | IN_PROGRESS → READY: груз собран, можно сажать на машину.

    В отличие от `assembly.status.mark_ready` НЕ требует ни поставки WB, ни
    заполненных паллет: переезд едет между нашими складами, поставки у него нет,
    а транспортная единица нужна только для стоимости забора (её проставляют
    вместе с машиной). Сток не двигается.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    current = TransferStatus(transfer.status)
    _check_transfer_transition(current, TransferStatus.READY)
    if not transfer.items:
        raise ValueError("Состав переезда пуст — отмечать готовым нечего")

    transfer.status = TransferStatus.READY
    transfer.actual_ready_date = date.today()
    _log_transfer_status_change(
        db, project_id, transfer.id, current.value, TransferStatus.READY.value, changed_by=changed_by
    )
    await db.commit()
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def return_transfer(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    changed_by: str = "user",
    comment: str | None = None,
) -> StockTransfer:
    """SHIPPED | DELIVERED → RETURNED: получатель не принял, груз вернулся на ИСТОЧНИК.

    Зеркало `assembly.status.return_to_warehouse` с поправкой на то, что у
    переезда получатель — ТОЖЕ наш склад:

      - на склад-ИСТОЧНИК товар возвращается приёмкой `InboundReceipt(ACCEPTED)`
        на ВЕСЬ план (движения `INBOUND` / `reference_type='RECEIPT'`) — тот же
        механизм, что у возврата заявки, поэтому возврат виден в приёмках склада;
      - у ПОЛУЧАТЕЛЯ снимается транзит, а из DELIVERED (и из частично принятого
        SHIPPED) дополнительно СПИСЫВАЕТСЯ уже зачисленное — иначе те же
        единицы лежали бы разом на обоих складах;
      - забор переезда (`OutboundShipment`) НЕ удаляется: перевозка состоялась и
        оплачена, история отгрузок и лист оплаты обязаны её помнить.

    Из RETURNED переезд либо переотправляют (→ READY), либо закрывают (→ CLOSED).
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    current = TransferStatus(transfer.status)
    _check_transfer_transition(current, TransferStatus.RETURNED)
    if not transfer.items:
        raise ValueError("Нет позиций для возврата")

    is_defect = transfer.is_defect
    # План по номенклатуре (дубли строк схлопываем — иначе каждая получила бы
    # полный откат) и уже зачисленное получателю по журналу движений.
    plan_map: dict[int, tuple[str, int]] = {}
    for item in transfer.items:
        bc, qty = plan_map.get(item.nomenclature_id, (item.barcode, 0))
        plan_map[item.nomenclature_id] = (bc, qty + item.quantity)
    received_map = await _transfer_received_map(db, project_id, transfer)

    # 1. Откат у ПОЛУЧАТЕЛЯ: снимаем зачисленное и гасим остаток транзита.
    #    reference_type='TRANSFER_RETURN', а не 'TRANSFER': производная «сколько
    #    уже принято» (`_transfer_received_map`) обязана остаться честной — она
    #    суммирует ТОЛЬКО приходные движения переезда.
    for nomenclature_id, (barcode, plan_qty) in plan_map.items():
        taken = min(received_map.get(nomenclature_id, 0), plan_qty)
        if taken > 0:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=nomenclature_id,
                barcode=barcode,
                delta=0 if is_defect else -taken,
                defect_delta=-taken if is_defect else 0,
                movement_type=(
                    MovementType.DEFECT_TRANSFER_OUT if is_defect else MovementType.TRANSFER_OUT
                ),
                reference_type="TRANSFER_RETURN",
                reference_id=transfer.id,
                comment=f"Возврат переезда {transfer.number}",
            )
        target_stock = (
            await db.execute(
                select(WarehouseStock).where(
                    WarehouseStock.project_id == project_id,
                    WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                    WarehouseStock.nomenclature_id == nomenclature_id,
                )
            )
        ).scalar_one_or_none()
        if target_stock is not None:
            rest = max(plan_qty - taken, 0)
            if is_defect:
                target_stock.defect_in_transit = max(0, target_stock.defect_in_transit - rest)
            else:
                target_stock.in_transit = max(0, target_stock.in_transit - rest)
            target_stock.updated_at = utcnow()

    # 2. Приёмка-возврат на складе-ИСТОЧНИКЕ (сразу ACCEPTED) + приход стока.
    receipt_number = await _next_number(db, project_id, "IN", InboundReceipt)
    auto_comment = f"Возврат переезда {transfer.number} (получатель не принял)"
    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=transfer.from_warehouse_id,
        number=receipt_number,
        status=InboundStatus.ACCEPTED,
        planned_date=date.today(),
        actual_date=date.today(),
        comment=f"{auto_comment}. {comment}" if comment else auto_comment,
        is_defect=is_defect,
        defect_reason=transfer.defect_reason if is_defect else None,
    )
    db.add(receipt)
    await db.flush()

    for nomenclature_id, (barcode, plan_qty) in plan_map.items():
        db.add(
            InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=nomenclature_id,
                barcode=barcode,
                expected_qty=plan_qty,
                actual_qty=0 if is_defect else plan_qty,
                defect_qty=plan_qty if is_defect else 0,
            )
        )
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=transfer.from_warehouse_id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            delta=0 if is_defect else plan_qty,
            defect_delta=plan_qty if is_defect else 0,
            movement_type=MovementType.DEFECT_RECEIVE if is_defect else MovementType.INBOUND,
            reference_type="RECEIPT",
            reference_id=receipt.id,
            comment=auto_comment,
        )

    transfer.status = TransferStatus.RETURNED
    # Зеркало `return_to_warehouse`: снимок попытки живёт в заборе
    # (`OutboundShipment`), а на переезде веху отгрузки чистим — из RETURNED
    # переезд отправляют ЗАНОВО, и `shipped_at` будет уже у новой попытки.
    # `actual_ready_date` не трогаем: груз собран, собирать заново не нужно.
    transfer.shipped_at = None
    _log_transfer_status_change(
        db,
        project_id,
        transfer.id,
        current.value,
        TransferStatus.RETURNED.value,
        changed_by=changed_by,
        comment=comment or f"Возврат на склад забора (приёмка {receipt_number})",
    )
    await db.commit()
    # Сток вернулся на источник и ушёл с получателя — отчёты обязаны увидеть это
    # сразу (iron rule 7). assembly_link_anomalies — из-за связок ФФ переезда.
    await asyncio.gather(
        invalidate_cache("reports:balance"),
        invalidate_cache("reports:warehouse_need"),
        invalidate_cache("reports:assembly_link_anomalies"),
    )
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def close_transfer(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    changed_by: str = "user",
    comment: str | None = None,
) -> StockTransfer:
    """RETURNED | DELIVERED → CLOSED: терминальное закрытие переезда.

    Сток уже там, где должен быть (вернулся на источник при RETURNED либо
    зачислен получателю при DELIVERED) — здесь только статус. История забора и
    движений сохраняется.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Перемещение не найдено")
    current = TransferStatus(transfer.status)
    _check_transfer_transition(current, TransferStatus.CLOSED)

    transfer.status = TransferStatus.CLOSED
    _log_transfer_status_change(
        db,
        project_id,
        transfer.id,
        current.value,
        TransferStatus.CLOSED.value,
        changed_by=changed_by,
        comment=comment,
    )
    await db.commit()
    await db.refresh(transfer, ["items"])
    await _attach_transfer_labels(db, project_id, [transfer])
    return transfer


async def _assembly_net_stock_effect(
    db: AsyncSession, project_id: int, assembly_id: int
) -> dict[int, int]:
    """{warehouse_id: нетто-эффект заявки на сток} по журналу движений.

    Складываем ВСЕ движения, порождённые заявкой:
      - списание отгрузки  — `reference_type='ASSEMBLY'`   (отрицательные);
      - откат отмены       — `reference_type='ASSEMBLY_CANCEL'` (положительные);
      - возврат «WB не принял» — движения приёмок, привязанных к заявке
        (`inbound_receipts.assembly_request_id`), `reference_type='RECEIPT'`.

    Считаем `quantity + defect_delta`: вопрос «вернулись ли единицы на склад»,
    а в каком ведре они лежат (годное/брак) — вопрос уже следующий (перемещение
    годного стока при вернувшемся браке честно упрётся в проверку остатка).

    Приёмки берём БЕЗ фильтра `is_deleted`: источник истины здесь — журнал
    движений, а не карточка документа. Отмена приёмки (`cancel_receipt`) пишет
    компенсирующее движение с тем же `reference_id`, поэтому пара «приход +
    отмена» сама схлопывается в ноль; выкинув удалённые приёмки, мы бы посчитали
    отменённый возврат как состоявшийся.
    """
    receipt_ids = (
        select(InboundReceipt.id)
        .where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.assembly_request_id == assembly_id,
        )
        .scalar_subquery()
    )
    rows = await db.execute(
        select(
            StockMovement.warehouse_id,
            func.coalesce(func.sum(StockMovement.quantity + StockMovement.defect_delta), 0),
        )
        .where(
            StockMovement.project_id == project_id,
            or_(
                and_(
                    StockMovement.reference_type.in_(("ASSEMBLY", "ASSEMBLY_CANCEL")),
                    StockMovement.reference_id == assembly_id,
                ),
                and_(
                    StockMovement.reference_type == "RECEIPT",
                    StockMovement.reference_id.in_(receipt_ids),
                ),
            ),
        )
        .group_by(StockMovement.warehouse_id)
    )
    return {int(wh_id): int(total or 0) for wh_id, total in rows.all()}


async def convert_assembly_to_transfer(
    db: AsyncSession,
    project_id: int,
    assembly_request_id: int,
    payload: AssemblyToTransfer,
) -> AssemblyToTransferResult:
    """«Переделать заявку в перемещение» — создать переезд по составу заявки.

    Кейс: ФФ собрал заявку, но товар едет не на WB, а на наш транзитный склад
    (в т.ч. после «WB не принял» — ASM-807 → возврат IN-232 → переезд).

    🔴 ГАРД ДВОЙНОГО СПИСАНИЯ. Отправка перемещения спишет состав со склада-
    источника ещё раз. Поэтому конвертация разрешена, только если НЕТТО-эффект
    заявки на сток этого склада равен нулю: либо она никогда не отгружалась,
    либо списание компенсировано возвратом/отменой. Ненулевой нетто = единицы
    физически списаны и не вернулись → отказ.

    🔴 СУДЬБА ЗАЯВКИ ЗАВИСИТ ОТ СТАТУСА.
    Из НЕтерминального (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED — главный
    сценарий фичи «ФФ собрал, но везём не на WB») заявка ОТМЕНЯЕТСЯ с записью в
    историю. Оставить её живой нельзя по двум причинам:
      1. `_get_reserved_map` держит резерв по этим статусам. После отправки
         переезда физический сток упал на N, а резерв на те же N остался бы →
         `available = quantity − reserved` занижен вдвое в предброни и потребности.
      2. Живую заявку логист может «Отгрузить» — `ship_request` спишет те же
         единицы ВТОРОЙ раз, и `_validate_stock_for_ship` это не поймает, если на
         складе есть чужой остаток.
    Из терминального (CLOSED/RETURNED/CANCELLED) статус НЕ меняется: резерва там
    нет, отгрузить нельзя, а история заявки должна остаться честной.
    """
    from backend.services.assembly.status import _deny_fbs_manual, _log_status_change

    assembly = (
        await db.execute(
            select(AssemblyRequest)
            .options(selectinload(AssemblyRequest.items))
            .where(
                AssemblyRequest.id == assembly_request_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
            )
            # row-lock: сериализует конвертацию с параллельным ship/cancel по этой
            # же заявке — иначе оба прочитали бы «нетто 0» и списали единицы дважды.
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not assembly:
        raise ValueError("Заявка на сборку не найдена")
    # Учётное зеркало FBS ведёт джоб, и списание оно пишет движениями
    # `reference_type='FBS_ORDER'` — их `_assembly_net_stock_effect` не видит,
    # поэтому нетто у FBS ВСЕГДА 0 и гард двойного списания на нём слеп.
    # Запрещаем целиком (канон: жёсткая гарантия на бэке, фронт лишь дублирует).
    _deny_fbs_manual(assembly)
    if not assembly.items:
        raise ValueError("В заявке нет позиций — переезд создавать не из чего")

    to_wh = await get_warehouse(db, project_id, payload.to_warehouse_id)
    if not to_wh:
        raise ValueError("Склад назначения не найден")
    if to_wh.id == assembly.warehouse_id:
        raise ValueError("Склад назначения совпадает со складом заявки")

    # Повторная конвертация той же заявки — почти всегда двойной клик. Два
    # переезда по одному составу списали бы склад дважды (уже реальным стоком).
    existing = (
        await db.execute(
            select(StockTransfer.number)
            .where(
                StockTransfer.project_id == project_id,
                StockTransfer.converted_from_assembly_id == assembly.id,
                StockTransfer.is_deleted == False,  # noqa: E712
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        raise ValueError(f"Заявка {assembly.number} уже переделана в перемещение {existing}")

    net_by_wh = await _assembly_net_stock_effect(db, project_id, assembly.id)
    src_net = net_by_wh.get(assembly.warehouse_id, 0)
    if src_net < 0:
        total_net = sum(net_by_wh.values())
        msg = (
            f"Заявка {assembly.number}: со склада списано {abs(src_net)} ед. и товар не вернулся — "
            f"перемещение списало бы те же единицы второй раз."
        )
        if total_net == 0:
            msg += " Возврат по заявке оформлен на ДРУГОЙ склад — перемещайте оттуда."
        else:
            msg += " Сначала оформите возврат на склад заявки."
        raise ValueError(msg)

    # Готовность НАСЛЕДУЕТСЯ. Если ФФ заявку уже собрал (READY и дальше), гонять
    # переезд по ступеням «собирается → собран» заново бессмысленно: груз стоит
    # на складе упакованным, человеку остаётся посадить его на машину. Кейс
    # юзера: ASM-726/727 в READY. Из PENDING/IN_PROGRESS готовность не выдумываем.
    number = await _next_number(db, project_id, "TR", StockTransfer)
    inherited_ready = AssemblyStatus(assembly.status) in _ASSEMBLY_READY_FOR_TRANSFER
    transfer = StockTransfer(
        project_id=project_id,
        from_warehouse_id=assembly.warehouse_id,
        to_warehouse_id=to_wh.id,
        number=number,
        status=TransferStatus.READY if inherited_ready else TransferStatus.PENDING,
        # Дата готовности наследуется вместе со статусом: груз собран тогда, когда
        # его собрал ФФ по заявке, а не в момент, когда логист нажал «переделать».
        # Фолбэк на сегодня — для терминальных заявок без проставленной даты.
        actual_ready_date=(
            (assembly.actual_ready_date or date.today()) if inherited_ready else None
        ),
        comment=payload.comment or f"Переезд из заявки {assembly.number}",
        converted_from_assembly_id=assembly.id,
        # Транспортная единица наследуется от заявки: она уже посчитана логистом
        # (паллеты/короба и вес одной единицы), и терять её при конвертации
        # нельзя — иначе забор уедет без основания для стоимости и назначения
        # платежа, а ₽/паллета переезда не посчитается.
        pallets_count=assembly.pallets_count,
        pallet_weight_kg=assembly.pallet_weight_kg,
        shipped_as_boxes=assembly.shipped_as_boxes,
    )
    db.add(transfer)
    await db.flush()
    _log_transfer_status_change(
        db,
        project_id,
        transfer.id,
        None,
        transfer.status,
        changed_by="system",
        comment=f"Создан из заявки {assembly.number}",
    )

    units_total = 0
    for item in assembly.items:
        db.add(
            StockTransferItem(
                project_id=project_id,
                transfer_id=transfer.id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                quantity=item.quantity,
            )
        )
        units_total += item.quantity

    # Зеркала ФФ. По умолчанию остаются на заявке (её история), на переезд
    # вяжутся свежие заявки провайдера с обеих сторон. move_ff_links=True —
    # когда ФФ собрал ровно этот переезд под видом сборки и заводить новую
    # заявку у провайдера никто не будет.
    ff_links_moved = 0
    if payload.move_ff_links:
        from backend.models.fulfillment import FulfillmentRequest

        mirrors = (
            (
                await db.execute(
                    select(FulfillmentRequest)
                    .where(
                        FulfillmentRequest.project_id == project_id,
                        FulfillmentRequest.assembly_request_id == assembly.id,
                    )
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
        for mirror in mirrors:
            mirror.assembly_request_id = None
            mirror.stock_transfer_id = transfer.id
            ff_links_moved += 1

    # Отмена активной заявки — см. блок 🔴 в докстринге. Терминальные не трогаем.
    cancelled = AssemblyStatus(assembly.status) not in _TERMINAL_ASSEMBLY_STATUSES
    if cancelled:
        old_status = assembly.status
        assembly.status = AssemblyStatus.CANCELLED
        await _log_status_change(
            db,
            project_id,
            assembly.id,
            old_status,
            AssemblyStatus.CANCELLED,
            changed_by="system",
            comment=f"Переделана в перемещение {transfer.number}",
        )

    try:
        await db.commit()
    except IntegrityError as e:
        # Партиальный уникальный индекс uq_stock_transfers_converted_from_assembly
        # (миграция trv03) ловит гонку двойного клика, которую SELECT выше не видит:
        # обе транзакции читают «переезда нет» до коммита друг друга.
        await db.rollback()
        raise ValueError(
            f"Заявка {assembly.number} уже переделана в перемещение"
        ) from e

    # Отменённая заявка сняла резерв и ушла из воронки сборок — эти отчёты
    # обязаны увидеть это сразу. `assembly_link_anomalies` сбрасываем только
    # когда связки ФФ реально переехали: сам по себе новый черновик переезда
    # эту вкладку не меняет.
    if cancelled:
        await invalidate_cache("reports:assembly_flow")
        await invalidate_cache("reports:warehouse_need")
    if ff_links_moved:
        await invalidate_cache("reports:assembly_link_anomalies")
    return AssemblyToTransferResult(
        transfer_id=transfer.id,
        transfer_number=transfer.number,
        assembly_number=assembly.number,
        items_count=len(assembly.items),
        units_total=units_total,
        ff_links_moved=ff_links_moved,
        assembly_cancelled=cancelled,
    )
