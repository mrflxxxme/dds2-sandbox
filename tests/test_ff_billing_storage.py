# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: FF billing — посуточный снапшот хранения (штуки→короба→паллеты) и
пересчёт стоимости по актуальным тарифам (recompute_storage_costs).
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from backend.models.cost import BoxQtyPerWarehouse
from backend.models.ff_billing import FfStorageDaily, WarehouseTariff
from backend.models.fulfillment import FulfillmentStock
from backend.models.warehouse import Warehouse, WarehouseStock
from backend.services.ff_billing import compute_storage_snapshot, recompute_storage_costs
from backend.services.settings_service import set_pallet_boxes_by_size

BC1 = "FFB_ST_BC1"
BC2 = "FFB_ST_BC2"
BOX1 = "FFB_ST_BOX1"

D = date(2026, 7, 20)


@pytest_asyncio.fixture
async def env(db_session, project, other_project):
    """wh — ФФ-зеркальный (FulfillmentStock), wh2 — без зеркала (WarehouseStock)."""
    wh = Warehouse(project_id=project.id, name="FFB ST WH", warehouse_type="FULFILLMENT")
    wh2 = Warehouse(project_id=project.id, name="FFB ST WH2", warehouse_type="FULFILLMENT")
    db_session.add_all([wh, wh2])
    await db_session.flush()

    nom1 = (
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, updated_at) "
                "VALUES (:pid, :bc, NOW()) RETURNING id"
            ),
            {"pid": project.id, "bc": BC1},
        )
    ).scalar()

    # Зеркало ФФ: россыпь BC1 (50+10+5=65) + короб BOX1→BC1 (2×10=20) + россыпь BC2 (30).
    db_session.add_all(
        [
            FulfillmentStock(
                project_id=project.id, warehouse_id=wh.id, provider="migfull",
                barcode=BC1, qty_good=50, qty_reserve=10, qty_defect=5, units_per_box=1,
            ),
            FulfillmentStock(
                project_id=project.id, warehouse_id=wh.id, provider="migfull",
                barcode=BOX1, base_barcode=BC1, units_per_box=10, qty_good=2,
            ),
            FulfillmentStock(
                project_id=project.id, warehouse_id=wh.id, provider="migfull",
                barcode=BC2, qty_good=30, units_per_box=1,
            ),
        ]
    )
    # wh2 — без зеркала: WarehouseStock 12 + 3 брака = 15 штук.
    db_session.add(
        WarehouseStock(
            project_id=project.id, warehouse_id=wh2.id, nomenclature_id=nom1,
            barcode=BC1, quantity=12, defect_quantity=3,
        )
    )
    # Кратности: BC1 10шт/короб размер 60x40x40; BC2 20шт/короб размер 50x30x30
    # (размера 50x30x30 НЕТ в маппинге паллет → группа самого частого известного).
    db_session.add_all(
        [
            BoxQtyPerWarehouse(
                project_id=project.id, barcode=BC1, warehouse_id=wh.id,
                box_qty=10, box_size="60x40x40",
            ),
            BoxQtyPerWarehouse(
                project_id=project.id, barcode=BC2, warehouse_id=wh.id,
                box_qty=20, box_size="50x30x30",
            ),
        ]
    )
    db_session.add(
        WarehouseTariff(
            project_id=project.id, warehouse_id=wh.id, service_type="STORAGE",
            rate=Decimal("25"), valid_from=date(2026, 7, 1),
        )
    )
    await db_session.commit()
    await set_pallet_boxes_by_size(db_session, project.id, {"60x40x40": 4})
    return SimpleNamespace(
        project_id=project.id, other_project_id=other_project.id,
        wh_id=wh.id, wh2_id=wh2.id,
    )


async def _row(db, pid, wh_id, d) -> FfStorageDaily | None:
    return (
        await db.execute(
            select(FfStorageDaily).where(
                FfStorageDaily.project_id == pid,
                FfStorageDaily.warehouse_id == wh_id,
                FfStorageDaily.snapshot_date == d,
            )
        )
    ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_snapshot_mirror_units_boxes_pallets(db_session, env):
    """BC1: 65+20=85 шт → 9 коробов (60x40x40); BC2: 30 шт → 2 короба (размер
    вне маппинга → в группу 60x40x40). 11 коробов / 4 = 3 паллеты; cost 75."""
    await compute_storage_snapshot(db_session, env.project_id, env.wh_id, D)
    await db_session.commit()
    row = await _row(db_session, env.project_id, env.wh_id, D)
    assert row is not None
    assert row.units == 115  # 85 + 30
    assert row.boxes == 11  # 9 + 2
    assert row.pallets == 3  # ceil(11/4)
    assert row.storage_rate == Decimal("25.00")
    assert row.storage_cost == Decimal("75.00")
    assert row.detail[BC1] == {"units": 85, "boxes": 9}
    assert row.detail[BC2] == {"units": 30, "boxes": 2}


@pytest.mark.asyncio
async def test_snapshot_upsert_idempotent(db_session, env):
    await compute_storage_snapshot(db_session, env.project_id, env.wh_id, D)
    await db_session.commit()
    await compute_storage_snapshot(db_session, env.project_id, env.wh_id, D)
    await db_session.commit()
    rows = (
        await db_session.execute(
            select(FfStorageDaily).where(
                FfStorageDaily.project_id == env.project_id,
                FfStorageDaily.warehouse_id == env.wh_id,
                FfStorageDaily.snapshot_date == D,
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_snapshot_fallback_warehouse_stock_no_boxes(db_session, env):
    """Склад без зеркала: штуки из WarehouseStock (＋брак); кратностей нет →
    короба 0, паллеты 0, units>0 → storage_cost NULL (честно не считаем)."""
    await compute_storage_snapshot(db_session, env.project_id, env.wh2_id, D)
    await db_session.commit()
    row = await _row(db_session, env.project_id, env.wh2_id, D)
    assert row is not None
    assert row.units == 15  # 12 + 3 брака
    assert row.boxes == 0
    assert row.pallets == 0
    assert row.storage_cost is None


@pytest.mark.asyncio
async def test_snapshot_empty_pallet_mapping(db_session, env):
    """Пустой маппинг паллет → pallets=0 и storage_cost NULL при units>0."""
    await set_pallet_boxes_by_size(db_session, env.project_id, {})
    await compute_storage_snapshot(db_session, env.project_id, env.wh_id, D)
    await db_session.commit()
    row = await _row(db_session, env.project_id, env.wh_id, D)
    assert row.units == 115
    assert row.boxes == 11
    assert row.pallets == 0
    assert row.storage_cost is None


@pytest.mark.asyncio
async def test_recompute_updates_rate_only(db_session, env):
    """Recompute: новая ставка задним числом меняет rate/cost, объёмы заморожены."""
    await compute_storage_snapshot(db_session, env.project_id, env.wh_id, D)
    await compute_storage_snapshot(db_session, env.project_id, env.wh2_id, D)
    await db_session.commit()

    # Тариф меняется задним числом (правка существующей ставки).
    t = (
        await db_session.execute(
            select(WarehouseTariff).where(
                WarehouseTariff.project_id == env.project_id,
                WarehouseTariff.warehouse_id == env.wh_id,
                WarehouseTariff.service_type == "STORAGE",
                WarehouseTariff.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one()
    t.rate = Decimal("40")
    await db_session.commit()

    n = await recompute_storage_costs(db_session, env.project_id, env.wh_id, D, D)
    assert n == 1
    row = await _row(db_session, env.project_id, env.wh_id, D)
    assert row.units == 115 and row.boxes == 11 and row.pallets == 3  # заморожены
    assert row.storage_rate == Decimal("40.00")
    assert row.storage_cost == Decimal("120.00")  # 3 × 40

    # Непосчитанная строка (units>0, pallets=0) остаётся NULL и после recompute.
    db_session.add(
        WarehouseTariff(
            project_id=env.project_id, warehouse_id=env.wh2_id, service_type="STORAGE",
            rate=Decimal("40"), valid_from=date(2026, 7, 1),
        )
    )
    await db_session.commit()
    await recompute_storage_costs(db_session, env.project_id, env.wh2_id, D, D)
    row2 = await _row(db_session, env.project_id, env.wh2_id, D)
    assert row2.storage_cost is None


@pytest.mark.asyncio
async def test_snapshot_project_isolation(db_session, env):
    """Снапшот чужого проекта не видит наш сток (склад чужого проекта пуст)."""
    wh_other = Warehouse(
        project_id=env.other_project_id, name="FFB ST OTHER", warehouse_type="FULFILLMENT"
    )
    db_session.add(wh_other)
    await db_session.flush()
    await compute_storage_snapshot(db_session, env.other_project_id, wh_other.id, D)
    await db_session.commit()
    row = await _row(db_session, env.other_project_id, wh_other.id, D)
    assert row.units == 0 and row.boxes == 0 and row.pallets == 0
