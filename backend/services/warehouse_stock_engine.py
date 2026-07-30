"""
Warehouse stock engine — stock balance updates, movements, adjustments, summaries.
"""

import logging
import zlib
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import and_, case, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.cache import invalidate_project_reports
from backend.models.assembly import AssemblyKind, AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import CostOrder, CostOrderItem, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.integrations import WbWarehouseRemains, WbWarehouseStock
from backend.models.refs import ImtAlias, ProductTag, ProductTagMap
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem
from backend.models.warehouse import (
    MovementType,
    StockAdjustment,
    StockMovement,
    Warehouse,
    WarehouseStock,
)
from backend.models.wb_finance import WbFinanceRow
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

# Псевдо-склады отчёта WB «Остатки на складах». Итоговая строка дублирует
# сумму реальных складов — при суммировании её нужно исключать; «в пути»
# выносятся в отдельные поля (wb_in_way_to_client / wb_in_way_from_client),
# а не в разбивку по складам.
WB_REMAINS_TOTAL_ROW = "Всего находится на складах"
WB_REMAINS_IN_WAY_TO_CLIENT = "В пути до получателей"
WB_REMAINS_IN_WAY_FROM_CLIENT = "В пути возвраты на склад WB"


# ─── Barcode → Nomenclature lookup ─────────────────────────────────────────


async def _resolve_barcode(db: AsyncSession, project_id: int, barcode: str) -> Nomenclature:
    """Find Nomenclature by barcode. Raises ValueError if not found."""
    result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode == barcode,
        )
    )
    nom = result.scalar_one_or_none()
    if not nom:
        raise ValueError(f"Barcode not found: {barcode}")
    return nom


async def _resolve_barcodes_batch(db: AsyncSession, project_id: int, barcodes: list[str]) -> dict[str, Nomenclature]:
    """Batch barcode → Nomenclature lookup. One SELECT with IN()."""
    if not barcodes:
        return {}
    result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(barcodes),
        )
    )
    mapping = {nom.barcode: nom for nom in result.scalars().all()}
    missing = [b for b in barcodes if b not in mapping]
    if missing:
        raise ValueError(f"Barcode not found: {missing[0]}")
    return mapping


# ─── Next number generator ─────────────────────────────────────────────────


def _number_lock_ns(prefix: str) -> int:
    """Стабильный int32-неймспейс advisory-лока из префикса документа.

    crc32, а не hash(): тот рандомизирован PYTHONHASHSEED и различается между
    процессами (api ‖ worker) — лок обязан совпадать. Приводим к знаковому int32,
    как требует pg_advisory_xact_lock(int4, int4)."""
    ns = zlib.crc32(prefix.encode("utf-8"))
    return ns - 0x1_0000_0000 if ns >= 0x8000_0000 else ns


async def _next_number(db: AsyncSession, project_id: int, prefix: str, table) -> str:
    """Generate next sequential number for a document (IN-1, OUT-1, TR-1).
    Counts ALL records (including soft-deleted) to avoid number collisions.

    Конкурентные транзакции не видят незакоммиченных INSERT друг друга
    (READ COMMITTED) → одинаковый count → два документа с одним номером
    (unique-индекса на number нет; номер — идентификатор для оператора ФФ в
    TG-нотификациях и Excel). pg_advisory_xact_lock(_number_lock_ns(prefix), project_id)
    сериализует выдачу per (тип документа, проект); лок снимается на commit —
    к этому моменту строка с номером уже видна следующему ожидающему."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :pid)"),
        {"ns": _number_lock_ns(prefix), "pid": project_id},
    )
    result = await db.execute(
        select(func.count(table.id)).where(
            table.project_id == project_id,
        )
    )
    count = result.scalar() or 0
    return f"{prefix}-{count + 1}"


# ─── Stock Engine ──────────────────────────────────────────────────────────


async def _update_stock(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    nomenclature_id: int,
    barcode: str,
    delta: int,
    movement_type: MovementType,
    reference_type: str,
    reference_id: int | None = None,
    comment: str | None = None,
    defect_delta: int = 0,
) -> None:
    """
    Update warehouse_stock balance and create stock_movement in one go.

    delta: positive = inbound, negative = outbound (for quantity).
    defect_delta: positive = add defect, negative = remove defect (for defect_quantity).
    Raises ValueError if resulting quantity or defect_quantity < 0.
    """
    # 1. Get or create WarehouseStock row
    result = await db.execute(
        select(WarehouseStock).where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.nomenclature_id == nomenclature_id,
        )
    )
    stock = result.scalar_one_or_none()

    if stock is None:
        stock = WarehouseStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            quantity=0,
            in_transit=0,
            defect_quantity=0,
            defect_in_transit=0,
        )
        db.add(stock)
        await db.flush()

    # 2. Check non-negative constraints
    new_qty = stock.quantity + delta
    if new_qty < 0:
        raise ValueError(f"Insufficient stock for barcode {barcode}: " f"have {stock.quantity}, need {abs(delta)}")

    new_defect = stock.defect_quantity + defect_delta
    if new_defect < 0:
        raise ValueError(
            f"Insufficient defect stock for barcode {barcode}: "
            f"have {stock.defect_quantity}, need {abs(defect_delta)}"
        )

    stock.quantity = new_qty
    stock.defect_quantity = new_defect
    stock.updated_at = utcnow()

    # 3. Create movement record (audit log)
    movement = StockMovement(
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nomenclature_id,
        barcode=barcode,
        movement_type=movement_type,
        quantity=delta,
        defect_delta=defect_delta,
        reference_type=reference_type,
        reference_id=reference_id,
        comment=comment,
        created_at=utcnow(),
    )
    db.add(movement)


# ─── Warehouse Stock queries ──────────────────────────────────────────────


async def _get_reserved_map(
    db: AsyncSession, project_id: int, warehouse_id: int, exclude_assembly_id: int | None = None
) -> dict[int, int]:
    """Get reserved qty per nomenclature_id from active assembly requests.

    PRE_DISTRIBUTED НАМЕРЕННО исключён: такая заявка зарезервирована под товар машины
    в пути, которого ещё нет на ФФ-складе — она НЕ держит реальный ФФ-сток. Включить
    её сюда = занять несуществующий остаток (фейк-резерв). На разгрузке машины статус
    становится IN_PROGRESS и заявка попадает в резерв уже законно. См. C1 в PREDIST_DESIGN.md.

    exclude_assembly_id — не считать резерв этой сборки (экран её редактирования:
    собственные позиции не должны «съедать» доступный остаток, иначе даже
    уменьшение количества выглядит дефицитом).
    """
    query = (
        select(
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("reserved"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id == warehouse_id,
            AssemblyRequest.is_deleted.is_(False),
            # kind=fbs — учётное зеркало сборки ФФ: его резерв УЖЕ держат
            # открытые FBS-задания (get_open_fbs_reserved); включить сюда =
            # задвоить резерв (см. AssemblyKind docstring).
            AssemblyRequest.kind != AssemblyKind.FBS.value,
            AssemblyRequest.status.in_(
                [
                    AssemblyStatus.PENDING,
                    AssemblyStatus.IN_PROGRESS,
                    AssemblyStatus.READY,
                    AssemblyStatus.VEHICLE_ASSIGNED,
                ]
            ),
        )
        .group_by(AssemblyRequestItem.nomenclature_id)
    )
    if exclude_assembly_id is not None:
        query = query.where(AssemblyRequest.id != exclude_assembly_id)
    result = await db.execute(query)
    return {row.nomenclature_id: row.reserved for row in result.all()}


async def _get_reserved_map_batch(
    db: AsyncSession, project_id: int, warehouse_ids: set[int]
) -> dict[int, dict[int, int]]:
    """Batch version: one query for all warehouses. Returns {warehouse_id: {nomenclature_id: reserved}}.

    PRE_DISTRIBUTED исключён намеренно — см. _get_reserved_map (товар машины в пути не
    держит реальный ФФ-сток; включение = фейк-резерв). C1 в PREDIST_DESIGN.md.
    """
    if not warehouse_ids:
        return {}
    result = await db.execute(
        select(
            AssemblyRequest.warehouse_id,
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("reserved"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id.in_(warehouse_ids),
            AssemblyRequest.is_deleted.is_(False),
            # kind=fbs исключён: резерв держат открытые FBS-задания (см. _get_reserved_map).
            AssemblyRequest.kind != AssemblyKind.FBS.value,
            AssemblyRequest.status.in_(
                [
                    AssemblyStatus.PENDING,
                    AssemblyStatus.IN_PROGRESS,
                    AssemblyStatus.READY,
                    AssemblyStatus.VEHICLE_ASSIGNED,
                ]
            ),
        )
        .group_by(AssemblyRequest.warehouse_id, AssemblyRequestItem.nomenclature_id)
    )
    all_reserved: dict[int, dict[int, int]] = {}
    for row in result.all():
        wh_map = all_reserved.setdefault(row.warehouse_id, {})
        wh_map[row.nomenclature_id] = row.reserved
    return all_reserved


async def _get_reserved_detail_batch(
    db: AsyncSession, project_id: int, warehouse_ids: set[int]
) -> dict[int, list[dict]]:
    """Резерв с сохранением ЗАЯВКИ: {nomenclature_id: [{request_id, number, status,
    warehouse_id, quantity}]}.

    Зеркало _get_reserved_map_batch (тот же join, тот же фильтр статусов, тот же
    намеренно исключённый PRE_DISTRIBUTED), но без схлопывания по заявке — питает
    раскрывающуюся колонку «В сборке на ФФ» на «Единых остатках». Агрегат резерва
    вызывающий код получает суммированием этих же строк, вторым запросом ходить
    не нужно.
    """
    if not warehouse_ids:
        return {}
    result = await db.execute(
        select(
            AssemblyRequest.id,
            AssemblyRequest.number,
            AssemblyRequest.status,
            AssemblyRequest.warehouse_id,
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("quantity"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id.in_(warehouse_ids),
            AssemblyRequest.is_deleted.is_(False),
            # kind=fbs исключён — зеркало _get_reserved_map_batch (тот же инвариант).
            AssemblyRequest.kind != AssemblyKind.FBS.value,
            AssemblyRequest.status.in_(
                [
                    AssemblyStatus.PENDING,
                    AssemblyStatus.IN_PROGRESS,
                    AssemblyStatus.READY,
                    AssemblyStatus.VEHICLE_ASSIGNED,
                ]
            ),
        )
        .group_by(
            AssemblyRequest.id,
            AssemblyRequest.number,
            AssemblyRequest.status,
            AssemblyRequest.warehouse_id,
            AssemblyRequestItem.nomenclature_id,
        )
        # Лимита здесь СОЗНАТЕЛЬНО нет: из этих же строк выводится агрегат
        # «В сборке на ФФ», и усечение молча занизило бы саму цифру (а без
        # ORDER BY — ещё и недетерминированно). Зеркалит _get_reserved_map_batch,
        # который тоже без лимита. Выборка узкая: только активные статусы —
        # на реальных данных это сотни строк, не тысячи.
    )
    details: dict[int, list[dict]] = {}
    for row in result.all():
        details.setdefault(row.nomenclature_id, []).append(
            {
                "request_id": row.id,
                "number": row.number,
                "status": row.status.value if hasattr(row.status, "value") else str(row.status),
                "warehouse_id": row.warehouse_id,
                "quantity": int(row.quantity or 0),
            }
        )
    return details


async def get_open_fbs_reserved(db: AsyncSession, project_id: int, warehouse_ids: list[int]) -> dict[int, int]:
    """Открытые FBS-задания как резерв нашего склада: {nomenclature_id: шт}.

    Товар, проданный по FBS и ещё не отгруженный (`supplier_status` new/confirm),
    физически лежит на нашем складе и НЕ списан из ledger'а (списание делает
    `orders_service.writeoff_completed_orders` только по `complete`). Поэтому в
    `WarehouseStock.quantity` он есть, а продать его второй раз — через сборку —
    нельзя. Это второе слагаемое резерва рядом с резервом активных заявок.

    Единственный источник цифры — `wb_fbs.stock_service.get_open_fbs_qty` (та же
    функция кормит формулу FBS-остатка), чтобы прямой и обратный гейт не разъехались.

    Домен FBS опционален: пока модуль не установлен, возвращаем пустой словарь —
    вычет = 0, поведение вызывающего кода бит-в-бит прежнее.
    """
    if not warehouse_ids:
        return {}
    try:
        from backend.services.wb_fbs.stock_service import get_open_fbs_qty
    except ImportError:  # домен FBS не установлен — вычитать нечего
        return {}
    return await get_open_fbs_qty(db, project_id, list(warehouse_ids))


async def get_warehouse_stock(
    db: AsyncSession, project_id: int, warehouse_id: int, exclude_assembly_id: int | None = None
) -> list[dict]:
    """Get current stock for a warehouse, enriched with reserved/available.

    exclude_assembly_id — резерв этой сборки не вычитается из available
    (для экрана редактирования сборки: «сколько могу заказать в ЭТОЙ сборке»).

    Из available дополнительно вычитаются открытые FBS-задания (`fbs_open`):
    этот товар уже продан со склада продавца и ждёт отгрузки. Без FBS-заказов
    вычет = 0 и available считается ровно как раньше.
    """
    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            or_(WarehouseStock.quantity > 0, WarehouseStock.defect_quantity > 0),
        )
        .order_by(WarehouseStock.barcode)
    )
    rows = result.scalars().all()

    reserved_map = await _get_reserved_map(db, project_id, warehouse_id, exclude_assembly_id=exclude_assembly_id)
    fbs_map = await get_open_fbs_reserved(db, project_id, [warehouse_id])

    enriched = []
    for row in rows:
        reserved = reserved_map.get(row.nomenclature_id, 0)
        fbs_open = fbs_map.get(row.nomenclature_id, 0)
        available = max(0, row.quantity - reserved - fbs_open)
        enriched.append(
            {
                "id": row.id,
                "project_id": row.project_id,
                "warehouse_id": row.warehouse_id,
                "nomenclature_id": row.nomenclature_id,
                "barcode": row.barcode,
                "quantity": row.quantity,
                "in_transit": row.in_transit,
                "defect_quantity": row.defect_quantity,
                "defect_in_transit": row.defect_in_transit,
                "cost_price": row.cost_price,
                "updated_at": row.updated_at,
                "reserved": reserved,
                "available": available,
            }
        )
    return enriched


async def get_stock_movements(db: AsyncSession, project_id: int, warehouse_id: int, limit: int = 200) -> list:
    """Get movement history for a warehouse."""
    result = await db.execute(
        select(StockMovement)
        .where(
            StockMovement.project_id == project_id,
            StockMovement.warehouse_id == warehouse_id,
        )
        .order_by(StockMovement.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ─── Stock Adjustments (Корректировка) ─────────────────────────────────────


async def create_adjustment(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> StockAdjustment:
    """
    Create stock adjustment (inventory correction).
    delta > 0 = surplus, delta < 0 = shortage.
    """
    from backend.services.warehouse_crud import get_warehouse

    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    nom = await _resolve_barcode(db, project_id, payload["barcode"])

    adjustment = StockAdjustment(
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nom.id,
        barcode=payload["barcode"],
        delta=payload["delta"],
        reason=payload["reason"],
    )
    db.add(adjustment)

    await _update_stock(
        db,
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nom.id,
        barcode=payload["barcode"],
        delta=payload["delta"],
        movement_type=MovementType.ADJUSTMENT,
        reference_type="ADJUSTMENT",
        reference_id=None,  # will be set after flush
        comment=payload["reason"],
    )

    await db.commit()
    await db.refresh(adjustment)
    return adjustment


# ─── Summary Stock (Сводные остатки) ───────────────────────────────────────


async def get_stock_summary(db: AsyncSession, project_id: int) -> list[dict]:
    """
    Summary stock across all warehouses.
    Returns: [{barcode, nomenclature_id, warehouses: {wh_id: qty}, in_transit: {wh_id: qty},
               reserved: {wh_id: qty}, total, total_in_transit, total_reserved, total_available}]
    """
    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
        )
        .order_by(WarehouseStock.barcode)
        .limit(500)
    )
    rows = result.scalars().all()

    # Collect unique warehouse_ids and fetch reserved maps in one batch query
    wh_ids = {row.warehouse_id for row in rows}
    all_reserved = await _get_reserved_map_batch(db, project_id, wh_ids)

    # Group by nomenclature_id
    summary: dict[int, dict] = {}
    for row in rows:
        key = row.nomenclature_id
        if key not in summary:
            summary[key] = {
                "nomenclature_id": row.nomenclature_id,
                "barcode": row.barcode,
                "warehouses": {},
                "in_transit": {},
                "reserved": {},
                "total": 0,
                "total_in_transit": 0,
                "total_reserved": 0,
                "total_available": 0,
                "total_defect": 0,
            }
        entry = summary[key]
        if row.quantity > 0:
            entry["warehouses"][row.warehouse_id] = row.quantity
            entry["total"] += row.quantity
        if row.in_transit > 0:
            entry["in_transit"][row.warehouse_id] = row.in_transit
            entry["total_in_transit"] += row.in_transit
        if row.defect_quantity > 0:
            entry["total_defect"] += row.defect_quantity
        # Reserved from assembly requests
        res = all_reserved.get(row.warehouse_id, {}).get(row.nomenclature_id, 0)
        if res > 0:
            entry["reserved"][row.warehouse_id] = res
            entry["total_reserved"] += res

    # Compute available
    for entry in summary.values():
        entry["total_available"] = max(0, entry["total"] - entry["total_reserved"])

    return [v for v in summary.values() if v["total"] > 0 or v["total_in_transit"] > 0 or v["total_defect"] > 0]


# ─── Unified Stock (own + WB + in-transit) ────────────────────────────────


def _build_finance_query(project_id: int, cutoff, today):
    """Build per-nm aggregation matching wb_bdr_helpers.build_bdr_aggregate_sql.

    Filter mirrors BDR semantics (see wb_bdr_helpers._DATE_FILTER):
    - COALESCE(sale_dt, rr_dt) BETWEEN cutoff AND today, OR
    - sale_dt IS NULL AND rr_dt IS NULL AND the row's own date_from/date_to
      window is entirely inside [cutoff, today].
    Also excludes sa_name='Неопознанный товар' the way BDR does so the two
    reports cannot drift on realization totals.
    sale_qty/ret_qty are filtered by supplier_oper_name IN ('Продажа','Возврат')
    so compensations/recharges don't inflate cost_total.
    """
    R = WbFinanceRow
    sale_op = R.supplier_oper_name.in_(["Продажа", "Возврат"])
    effective_dt = func.coalesce(R.sale_dt, R.rr_dt)
    date_filter = or_(
        effective_dt.between(cutoff, today),
        and_(
            R.sale_dt.is_(None),
            R.rr_dt.is_(None),
            R.date_from >= cutoff,
            R.date_to <= today,
        ),
    )
    return (
        select(
            R.nm_id,
            func.coalesce(
                func.sum(case((R.doc_type_name == "Продажа", R.retail_price_withdisc_rub), else_=Decimal("0")))
                - func.sum(case((R.doc_type_name == "Возврат", R.retail_price_withdisc_rub), else_=Decimal("0"))),
                Decimal("0"),
            ).label("realization"),
            func.coalesce(
                func.sum(case((R.doc_type_name == "Продажа", R.retail_amount), else_=Decimal("0")))
                - func.sum(case((R.doc_type_name == "Возврат", R.retail_amount), else_=Decimal("0"))),
                Decimal("0"),
            ).label("sales_amount"),
            func.coalesce(
                func.sum(case((R.doc_type_name == "Продажа", R.ppvz_for_pay), else_=Decimal("0")))
                - func.sum(case((R.doc_type_name == "Возврат", R.ppvz_for_pay), else_=Decimal("0"))),
                Decimal("0"),
            ).label("ppvz_net"),
            func.coalesce(func.sum(R.delivery_rub), Decimal("0")).label("logistics"),
            func.coalesce(func.sum(R.storage_fee), Decimal("0")).label("storage"),
            func.coalesce(func.sum(R.penalty), Decimal("0")).label("penalties"),
            func.coalesce(func.sum(R.acceptance), Decimal("0")).label("acceptance"),
            func.coalesce(func.sum(R.deduction), Decimal("0")).label("deductions_total"),
            func.coalesce(
                func.sum(
                    case(
                        (R.bonus_type_name.ilike("%продвижение%"), R.deduction),
                        (R.bonus_type_name.ilike("%медиа%"), R.deduction),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("ad_deduction"),
            func.coalesce(
                func.sum(
                    case(
                        (R.bonus_type_name.ilike("%кредит%"), R.deduction),
                        (R.bonus_type_name.ilike("%заем%"), R.deduction),
                        (R.bonus_type_name.ilike("%заём%"), R.deduction),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("loan_deduction"),
            func.coalesce(
                func.sum(case(((R.doc_type_name == "Продажа") & sale_op, R.quantity), else_=0))
                - func.sum(case(((R.doc_type_name == "Возврат") & sale_op, R.quantity), else_=0)),
                0,
            ).label("sale_qty"),
            func.count(func.distinct(effective_dt)).label("days_count"),
        )
        .where(
            R.project_id == project_id,
            date_filter,
            func.lower(func.coalesce(R.sa_name, "")) != "неопознанный товар",
        )
        .group_by(R.nm_id)
        .limit(10000)
    )


def _compute_tax_and_profit(
    *,
    sales_amount: Decimal,
    to_pay: Decimal,
    ppvz_net: Decimal,
    logistics: Decimal,
    storage: Decimal,
    penalties: Decimal,
    deductions_total: Decimal,
    ad_deduction: Decimal,
    loan_deduction: Decimal,
    adv_sum: Decimal,
    cost_total: Decimal,
    usn_rate: Decimal,
    nds_rate: Decimal,
    regime: str,
    cost_as_expense: bool,
) -> tuple[Decimal, Decimal]:
    """Pure-function tax+profit calc — mirrors bdr_enrichment.apply_tax_article.

    Returns: (tax_total, total_profit).

    For `usn_income_expense_vat` regime the USN tax base is reduced by all
    operating expenses (and optionally cost). Otherwise USN is computed on
    income net of НДС. The `to_pay` parameter is expected to already have
    `deductions_total` subtracted, so ad/loan deductions are added back
    when computing profit (same convention as BDR).
    """
    commission = sales_amount - ppvz_net
    operating_deductions = deductions_total - ad_deduction - loan_deduction

    nds_sum = sales_amount * nds_rate / (Decimal("1") + nds_rate) if nds_rate > 0 else Decimal("0")
    tax_base = sales_amount - nds_sum
    if regime == "usn_income_expense_vat":
        expenses = (
            abs(logistics) + abs(storage) + abs(commission) + abs(penalties) + abs(operating_deductions) + adv_sum
        )
        if cost_as_expense:
            expenses += cost_total
        tax_base = sales_amount - nds_sum - expenses
    usn_sum = max(tax_base * usn_rate, Decimal("0")) if usn_rate > 0 else Decimal("0")
    tax = nds_sum + usn_sum

    total_profit = to_pay + ad_deduction + loan_deduction - adv_sum - cost_total - tax
    return tax, total_profit


def _per_unit(total: Decimal, qty_raw: int) -> float:
    """Цена/прибыль за штуку периода. При нулевых/отрицательных нетто-штуках
    (одни возвраты или чистые расходы — хранение/реклама без продаж) НЕ делим
    на «1»: иначе весь период-расход становился «прибылью за штуку», а страница
    остатков умножала его на весь сток (прод 28.07: 15 строк давали −8.7M из
    −12.1M KPI «Прибыль» — 72% минуса фейковые)."""
    if qty_raw <= 0:
        return 0.0
    return float(round(total / qty_raw, 2))


async def _compute_period_metrics(
    db: AsyncSession,
    project_id: int,
    cutoff,
    today,
    nm_id_to_nom_id: dict[int, int],
    cost_map: dict[int, float],
    tax_info: dict,
) -> dict[int, dict]:
    """Compute per-nm period metrics: realization, profit, sale_qty, days_count.

    Tax formula matches bdr_enrichment.apply_tax_article — for `usn_income_expense_vat`
    regime expenses (logistics/storage/penalties/deductions/adv [+cost]) are subtracted
    from the USN tax base; otherwise USN is computed on net income (income minus НДС).

    Returns: {nom_id: {realization, total_profit, sale_qty, days_count, avg_price, avg_profit}}
    """
    from backend.models.integrations import WbFunnelDaily

    usn_rate = Decimal(str(tax_info.get("usn_rate", 0))) / Decimal("100")
    nds_rate = Decimal(str(tax_info.get("nds_rate", 0))) / Decimal("100")
    regime = tax_info.get("tax_regime", "usn_income")
    cost_as_expense = bool(tax_info.get("cost_as_expense", False))

    finance_result = await db.execute(_build_finance_query(project_id, cutoff, today))
    finance_rows = finance_result.all()

    adv_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.coalesce(func.sum(WbFunnelDaily.adv_sum), Decimal("0")).label("adv_sum"),
            func.coalesce(func.sum(WbFunnelDaily.orders_count), 0).label("orders_count"),
        )
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= cutoff,
            WbFunnelDaily.date <= today,
        )
        .group_by(WbFunnelDaily.nm_id)
        .limit(10000)
    )
    adv_map: dict[int, Decimal] = {}
    # «Тренд шт/д» («средние заказы») is driven by ORDERS (orders_count from the
    # WB funnel), NOT by finance buy-outs (Продажа − Возврат). The two differ by
    # the buy-out rate — e.g. for the «Диваны бескаркасные» category 383 orders
    # vs 227 buy-outs over 7 days (~59%). The user reads this column as «сколько
    # штук в день заказывают», so it must track demand, not realized sales.
    # Orders are aggregated per nomenclature_id (many nm_id → one nom_id).
    orders_by_nom: dict[int, int] = {}
    for r in adv_result.all():
        adv_map[r.nm_id] = Decimal(str(r.adv_sum or 0))
        nom_id = nm_id_to_nom_id.get(r.nm_id)
        if nom_id is not None:
            orders_by_nom[nom_id] = orders_by_nom.get(nom_id, 0) + int(r.orders_count or 0)

    period_map: dict[int, dict] = {}
    for row in finance_rows:
        nom_id = nm_id_to_nom_id.get(row.nm_id)
        if nom_id is None:
            continue
        days = max(int(row.days_count or 0), 1)
        sale_qty_raw = int(row.sale_qty or 0)
        realization = Decimal(str(row.realization or 0))
        sales_amount = Decimal(str(row.sales_amount or 0))
        ppvz_net = Decimal(str(row.ppvz_net or 0))
        logistics = Decimal(str(row.logistics or 0))
        storage = Decimal(str(row.storage or 0))
        penalties = Decimal(str(row.penalties or 0))
        acceptance = Decimal(str(row.acceptance or 0))
        deductions_total = Decimal(str(row.deductions_total or 0))
        ad_deduction = Decimal(str(row.ad_deduction or 0))
        loan_deduction = Decimal(str(row.loan_deduction or 0))
        adv_sum = adv_map.get(row.nm_id, Decimal("0"))
        cost_per_unit = Decimal(str(cost_map.get(nom_id, 0)))
        cost_total = cost_per_unit * sale_qty_raw

        to_pay = ppvz_net - logistics - storage - penalties - acceptance - deductions_total

        _tax, total_profit = _compute_tax_and_profit(
            sales_amount=sales_amount,
            to_pay=to_pay,
            ppvz_net=ppvz_net,
            logistics=logistics,
            storage=storage,
            penalties=penalties,
            deductions_total=deductions_total,
            ad_deduction=ad_deduction,
            loan_deduction=loan_deduction,
            adv_sum=adv_sum,
            cost_total=cost_total,
            usn_rate=usn_rate,
            nds_rate=nds_rate,
            regime=regime,
            cost_as_expense=cost_as_expense,
        )

        period_map[nom_id] = {
            "realization": realization,
            "total_profit": total_profit,
            "sale_qty_raw": sale_qty_raw,
            "orders_qty": orders_by_nom.get(nom_id, 0),
            "days_count": days,
            "avg_price": _per_unit(realization, sale_qty_raw),
            "avg_profit": _per_unit(total_profit, sale_qty_raw),
        }

    # Items ordered in the window but with no finance rows yet (cold-start:
    # ordered, not yet bought out) must still contribute to «тренд шт/д», else
    # the category average silently undercounts new SKUs. They carry zero
    # realization/profit — only the order-driven velocity is populated.
    for nom_id, orders_qty in orders_by_nom.items():
        if orders_qty <= 0 or nom_id in period_map:
            continue
        period_map[nom_id] = {
            "realization": Decimal("0"),
            "total_profit": Decimal("0"),
            "sale_qty_raw": 0,
            "orders_qty": orders_qty,
            "days_count": 1,
            "avg_price": 0.0,
            "avg_profit": 0.0,
        }
    return period_map


def _compute_trend_cutoffs(today_date: date) -> tuple[date, date, date, date]:
    """Compute 7/14/30-day trend window anchored at yesterday.

    WB finance data for the current day is not yet available (the day is not
    over), so the trend window must end at ``yesterday``. This matches the
    Cost-DNA fix in PR #158 (`cost_dna_service.get_cost_dna` uses the same
    anchor) so unified-stock and Cost-DNA stay on the same period grid.

    Returns:
        ``(anchor, cutoff_30, cutoff_14, cutoff_7)`` where ``anchor`` is the
        last day of the window (inclusive, = yesterday), and each cutoff is
        the first day of its window (inclusive). Ranges are exactly 30/14/7
        calendar days: ``[cutoff, anchor]``.
    """
    anchor = today_date - timedelta(days=1)
    return (
        anchor,
        anchor - timedelta(days=29),  # 30 days inclusive
        anchor - timedelta(days=13),  # 14 days inclusive
        anchor - timedelta(days=6),  # 7 days inclusive
    )


def _build_trend_payload(p: dict | None, period_from: date, period_to: date) -> dict:
    """Build one trend period payload (7/14/30 day window).

    ``avg_daily_qty`` («тренд шт/д», средние заказы за период) is the number of
    ORDERED units (``orders_qty`` from the WB funnel) divided by the **full
    calendar length of the window** (7/14/30 days). Two prior bugs are fixed
    here:

    1. Source: it used to divide finance buy-outs (Продажа − Возврат), so the
       column labelled «средние заказы» actually showed realized sales — lower
       than real demand by the buy-out rate (e.g. «Диваны бескаркасные»: 383
       orders vs 227 buy-outs over 7 days). It now tracks orders.
    2. Divisor: it used to divide by the count of days that had finance rows,
       not the window length, inflating intermittent sellers and making the
       7↔14↔30 switch nearly inert. It now divides by 7/14/30.

    ``revenue``/``profit`` stay finance-based (realized money, matching БДР) —
    only the quantity trend reflects demand. Stock-days-of-cover on the UI
    derives from ``avg_daily_qty``, so it is now demand-based (more conservative).
    """
    base = {
        "date_from": period_from.isoformat(),
        "date_to": period_to.isoformat(),
    }
    if not p:
        return {**base, "avg_daily_qty": 0.0, "sale_qty": 0.0, "revenue": 0.0, "profit": 0.0}
    period_days = (period_to - period_from).days + 1  # inclusive window: 7 / 14 / 30
    orders_qty = int(p.get("orders_qty", 0))
    return {
        **base,
        "avg_daily_qty": float(round(orders_qty / period_days, 2)),
        "sale_qty": float(orders_qty),
        "revenue": float(round(p["realization"], 2)),
        "profit": float(round(p["total_profit"], 2)),
    }


async def _load_bdr_metrics(
    db: AsyncSession,
    project_id: int,
    nm_id_to_nom_id: dict[int, int],
    cost_map: dict[int, float],
) -> dict[int, dict]:
    """Load BDR-style metrics per nomenclature_id for 7/14/30-day periods.

    Returns trend data per period + legacy avg_daily_revenue/profit/price/profit (30d) for compat.
    Each trend period payload includes ``date_from``/``date_to`` so the UI can
    label exactly which window was computed.
    """
    from backend.services.bdr_loaders import load_tax_settings

    anchor, cutoff_30, cutoff_14, cutoff_7 = _compute_trend_cutoffs(utcnow().date())

    # Load tax_info per-period using the period START month — matches BDR
    # which keys `load_tax_settings` off `d_from.month`. If we key off the
    # window END (anchor=yesterday) we can land on a "current" month whose
    # rates are still placeholder zeros, while BDR for the same period uses
    # the prior month's real rates — profits diverge by the whole tax amount.
    # Seen in production 2026-04-15: March rates 15%/7%, April rates 0%/0%,
    # BDR profit ~32M vs Unified ~43M (delta == sum of BDR tax per article).
    tax_info_30 = await load_tax_settings(db, project_id, cutoff_30, anchor)
    tax_info_14 = await load_tax_settings(db, project_id, cutoff_14, anchor)
    tax_info_7 = await load_tax_settings(db, project_id, cutoff_7, anchor)

    period_30 = await _compute_period_metrics(db, project_id, cutoff_30, anchor, nm_id_to_nom_id, cost_map, tax_info_30)
    period_14 = await _compute_period_metrics(db, project_id, cutoff_14, anchor, nm_id_to_nom_id, cost_map, tax_info_14)
    period_7 = await _compute_period_metrics(db, project_id, cutoff_7, anchor, nm_id_to_nom_id, cost_map, tax_info_7)

    bdr_map: dict[int, dict] = {}
    all_nom_ids = set(period_30.keys()) | set(period_14.keys()) | set(period_7.keys())
    for nom_id in all_nom_ids:
        p30 = period_30.get(nom_id)
        p14 = period_14.get(nom_id)
        p7 = period_7.get(nom_id)
        legacy = p30 or {
            "realization": Decimal("0"),
            "total_profit": Decimal("0"),
            "days_count": 1,
            "avg_price": 0.0,
            "avg_profit": 0.0,
        }
        bdr_map[nom_id] = {
            "avg_daily_revenue": float(round(legacy["realization"] / legacy["days_count"], 2)),
            "avg_daily_profit": float(round(legacy["total_profit"] / legacy["days_count"], 2)),
            "avg_price": legacy["avg_price"],
            "avg_profit": legacy["avg_profit"],
            "trend_7": _build_trend_payload(p7, cutoff_7, anchor),
            "trend_14": _build_trend_payload(p14, cutoff_14, anchor),
            "trend_30": _build_trend_payload(p30, cutoff_30, anchor),
        }
    return bdr_map


async def _load_category_markup_and_rate(db: AsyncSession, project_id: int) -> tuple[dict[str, float], float, float]:
    """Compute weighted-avg markup factor per subject + avg CNY rate from shipped vehicles.

    NOTE: CostOrderItem.total_rub is stored PER-UNIT (see services/cost/items.py L160-165).
    markup = total_rub / (price_cny * rate_cny)  — both per unit
    Subject is joined from Nomenclature (CostOrderItem.subject is often NULL in legacy data).
    Rate is averaged across SHIPPED+CUSTOMS+DISPATCHED+DELIVERED vehicles, weighted by qty.

    Returns: (markup_by_subject, global_avg_markup, avg_rate_cny)
    Fallbacks if no data: markup=1.5, rate=12.5
    """
    result = await db.execute(
        select(
            func.coalesce(Nomenclature.subject, CostOrderItem.subject).label("subject"),
            CostOrderItem.total_rub,
            CostOrderItem.price_cny,
            CostOrderItem.qty,
            CostOrder.rate_cny,
        )
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .outerjoin(
            Nomenclature,
            (Nomenclature.barcode == CostOrderItem.barcode) & (Nomenclature.project_id == project_id),
        )
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted.is_(False),
            CostOrder.status.in_(
                [
                    VehicleStatus.SHIPPED,
                    VehicleStatus.CUSTOMS,
                    VehicleStatus.DISPATCHED,
                    VehicleStatus.DELIVERED,
                ]
            ),
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted.is_(False),
            CostOrderItem.total_rub.isnot(None),
            CostOrderItem.price_cny.isnot(None),
        )
        .limit(50000)
    )

    subject_acc: dict[str, dict[str, float]] = {}
    rate_weighted_sum = 0.0
    rate_weight = 0.0

    for row in result.all():
        rate = float(row.rate_cny or 0)
        price = float(row.price_cny or 0)
        total_rub_per_unit = float(row.total_rub or 0)
        qty = float(row.qty or 0)
        if rate <= 0 or price <= 0 or total_rub_per_unit <= 0 or qty <= 0:
            continue

        per_unit_cost_cny = price * rate
        if per_unit_cost_cny <= 0:
            continue
        markup = total_rub_per_unit / per_unit_cost_cny

        rate_weighted_sum += rate * qty
        rate_weight += qty

        subject = row.subject or "Без категории"
        if subject not in subject_acc:
            subject_acc[subject] = {"weighted_markup": 0.0, "weight": 0.0}
        subject_acc[subject]["weighted_markup"] += markup * qty
        subject_acc[subject]["weight"] += qty

    markup_by_subject: dict[str, float] = {}
    for subject, acc in subject_acc.items():
        if acc["weight"] > 0:
            markup_by_subject[subject] = acc["weighted_markup"] / acc["weight"]

    global_markup = sum(markup_by_subject.values()) / len(markup_by_subject) if markup_by_subject else 1.5
    avg_rate = rate_weighted_sum / rate_weight if rate_weight > 0 else 12.5

    return markup_by_subject, global_markup, avg_rate


async def _load_factory_unit_costs(
    db: AsyncSession,
    project_id: int,
    nom_map: dict[int, dict],
    markup_by_subject: dict[str, float],
    global_markup: float,
    avg_rate: float,
) -> dict[int, float]:
    """Per-unit estimated cost (RUB) for ALL factory items.

    cost_per_unit = price_cny * avg_rate * markup_by_subject[subject]

    Always populated for items in active factory orders. Used both:
    - As fallback for cost_map (when no historical cost exists)
    - As factory column display value (always ≈ in frontend)
    """
    result = await db.execute(
        select(
            Nomenclature.id.label("nomenclature_id"),
            func.avg(FactoryOrderItem.price_cny).label("avg_price_cny"),
        )
        .join(FactoryOrder, FactoryOrderItem.factory_order_id == FactoryOrder.id)
        .join(
            Nomenclature,
            (Nomenclature.barcode == FactoryOrderItem.barcode) & (Nomenclature.project_id == project_id),
        )
        .where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted.is_(False),
            FactoryOrder.status.notin_(["CLOSED"]),
            FactoryOrderItem.project_id == project_id,
            FactoryOrderItem.is_deleted.is_(False),
            FactoryOrderItem.price_cny > 0,
        )
        .group_by(Nomenclature.id)
        .limit(10000)
    )

    estimated: dict[int, float] = {}
    for row in result.all():
        nom_id = row.nomenclature_id
        price_cny = float(row.avg_price_cny or 0)
        if price_cny <= 0:
            continue
        info = nom_map.get(nom_id, {})
        subject = info.get("subject") or ""
        markup = markup_by_subject.get(subject, global_markup)
        estimated[nom_id] = round(price_cny * avg_rate * markup, 2)

    return estimated


def _compute_category_bdr_averages(
    bdr_map: dict[int, dict],
    nom_map: dict[int, dict],
) -> dict[str, dict]:
    """Compute average revenue/profit per item per subject for new-item estimates.

    Only counts items with actual sales (revenue > 0).
    avg_daily_qty is NOT estimated — kept at 0 so frontend shows dash.

    Additionally exposes ``unit_revenue_30`` / ``unit_profit_30`` — qty-weighted
    per-piece avg computed as ``sum(revenue_30) / sum(sale_qty_30)`` across
    items with sales in the 30-day window. These drive the «Новинки» KPI
    card: for an incoming SKU we estimate revenue as ``qty_incoming *
    unit_revenue_30`` of its subject (matches intuition «обычная цена продажи
    в этой категории»).
    """
    by_subject: dict[str, dict[str, float]] = {}
    for nom_id, bdr in bdr_map.items():
        info = nom_map.get(nom_id, {})
        subject = info.get("subject")
        if not subject:
            continue
        # Only count items with actual sales
        rev_30 = (bdr.get("trend_30", {}) or {}).get("revenue", 0) or 0
        if rev_30 <= 0:
            continue
        if subject not in by_subject:
            by_subject[subject] = {
                "items_count": 0.0,
                "rev_7": 0.0,
                "prof_7": 0.0,
                "rev_14": 0.0,
                "prof_14": 0.0,
                "rev_30": 0.0,
                "prof_30": 0.0,
                "qty_30": 0.0,
            }
        s = by_subject[subject]
        s["items_count"] += 1
        for period in ("7", "14", "30"):
            t = bdr.get(f"trend_{period}", {}) or {}
            s[f"rev_{period}"] += float(t.get("revenue", 0) or 0)
            s[f"prof_{period}"] += float(t.get("profit", 0) or 0)
        t30 = bdr.get("trend_30", {}) or {}
        s["qty_30"] += float(t30.get("sale_qty", 0) or 0)

    result: dict[str, dict] = {}
    for subject, s in by_subject.items():
        n = s["items_count"]
        if n == 0:
            continue
        qty_30 = s["qty_30"]
        unit_revenue_30 = round(s["rev_30"] / qty_30, 2) if qty_30 > 0 else 0.0
        unit_profit_30 = round(s["prof_30"] / qty_30, 2) if qty_30 > 0 else 0.0
        result[subject] = {
            "trend_7": {
                "avg_daily_qty": 0.0,
                "revenue": round(s["rev_7"] / n, 2),
                "profit": round(s["prof_7"] / n, 2),
            },
            "trend_14": {
                "avg_daily_qty": 0.0,
                "revenue": round(s["rev_14"] / n, 2),
                "profit": round(s["prof_14"] / n, 2),
            },
            "trend_30": {
                "avg_daily_qty": 0.0,
                "revenue": round(s["rev_30"] / n, 2),
                "profit": round(s["prof_30"] / n, 2),
            },
            "unit_revenue_30": unit_revenue_30,
            "unit_profit_30": unit_profit_30,
        }
    return result


def _apply_forecast_fallback(unified: dict[int, dict], category_bdr: dict[str, dict]) -> None:
    """Estimate revenue/profit for items en route but not yet physically on shelf.

    Runs only when ``include_forecast=True``. Applies to entries that satisfy
    ALL of:
      * ``trend_30.revenue <= 0`` — no real sales in the period;
      * ``total_own + total_wb == 0`` — nothing currently on shelf;
      * ``factory_qty + vehicle_forming_qty + vehicle_transit_qty > 0`` — item
        is en route via factory or a vehicle;
      * subject is present in ``category_bdr`` (i.e. the category has sales
        history to base the estimate on).

    Existing shelf stock without recent sales is NEVER estimated — zero stays
    zero so «Факт» mode matches БДР copeck-for-copeck.

    Mutates ``unified`` in place. ``is_revenue_estimated`` is set to True for
    every entry that gets a category-average trend — the frontend uses this
    flag to show the «≈» prefix.
    """
    for entry in unified.values():
        trend_30 = entry.get("trend_30") or {}
        if (trend_30.get("revenue") or 0) > 0:
            continue
        if (entry.get("total_own", 0) + entry.get("total_wb", 0)) > 0:
            continue
        incoming = (
            entry.get("factory_qty", 0) + entry.get("vehicle_forming_qty", 0) + entry.get("vehicle_transit_qty", 0)
        )
        if incoming <= 0:
            continue
        subject = entry.get("subject")
        if not subject or subject not in category_bdr:
            continue
        cat = category_bdr[subject]
        for period in ("trend_7", "trend_14", "trend_30"):
            entry[period] = {**entry.get(period, {}), **cat.get(period, {})}
        entry["is_revenue_estimated"] = True


def _assign_abc(items: list[dict], value_key: str, abc_key: str) -> None:
    """Assign ABC category based on cumulative share (A=80%, B=95%, C=rest)."""
    total = sum(max(item.get(value_key, 0), 0) for item in items)
    if total <= 0:
        for item in items:
            item[abc_key] = "C"
        return
    sorted_items = sorted(items, key=lambda x: x.get(value_key, 0), reverse=True)
    cumulative = 0.0
    for item in sorted_items:
        val = max(item.get(value_key, 0), 0)
        cumulative += val
        share = cumulative / total
        if share <= 0.80:
            item[abc_key] = "A"
        elif share <= 0.95:
            item[abc_key] = "B"
        else:
            item[abc_key] = "C"


def _group_abc(unified_list: list[dict]) -> list[dict]:
    """Group ABC-classified items: brand → subject → articles (3-level hierarchy)."""

    def _init_group(name: str) -> dict:
        return {
            "group_name": name,
            "items_count": 0,
            "total_own": 0,
            "total_wb": 0,
            "wb_in_way_to_client": 0,
            "wb_in_way_from_client": 0,
            "in_transit": 0,
            "total": 0,
            "avg_cost": 0.0,
            "avg_daily_revenue": 0.0,
            "avg_daily_profit": 0.0,
            "avg_price": 0.0,
            "avg_profit": 0.0,
            "warehouses": {},
            "wb_stocks": {},
            "_cost_sum": 0.0,
            "_cost_weight": 0,
            "_price_sum": 0.0,
            "_price_weight": 0,
            "_profit_sum": 0.0,
            "_profit_weight": 0,
        }

    def _acc(g: dict, row: dict) -> None:
        g["items_count"] += 1
        for k in ("total_own", "total_wb", "wb_in_way_to_client", "wb_in_way_from_client", "in_transit", "total"):
            g[k] += row[k]
        g["avg_daily_revenue"] += row.get("avg_daily_revenue", 0)
        g["avg_daily_profit"] += row.get("avg_daily_profit", 0)
        t = row["total"]
        for field, s, w in [
            ("avg_cost", "_cost_sum", "_cost_weight"),
            ("avg_price", "_price_sum", "_price_weight"),
            ("avg_profit", "_profit_sum", "_profit_weight"),
        ]:
            if row.get(field) and t > 0:
                g[s] += row[field] * t
                g[w] += t
        for wh, qty in row.get("warehouses", {}).items():
            g["warehouses"][wh] = g["warehouses"].get(wh, 0) + qty
        for wh, qty in row.get("wb_stocks", {}).items():
            g["wb_stocks"][wh] = g["wb_stocks"].get(wh, 0) + qty

    def _fin(g: dict) -> None:
        for field, s, w in [
            ("avg_cost", "_cost_sum", "_cost_weight"),
            ("avg_price", "_price_sum", "_price_weight"),
            ("avg_profit", "_profit_sum", "_profit_weight"),
        ]:
            if g[w] > 0:
                g[field] = round(g[s] / g[w], 2)
            del g[s], g[w]
        g["avg_daily_revenue"] = round(g["avg_daily_revenue"], 2)
        g["avg_daily_profit"] = round(g["avg_daily_profit"], 2)

    # Build brand → subject → articles hierarchy
    brands: dict[str, dict] = {}
    for row in unified_list:
        brand_key = row.get("brand") or "Без бренда"
        subject_key = row.get("subject") or "Без категории"

        if brand_key not in brands:
            b = _init_group(brand_key)
            b["_subjects"] = {}
            brands[brand_key] = b

        b = brands[brand_key]
        _acc(b, row)

        subj_map = b["_subjects"]
        if subject_key not in subj_map:
            s = _init_group(subject_key)
            s["children"] = []
            subj_map[subject_key] = s

        s = subj_map[subject_key]
        _acc(s, row)
        s["children"].append(row)

    result = []
    for b in brands.values():
        _fin(b)
        subjects = []
        for s in b.pop("_subjects").values():
            _fin(s)
            s["children"].sort(key=lambda r: r.get("avg_daily_revenue", 0), reverse=True)
            subjects.append(s)
        subjects.sort(key=lambda s: s.get("avg_daily_revenue", 0), reverse=True)
        b["children"] = subjects
        result.append(b)

    # Sort brands by revenue, assign ABC to brands too
    result.sort(key=lambda g: g.get("avg_daily_revenue", 0), reverse=True)
    _assign_abc(result, "avg_daily_revenue", "abc_class")
    return result


async def _load_tag_map(db: AsyncSession, project_id: int) -> dict[int, str]:
    """Load nm_id → tag_name map. Returns first tag per nm_id."""
    result = await db.execute(
        select(ProductTagMap.nm_id, ProductTag.name)
        .join(ProductTag, ProductTagMap.tag_id == ProductTag.id)
        .where(
            ProductTagMap.project_id == project_id,
            ProductTag.project_id == project_id,
            ProductTag.is_deleted.is_(False),
        )
        .limit(10000)
    )
    tag_map: dict[int, str] = {}
    for row in result.all():
        if row.nm_id not in tag_map:
            tag_map[row.nm_id] = row.name
    return tag_map


async def _load_imt_map(
    db: AsyncSession,
    project_id: int,
    nom_ids: set[int],
) -> dict[int, int]:
    """Load nomenclature_id → imt_id map."""
    if not nom_ids:
        return {}
    result = await db.execute(
        select(Nomenclature.id, Nomenclature.imt_id)
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.id.in_(nom_ids),
            Nomenclature.imt_id.isnot(None),
        )
        .limit(10000)
    )
    return {row.id: row.imt_id for row in result.all()}


async def _load_imt_aliases(db: AsyncSession, project_id: int) -> dict[int, str]:
    """Load imt_id → alias name map."""
    result = await db.execute(
        select(ImtAlias.imt_id, ImtAlias.name).where(ImtAlias.project_id == project_id).limit(10000)
    )
    return {row.imt_id: row.name for row in result.all()}


async def get_unified_stock_summary(
    db: AsyncSession,
    project_id: int,
    group_by: str = "sku",
    brand: str | None = None,
    include_forecast: bool = False,
) -> list[dict]:
    """
    Unified stock across own warehouses, WB marketplace, and in-transit.
    Merges WarehouseStock + WbWarehouseStock + SHIPPED assembly items.

    group_by: sku | brand | subject | imt | tag | abc
    brand: optional case-sensitive filter by Nomenclature.brand. When set, only
        nomenclature rows for that brand contribute (own/WB/in-transit/factory).
    include_forecast: when False (default) realization/profit reflect ONLY real
        WB sales — totals match БДР copeck-for-copeck. When True, items that are
        en route but not yet physically in stock (factory/vehicle SHIPPED/FORMING)
        get an estimated revenue/profit from the category average. Shelf stock
        without sales is NEVER estimated — zero stays zero.
    """
    # 0. If brand filter is set, resolve allowed nomenclature_ids first
    allowed_nom_ids: set[int] | None = None
    if brand:
        brand_result = await db.execute(
            select(Nomenclature.id)
            .where(
                Nomenclature.project_id == project_id,
                Nomenclature.brand == brand,
            )
            .limit(50000)
        )
        allowed_nom_ids = {r[0] for r in brand_result.all()}
        if not allowed_nom_ids:
            return []

    # 1. Own warehouse stock grouped by nomenclature_id
    own_query = select(WarehouseStock).where(WarehouseStock.project_id == project_id)
    if allowed_nom_ids is not None:
        own_query = own_query.where(WarehouseStock.nomenclature_id.in_(allowed_nom_ids))
    own_result = await db.execute(own_query.limit(10000))
    own_rows = own_result.scalars().all()

    # 2. Warehouse id->name map
    wh_result = await db.execute(
        select(Warehouse.id, Warehouse.name)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.is_deleted.is_(False),
        )
        .limit(10000)
    )
    wh_name_map: dict[int, str] = {row.id: row.name for row in wh_result.all()}

    # 3. WB warehouse stock. Primary source — wb_warehouse_remains: зеркало
    #    отчёта кабинета «Остатки на складах» (включает приёмку и межскладской
    #    транзит WB). Псевдо-склады «В пути до получателей» / «В пути возвраты
    #    на склад WB» уходят в отдельные поля wb_in_way_to_client /
    #    wb_in_way_from_client (см. цикл «WB stock» ниже); итоговая псевдо-строка
    #    «Всего находится на складах» — дубль суммы реальных складов, исключается.
    #    Fallback до первого синка remains — statistics supplier/stocks («доступно
    #    к продаже» по складам + in_way_* синтетическими псевдо-строками, чтобы
    #    обе ветки обрабатывались одним циклом).
    remains_exists = (
        await db.execute(
            select(WbWarehouseRemains.id).where(WbWarehouseRemains.project_id == project_id).limit(1)
        )
    ).first() is not None

    if remains_exists:
        # Primary join by barcode (точный, per-size; не дублирует остаток при
        # нескольких карточках с одним article_wb). Строки, чей barcode не
        # заведён в номенклатуре, добираются fallback-джойном по nm_id.
        remains_common = (
            WbWarehouseRemains.project_id == project_id,
            WbWarehouseRemains.warehouse_name != WB_REMAINS_TOTAL_ROW,
        )
        by_barcode_query = (
            select(
                Nomenclature.id.label("nomenclature_id"),
                WbWarehouseRemains.warehouse_name,
                func.sum(WbWarehouseRemains.quantity).label("qty"),
            )
            .join(
                Nomenclature,
                (Nomenclature.barcode == WbWarehouseRemains.barcode)
                & (Nomenclature.project_id == project_id),
            )
            .where(*remains_common)
            .group_by(Nomenclature.id, WbWarehouseRemains.warehouse_name)
            .limit(50000)
        )
        nom_bc = aliased(Nomenclature)
        by_nm_query = (
            select(
                Nomenclature.id.label("nomenclature_id"),
                WbWarehouseRemains.warehouse_name,
                func.sum(WbWarehouseRemains.quantity).label("qty"),
            )
            .join(
                Nomenclature,
                (Nomenclature.article_wb == WbWarehouseRemains.nm_id)
                & (Nomenclature.project_id == project_id),
            )
            .where(
                *remains_common,
                ~exists().where(
                    (nom_bc.barcode == WbWarehouseRemains.barcode) & (nom_bc.project_id == project_id)
                ),
            )
            .group_by(Nomenclature.id, WbWarehouseRemains.warehouse_name)
            .limit(50000)
        )
        if allowed_nom_ids is not None:
            by_barcode_query = by_barcode_query.where(Nomenclature.id.in_(allowed_nom_ids))
            by_nm_query = by_nm_query.where(Nomenclature.id.in_(allowed_nom_ids))
        wb_rows = list((await db.execute(by_barcode_query)).all())
        wb_rows.extend((await db.execute(by_nm_query)).all())
    else:
        wb_query = (
            select(
                Nomenclature.id.label("nomenclature_id"),
                WbWarehouseStock.warehouse_name,
                func.sum(WbWarehouseStock.quantity).label("qty"),
            )
            .join(Nomenclature, Nomenclature.article_wb == WbWarehouseStock.nm_id)
            .where(
                WbWarehouseStock.project_id == project_id,
                Nomenclature.project_id == project_id,
            )
            .group_by(Nomenclature.id, WbWarehouseStock.warehouse_name)
            .limit(10000)
        )
        # in_way_* — те же данные, что раньше сидели внутри quantity_full,
        # теперь отдельными псевдо-строками (единый цикл обработки с remains).
        inway_query = (
            select(
                Nomenclature.id.label("nomenclature_id"),
                func.sum(WbWarehouseStock.in_way_to_client).label("to_client"),
                func.sum(WbWarehouseStock.in_way_from_client).label("from_client"),
            )
            .join(Nomenclature, Nomenclature.article_wb == WbWarehouseStock.nm_id)
            .where(
                WbWarehouseStock.project_id == project_id,
                Nomenclature.project_id == project_id,
            )
            .group_by(Nomenclature.id)
            .limit(10000)
        )
        if allowed_nom_ids is not None:
            wb_query = wb_query.where(Nomenclature.id.in_(allowed_nom_ids))
            inway_query = inway_query.where(Nomenclature.id.in_(allowed_nom_ids))
        wb_result = await db.execute(wb_query)
        wb_rows = list(wb_result.all())
        for r in (await db.execute(inway_query)).all():
            if int(r.to_client or 0) > 0:
                wb_rows.append(
                    SimpleNamespace(
                        nomenclature_id=r.nomenclature_id,
                        warehouse_name=WB_REMAINS_IN_WAY_TO_CLIENT,
                        qty=int(r.to_client),
                    )
                )
            if int(r.from_client or 0) > 0:
                wb_rows.append(
                    SimpleNamespace(
                        nomenclature_id=r.nomenclature_id,
                        warehouse_name=WB_REMAINS_IN_WAY_FROM_CLIENT,
                        qty=int(r.from_client),
                    )
                )

    # 4. In-transit (SHIPPED assembly request items) grouped by nomenclature_id
    shipped_query = (
        select(
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status == AssemblyStatus.SHIPPED,
            # Зеркала FBS — не транзит на склад WB: их единицы уже списаны
            # writeoff_completed_orders и уехали ПОКУПАТЕЛЮ. Без фильтра
            # проданный товар оседал фантомом в «в пути» и «Итого — капитал».
            AssemblyRequest.kind != AssemblyKind.FBS.value,
        )
        .group_by(AssemblyRequestItem.nomenclature_id)
        .limit(10000)
    )
    if allowed_nom_ids is not None:
        shipped_query = shipped_query.where(AssemblyRequestItem.nomenclature_id.in_(allowed_nom_ids))
    shipped_result = await db.execute(shipped_query)
    in_transit_map: dict[int, int] = {row.nomenclature_id: int(row.qty or 0) for row in shipped_result.all()}

    # 4b. Factory orders — remaining qty (not yet assigned to vehicles)
    factory_query = (
        select(
            Nomenclature.id.label("nomenclature_id"),
            func.sum(FactoryOrderItem.qty - FactoryOrderItem.assigned_qty).label("remaining"),
        )
        .join(FactoryOrder, FactoryOrderItem.factory_order_id == FactoryOrder.id)
        .join(
            Nomenclature,
            (Nomenclature.barcode == FactoryOrderItem.barcode) & (Nomenclature.project_id == project_id),
        )
        .where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted.is_(False),
            FactoryOrder.status.notin_(["CLOSED"]),
            FactoryOrderItem.project_id == project_id,
            FactoryOrderItem.is_deleted.is_(False),
        )
        .group_by(Nomenclature.id)
        .limit(10000)
    )
    if allowed_nom_ids is not None:
        factory_query = factory_query.where(Nomenclature.id.in_(allowed_nom_ids))
    factory_result = await db.execute(factory_query)
    factory_map: dict[int, int] = {row.nomenclature_id: max(int(row.remaining or 0), 0) for row in factory_result.all()}

    # 4c. Vehicles being formed (FORMING) — items already assigned but not yet shipped
    vehicle_forming_query = (
        select(
            Nomenclature.id.label("nomenclature_id"),
            func.sum(CostOrderItem.qty).label("qty"),
        )
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .join(
            Nomenclature,
            (Nomenclature.barcode == CostOrderItem.barcode) & (Nomenclature.project_id == project_id),
        )
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted.is_(False),
            CostOrder.status == VehicleStatus.FORMING,
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted.is_(False),
        )
        .group_by(Nomenclature.id)
        .limit(10000)
    )
    if allowed_nom_ids is not None:
        vehicle_forming_query = vehicle_forming_query.where(Nomenclature.id.in_(allowed_nom_ids))
    vehicle_forming_result = await db.execute(vehicle_forming_query)
    vehicle_forming_map: dict[int, int] = {
        row.nomenclature_id: int(row.qty or 0) for row in vehicle_forming_result.all()
    }

    # 4d. Vehicles in transit (SHIPPED, CUSTOMS, DISPATCHED — en route to warehouse)
    vehicle_transit_query = (
        select(
            Nomenclature.id.label("nomenclature_id"),
            func.sum(CostOrderItem.qty).label("qty"),
        )
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .join(
            Nomenclature,
            (Nomenclature.barcode == CostOrderItem.barcode) & (Nomenclature.project_id == project_id),
        )
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted.is_(False),
            CostOrder.status.in_([VehicleStatus.SHIPPED, VehicleStatus.CUSTOMS, VehicleStatus.DISPATCHED]),
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted.is_(False),
        )
        .group_by(Nomenclature.id)
        .limit(10000)
    )
    if allowed_nom_ids is not None:
        vehicle_transit_query = vehicle_transit_query.where(Nomenclature.id.in_(allowed_nom_ids))
    vehicle_transit_result = await db.execute(vehicle_transit_query)
    vehicle_transit_map: dict[int, int] = {
        row.nomenclature_id: int(row.qty or 0) for row in vehicle_transit_result.all()
    }

    # 5. Reserved from active assembly requests (all warehouses)
    wh_ids = {row.warehouse_id for row in own_rows}
    reserved_details = await _get_reserved_detail_batch(db, project_id, wh_ids)
    all_reserved: dict[int, int] = {  # nomenclature_id -> total reserved
        nom_id: sum(d["quantity"] for d in rows) for nom_id, rows in reserved_details.items()
    }

    # 6. Nomenclature details (include article_wb for BDR nm_id mapping)
    all_nom_ids: set[int] = set()
    for row in own_rows:
        all_nom_ids.add(row.nomenclature_id)
    for row in wb_rows:
        all_nom_ids.add(row.nomenclature_id)
    all_nom_ids.update(in_transit_map.keys())
    all_nom_ids.update(factory_map.keys())
    all_nom_ids.update(vehicle_forming_map.keys())
    all_nom_ids.update(vehicle_transit_map.keys())

    nom_map: dict[int, dict] = {}
    nm_id_to_nom_id: dict[int, int] = {}  # article_wb -> nomenclature.id
    if all_nom_ids:
        nom_result = await db.execute(
            select(
                Nomenclature.id,
                Nomenclature.barcode,
                Nomenclature.article_seller,
                Nomenclature.article_wb,
                Nomenclature.subject,
                Nomenclature.brand,
            )
            .where(
                Nomenclature.project_id == project_id,
                Nomenclature.id.in_(all_nom_ids),
            )
            .limit(10000)
        )
        for n in nom_result.all():
            nom_map[n.id] = {
                "barcode": n.barcode,
                "article_seller": n.article_seller,
                "article_wb": n.article_wb,
                "subject": n.subject,
                "brand": n.brand,
            }
            if n.article_wb:
                nm_id_to_nom_id[n.article_wb] = n.id

    # 7. Load average costs: CostOrderItem → WbCostOverride → WarehouseStock.cost_price.
    # lifetime_avg — взвешенное среднее по заказам (легаси-путь). fifo/moving_avg —
    # as-of цена из движка оценки (eff_now[method]) по article_seller_lower; остальные
    # ступени приоритета (override → склад) сохраняются.
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides
    from backend.services.cost.valuation import (
        DEFAULT_METHOD,
        compute_project_valuation,
    )
    from backend.services.settings_service import get_valuation_method

    valuation_method = await get_valuation_method(db, project_id)
    if valuation_method == DEFAULT_METHOD:
        avg_costs_by_article = await load_avg_costs(db, project_id)
    else:
        _val = await compute_project_valuation(db, project_id)
        # Skip zero/negative as-of costs so the override → stock priority chain still
        # applies for SKUs the engine can't price (matches lifetime_avg, which simply
        # has no map entry for such SKUs).
        avg_costs_by_article = {
            sku: float(v["eff_now"][valuation_method])
            for sku, v in _val.items()
            if float(v["eff_now"][valuation_method]) > 0
        }
    cost_overrides = await load_cost_overrides(db, project_id)  # nm_id → cost_price

    cost_map: dict[int, float] = {}
    for nom_id, info in nom_map.items():
        # Priority 1: WbCostOverride — РУЧНАЯ цена по nm_id ПОБЕЖДАЕТ движок
        # (канон юзера 2026-07-28): override ставится сознательно, чаще всего
        # там, где закупки не заведены/кривые; раньше цена движка молча затеняла
        # 67 из 133 живых override'ов (±1M на остатке) без какой-либо индикации.
        nm_id = info.get("article_wb")
        if nm_id and nm_id in cost_overrides and cost_overrides[nm_id] > 0:
            cost_map[nom_id] = cost_overrides[nm_id]
            continue
        # Priority 2: per-unit cost by article_seller (case-insensitive) — legacy
        # weighted average or, for fifo/moving_avg, the engine's as-of unit cost.
        article = info.get("article_seller")
        article_key = article.strip().lower() if article else ""
        if article_key and article_key in avg_costs_by_article:
            cost_map[nom_id] = avg_costs_by_article[article_key]

    # Priority 3: WarehouseStock.cost_price (manual per-warehouse)
    for row in own_rows:
        nom_id = row.nomenclature_id
        if nom_id not in cost_map and row.cost_price and float(row.cost_price) > 0:
            cost_map[nom_id] = float(row.cost_price)

    # 7b. Load BDR daily metrics (avg_daily_revenue, avg_daily_profit)
    bdr_map = await _load_bdr_metrics(db, project_id, nm_id_to_nom_id, cost_map)

    # 7c. Estimated per-unit cost for factory items (always computed via markup factor)
    markup_by_subject, global_markup, avg_rate_cny = await _load_category_markup_and_rate(db, project_id)
    factory_unit_costs = await _load_factory_unit_costs(
        db, project_id, nom_map, markup_by_subject, global_markup, avg_rate_cny
    )
    # Use as cost_map fallback when no historical data exists
    estimated_cost_set: set[int] = set()
    for nom_id, est_cost in factory_unit_costs.items():
        if nom_id not in cost_map and est_cost > 0:
            cost_map[nom_id] = est_cost
            estimated_cost_set.add(nom_id)

    # 7d. Category-level BDR averages for new items without sales
    category_bdr = _compute_category_bdr_averages(bdr_map, nom_map)

    # 7e. «Новинка» detection — SKU with no sales in the last 60 days.
    # A truly new product for the warehouse: either it's a brand-new listing
    # or an item that hasn't moved in 2+ months. Used by the Новинки KPI card
    # so the user can track expected revenue/cost of incoming + on-shelf new
    # items separately from the bulk of ongoing catalog.
    novelty_cutoff_dt = utcnow().date() - timedelta(days=60)
    sold_result = await db.execute(
        select(WbFinanceRow.nm_id)
        .distinct()
        .where(
            WbFinanceRow.project_id == project_id,
            func.coalesce(WbFinanceRow.sale_dt, WbFinanceRow.rr_dt) >= novelty_cutoff_dt,
            WbFinanceRow.doc_type_name == "Продажа",
            WbFinanceRow.supplier_oper_name.in_(["Продажа", "Возврат"]),
            WbFinanceRow.nm_id.isnot(None),
            WbFinanceRow.nm_id != 0,
        )
        .limit(50000)
    )
    sold_last_60d: set[int] = {int(r[0]) for r in sold_result.all() if r[0]}

    # 8. Merge all by nomenclature_id
    unified: dict[int, dict] = {}

    def _ensure(nom_id: int) -> dict:
        if nom_id not in unified:
            info = nom_map.get(nom_id, {})
            bdr = bdr_map.get(nom_id, {})
            # Forecast fallback is applied post-hoc (see block below) — keep
            # `_ensure` free of estimation so default totals match БДР exactly.
            is_revenue_estimated = False
            subject = info.get("subject")
            cat_ref = category_bdr.get(subject, {}) if subject else {}
            unified[nom_id] = {
                "nomenclature_id": nom_id,
                "barcode": info.get("barcode", ""),
                "article_seller": info.get("article_seller"),
                "article_wb": info.get("article_wb"),
                "subject": info.get("subject"),
                "brand": info.get("brand"),
                "warehouses": {},
                "wb_stocks": {},
                "in_transit": 0,
                "reserved": 0,
                "reserved_by_warehouse": {},
                "reserved_details": [],
                "total_own": 0,
                "total_wb": 0,
                "wb_in_way_to_client": 0,
                "wb_in_way_from_client": 0,
                "total_defect": 0,
                "transfer_transit": 0,
                "total": 0,
                "factory_qty": 0,
                "vehicle_forming_qty": 0,
                "vehicle_transit_qty": 0,
                "avg_cost": cost_map.get(nom_id, 0),
                "cost_factory_unit": factory_unit_costs.get(nom_id, 0),
                "is_cost_estimated": nom_id in estimated_cost_set,
                "is_revenue_estimated": is_revenue_estimated,
                "avg_daily_revenue": bdr.get("avg_daily_revenue", 0),
                "avg_daily_profit": bdr.get("avg_daily_profit", 0),
                "avg_price": bdr.get("avg_price", 0),
                "avg_profit": bdr.get("avg_profit", 0),
                "trend_7": bdr.get("trend_7", {"avg_daily_qty": 0, "revenue": 0, "profit": 0}),
                "trend_14": bdr.get("trend_14", {"avg_daily_qty": 0, "revenue": 0, "profit": 0}),
                "trend_30": bdr.get("trend_30", {"avg_daily_qty": 0, "revenue": 0, "profit": 0}),
                # Reference per-unit price/profit drawn from the item's subject
                # average. Drives the «Новинки» KPI card — frontend multiplies
                # by available + incoming qty for items flagged `is_novelty`.
                "novelty_unit_revenue": float(cat_ref.get("unit_revenue_30", 0)),
                "novelty_unit_profit": float(cat_ref.get("unit_profit_30", 0)),
                # «Новинка» = no sales recorded in the last 60 days. Includes
                # brand-new listings AND items that went silent for 2+ months.
                # Mapped from `nm_id` → `article_wb`. Items with no `article_wb`
                # (no WB card) default to False — can't tell if they were sold.
                "is_novelty": bool(info.get("article_wb") and int(info["article_wb"]) not in sold_last_60d),
            }
        return unified[nom_id]

    # Own warehouse stock. in_transit/defect_in_transit — отправленные, но ещё
    # не принятые перемещения между нашими складами (send_transfer кредитует их
    # на склад-назначение): это наш товар в пути, без него SKU, целиком уехавший
    # в TR, пропадал из сводки (кейс TR-21: 9 234 шт невидимы 12 дней).
    for row in own_rows:
        transit = int(row.in_transit or 0) + int(row.defect_in_transit or 0)
        if row.quantity <= 0 and row.defect_quantity <= 0 and transit <= 0:
            continue
        entry = _ensure(row.nomenclature_id)
        wh_name = wh_name_map.get(row.warehouse_id, str(row.warehouse_id))
        if row.quantity > 0:
            entry["warehouses"][wh_name] = entry["warehouses"].get(wh_name, 0) + row.quantity
            entry["total_own"] += row.quantity
        if row.defect_quantity > 0:
            entry["total_defect"] += row.defect_quantity
        if transit > 0:
            entry["transfer_transit"] += transit

    # WB stock. «В пути…» — отдельные поля-колонки, не разбивка по складам;
    # total_wb = только физически на складах WB («Всего находится на складах»).
    for row in wb_rows:
        qty = int(row.qty or 0)
        if qty <= 0:
            continue
        entry = _ensure(row.nomenclature_id)
        if row.warehouse_name == WB_REMAINS_IN_WAY_TO_CLIENT:
            entry["wb_in_way_to_client"] += qty
            continue
        if row.warehouse_name == WB_REMAINS_IN_WAY_FROM_CLIENT:
            entry["wb_in_way_from_client"] += qty
            continue
        entry["wb_stocks"][row.warehouse_name] = entry["wb_stocks"].get(row.warehouse_name, 0) + qty
        entry["total_wb"] += qty

    # In-transit
    for nom_id, qty in in_transit_map.items():
        entry = _ensure(nom_id)
        entry["in_transit"] = qty

    # Reserved (+ разбивка по заявкам для раскрывающейся ячейки и по складам,
    # чтобы колонка каждого ФФ показывала «свободно / в сборке», а не сырой остаток)
    for nom_id, qty in all_reserved.items():
        if nom_id in unified:
            named = [
                {**d, "warehouse_name": wh_name_map.get(d["warehouse_id"], str(d["warehouse_id"]))}
                for d in reserved_details.get(nom_id, ())
            ]
            by_wh: dict[str, int] = {}
            for d in named:
                by_wh[d["warehouse_name"]] = by_wh.get(d["warehouse_name"], 0) + d["quantity"]
            unified[nom_id]["reserved"] = qty
            unified[nom_id]["reserved_by_warehouse"] = by_wh
            unified[nom_id]["reserved_details"] = sorted(
                named, key=lambda d: d["quantity"], reverse=True
            )

    # Factory orders (remaining)
    for nom_id, qty in factory_map.items():
        entry = _ensure(nom_id)
        entry["factory_qty"] = qty

    # Vehicles (forming)
    for nom_id, qty in vehicle_forming_map.items():
        entry = _ensure(nom_id)
        entry["vehicle_forming_qty"] = qty

    # Vehicles (in transit)
    for nom_id, qty in vehicle_transit_map.items():
        entry = _ensure(nom_id)
        entry["vehicle_transit_qty"] = qty

    # Compute totals. «Итого» — КАПИТАЛ в товаре (канон 2026-07-28):
    # «В пути до получателей» входит — до выкупа товар всё ещё наш (невыкуп
    # вернётся); брак входит — это наш товар по себестоимости, пусть и с другой
    # ликвидностью. Для скорости продаж / «запаса дней» фронт считает отдельный
    # sellable-срез без этих двух слагаемых. Supply chain — отдельные колонки.
    for entry in unified.values():
        entry["total"] = (
            entry["total_own"]
            + entry["total_wb"]
            + entry["wb_in_way_to_client"]
            + entry["wb_in_way_from_client"]
            + entry["in_transit"]
            + entry["total_defect"]
            + entry["transfer_transit"]
        )

    # Narrowed forecast fallback — only runs in forecast mode. Estimates
    # revenue/profit for items that are en route but not yet physically in
    # stock (factory/vehicle FORMING/SHIPPED/CUSTOMS/DISPATCHED). Existing
    # shelf stock without sales stays at zero. Default mode leaves everything
    # untouched so group totals match БДР.
    if include_forecast:
        _apply_forecast_fallback(unified, category_bdr)

    unified_list = [
        v
        for v in unified.values()
        if v["total"] > 0 or v["factory_qty"] > 0 or v["vehicle_forming_qty"] > 0 or v["vehicle_transit_qty"] > 0
    ]

    # 9. Grouping
    if group_by == "abc":
        # ABC: flat list per article with abc_class badge, sorted by revenue desc
        _assign_abc(unified_list, "avg_daily_revenue", "abc_class")
        unified_list.sort(key=lambda r: r.get("avg_daily_revenue", 0), reverse=True)
        return unified_list

    if group_by == "sku":
        return unified_list

    return await _group_unified(db, project_id, unified_list, group_by)


async def _group_unified(
    db: AsyncSession,
    project_id: int,
    unified_list: list[dict],
    group_by: str,
) -> list[dict]:
    """Aggregate unified stock rows by the requested dimension."""
    # Pre-load lookup maps depending on group_by
    tag_map: dict[int, str] = {}
    imt_map: dict[int, int] = {}
    imt_aliases: dict[int, str] = {}

    if group_by == "tag":
        tag_map = await _load_tag_map(db, project_id)
    elif group_by == "imt":
        nom_ids = {r["nomenclature_id"] for r in unified_list}
        imt_map = await _load_imt_map(db, project_id, nom_ids)
        imt_aliases = await _load_imt_aliases(db, project_id)

    def _get_group_key(row: dict) -> str:
        nm_id = row.get("article_wb")
        nom_id = row["nomenclature_id"]
        if group_by == "brand":
            return row.get("brand") or "Без бренда"
        if group_by == "subject":
            return row.get("subject") or "Без категории"
        if group_by == "imt":
            imt_id = imt_map.get(nom_id)
            if imt_id is None:
                return "Без склейки"
            alias = imt_aliases.get(imt_id)
            return alias or f"IMT {imt_id}"
        if group_by in ("tag", "abc"):
            if nm_id and nm_id in tag_map:
                return tag_map[nm_id]
            return "Без ярлыка"
        return str(nom_id)

    def _init_group(name: str) -> dict:
        return {
            "group_name": name,
            "items_count": 0,
            "total_own": 0,
            "total_wb": 0,
            "wb_in_way_to_client": 0,
            "wb_in_way_from_client": 0,
            "in_transit": 0,
            "reserved": 0,
            "reserved_by_warehouse": {},
            "reserved_details": [],
            "total": 0,
            "total_defect": 0,
            "transfer_transit": 0,
            "factory_qty": 0,
            "vehicle_forming_qty": 0,
            "vehicle_transit_qty": 0,
            "avg_cost": 0.0,
            "cost_factory_unit": 0.0,
            "avg_daily_revenue": 0.0,
            "avg_daily_profit": 0.0,
            "avg_price": 0.0,
            "avg_profit": 0.0,
            "warehouses": {},
            "wb_stocks": {},
            "trend_7": {"avg_daily_qty": 0.0, "revenue": 0.0, "profit": 0.0},
            "trend_14": {"avg_daily_qty": 0.0, "revenue": 0.0, "profit": 0.0},
            "trend_30": {"avg_daily_qty": 0.0, "revenue": 0.0, "profit": 0.0},
            "_cost_sum": 0.0,
            "_cost_weight": 0,
            "_cost_factory_sum": 0.0,
            "_cost_factory_weight": 0,
            "_price_sum": 0.0,
            "_price_weight": 0,
            "_profit_sum": 0.0,
            "_profit_weight": 0,
        }

    def _accumulate(g: dict, row: dict) -> None:
        g["items_count"] += 1
        for k in (
            "total_own",
            "total_wb",
            "wb_in_way_to_client",
            "wb_in_way_from_client",
            "in_transit",
            "reserved",
            "total",
            "total_defect",
            "transfer_transit",
            "factory_qty",
            "vehicle_forming_qty",
            "vehicle_transit_qty",
        ):
            g[k] += row.get(k, 0)
        g["avg_daily_revenue"] += row.get("avg_daily_revenue", 0)
        g["avg_daily_profit"] += row.get("avg_daily_profit", 0)
        for period in ("trend_7", "trend_14", "trend_30"):
            rp = row.get(period, {})
            gp = g[period]
            for key in ("avg_daily_qty", "revenue", "profit"):
                gp[key] += rp.get(key, 0)
        t = row["total"]
        for field, s, w in [
            ("avg_cost", "_cost_sum", "_cost_weight"),
            ("avg_price", "_price_sum", "_price_weight"),
            ("avg_profit", "_profit_sum", "_profit_weight"),
        ]:
            if row.get(field) and t > 0:
                g[s] += row[field] * t
                g[w] += t
        # cost_factory_unit weighted by factory_qty
        fq = row.get("factory_qty", 0)
        cfu = row.get("cost_factory_unit", 0)
        if cfu and fq > 0:
            g["_cost_factory_sum"] += cfu * fq
            g["_cost_factory_weight"] += fq
        for wh_name, qty in row.get("warehouses", {}).items():
            g["warehouses"][wh_name] = g["warehouses"].get(wh_name, 0) + qty
        for wh_name, qty in row.get("wb_stocks", {}).items():
            g["wb_stocks"][wh_name] = g["wb_stocks"].get(wh_name, 0) + qty
        for wh_name, qty in row.get("reserved_by_warehouse", {}).items():
            g["reserved_by_warehouse"][wh_name] = g["reserved_by_warehouse"].get(wh_name, 0) + qty
        # Одна заявка держит несколько SKU группы — склеиваем по заявке, иначе
        # в раскрытой ячейке один и тот же ASM-номер повторится N раз.
        for d in row.get("reserved_details", ()):
            g["_reserved_by_request"] = g.get("_reserved_by_request", {})
            acc = g["_reserved_by_request"].get(d["request_id"])
            if acc is None:
                g["_reserved_by_request"][d["request_id"]] = {**d}
            else:
                acc["quantity"] += d["quantity"]

    def _finalize(g: dict) -> None:
        by_request = g.pop("_reserved_by_request", {})
        g["reserved_details"] = sorted(
            by_request.values(), key=lambda d: d["quantity"], reverse=True
        )
        for field, s, w in [
            ("avg_cost", "_cost_sum", "_cost_weight"),
            ("avg_price", "_price_sum", "_price_weight"),
            ("avg_profit", "_profit_sum", "_profit_weight"),
        ]:
            if g[w] > 0:
                g[field] = round(g[s] / g[w], 2)
            del g[s], g[w]
        if g["_cost_factory_weight"] > 0:
            g["cost_factory_unit"] = round(g["_cost_factory_sum"] / g["_cost_factory_weight"], 2)
        del g["_cost_factory_sum"], g["_cost_factory_weight"]
        g["avg_daily_revenue"] = round(g["avg_daily_revenue"], 2)
        g["avg_daily_profit"] = round(g["avg_daily_profit"], 2)
        for period in ("trend_7", "trend_14", "trend_30"):
            gp = g[period]
            for key in ("avg_daily_qty", "revenue", "profit"):
                gp[key] = round(gp[key], 2)

    grouped: dict[str, dict] = {}

    if group_by == "brand":
        # Two-level hierarchy: brand → subjects → articles
        for row in unified_list:
            brand_key = row.get("brand") or "Без бренда"
            subject_key = row.get("subject") or "Без категории"

            if brand_key not in grouped:
                g = _init_group(brand_key)
                g["_subjects"] = {}
                grouped[brand_key] = g

            g = grouped[brand_key]
            _accumulate(g, row)

            # Track subjects within brand
            subj_map = g["_subjects"]
            if subject_key not in subj_map:
                s = _init_group(subject_key)
                s["children"] = []
                subj_map[subject_key] = s

            s = subj_map[subject_key]
            _accumulate(s, row)
            s["children"].append(row)

        # Finalize brand groups and convert subjects
        result_list = []
        for g in grouped.values():
            _finalize(g)
            subjects = []
            for s in g.pop("_subjects").values():
                _finalize(s)
                s["children"].sort(key=lambda r: r.get("total", 0), reverse=True)
                subjects.append(s)
            subjects.sort(key=lambda s: s.get("total", 0), reverse=True)
            g["children"] = subjects
            result_list.append(g)
    else:
        # Single-level children: group → article rows
        for row in unified_list:
            key = _get_group_key(row)
            if key not in grouped:
                g = _init_group(key)
                g["children"] = []
                grouped[key] = g

            g = grouped[key]
            _accumulate(g, row)
            g["children"].append(row)

        result_list = []
        for g in grouped.values():
            _finalize(g)
            g["children"].sort(key=lambda r: r.get("total", 0), reverse=True)
            result_list.append(g)

    # ABC classification by avg_daily_revenue
    if group_by == "abc":
        _assign_abc(result_list, "avg_daily_revenue", "abc_class")

    return result_list


async def update_cost_price(db: AsyncSession, project_id: int, stock_id: int, cost_price) -> WarehouseStock | None:
    """Set manual cost_price on a warehouse_stock row."""
    result = await db.execute(
        select(WarehouseStock).where(
            WarehouseStock.id == stock_id,
            WarehouseStock.project_id == project_id,
        )
    )
    stock = result.scalar_one_or_none()
    if not stock:
        return None

    stock.cost_price = cost_price
    stock.updated_at = utcnow()
    await db.commit()
    await db.refresh(stock)
    # Iron rule 7: changing a unit cost feeds COGS in BDR/ОПИУ/funnel/stock summaries.
    await invalidate_project_reports(project_id)
    return stock
