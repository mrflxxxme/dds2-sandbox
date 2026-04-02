"""
Warehouse stock engine — stock balance updates, movements, adjustments, summaries.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import Nomenclature
from backend.models.integrations import WbWarehouseStock
from backend.models.refs import ImtAlias, ProductTag, ProductTagMap
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


# ─── Next number generator ─────────────────────────────────────────────────


async def _next_number(db: AsyncSession, project_id: int, prefix: str, table) -> str:
    """Generate next sequential number for a document (IN-1, OUT-1, TR-1).
    Counts ALL records (including soft-deleted) to avoid number collisions."""
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
) -> None:
    """
    Update warehouse_stock balance and create stock_movement in one go.

    delta: positive = inbound, negative = outbound.
    Raises ValueError if resulting quantity < 0.
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
        )
        db.add(stock)
        await db.flush()

    # 2. Check non-negative constraint
    new_qty = stock.quantity + delta
    if new_qty < 0:
        raise ValueError(f"Insufficient stock for barcode {barcode}: " f"have {stock.quantity}, need {abs(delta)}")

    stock.quantity = new_qty
    stock.updated_at = utcnow()

    # 3. Create movement record (audit log)
    movement = StockMovement(
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nomenclature_id,
        barcode=barcode,
        movement_type=movement_type,
        quantity=delta,
        reference_type=reference_type,
        reference_id=reference_id,
        comment=comment,
        created_at=utcnow(),
    )
    db.add(movement)


# ─── Warehouse Stock queries ──────────────────────────────────────────────


async def _get_reserved_map(db: AsyncSession, project_id: int, warehouse_id: int) -> dict[int, int]:
    """Get reserved qty per nomenclature_id from active assembly requests."""
    result = await db.execute(
        select(
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("reserved"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id == warehouse_id,
            AssemblyRequest.is_deleted.is_(False),
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
    return {row.nomenclature_id: row.reserved for row in result.all()}


async def get_warehouse_stock(db: AsyncSession, project_id: int, warehouse_id: int) -> list[dict]:
    """Get current stock for a warehouse, enriched with reserved/available."""
    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.quantity > 0,
        )
        .order_by(WarehouseStock.barcode)
    )
    rows = result.scalars().all()

    reserved_map = await _get_reserved_map(db, project_id, warehouse_id)

    enriched = []
    for row in rows:
        reserved = reserved_map.get(row.nomenclature_id, 0)
        available = max(0, row.quantity - reserved)
        enriched.append(
            {
                "id": row.id,
                "project_id": row.project_id,
                "warehouse_id": row.warehouse_id,
                "nomenclature_id": row.nomenclature_id,
                "barcode": row.barcode,
                "quantity": row.quantity,
                "in_transit": row.in_transit,
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
    )
    rows = result.scalars().all()

    # Collect unique warehouse_ids to get reserved maps
    wh_ids = {row.warehouse_id for row in rows}
    all_reserved: dict[int, dict[int, int]] = {}
    for wh_id in wh_ids:
        all_reserved[wh_id] = await _get_reserved_map(db, project_id, wh_id)

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
            }
        entry = summary[key]
        if row.quantity > 0:
            entry["warehouses"][row.warehouse_id] = row.quantity
            entry["total"] += row.quantity
        if row.in_transit > 0:
            entry["in_transit"][row.warehouse_id] = row.in_transit
            entry["total_in_transit"] += row.in_transit
        # Reserved from assembly requests
        res = all_reserved.get(row.warehouse_id, {}).get(row.nomenclature_id, 0)
        if res > 0:
            entry["reserved"][row.warehouse_id] = res
            entry["total_reserved"] += res

    # Compute available
    for entry in summary.values():
        entry["total_available"] = max(0, entry["total"] - entry["total_reserved"])

    return [v for v in summary.values() if v["total"] > 0 or v["total_in_transit"] > 0]


# ─── Unified Stock (own + WB + in-transit) ────────────────────────────────


async def _load_bdr_metrics(
    db: AsyncSession,
    project_id: int,
    nm_id_to_nom_id: dict[int, int],
) -> dict[int, dict]:
    """Load avg daily revenue/profit AND avg selling price per nomenclature_id.

    From WB finance (last 30 days, doc_type=Продажа):
    - avg_daily_revenue = total_retail_amount / days
    - avg_daily_profit = total_ppvz_for_pay / days
    - avg_price = total_realization / total_quantity (avg selling price per unit, реализация)
    """
    cutoff = utcnow().date() - timedelta(days=30)
    finance_result = await db.execute(
        select(
            WbFinanceRow.nm_id,
            func.sum(WbFinanceRow.retail_price_withdisc_rub).label("total_revenue"),
            func.sum(WbFinanceRow.ppvz_for_pay).label("total_to_pay"),
            func.sum(WbFinanceRow.quantity).label("total_qty"),
            func.count(func.distinct(WbFinanceRow.rr_dt)).label("days_count"),
        )
        .where(
            WbFinanceRow.project_id == project_id,
            WbFinanceRow.rr_dt >= cutoff,
            WbFinanceRow.doc_type_name == "Продажа",
        )
        .group_by(WbFinanceRow.nm_id)
        .limit(10000)
    )
    bdr_map: dict[int, dict] = {}
    for row in finance_result.all():
        nom_id = nm_id_to_nom_id.get(row.nm_id)
        if nom_id is None:
            continue
        days = max(row.days_count, 1)
        revenue = Decimal(str(row.total_revenue or 0))
        to_pay = Decimal(str(row.total_to_pay or 0))
        total_qty = int(row.total_qty or 0)
        avg_price = float(round(revenue / max(total_qty, 1), 2)) if total_qty > 0 else 0
        bdr_map[nom_id] = {
            "avg_daily_revenue": float(round(revenue / days, 2)),
            "avg_daily_profit": float(round(to_pay / days, 2)),
            "avg_price": avg_price,
        }
    return bdr_map


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
            "in_transit": 0,
            "total": 0,
            "avg_cost": 0.0,
            "avg_daily_revenue": 0.0,
            "avg_daily_profit": 0.0,
            "avg_price": 0.0,
            "warehouses": {},
            "wb_stocks": {},
            "_cost_sum": 0.0,
            "_cost_weight": 0,
            "_price_sum": 0.0,
            "_price_weight": 0,
        }

    def _acc(g: dict, row: dict) -> None:
        g["items_count"] += 1
        g["total_own"] += row["total_own"]
        g["total_wb"] += row["total_wb"]
        g["in_transit"] += row["in_transit"]
        g["total"] += row["total"]
        g["avg_daily_revenue"] += row.get("avg_daily_revenue", 0)
        g["avg_daily_profit"] += row.get("avg_daily_profit", 0)
        t = row["total"]
        if row.get("avg_cost") and t > 0:
            g["_cost_sum"] += row["avg_cost"] * t
            g["_cost_weight"] += t
        if row.get("avg_price") and t > 0:
            g["_price_sum"] += row["avg_price"] * t
            g["_price_weight"] += t
        for wh, qty in row.get("warehouses", {}).items():
            g["warehouses"][wh] = g["warehouses"].get(wh, 0) + qty
        for wh, qty in row.get("wb_stocks", {}).items():
            g["wb_stocks"][wh] = g["wb_stocks"].get(wh, 0) + qty

    def _fin(g: dict) -> None:
        if g["_cost_weight"] > 0:
            g["avg_cost"] = round(g["_cost_sum"] / g["_cost_weight"], 2)
        if g["_price_weight"] > 0:
            g["avg_price"] = round(g["_price_sum"] / g["_price_weight"], 2)
        g["avg_daily_revenue"] = round(g["avg_daily_revenue"], 2)
        g["avg_daily_profit"] = round(g["avg_daily_profit"], 2)
        del g["_cost_sum"]
        del g["_cost_weight"]
        del g["_price_sum"]
        del g["_price_weight"]

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
) -> list[dict]:
    """
    Unified stock across own warehouses, WB marketplace, and in-transit.
    Merges WarehouseStock + WbWarehouseStock + SHIPPED assembly items.

    group_by: sku | brand | subject | imt | tag | abc
    """
    # 1. Own warehouse stock grouped by nomenclature_id
    own_result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
        )
        .limit(10000)
    )
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

    # 3. WB warehouse stock -- join with Nomenclature to get nomenclature_id
    #    Use quantity_full (includes in_way_to_client + in_way_from_client)
    wb_result = await db.execute(
        select(
            Nomenclature.id.label("nomenclature_id"),
            WbWarehouseStock.warehouse_name,
            func.sum(WbWarehouseStock.quantity_full).label("qty"),
        )
        .join(Nomenclature, Nomenclature.article_wb == WbWarehouseStock.nm_id)
        .where(
            WbWarehouseStock.project_id == project_id,
            Nomenclature.project_id == project_id,
        )
        .group_by(Nomenclature.id, WbWarehouseStock.warehouse_name)
        .limit(10000)
    )
    wb_rows = wb_result.all()

    # 4. In-transit (SHIPPED assembly request items) grouped by nomenclature_id
    shipped_result = await db.execute(
        select(
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status == AssemblyStatus.SHIPPED,
        )
        .group_by(AssemblyRequestItem.nomenclature_id)
        .limit(10000)
    )
    in_transit_map: dict[int, int] = {row.nomenclature_id: int(row.qty or 0) for row in shipped_result.all()}

    # 5. Reserved from active assembly requests (all warehouses)
    wh_ids = {row.warehouse_id for row in own_rows}
    all_reserved: dict[int, int] = {}  # nomenclature_id -> total reserved
    for wh_id in wh_ids:
        wh_reserved = await _get_reserved_map(db, project_id, wh_id)
        for nom_id, qty in wh_reserved.items():
            all_reserved[nom_id] = all_reserved.get(nom_id, 0) + qty

    # 6. Nomenclature details (include article_wb for BDR nm_id mapping)
    all_nom_ids: set[int] = set()
    for row in own_rows:
        all_nom_ids.add(row.nomenclature_id)
    for row in wb_rows:
        all_nom_ids.add(row.nomenclature_id)
    all_nom_ids.update(in_transit_map.keys())

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

    # 7. Load average costs: CostOrderItem → WbCostOverride → WarehouseStock.cost_price
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides

    avg_costs_by_article = await load_avg_costs(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)  # nm_id → cost_price

    cost_map: dict[int, float] = {}
    for nom_id, info in nom_map.items():
        # Priority 1: CostOrderItem weighted average by article_seller
        article = info.get("article_seller")
        if article and article in avg_costs_by_article:
            cost_map[nom_id] = avg_costs_by_article[article]
            continue
        # Priority 2: WbCostOverride manual override by nm_id
        nm_id = info.get("article_wb")
        if nm_id and nm_id in cost_overrides and cost_overrides[nm_id] > 0:
            cost_map[nom_id] = cost_overrides[nm_id]

    # Priority 3: WarehouseStock.cost_price (manual per-warehouse)
    for row in own_rows:
        nom_id = row.nomenclature_id
        if nom_id not in cost_map and row.cost_price and float(row.cost_price) > 0:
            cost_map[nom_id] = float(row.cost_price)

    # 7b. Load BDR daily metrics (avg_daily_revenue, avg_daily_profit)
    bdr_map = await _load_bdr_metrics(db, project_id, nm_id_to_nom_id)

    # 8. Merge all by nomenclature_id
    unified: dict[int, dict] = {}

    def _ensure(nom_id: int) -> dict:
        if nom_id not in unified:
            info = nom_map.get(nom_id, {})
            bdr = bdr_map.get(nom_id, {})
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
                "total_own": 0,
                "total_wb": 0,
                "total": 0,
                "avg_cost": cost_map.get(nom_id, 0),
                "avg_daily_revenue": bdr.get("avg_daily_revenue", 0),
                "avg_daily_profit": bdr.get("avg_daily_profit", 0),
                "avg_price": bdr.get("avg_price", 0),
            }
        return unified[nom_id]

    # Own warehouse stock
    for row in own_rows:
        if row.quantity <= 0:
            continue
        entry = _ensure(row.nomenclature_id)
        wh_name = wh_name_map.get(row.warehouse_id, str(row.warehouse_id))
        entry["warehouses"][wh_name] = entry["warehouses"].get(wh_name, 0) + row.quantity
        entry["total_own"] += row.quantity

    # WB stock
    for row in wb_rows:
        qty = int(row.qty or 0)
        if qty <= 0:
            continue
        entry = _ensure(row.nomenclature_id)
        entry["wb_stocks"][row.warehouse_name] = entry["wb_stocks"].get(row.warehouse_name, 0) + qty
        entry["total_wb"] += qty

    # In-transit
    for nom_id, qty in in_transit_map.items():
        entry = _ensure(nom_id)
        entry["in_transit"] = qty

    # Reserved
    for nom_id, qty in all_reserved.items():
        if nom_id in unified:
            unified[nom_id]["reserved"] = qty

    # Compute totals
    for entry in unified.values():
        entry["total"] = entry["total_own"] + entry["total_wb"] + entry["in_transit"]

    unified_list = [v for v in unified.values() if v["total"] > 0]

    # 9. Grouping
    if group_by == "abc":
        # ABC: three-level hierarchy brand → subject → articles, with ABC on each article
        _assign_abc(unified_list, "avg_daily_revenue", "abc_class")
        return _group_abc(unified_list)

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
            "in_transit": 0,
            "total": 0,
            "avg_cost": 0.0,
            "avg_daily_revenue": 0.0,
            "avg_daily_profit": 0.0,
            "avg_price": 0.0,
            "warehouses": {},
            "wb_stocks": {},
            "_cost_sum": 0.0,
            "_cost_weight": 0,
            "_price_sum": 0.0,
            "_price_weight": 0,
        }

    def _accumulate(g: dict, row: dict) -> None:
        g["items_count"] += 1
        g["total_own"] += row["total_own"]
        g["total_wb"] += row["total_wb"]
        g["in_transit"] += row["in_transit"]
        g["total"] += row["total"]
        g["avg_daily_revenue"] += row.get("avg_daily_revenue", 0)
        g["avg_daily_profit"] += row.get("avg_daily_profit", 0)
        item_total = row["total"]
        if row.get("avg_cost") and item_total > 0:
            g["_cost_sum"] += row["avg_cost"] * item_total
            g["_cost_weight"] += item_total
        if row.get("avg_price") and item_total > 0:
            g["_price_sum"] += row["avg_price"] * item_total
            g["_price_weight"] += item_total
        for wh_name, qty in row.get("warehouses", {}).items():
            g["warehouses"][wh_name] = g["warehouses"].get(wh_name, 0) + qty
        for wh_name, qty in row.get("wb_stocks", {}).items():
            g["wb_stocks"][wh_name] = g["wb_stocks"].get(wh_name, 0) + qty

    def _finalize(g: dict) -> None:
        if g["_cost_weight"] > 0:
            g["avg_cost"] = round(g["_cost_sum"] / g["_cost_weight"], 2)
        if g["_price_weight"] > 0:
            g["avg_price"] = round(g["_price_sum"] / g["_price_weight"], 2)
        g["avg_daily_revenue"] = round(g["avg_daily_revenue"], 2)
        g["avg_daily_profit"] = round(g["avg_daily_profit"], 2)
        del g["_cost_sum"]
        del g["_cost_weight"]
        del g["_price_sum"]
        del g["_price_weight"]

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
    return stock
