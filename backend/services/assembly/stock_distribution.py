# ruff: noqa: RUF002, RUF003
"""
Анализ сборки → вкладка «Распределение остатков»: «где сейчас товар» (100%).

Бакеты (штуки):
  - ff_stock    = SUM(FulfillmentStock.qty_good - qty_reserve, ≥0); короба сведены
                  к россыпи (qty × units_per_box при base_barcode IS NOT NULL).
  - in_assembly = SUM(AssemblyRequestItem.quantity) по сборкам в статусе IN_PROGRESS.
  - ready       = по сборкам в статусах READY + VEHICLE_ASSIGNED.
  - in_transit  = по сборкам в статусе SHIPPED.

Разрезы: total (суммарно), by_warehouse (ФФ-склад), by_status (статус товара из
ProductStatusMap: active/new/clearance/none; join nomenclature_id → Nomenclature.
article_wb → ProductStatusMap.nm_id; без статуса → ключ "none", «Без статуса»).

История по дням (`get_stock_distribution_history`) — поверх таблицы
`assembly_stock_distribution_daily`, которую ежедневно пишет scheduler-джоба
(`snapshot_stock_distribution`): склад ФФ хранится лишь как текущий снимок
(перезатир синком) → динамику копим вперёд, без бэкфилла.
"""

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStockDistributionDaily,
)
from backend.models.cost import Nomenclature
from backend.models.fulfillment import FulfillmentStock
from backend.models.refs import ProductStatusMap
from backend.models.warehouse import Warehouse

# Статусы сборки → бакет пайплайна.
_STATUS_TO_BUCKET: dict[str, str] = {
    AssemblyStatus.IN_PROGRESS.value: "in_assembly",
    AssemblyStatus.READY.value: "ready",
    AssemblyStatus.VEHICLE_ASSIGNED.value: "ready",
    AssemblyStatus.SHIPPED.value: "in_transit",
}
_PIPELINE_STATUSES: tuple[str, ...] = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
    AssemblyStatus.SHIPPED.value,
)

# Ключ статуса товара «нет статуса».
_NONE_STATUS = "none"
_STATUS_LABELS: dict[str, str] = {
    "active": "Активный",
    "new": "Новинка",
    "clearance": "Слив",
    _NONE_STATUS: "Без статуса",
}

_BUCKET_KEYS = ("ff_stock", "in_assembly", "ready", "in_transit")


def _empty_acc() -> dict[str, int]:
    return {"ff_stock": 0, "in_assembly": 0, "ready": 0, "in_transit": 0}


def _finalize_bucket(acc: dict[str, int]) -> dict:
    """Из аккумулятора штук → бакет с total и долями (round(.,1))."""
    total = acc["ff_stock"] + acc["in_assembly"] + acc["ready"] + acc["in_transit"]
    bucket: dict = {**acc, "total": total}
    for key in _BUCKET_KEYS:
        bucket[f"{key}_pct"] = round(acc[key] / total * 100, 1) if total > 0 else 0.0
    return bucket


def _status_key(nomenclature_id: int | None, nom_to_nm: dict[int, int], status_map: dict[int, str]) -> str:
    """nomenclature_id → article_wb (nm_id) → ProductStatusMap.status; иначе 'none'."""
    if nomenclature_id is None:
        return _NONE_STATUS
    nm_id = nom_to_nm.get(nomenclature_id)
    if nm_id is None:
        return _NONE_STATUS
    return status_map.get(nm_id) or _NONE_STATUS


def _status_label(key: str) -> str:
    return _STATUS_LABELS.get(key, key)


async def _compute_distribution_cells(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_ids: list[int] | None = None,
) -> dict[tuple[int, str], dict[str, int]]:
    """Сырые штуки по ячейкам (warehouse_id, status_key) → {ff_stock,in_assembly,ready,in_transit}.

    Единый источник правды для live-распределения, снапшота и (через таблицу) истории.
    product_status-фильтр тут НЕ применяется — это полная разбивка.
    """
    # --- FF-сток: агрегируем штуки в Python (короб→россыпь, max(...,0)) ---
    ff_filters = [FulfillmentStock.project_id == project_id]
    if warehouse_ids:
        ff_filters.append(FulfillmentStock.warehouse_id.in_(warehouse_ids))
    ff_rows = (
        await db.execute(
            select(
                FulfillmentStock.warehouse_id,
                FulfillmentStock.nomenclature_id,
                FulfillmentStock.qty_good,
                FulfillmentStock.qty_reserve,
                FulfillmentStock.base_barcode,
                FulfillmentStock.units_per_box,
            ).where(*ff_filters)
        )
    ).all()

    # --- Пайплайн: SUM(quantity) по складу/статусу/номенклатуре ---
    pipe_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        AssemblyRequest.status.in_(_PIPELINE_STATUSES),
    ]
    if warehouse_ids:
        pipe_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))
    pipe_rows = (
        await db.execute(
            select(
                AssemblyRequest.warehouse_id,
                AssemblyRequest.status,
                AssemblyRequestItem.nomenclature_id,
                func.coalesce(func.sum(AssemblyRequestItem.quantity), 0).label("qty"),
            )
            .join(AssemblyRequestItem, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
            .where(*pipe_filters)
            .group_by(AssemblyRequest.warehouse_id, AssemblyRequest.status, AssemblyRequestItem.nomenclature_id)
        )
    ).all()

    # --- Резолв статуса товара для всех задействованных номенклатур ---
    nom_ids = {r.nomenclature_id for r in ff_rows if r.nomenclature_id is not None}
    nom_ids |= {r.nomenclature_id for r in pipe_rows if r.nomenclature_id is not None}
    nom_to_nm: dict[int, int] = {}
    if nom_ids:
        nm_rows = (
            await db.execute(
                select(Nomenclature.id, Nomenclature.article_wb).where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.id.in_(nom_ids),
                )
            )
        ).all()
        nom_to_nm = {row.id: row.article_wb for row in nm_rows if row.article_wb is not None}

    status_map = {
        r.nm_id: r.status
        for r in (
            await db.execute(
                select(ProductStatusMap.nm_id, ProductStatusMap.status).where(ProductStatusMap.project_id == project_id)
            )
        ).all()
    }

    cells: dict[tuple[int, str], dict[str, int]] = {}

    def _add(warehouse_id: int, status_key: str, bucket_key: str, qty: int) -> None:
        cells.setdefault((warehouse_id, status_key), _empty_acc())[bucket_key] += qty

    for r in ff_rows:
        units_per_box = r.units_per_box or 1
        available = max(r.qty_good - r.qty_reserve, 0) * units_per_box
        if available == 0:
            continue
        _add(r.warehouse_id, _status_key(r.nomenclature_id, nom_to_nm, status_map), "ff_stock", available)

    for pr in pipe_rows:
        qty = int(pr.qty)
        if qty == 0:
            continue
        _add(pr.warehouse_id, _status_key(pr.nomenclature_id, nom_to_nm, status_map), _STATUS_TO_BUCKET[pr.status], qty)

    return cells


@cached(prefix="reports:assembly_stock_distribution", ttl=300)
async def get_stock_distribution(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_ids: list[int] | None = None,
    product_status: str | None = None,
) -> dict:
    """StockDistributionResponse-shape (см. backend/schemas/assembly.py)."""
    cells = await _compute_distribution_cells(db, project_id, warehouse_ids=warehouse_ids)

    total_acc = _empty_acc()
    by_wh: dict[int, dict[str, int]] = {}
    by_status: dict[str, dict[str, int]] = {}

    for (warehouse_id, status_key), acc in cells.items():
        for bucket_key in _BUCKET_KEYS:
            qty = acc[bucket_key]
            if qty == 0:
                continue
            # total и by_warehouse — с учётом product_status-фильтра.
            if product_status is None or status_key == product_status:
                total_acc[bucket_key] += qty
                by_wh.setdefault(warehouse_id, _empty_acc())[bucket_key] += qty
            # by_status — ВСЕГДА полный разрез (без product_status-фильтра).
            by_status.setdefault(status_key, _empty_acc())[bucket_key] += qty

    # --- Имена складов (батч-резолв; включаем soft-deleted для display) ---
    wh_name_map: dict[int, str] = {}
    if by_wh:
        wh_rows = (
            await db.execute(
                select(Warehouse.id, Warehouse.name).where(
                    Warehouse.project_id == project_id,
                    Warehouse.id.in_(by_wh.keys()),
                )
            )
        ).all()
        wh_name_map = {row.id: row.name for row in wh_rows}

    by_warehouse_groups: list[dict] = [
        {"key": str(wid), "label": wh_name_map.get(wid, str(wid)), "bucket": _finalize_bucket(acc)}
        for wid, acc in by_wh.items()
    ]
    by_warehouse_groups.sort(key=lambda g: (-g["bucket"]["total"], g["key"]))

    by_status_groups: list[dict] = [
        {"key": skey, "label": _status_label(skey), "bucket": _finalize_bucket(acc)} for skey, acc in by_status.items()
    ]
    by_status_groups.sort(key=lambda g: (-g["bucket"]["total"], g["key"]))

    return {
        "total": _finalize_bucket(total_acc),
        "by_warehouse": by_warehouse_groups,
        "by_status": by_status_groups,
    }


async def snapshot_stock_distribution(db: AsyncSession, project_id: int, snapshot_date: date) -> int:
    """Записать снимок распределения за день (idempotent: снимок дня перезаписывается).

    Пишет по строке на (склад, статус товара) с ненулевыми бакетами. Коммит — на
    стороне вызывающего (scheduler-джоба). Возвращает число записанных ячеек.
    """
    cells = await _compute_distribution_cells(db, project_id)
    await db.execute(
        delete(AssemblyStockDistributionDaily).where(
            AssemblyStockDistributionDaily.project_id == project_id,
            AssemblyStockDistributionDaily.snapshot_date == snapshot_date,
        )
    )
    written = 0
    for (warehouse_id, status_key), acc in cells.items():
        if not any(acc.values()):
            continue
        db.add(
            AssemblyStockDistributionDaily(
                project_id=project_id,
                snapshot_date=snapshot_date,
                warehouse_id=warehouse_id,
                product_status=status_key,
                ff_stock=acc["ff_stock"],
                in_assembly=acc["in_assembly"],
                ready=acc["ready"],
                in_transit=acc["in_transit"],
            )
        )
        written += 1
    return written


async def get_stock_distribution_history(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_ids: list[int] | None = None,
    product_status: str | None = None,
) -> dict:
    """StockDistributionHistoryResponse-shape: динамика 4 бакетов по дням.

    Суммирует снимки по (snapshot_date) с учётом фильтров склада/статуса товара.
    """
    filters = [AssemblyStockDistributionDaily.project_id == project_id]
    if date_from:
        filters.append(AssemblyStockDistributionDaily.snapshot_date >= date_from)
    if date_to:
        filters.append(AssemblyStockDistributionDaily.snapshot_date <= date_to)
    if warehouse_ids:
        filters.append(AssemblyStockDistributionDaily.warehouse_id.in_(warehouse_ids))
    if product_status:
        filters.append(AssemblyStockDistributionDaily.product_status == product_status)

    rows = (
        await db.execute(
            select(
                AssemblyStockDistributionDaily.snapshot_date,
                func.coalesce(func.sum(AssemblyStockDistributionDaily.ff_stock), 0),
                func.coalesce(func.sum(AssemblyStockDistributionDaily.in_assembly), 0),
                func.coalesce(func.sum(AssemblyStockDistributionDaily.ready), 0),
                func.coalesce(func.sum(AssemblyStockDistributionDaily.in_transit), 0),
            )
            .where(*filters)
            .group_by(AssemblyStockDistributionDaily.snapshot_date)
            .order_by(AssemblyStockDistributionDaily.snapshot_date)
        )
    ).all()

    daily = [
        {
            "date": snap_date.isoformat(),
            "ff_stock": int(ff),
            "in_assembly": int(ia),
            "ready": int(rd),
            "in_transit": int(it),
        }
        for snap_date, ff, ia, rd, it in rows
    ]
    return {"daily": daily}
