# ruff: noqa: RUF001, RUF002, RUF003
"""
Вкладка «Динамика расхождения остатков» страницы «Анализ сборки».

`FulfillmentStock`/`WarehouseStock` хранят только ТЕКУЩИЙ снимок остатков —
по ним нельзя понять, растёт расхождение «наш склад vs ФФ-зеркало» или
самоустраняется. Эта служба копит историю вперёд: ежедневная scheduler-джоба
пишет строки `assembly_stock_mismatch_daily` — ТОЛЬКО (склад, эффективный ШК)
с diff≠0 в этот день. Источник diff — ЕДИНОЕ ядро
`link_anomalies.compute_stock_mismatch_cells` (то же, что кормит живую вкладку
«Связи и расхождения» → история ≡ живому блоку).

Наличие/отсутствие строки за день кодирует изменение: строка появилась —
«появился mismatch», строка пропала на следующий день после того, как была —
«сошёлся». `get_changes` восстанавливает журнал событий (appeared/resolved/
grew/shrank/flipped) по фактическим ДНЯМ СРЕЗА (а не по календарным дням —
если джоба день пропустила, соседним считается следующий реально записанный
день project-а/склада): для каждого SKU идём по глобальному списку дней,
когда ХОТЬ ГДЕ-ТО в отфильтрованной выборке была строка («дни среза»), и на
каждом дне присутствия SKU сравниваем с соседним днём среза — присутствовал
ли SKU там же. Так «сошёлся» датируется первым днём среза, где SKU уже нет
(а не последним днём, где он ещё был), а «появился» — первым днём присутствия
без более раннего соседа.
"""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyStockMismatchDaily
from backend.models.cost import Nomenclature
from backend.models.refs import CategoryOverride
from backend.models.warehouse import Warehouse
from backend.services.assembly.link_anomalies import compute_stock_mismatch_cells
from backend.utils.time import utcnow

MSK_OFFSET = timedelta(hours=3)
FALLBACK_CATEGORY = "Без категории"
RETENTION_DAYS = 90
# Окно (роутер: days ≤ 90) × склады × SKU — разумный потолок для одной выборки.
_ROWS_CAP = 200_000

_EVENT_LABELS_RU = {
    "appeared": "Появился",
    "resolved": "Сошёлся",
    "grew": "Вырос",
    "shrank": "Уменьшился",
    "flipped": "Сменил знак",
}


def msk_today() -> date:
    """Текущий MSK-день (для day=None в snapshot_project и границ окна истории)."""
    return (utcnow() + MSK_OFFSET).date()


async def _category_by_nomenclature_id(
    db: AsyncSession, project_id: int, nomenclature_ids: set[int]
) -> dict[int, str]:
    """Эффективная категория per Nomenclature.id (PK): CategoryOverride ?? subject.

    Зеркалит `draft_category_history._category_by_nm`, но ключ — ВНУТРЕННИЙ PK
    (`Nomenclature.id`, как несёт `compute_stock_mismatch_cells`), не WB nm_id
    (`article_wb`, как в JSONB черновиков). `CategoryOverride.nm_id` хранит WB
    nm_id → резолвим через `article_wb`, чтобы найти нужный override.
    """
    if not nomenclature_ids:
        return {}
    result: dict[int, str] = {}
    nm_to_pk: dict[int, int] = {}
    noms = await db.execute(
        select(Nomenclature.id, Nomenclature.article_wb, Nomenclature.subject)
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.id.in_(nomenclature_ids),
        )
        .limit(len(nomenclature_ids))
    )
    for pk, nm, subject in noms.all():
        if subject:
            result[int(pk)] = str(subject)
        if nm:
            nm_to_pk[int(nm)] = int(pk)

    if nm_to_pk:
        overrides = await db.execute(
            select(CategoryOverride.nm_id, CategoryOverride.category_value)
            .where(
                CategoryOverride.project_id == project_id,
                CategoryOverride.nm_id.in_(nm_to_pk.keys()),
            )
            .limit(len(nm_to_pk))
        )
        for nm, value in overrides.all():
            pk = nm_to_pk.get(int(nm)) if nm else None
            if pk is not None and value:
                result[pk] = str(value)  # override перебивает subject
    return result


async def snapshot_project(db: AsyncSession, project_id: int, day: date | None = None) -> int:
    """Записать снимок расхождения дня (idempotent: снимок дня перезаписывается).

    Считает через `compute_stock_mismatch_cells` (то же ядро, что живая вкладка),
    UPSERT-ит строки дня и удаляет строки этого дня, чьих (warehouse_id, barcode)
    в свежем расчёте больше нет (сошлись). Коммитит сам. Возвращает число строк.
    """
    snapshot_date = day or msk_today()
    cells = await compute_stock_mismatch_cells(db, project_id, None)

    nom_ids = {c["nomenclature_id"] for c in cells if c["nomenclature_id"]}
    category_by_nm = await _category_by_nomenclature_id(db, project_id, nom_ids)

    fresh_keys: set[tuple[int, str]] = set()
    now = utcnow()
    for c in cells:
        barcode = str(c["barcode"])[:64]
        fresh_keys.add((c["warehouse_id"], barcode))
        category = category_by_nm.get(c["nomenclature_id"]) or FALLBACK_CATEGORY
        article_seller = c.get("article_seller")
        values = {
            "project_id": project_id,
            "snapshot_date": snapshot_date,
            "warehouse_id": c["warehouse_id"],
            "provider": c.get("provider"),
            "barcode": barcode,
            "nomenclature_id": c["nomenclature_id"],
            "article_seller": article_seller[:255] if article_seller else None,
            "category": category[:100],
            "ff_good": c["ff_good"],
            "ff_logistics": c["ff_logistics"],
            "our_quantity": c["our_quantity"],
            "our_defect": c["our_defect"],
            "diff": c["diff"],
            "created_at": now,
            "updated_at": now,
        }
        await db.execute(
            pg_insert(AssemblyStockMismatchDaily)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_asm_stock_mismatch_daily",
                set_={
                    k: values[k]
                    for k in (
                        "provider",
                        "nomenclature_id",
                        "article_seller",
                        "category",
                        "ff_good",
                        "ff_logistics",
                        "our_quantity",
                        "our_defect",
                        "diff",
                        "updated_at",
                    )
                },
            )
        )

    # Сошедшиеся SKU: были в снимке дня раньше (либо повторный прогон дня),
    # в свежем расчёте их уже нет — строка дня для них подлежит удалению.
    stale = delete(AssemblyStockMismatchDaily).where(
        AssemblyStockMismatchDaily.project_id == project_id,
        AssemblyStockMismatchDaily.snapshot_date == snapshot_date,
    )
    if fresh_keys:
        stale = stale.where(
            tuple_(AssemblyStockMismatchDaily.warehouse_id, AssemblyStockMismatchDaily.barcode).not_in(
                list(fresh_keys)
            )
        )
    await db.execute(stale)
    await db.commit()
    return len(cells)


async def purge_old(db: AsyncSession, retention_days: int = RETENTION_DAYS) -> int:
    """Ретенция: снимки старше retention_days удаляются (все проекты)."""
    cutoff = msk_today() - timedelta(days=retention_days)
    result = await db.execute(
        delete(AssemblyStockMismatchDaily).where(AssemblyStockMismatchDaily.snapshot_date < cutoff)
    )
    await db.commit()
    return result.rowcount or 0  # type: ignore[attr-defined]


async def _resolve_warehouse_meta(
    db: AsyncSession, project_id: int, warehouse_ids: set[int]
) -> dict[int, tuple[str | None, str | None]]:
    """warehouse_id → (name, provider) для селектора/журнала (провайдер — MAX по строкам окна)."""
    if not warehouse_ids:
        return {}
    providers = {
        wid: prov
        for wid, prov in (
            await db.execute(
                select(
                    AssemblyStockMismatchDaily.warehouse_id,
                    func.max(AssemblyStockMismatchDaily.provider),
                )
                .where(
                    AssemblyStockMismatchDaily.project_id == project_id,
                    AssemblyStockMismatchDaily.warehouse_id.in_(warehouse_ids),
                )
                .group_by(AssemblyStockMismatchDaily.warehouse_id)
                .limit(len(warehouse_ids))
            )
        ).all()
    }
    names = {
        wid: name
        for wid, name in (
            await db.execute(
                select(Warehouse.id, Warehouse.name)
                .where(
                    Warehouse.project_id == project_id,
                    Warehouse.id.in_(warehouse_ids),
                )
                .limit(len(warehouse_ids))
            )
        ).all()
    }
    return {wid: (names.get(wid), providers.get(wid)) for wid in warehouse_ids}


async def get_history(
    db: AsyncSession,
    project_id: int,
    days: int,
    warehouse_id: int | None = None,
    category: str | None = None,
) -> dict:
    """StockMismatchHistoryResponse-shape: точки (склад×день) + справочники фильтров.

    `warehouses`/`categories` — по ПОЛНОМУ окну (project_id + дата), без учёта
    текущих warehouse_id/category — так селекторы дают переключиться, а не
    схлопываются до одного значения при уже применённом фильтре.
    """
    since = msk_today() - timedelta(days=days - 1)

    point_filters = [
        AssemblyStockMismatchDaily.project_id == project_id,
        AssemblyStockMismatchDaily.snapshot_date >= since,
    ]
    if warehouse_id is not None:
        point_filters.append(AssemblyStockMismatchDaily.warehouse_id == warehouse_id)
    if category is not None:
        point_filters.append(AssemblyStockMismatchDaily.category == category)

    rows = (
        await db.execute(
            select(
                AssemblyStockMismatchDaily.snapshot_date,
                AssemblyStockMismatchDaily.warehouse_id,
                AssemblyStockMismatchDaily.diff,
            )
            .where(*point_filters)
            .limit(_ROWS_CAP)
        )
    ).all()

    acc: dict[tuple[date, int], dict[str, int]] = {}
    for snap_date, wid, diff in rows:
        cell = acc.setdefault(
            (snap_date, wid),
            {"surplus_ff_qty": 0, "surplus_ff_sku": 0, "surplus_our_qty": 0, "surplus_our_sku": 0},
        )
        if diff > 0:
            cell["surplus_ff_qty"] += diff
            cell["surplus_ff_sku"] += 1
        elif diff < 0:
            cell["surplus_our_qty"] += -diff
            cell["surplus_our_sku"] += 1

    points = [
        {
            "snapshot_date": snap_date.isoformat(),
            "warehouse_id": wid,
            "surplus_ff_qty": cell["surplus_ff_qty"],
            "surplus_ff_sku": cell["surplus_ff_sku"],
            "surplus_our_qty": cell["surplus_our_qty"],
            "surplus_our_sku": cell["surplus_our_sku"],
            "net_diff": cell["surplus_ff_qty"] - cell["surplus_our_qty"],
        }
        for (snap_date, wid), cell in acc.items()
    ]
    points.sort(key=lambda p: (p["snapshot_date"], p["warehouse_id"]))

    # Полное окно (без warehouse_id/category) — для справочников фильтра.
    wh_rows = (
        await db.execute(
            select(AssemblyStockMismatchDaily.warehouse_id)
            .where(
                AssemblyStockMismatchDaily.project_id == project_id,
                AssemblyStockMismatchDaily.snapshot_date >= since,
            )
            .distinct()
            .limit(2000)
        )
    ).all()
    wh_ids = {wid for (wid,) in wh_rows}
    wh_meta = await _resolve_warehouse_meta(db, project_id, wh_ids)
    warehouses = [
        {"warehouse_id": wid, "warehouse_name": meta[0], "provider": meta[1]}
        for wid, meta in sorted(wh_meta.items())
    ]

    cat_rows = (
        await db.execute(
            select(AssemblyStockMismatchDaily.category)
            .where(
                AssemblyStockMismatchDaily.project_id == project_id,
                AssemblyStockMismatchDaily.snapshot_date >= since,
                AssemblyStockMismatchDaily.category.isnot(None),
            )
            .distinct()
            .limit(500)
        )
    ).all()
    categories = sorted({cat for (cat,) in cat_rows if cat})

    return {"points": points, "warehouses": warehouses, "categories": categories}


class _Row:
    __slots__ = ("diff", "ff_good", "our_quantity", "article_seller", "category")

    def __init__(self, diff: int, ff_good: int, our_quantity: int, article_seller: str | None, category: str | None):
        self.diff = diff
        self.ff_good = ff_good
        self.our_quantity = our_quantity
        self.article_seller = article_seller
        self.category = category


def _event_for(prev_diff: int, cur_diff: int) -> str | None:
    """appeared/resolved/grew/shrank/flipped/None(без изменений) по паре diff."""
    if prev_diff == 0 and cur_diff == 0:
        return None
    if prev_diff == 0:
        return "appeared"
    if cur_diff == 0:
        return "resolved"
    prev_sign = 1 if prev_diff > 0 else -1
    cur_sign = 1 if cur_diff > 0 else -1
    if prev_sign != cur_sign:
        return "flipped"
    if abs(cur_diff) > abs(prev_diff):
        return "grew"
    if abs(cur_diff) < abs(prev_diff):
        return "shrank"
    return None


async def get_changes(
    db: AsyncSession,
    project_id: int,
    days: int,
    warehouse_id: int | None = None,
    category: str | None = None,
) -> dict:
    """StockMismatchChangesResponse-shape: журнал изменений, свежие дни сверху.

    См. docstring модуля — соседство по фактическим дням среза, не по календарю.
    Буфер в 1 день ДО окна нужен только чтобы граничный день окна корректно
    отличал «появился с нуля» от «продолжение расхождения, начатого вчера»;
    события буферного дня в ответ не попадают (фильтр `>= since`).
    """
    since = msk_today() - timedelta(days=days - 1)
    fetch_from = since - timedelta(days=1)

    filters = [
        AssemblyStockMismatchDaily.project_id == project_id,
        AssemblyStockMismatchDaily.snapshot_date >= fetch_from,
    ]
    if warehouse_id is not None:
        filters.append(AssemblyStockMismatchDaily.warehouse_id == warehouse_id)
    if category is not None:
        filters.append(AssemblyStockMismatchDaily.category == category)

    rows = (
        await db.execute(
            select(
                AssemblyStockMismatchDaily.snapshot_date,
                AssemblyStockMismatchDaily.warehouse_id,
                AssemblyStockMismatchDaily.barcode,
                AssemblyStockMismatchDaily.article_seller,
                AssemblyStockMismatchDaily.category,
                AssemblyStockMismatchDaily.ff_good,
                AssemblyStockMismatchDaily.our_quantity,
                AssemblyStockMismatchDaily.diff,
            )
            .where(*filters)
            .limit(_ROWS_CAP)
        )
    ).all()

    run_days: set[date] = set()
    by_sku: dict[tuple[int, str], dict[date, _Row]] = {}
    for snap_date, wid, barcode, article_seller, cat, ff_good, our_qty, diff in rows:
        run_days.add(snap_date)
        by_sku.setdefault((wid, barcode), {})[snap_date] = _Row(diff, ff_good, our_qty, article_seller, cat)

    run_days_sorted = sorted(run_days)
    run_day_idx = {d: i for i, d in enumerate(run_days_sorted)}

    changes: list[dict] = []
    wh_ids_seen: set[int] = set()
    for (wid, barcode), day_map in by_sku.items():
        for d, cur in sorted(day_map.items()):
            idx = run_day_idx[d]

            # Назад: сосед-день среза, где SKU присутствовал — appeared/grew/shrank/flipped.
            prev_row = day_map.get(run_days_sorted[idx - 1]) if idx > 0 else None
            prev_diff = prev_row.diff if prev_row else 0
            event = _event_for(prev_diff, cur.diff)
            if event and d >= since:
                wh_ids_seen.add(wid)
                changes.append(
                    {
                        "snapshot_date": d.isoformat(),
                        "warehouse_id": wid,
                        "barcode": barcode,
                        "article_seller": cur.article_seller,
                        "name": None,
                        "category": cur.category,
                        "event": event,
                        "prev_diff": prev_diff,
                        "cur_diff": cur.diff,
                        "delta": cur.diff - prev_diff,
                        "prev_ff_good": prev_row.ff_good if prev_row else 0,
                        "cur_ff_good": cur.ff_good,
                        "prev_our_quantity": prev_row.our_quantity if prev_row else 0,
                        "cur_our_quantity": cur.our_quantity,
                    }
                )

            # Вперёд: следующий день среза существует (джоба реально прогонялась),
            # а этого SKU там нет — «сошёлся», датируем ЭТИМ следующим днём.
            if idx < len(run_days_sorted) - 1:
                next_day = run_days_sorted[idx + 1]
                if next_day not in day_map and next_day >= since:
                    wh_ids_seen.add(wid)
                    changes.append(
                        {
                            "snapshot_date": next_day.isoformat(),
                            "warehouse_id": wid,
                            "barcode": barcode,
                            "article_seller": cur.article_seller,
                            "name": None,
                            "category": cur.category,
                            "event": "resolved",
                            "prev_diff": cur.diff,
                            "cur_diff": 0,
                            "delta": -cur.diff,
                            "prev_ff_good": cur.ff_good,
                            "cur_ff_good": 0,
                            "prev_our_quantity": cur.our_quantity,
                            "cur_our_quantity": 0,
                        }
                    )

    wh_meta = await _resolve_warehouse_meta(db, project_id, wh_ids_seen)
    for row in changes:
        row["warehouse_name"] = wh_meta.get(row["warehouse_id"], (None, None))[0]

    changes.sort(key=lambda r: (r["snapshot_date"], r["warehouse_id"], r["barcode"]), reverse=True)
    return {"changes": changes}


def build_mismatch_changes_xlsx(changes: list[dict]) -> bytes:
    """Журнал изменений → .xlsx (те же строки, что `get_changes`, событие по-русски)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Динамика расхождения"
    ws.append(
        [
            "Дата",
            "Склад",
            "Артикул",
            "ШК",
            "Категория",
            "Событие",
            "Было diff",
            "Стало diff",
            "Δ",
            "Было ФФ",
            "Стало ФФ",
            "Было у нас",
            "Стало у нас",
        ]
    )
    for row in changes:
        ws.append(
            [
                row["snapshot_date"],
                row.get("warehouse_name") or row["warehouse_id"],
                row.get("article_seller") or "",
                row["barcode"],
                row.get("category") or "",
                _EVENT_LABELS_RU.get(row["event"], row["event"]),
                row["prev_diff"],
                row["cur_diff"],
                row["delta"],
                row["prev_ff_good"],
                row["cur_ff_good"],
                row["prev_our_quantity"],
                row["cur_our_quantity"],
            ]
        )
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
