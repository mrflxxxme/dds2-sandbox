# ruff: noqa: RUF001, RUF002, RUF003
"""
Учётное зеркало сборки FBS: заявки `AssemblyRequest(kind=fbs)` из поставок FBS.

Для складов, где WMS провайдера САМ заводит свои заявки по FBS-заказам WB
(`WbFbsWarehouse.auto_assembly = true`), джоб зеркалит каждую поставку FBS
(`WB-GI-…`) учётной заявкой на сборку: одна поставка — максимум одна заявка
(partial unique `(project_id, fbs_supply_id)`), позиции — агрегат живых заданий
поставки по номенклатуре, статусы двигаются по фазе поставки/заданий.

ЖЕЛЕЗНЫЕ ИНВАРИАНТЫ (нарушение = двойной учёт денег/остатков):
  • kind=fbs НЕ резервирует сток — резерв уже держат открытые задания через
    `warehouse_stock_engine.get_open_fbs_reserved` (все читатели резерва
    исключают kind=fbs фильтром `AssemblyRequest.kind != 'fbs'`);
  • kind=fbs НЕ списывает сток НИКОГДА — списание делает ТОЛЬКО
    `orders_service.writeoff_completed_orders` по `complete` (идемпотентно).
    Переход в SHIPPED здесь — прямое обновление статуса + history, БЕЗ
    ship_request, БЕЗ OutboundShipment, БЕЗ StockMovement;
  • задания связаны с заявкой транзитивно через
    `wb_fbs_orders.supply_id == fbs_supply_id` — своего FK нет.

Статусы (идемпотентный пересчёт на каждом тике, только ВПЕРЁД по цепочке
IN_PROGRESS → SHIPPED → DELIVERED, назад никогда; CANCELLED терминален):
  • поставка активна (`done=false`) → IN_PROGRESS;
  • `done=true` (передана) → SHIPPED, `shipped_at = scan_dt | closed_at`;
  • все живые задания прошли СЦ (`wb_status` ∈ SORTED ∪ DELIVERED) → DELIVERED
    (для FBS «WB принял» = задания отсортированы/вручены, аналог приёмки FBO);
  • `reject_dt` поставки ИЛИ все задания отменены (effective cancel) → CANCELLED.

Состав пересобирается, пока заявка IN_PROGRESS (доложили/отменили задания до
передачи); после SHIPPED состав заморожен.
"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import invalidate_cache
from backend.models.assembly import (
    AssemblyKind,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.warehouse import Warehouse
from backend.models.wb_fbs import (
    FBS_TERMINAL_STATUSES,
    FBS_WB_CANCELLED_STATUSES,
    FBS_WB_DELIVERED_STATUSES,
    FBS_WB_SORTED_STATUSES,
    WbFbsOrder,
    WbFbsSupply,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.services.warehouse_stock_engine import _next_number
from backend.services.wb_fbs.contour import contour_condition, is_sandbox_contour
from backend.services.wb_fbs.locks import (
    MIRROR_LOCK_NAME,
    MIRROR_LOCK_TTL_SEC,
    acquire_lock,
    release_lock,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.wb_fbs.assembly_mirror")

#: Окно зеркалирования: поставки старше не трогаем на СОЗДАНИЕ (старьё, жившее
#: до включения auto_assembly, не зеркалим задним числом — историю уже ведёт
#: сам ФФ). Статусы УЖЕ созданных зеркал догоняются и после выхода из окна.
MIRROR_WINDOW_DAYS = 14

#: `changed_by` строк истории статусов, которые пишет зеркало.
MIRROR_CHANGED_BY = "fbs_mirror"

#: Страховочные капы выборок (см. anti-pattern «.all() без limit»). Кап
#: заданий — АВАРИЙНЫЙ: частичный набор заданий поставки дал бы ложный
#: DELIVERED (терминал!) и занижённый состав, поэтому при его достижении
#: прогон прерывается с ERROR, а не молча усекается.
_SUPPLIES_CAP = 2000
_MIRRORS_CAP = 5000
_ORDERS_CAP = 50_000

#: Потолок догона статусов: зеркало, не дошедшее до терминала за N дней, —
#: это застывший wb_status задания (синк опрашивает не всё, см.
#: `orders_service._TRANSIT_STUCK_MAX_DAYS`). Без потолка такие зеркала и их
#: поставки перечитывались бы каждые 5 минут вечно, монотонно раздувая прогон.
_CATCHUP_MAX_DAYS = 30

#: Позиция в цепочке «только вперёд». CANCELLED вне цепочки — терминален.
_STATUS_RANK: dict[str, int] = {
    AssemblyStatus.IN_PROGRESS.value: 1,
    AssemblyStatus.SHIPPED.value: 2,
    AssemblyStatus.DELIVERED.value: 3,
}

_TERMINAL = {AssemblyStatus.DELIVERED.value, AssemblyStatus.CANCELLED.value}

#: Живое задание считается «прошло СЦ»: sorted/ready_for_pickup/postponed +
#: sold/defect. Для FBS это аналог «WB принял поставку» (DELIVERED зеркала).
_DONE_WB_STATUSES = frozenset(FBS_WB_SORTED_STATUSES) | frozenset(FBS_WB_DELIVERED_STATUSES)


def _order_cancelled(supplier_status: str | None, wb_status: str | None) -> bool:
    """Effective cancel задания: отменено продавцом ИЛИ на стороне WB.

    Python-зеркало `orders_service.effective_status_expr()` для этих строк:
    WB-отмена (`declined_by_client` и т.п.) гасит задание, даже когда
    `supplierStatus` навсегда застыл в `new`.
    """
    if supplier_status in FBS_TERMINAL_STATUSES:
        return True
    return wb_status in FBS_WB_CANCELLED_STATUSES and supplier_status not in FBS_TERMINAL_STATUSES


def _target_status(supply: WbFbsSupply, orders: list[Any]) -> str:
    """Фаза поставки → целевой статус зеркала (без учёта «только вперёд»)."""
    alive = [o for o in orders if not _order_cancelled(o.supplier_status, o.wb_status)]
    if supply.reject_dt is not None or (orders and not alive):
        return AssemblyStatus.CANCELLED.value
    # DELIVERED требует и done: задания не могут пройти СЦ до передачи поставки,
    # а рассинхрон зеркала (`done` отстал) не должен прыгать через SHIPPED.
    if supply.done and alive and all((o.wb_status or "") in _DONE_WB_STATUSES for o in alive):
        return AssemblyStatus.DELIVERED.value
    if supply.done:
        return AssemblyStatus.SHIPPED.value
    return AssemblyStatus.IN_PROGRESS.value


def _aggregate_items(orders: list[Any]) -> tuple[dict[int, dict[str, Any]], int]:
    """Живые задания → {nomenclature_id: {barcode, qty}} + счётчик пропущенных.

    Одно задание WB = одна единица товара. Задания без карточки
    (`nomenclature_id IS NULL`) в позиции не попадают — считаем в лог.
    """
    agg: dict[int, dict[str, Any]] = {}
    skipped = 0
    for o in orders:
        if _order_cancelled(o.supplier_status, o.wb_status):
            continue
        if o.nomenclature_id is None:
            skipped += 1
            continue
        entry = agg.setdefault(int(o.nomenclature_id), {"barcode": o.barcode or "", "qty": 0})
        entry["qty"] += 1
        if not entry["barcode"] and o.barcode:
            entry["barcode"] = o.barcode
    return agg, skipped


def _supply_seller_warehouse(orders: list[Any]) -> int | None:
    """Склад продавца поставки — из заданий (у payload'а поставки его нет)."""
    for o in orders:
        if o.wb_warehouse_id is not None:
            return int(o.wb_warehouse_id)
    return None


async def _load_targets(db: AsyncSession, project_id: int) -> dict[int, tuple[int, str | None]]:
    """Целевые склады: {wb_warehouse_id: (наш warehouse_id, имя склада продавца)}.

    Цель = склад продавца с `auto_assembly=true` И живой активной привязкой;
    наш склад заявки — ПЕРВЫЙ привязанный живой (мин. id — тот же порядок, что
    в `warehouse_service.get_linked_warehouse_ids`). Один батч-запрос, без N+1.
    """
    rows = (
        await db.execute(
            select(
                WbFbsWarehouse.wb_warehouse_id,
                WbFbsWarehouse.name,
                WbFbsWarehouseLink.warehouse_id,
            )
            .join(
                WbFbsWarehouseLink,
                (WbFbsWarehouseLink.project_id == WbFbsWarehouse.project_id)
                & (WbFbsWarehouseLink.wb_warehouse_id == WbFbsWarehouse.wb_warehouse_id),
            )
            .join(Warehouse, Warehouse.id == WbFbsWarehouseLink.warehouse_id)
            .where(
                WbFbsWarehouse.project_id == project_id,
                WbFbsWarehouse.auto_assembly == True,  # noqa: E712 — SQLAlchemy expression
                WbFbsWarehouseLink.is_active == True,  # noqa: E712 — SQLAlchemy expression
                Warehouse.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
            )
            .order_by(WbFbsWarehouse.wb_warehouse_id, WbFbsWarehouseLink.warehouse_id)
            .limit(500)
        )
    ).all()
    targets: dict[int, tuple[int, str | None]] = {}
    for wb_id, name, our_id in rows:
        targets.setdefault(int(wb_id), (int(our_id), name))  # первый = мин. warehouse_id
    return targets


async def sync_fbs_assembly_mirror(db: AsyncSession, project_id: int) -> int:
    """Синк учётного зеркала сборки FBS проекта. Возвращает число мутаций.

    Идемпотентен: создание защищено partial unique `(project_id, fbs_supply_id)`
    (гонка с соседним прогоном → IntegrityError → пропуск), статусы двигаются
    только вперёд, повторный тик без изменений — no-op.

    Песочница: у `assembly_requests` нет метки контура, а зеркало пишет в ОБЩИЙ
    реестр заявок (номера ASM, списки, слоты) — в sandbox не работаем ВООБЩЕ
    (ранний выход, канон `writeoff_completed_orders`). `contour_condition` в
    выборках при этом остаётся: он отсекает sandbox-строки в боевом контуре.

    Гонка расписаний: джоб статусов (5 мин) и джоб поставок (15 мин) зовут синк
    из одного worker'а и раз в 15 минут пересекаются — Redis-лок (как у
    трансляции/списания), занят → сосед уже делает эту работу.
    """
    if is_sandbox_contour():
        return 0
    token = await acquire_lock(MIRROR_LOCK_NAME, project_id, ttl=MIRROR_LOCK_TTL_SEC)
    if token is None:
        logger.info("fbs_mirror: project=%s пропуск — синк уже идёт", project_id)
        return 0
    try:
        return await _sync_locked(db, project_id)
    finally:
        await release_lock(MIRROR_LOCK_NAME, project_id, token)


async def _sync_locked(db: AsyncSession, project_id: int) -> int:
    """Тело синка под локом (см. `sync_fbs_assembly_mirror`)."""
    targets = await _load_targets(db, project_id)
    if not targets:
        return 0

    cutoff = utcnow() - timedelta(days=MIRROR_WINDOW_DAYS)

    # 1. Кандидаты на создание: боевые поставки в окне.
    window_supplies = list(
        (
            await db.execute(
                select(WbFbsSupply)
                .where(
                    WbFbsSupply.project_id == project_id,
                    contour_condition(WbFbsSupply.raw),
                    WbFbsSupply.created_at_wb >= cutoff,
                )
                .order_by(WbFbsSupply.created_at_wb.desc())
                .limit(_SUPPLIES_CAP)
            )
        )
        .scalars()
        .all()
    )
    supplies_by_id: dict[str, WbFbsSupply] = {s.wb_supply_id: s for s in window_supplies}

    # 2. Существующие зеркала: для дедупа (включая soft-deleted — partial unique
    #    их НЕ освобождает) и для догона статусов (не-терминальные, даже когда
    #    их поставка уже выпала из окна создания).
    mirrors = list(
        (
            await db.execute(
                select(AssemblyRequest)
                .options(selectinload(AssemblyRequest.items))
                .where(
                    AssemblyRequest.project_id == project_id,
                    AssemblyRequest.kind == AssemblyKind.FBS.value,
                    AssemblyRequest.fbs_supply_id.is_not(None),
                    or_(
                        AssemblyRequest.fbs_supply_id.in_(list(supplies_by_id) or [""]),
                        # Догон статусов — с потолком возраста: зеркало, не
                        # дошедшее до терминала за N дней, застыло навсегда
                        # (см. _CATCHUP_MAX_DAYS) и не должно вечно раздувать
                        # каждый прогон.
                        and_(
                            AssemblyRequest.status.notin_(list(_TERMINAL)),
                            AssemblyRequest.created_at >= utcnow() - timedelta(days=_CATCHUP_MAX_DAYS),
                        ),
                    ),
                )
                .limit(_MIRRORS_CAP)
            )
        )
        .scalars()
        .all()
    )
    mirrors_by_supply: dict[str, AssemblyRequest] = {}
    for m in mirrors:
        if m.fbs_supply_id:
            mirrors_by_supply.setdefault(m.fbs_supply_id, m)

    # Поставки живых зеркал вне окна — добираем точечно (статусы должны догонять).
    missing_ids = [sid for sid in mirrors_by_supply if sid not in supplies_by_id]
    if missing_ids:
        extra = (
            await db.execute(
                select(WbFbsSupply)
                .where(
                    WbFbsSupply.project_id == project_id,
                    WbFbsSupply.wb_supply_id.in_(missing_ids),
                    contour_condition(WbFbsSupply.raw),
                )
                .limit(_MIRRORS_CAP)
            )
        ).scalars()
        for s in extra:
            supplies_by_id[s.wb_supply_id] = s

    if not supplies_by_id:
        return 0

    # 3. Задания всех затронутых поставок — одним запросом (боевой контур).
    order_rows = (
        await db.execute(
            select(
                WbFbsOrder.supply_id,
                WbFbsOrder.wb_warehouse_id,
                WbFbsOrder.nomenclature_id,
                WbFbsOrder.barcode,
                WbFbsOrder.supplier_status,
                WbFbsOrder.wb_status,
            )
            .where(
                WbFbsOrder.project_id == project_id,
                WbFbsOrder.supply_id.in_(list(supplies_by_id)),
                contour_condition(WbFbsOrder.raw),
            )
            .limit(_ORDERS_CAP)
        )
    ).all()
    if len(order_rows) >= _ORDERS_CAP:
        # Частичный набор заданий = ложные статусы (DELIVERED — терминал!) и
        # занижённый состав. Лучше пропустить тик с ERROR, чем молча соврать.
        logger.error(
            "fbs_mirror: project=%s кап заданий %s достигнут — прогон прерван, "
            "сузьте окно/капы",
            project_id,
            _ORDERS_CAP,
        )
        return 0
    orders_by_supply: dict[str, list[Any]] = {}
    for row in order_rows:
        orders_by_supply.setdefault(row.supply_id, []).append(row)

    created = 0
    changed = 0
    skipped_no_card_total = 0
    created_supply_ids: set[str] = set()

    # ─── Создание зеркал для поставок окна без заявки ────────────────────────
    for supply in window_supplies:
        if supply.wb_supply_id in mirrors_by_supply:
            continue
        orders = orders_by_supply.get(supply.wb_supply_id, [])
        seller_wh = _supply_seller_warehouse(orders)
        if seller_wh is None or seller_wh not in targets:
            continue  # поставка не с целевого склада (или задания ещё не синкнулись)
        target = _target_status(supply, orders)
        if target == AssemblyStatus.CANCELLED.value:
            # Заводить зеркало сразу отменённым — шум без учётной ценности.
            continue
        items_agg, skipped_no_card = _aggregate_items(orders)
        skipped_no_card_total += skipped_no_card
        if not items_agg:
            continue  # нечего учитывать (нет живых заданий с карточкой)

        our_warehouse_id, seller_name = targets[seller_wh]
        number = await _next_number(db, project_id, "ASM", AssemblyRequest)
        shipped_at = None
        if target in (AssemblyStatus.SHIPPED.value, AssemblyStatus.DELIVERED.value):
            shipped_at = supply.scan_dt or supply.closed_at or utcnow()
        req = AssemblyRequest(
            project_id=project_id,
            warehouse_id=our_warehouse_id,
            number=number,
            status=target,
            kind=AssemblyKind.FBS.value,
            fbs_supply_id=supply.wb_supply_id,
            pallets_count=0,
            pallet_weight_kg=0,
            shipped_at=shipped_at,
            wb_warehouse_name_manual=seller_name,
        )
        try:
            # Savepoint: гонка с соседним прогоном (partial unique по
            # (project_id, fbs_supply_id)) не должна ронять весь батч.
            async with db.begin_nested():
                db.add(req)
                await db.flush()
                for nom_id, entry in items_agg.items():
                    db.add(
                        AssemblyRequestItem(
                            project_id=project_id,
                            assembly_request_id=req.id,
                            nomenclature_id=nom_id,
                            barcode=entry["barcode"],
                            quantity=entry["qty"],
                        )
                    )
                db.add(
                    AssemblyStatusHistory(
                        project_id=project_id,
                        assembly_request_id=req.id,
                        old_status=None,
                        new_status=target,
                        changed_at=utcnow(),
                        changed_by=MIRROR_CHANGED_BY,
                        comment=f"Зеркало поставки FBS {supply.wb_supply_id}",
                    )
                )
        except IntegrityError:
            logger.info(
                "fbs_mirror: зеркало %s уже создано соседним прогоном (project=%s)",
                supply.wb_supply_id,
                project_id,
            )
            continue
        mirrors_by_supply[supply.wb_supply_id] = req
        created_supply_ids.add(supply.wb_supply_id)
        created += 1

    # ─── Догон статусов и состава существующих зеркал ────────────────────────
    for supply_id, mirror in mirrors_by_supply.items():
        if supply_id in created_supply_ids:
            continue  # только что созданные уже в актуальной фазе
        if mirror.is_deleted:
            continue  # tombstone: partial unique держит слот, но зеркало мертво
        sup = supplies_by_id.get(supply_id)
        if sup is None:
            continue  # поставка пропала из зеркала/контура — статус не трогаем
        current = str(mirror.status)
        if current in _TERMINAL:
            continue
        if current not in _STATUS_RANK:
            # Статус вне цепочки зеркала (PENDING/READY/… — руками он недостижим,
            # но ранг 0 молча утащил бы заявку НАЗАД в IN_PROGRESS). Не трогаем.
            logger.warning(
                "fbs_mirror: project=%s зеркало %s в неожиданном статусе %s — пропуск",
                project_id,
                mirror.number,
                current,
            )
            continue
        orders = orders_by_supply.get(supply_id, [])

        # Пересборка состава — только пока IN_PROGRESS (после SHIPPED заморожен).
        if current == AssemblyStatus.IN_PROGRESS.value and orders:
            items_agg, skipped_no_card = _aggregate_items(orders)
            skipped_no_card_total += skipped_no_card
            if items_agg:
                have = {
                    int(i.nomenclature_id): (i.barcode, int(i.quantity)) for i in mirror.items
                }
                want = {
                    nom_id: (entry["barcode"], int(entry["qty"]))
                    for nom_id, entry in items_agg.items()
                }
                if have != want:
                    mirror.items = [
                        AssemblyRequestItem(
                            project_id=project_id,
                            nomenclature_id=nom_id,
                            barcode=bc,
                            quantity=qty,
                        )
                        for nom_id, (bc, qty) in want.items()
                    ]
                    changed += 1

        target = _target_status(sup, orders)
        if target == current:
            continue
        if target == AssemblyStatus.CANCELLED.value:
            pass  # отмена доступна из любого не-терминального статуса зеркала
        elif _STATUS_RANK.get(target, 0) <= _STATUS_RANK.get(current, 0):
            continue  # только вперёд по цепочке, назад никогда
        old = current
        mirror.status = target
        if (
            target in (AssemblyStatus.SHIPPED.value, AssemblyStatus.DELIVERED.value)
            and mirror.shipped_at is None
        ):
            mirror.shipped_at = sup.scan_dt or sup.closed_at or utcnow()
        db.add(
            AssemblyStatusHistory(
                project_id=project_id,
                assembly_request_id=mirror.id,
                old_status=old,
                new_status=target,
                changed_at=utcnow(),
                changed_by=MIRROR_CHANGED_BY,
            )
        )
        changed += 1

    if skipped_no_card_total:
        logger.warning(
            "fbs_mirror: project=%s заданий без карточки товара пропущено=%s "
            "(в позиции зеркала не попали)",
            project_id,
            skipped_no_card_total,
        )

    mutations = created + changed
    if mutations:
        await db.commit()
        # Списки/аналитика сборки читают заявки с kind=fbs — гасим их кэши.
        await invalidate_cache("reports:assembly_flow")
        await invalidate_cache("reports:assembly_link_anomalies")
        # Потребность читает SHIPPED-заявки (kind-фильтр) — TTL 300 с без гашения
        # держал бы фантомный транзит ещё 5 минут после смены статусов зеркал.
        await invalidate_cache("reports:warehouse_need")
        logger.info(
            "fbs_mirror: project=%s created=%s updated=%s", project_id, created, changed
        )
    return mutations
