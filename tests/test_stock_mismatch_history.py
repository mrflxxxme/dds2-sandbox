# ruff: noqa: RUF001, RUF002, RUF003
"""
`backend/services/assembly/stock_mismatch_history.py` — динамика расхождения
остатков «наш склад vs ФФ-зеркало».

Покрывает snapshot_project (пишет diff≠0, резолвит категорию, идемпотентный
UPSERT дня, удаление сошедшихся), get_history (агрегат по (склад, день),
фильтры, изоляция project_id) и get_changes (5 событий журнала на
синтетических данных). Фикстуры — зеркало `test_assembly_link_anomalies.py`
(_integrate/_ff_stock/_our_stock).
"""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import CategoryOverride, Nomenclature, Warehouse
from backend.models.assembly import AssemblyStockMismatchDaily
from backend.models.fulfillment import FulfillmentStock
from backend.models.integrations import IntegrationKey
from backend.models.warehouse import WarehouseStock
from backend.services.assembly.stock_mismatch_history import (
    FALLBACK_CATEGORY,
    get_changes,
    get_history,
    msk_today,
    purge_old,
    snapshot_project,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _add(db_session, obj):
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj


@pytest_asyncio.fixture
async def warehouse(db_session, project):
    return await _add(
        db_session,
        Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT"),
    )


async def _integrate(db_session, project_id, warehouse_id, service="skladbot"):
    return await _add(
        db_session,
        IntegrationKey(
            project_id=project_id,
            service=service,
            warehouse_id=warehouse_id,
            encrypted_key="enc",
            label=f"warehouse:{warehouse_id}-{_uid()}",
        ),
    )


async def _ff_stock(db_session, project_id, warehouse_id, barcode, qty_good, *, provider="skladbot", nomenclature_id=None):
    return await _add(
        db_session,
        FulfillmentStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            barcode=barcode,
            qty_good=qty_good,
            nomenclature_id=nomenclature_id,
        ),
    )


async def _our_stock(db_session, project_id, warehouse_id, nomenclature_id, barcode, quantity, defect_quantity=0):
    return await _add(
        db_session,
        WarehouseStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            quantity=quantity,
            defect_quantity=defect_quantity,
        ),
    )


async def _nom(db_session, project_id, barcode, *, article_wb=None, subject=None, article_seller=None):
    return await _add(
        db_session,
        Nomenclature(
            project_id=project_id,
            barcode=barcode,
            article_wb=article_wb,
            subject=subject,
            article_seller=article_seller or f"ART-{_uid()[:6]}",
        ),
    )


async def _row(
    db_session,
    project_id,
    warehouse_id,
    snapshot_date,
    barcode,
    diff,
    *,
    ff_good=0,
    our_quantity=0,
    category="Категория A",
    article_seller=None,
    provider="skladbot",
):
    return await _add(
        db_session,
        AssemblyStockMismatchDaily(
            project_id=project_id,
            snapshot_date=snapshot_date,
            warehouse_id=warehouse_id,
            provider=provider,
            barcode=barcode,
            article_seller=article_seller,
            category=category,
            ff_good=ff_good,
            ff_logistics=0,
            our_quantity=our_quantity,
            our_defect=0,
            diff=diff,
        ),
    )


# ─── snapshot_project ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_writes_mismatch_row_with_category(db_session, project, warehouse):
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc, article_wb=555001, subject="Носки")
    await _ff_stock(db_session, project.id, warehouse.id, bc, 100, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 60)

    written = await snapshot_project(db_session, project.id)
    assert written == 1

    rows = (
        await db_session.execute(
            select(AssemblyStockMismatchDaily).where(AssemblyStockMismatchDaily.project_id == project.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.warehouse_id == warehouse.id
    assert row.barcode == bc
    assert row.ff_good == 100
    assert row.our_quantity == 60
    assert row.diff == 40
    assert row.category == "Носки"
    assert row.snapshot_date == msk_today()


@pytest.mark.asyncio
async def test_snapshot_category_override_wins(db_session, project, warehouse):
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc, article_wb=555002, subject="Носки")
    await _add(
        db_session,
        CategoryOverride(project_id=project.id, nm_id=555002, category_value="Обувь"),
    )
    await _ff_stock(db_session, project.id, warehouse.id, bc, 10, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 0)

    await snapshot_project(db_session, project.id)

    row = (
        await db_session.execute(
            select(AssemblyStockMismatchDaily).where(AssemblyStockMismatchDaily.project_id == project.id)
        )
    ).scalar_one()
    assert row.category == "Обувь"


@pytest.mark.asyncio
async def test_snapshot_no_nomenclature_falls_back_category(db_session, project, warehouse):
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    # Карточка есть, но без subject/article_wb (и override не заведён) — категория
    # не резолвится ни от subject, ни от override → фолбэк.
    nom = await _nom(db_session, project.id, bc, article_wb=None, subject=None)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 10, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 0)

    await snapshot_project(db_session, project.id)

    row = (
        await db_session.execute(
            select(AssemblyStockMismatchDaily).where(AssemblyStockMismatchDaily.project_id == project.id)
        )
    ).scalar_one()
    assert row.category == FALLBACK_CATEGORY


@pytest.mark.asyncio
async def test_snapshot_idempotent_upsert_same_day(db_session, project, warehouse):
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 100, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 60)

    await snapshot_project(db_session, project.id)
    await snapshot_project(db_session, project.id)  # повторный прогон того же дня

    rows = (
        await db_session.execute(
            select(AssemblyStockMismatchDaily).where(AssemblyStockMismatchDaily.project_id == project.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].diff == 40


@pytest.mark.asyncio
async def test_snapshot_deletes_resolved_sku(db_session, project, warehouse):
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    ff = await _ff_stock(db_session, project.id, warehouse.id, bc, 100, nomenclature_id=nom.id)
    ours = await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 60)

    written1 = await snapshot_project(db_session, project.id)
    assert written1 == 1

    # Сходится: остатки выравниваются → diff=0 → cells пустой для этого SKU.
    ff.qty_good = 60
    await db_session.commit()

    written2 = await snapshot_project(db_session, project.id)
    assert written2 == 0

    rows = (
        await db_session.execute(
            select(AssemblyStockMismatchDaily).where(AssemblyStockMismatchDaily.project_id == project.id)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_snapshot_project_isolation(db_session, project, other_project, warehouse):
    other = other_project
    other_wh = await _add(
        db_session, Warehouse(project_id=other.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    )
    await _integrate(db_session, project.id, warehouse.id)
    await _integrate(db_session, other.id, other_wh.id)

    bc = f"20{_uid()}"
    nom_a = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 50, nomenclature_id=nom_a.id)
    await _our_stock(db_session, project.id, warehouse.id, nom_a.id, bc, 10)

    written_other = await snapshot_project(db_session, other.id)
    assert written_other == 0

    written = await snapshot_project(db_session, project.id)
    assert written == 1


# ─── get_history ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_aggregates_by_warehouse_and_day(db_session, project, warehouse):
    today = msk_today()
    yesterday = today - timedelta(days=1)
    await _row(db_session, project.id, warehouse.id, yesterday, "BC1", 30, ff_good=30, our_quantity=0)
    await _row(db_session, project.id, warehouse.id, yesterday, "BC2", -10, ff_good=0, our_quantity=10)
    await _row(db_session, project.id, warehouse.id, today, "BC1", 50, ff_good=50, our_quantity=0)

    res = await get_history(db_session, project.id, 30)
    points = {(p["snapshot_date"], p["warehouse_id"]): p for p in res["points"]}

    y = points[(yesterday.isoformat(), warehouse.id)]
    assert y["surplus_ff_qty"] == 30
    assert y["surplus_ff_sku"] == 1
    assert y["surplus_our_qty"] == 10
    assert y["surplus_our_sku"] == 1
    assert y["net_diff"] == 20

    t = points[(today.isoformat(), warehouse.id)]
    assert t["surplus_ff_qty"] == 50
    assert t["net_diff"] == 50

    wh_ids = {w["warehouse_id"] for w in res["warehouses"]}
    assert warehouse.id in wh_ids
    assert "Категория A" in res["categories"]


@pytest.mark.asyncio
async def test_history_filters_by_warehouse_and_category(db_session, project, warehouse):
    other_wh = await _add(
        db_session, Warehouse(project_id=project.id, name=f"FF2-{_uid()}", warehouse_type="FULFILLMENT")
    )
    today = msk_today()
    await _row(db_session, project.id, warehouse.id, today, "BC1", 10, ff_good=10, category="Обувь")
    await _row(db_session, project.id, other_wh.id, today, "BC2", 20, ff_good=20, category="Носки")

    res = await get_history(db_session, project.id, 30, warehouse_id=warehouse.id)
    assert len(res["points"]) == 1
    assert res["points"][0]["warehouse_id"] == warehouse.id

    res_cat = await get_history(db_session, project.id, 30, category="Носки")
    assert len(res_cat["points"]) == 1
    assert res_cat["points"][0]["warehouse_id"] == other_wh.id


@pytest.mark.asyncio
async def test_history_project_isolation(db_session, project, other_project, warehouse):
    other = other_project
    other_wh = await _add(
        db_session, Warehouse(project_id=other.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    )
    today = msk_today()
    await _row(db_session, project.id, warehouse.id, today, "BC1", 10, ff_good=10)
    await _row(db_session, other.id, other_wh.id, today, "BC2", 20, ff_good=20)

    res = await get_history(db_session, project.id, 30)
    assert len(res["points"]) == 1
    assert res["points"][0]["warehouse_id"] == warehouse.id


@pytest.mark.asyncio
async def test_history_window_excludes_old_days(db_session, project, warehouse):
    today = msk_today()
    old = today - timedelta(days=40)
    await _row(db_session, project.id, warehouse.id, old, "BC1", 10, ff_good=10)
    await _row(db_session, project.id, warehouse.id, today, "BC2", 20, ff_good=20)

    res = await get_history(db_session, project.id, 30)
    dates = {p["snapshot_date"] for p in res["points"]}
    assert old.isoformat() not in dates
    assert today.isoformat() in dates


# ─── get_changes ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_changes_appeared(db_session, project, warehouse):
    today = msk_today()
    d0 = today - timedelta(days=5)  # baseline day (другой SKU) — задаёт run_days
    # Baseline: другой SKU в тот же день среза, чтобы «сегодня» не было idx==0.
    await _row(db_session, project.id, warehouse.id, d0, "BASE", 5, ff_good=5)
    await _row(db_session, project.id, warehouse.id, today, "NEW1", 15, ff_good=15, our_quantity=0, category="Носки")

    res = await get_changes(db_session, project.id, 30)
    matches = [c for c in res["changes"] if c["barcode"] == "NEW1"]
    assert len(matches) == 1
    row = matches[0]
    assert row["event"] == "appeared"
    assert row["snapshot_date"] == today.isoformat()
    assert row["prev_diff"] == 0
    assert row["cur_diff"] == 15
    assert row["delta"] == 15
    assert row["warehouse_id"] == warehouse.id
    assert row["category"] == "Носки"


@pytest.mark.asyncio
async def test_changes_resolved(db_session, project, warehouse):
    today = msk_today()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    # R1 стабильно висит два дня подряд (day_before → yesterday: тот же diff,
    # без события — изолирует «resolved» от «appeared» на первом occurrence).
    await _row(db_session, project.id, warehouse.id, day_before, "R1", 25, ff_good=25, our_quantity=0)
    await _row(db_session, project.id, warehouse.id, yesterday, "R1", 25, ff_good=25, our_quantity=0)
    # Сегодня строки для R1 нет — сошёлся сегодня. Другой SKU сегодня — чтобы
    # «сегодня» присутствовал в run_days (джоба реально прогонялась).
    await _row(db_session, project.id, warehouse.id, today, "OTHER", 5, ff_good=5)

    res = await get_changes(db_session, project.id, 30)
    matches = [c for c in res["changes"] if c["barcode"] == "R1" and c["snapshot_date"] == today.isoformat()]
    assert len(matches) == 1
    row = matches[0]
    assert row["event"] == "resolved"
    assert row["snapshot_date"] == today.isoformat()
    assert row["prev_diff"] == 25
    assert row["cur_diff"] == 0
    assert row["delta"] == -25


@pytest.mark.asyncio
async def test_changes_grew(db_session, project, warehouse):
    today = msk_today()
    yesterday = today - timedelta(days=1)
    await _row(db_session, project.id, warehouse.id, yesterday, "G1", 10, ff_good=10, our_quantity=0)
    await _row(db_session, project.id, warehouse.id, today, "G1", 30, ff_good=30, our_quantity=0)

    res = await get_changes(db_session, project.id, 30)
    matches = [c for c in res["changes"] if c["barcode"] == "G1" and c["snapshot_date"] == today.isoformat()]
    assert len(matches) == 1
    row = matches[0]
    assert row["event"] == "grew"
    assert row["prev_diff"] == 10
    assert row["cur_diff"] == 30
    assert row["delta"] == 20


@pytest.mark.asyncio
async def test_changes_shrank(db_session, project, warehouse):
    today = msk_today()
    yesterday = today - timedelta(days=1)
    await _row(db_session, project.id, warehouse.id, yesterday, "S1", 30, ff_good=30, our_quantity=0)
    await _row(db_session, project.id, warehouse.id, today, "S1", 10, ff_good=10, our_quantity=0)

    res = await get_changes(db_session, project.id, 30)
    matches = [c for c in res["changes"] if c["barcode"] == "S1" and c["snapshot_date"] == today.isoformat()]
    assert len(matches) == 1
    row = matches[0]
    assert row["event"] == "shrank"
    assert row["prev_diff"] == 30
    assert row["cur_diff"] == 10
    assert row["delta"] == -20


@pytest.mark.asyncio
async def test_changes_flipped(db_session, project, warehouse):
    today = msk_today()
    yesterday = today - timedelta(days=1)
    # Вчера у ФФ больше (+20), сегодня у нас больше (-15) — тот же SKU сменил знак.
    await _row(db_session, project.id, warehouse.id, yesterday, "F1", 20, ff_good=20, our_quantity=0)
    await _row(db_session, project.id, warehouse.id, today, "F1", -15, ff_good=0, our_quantity=15)

    res = await get_changes(db_session, project.id, 30)
    matches = [c for c in res["changes"] if c["barcode"] == "F1" and c["snapshot_date"] == today.isoformat()]
    assert len(matches) == 1
    row = matches[0]
    assert row["event"] == "flipped"
    assert row["prev_diff"] == 20
    assert row["cur_diff"] == -15
    assert row["delta"] == -35


@pytest.mark.asyncio
async def test_changes_unchanged_no_event(db_session, project, warehouse):
    today = msk_today()
    yesterday = today - timedelta(days=1)
    await _row(db_session, project.id, warehouse.id, yesterday, "U1", 10, ff_good=10, our_quantity=0)
    await _row(db_session, project.id, warehouse.id, today, "U1", 10, ff_good=10, our_quantity=0)

    res = await get_changes(db_session, project.id, 30)
    matches = [c for c in res["changes"] if c["barcode"] == "U1" and c["snapshot_date"] == today.isoformat()]
    assert matches == []


@pytest.mark.asyncio
async def test_changes_project_isolation(db_session, project, other_project, warehouse):
    other = other_project
    other_wh = await _add(
        db_session, Warehouse(project_id=other.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    )
    today = msk_today()
    await _row(db_session, other.id, other_wh.id, today, "X1", 10, ff_good=10)

    res = await get_changes(db_session, project.id, 30)
    assert res["changes"] == []


# ─── purge_old ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_old_removes_stale_rows(db_session, project, warehouse):
    today = msk_today()
    old = today - timedelta(days=200)
    await _row(db_session, project.id, warehouse.id, old, "OLD1", 10, ff_good=10)
    await _row(db_session, project.id, warehouse.id, today, "NEW1", 10, ff_good=10)

    purged = await purge_old(db_session, retention_days=90)
    assert purged == 1

    rows = (
        await db_session.execute(
            select(AssemblyStockMismatchDaily).where(AssemblyStockMismatchDaily.project_id == project.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].barcode == "NEW1"
