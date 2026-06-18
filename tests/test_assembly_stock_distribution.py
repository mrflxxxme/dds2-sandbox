"""
Tests for get_stock_distribution — вкладка «Распределение остатков» («где сейчас
товар», 100%).

Covers:
(a) изоляция по project_id;
(b) распределение бакетов по статусам сборки (IN_PROGRESS → in_assembly,
    READY/VEHICLE_ASSIGNED → ready, SHIPPED → in_transit);
(c) ff_stock с вычетом резерва (qty_good=100, qty_reserve=30 → 70);
(d) короб-строка base_barcode!=null, units_per_box=10, qty_good=5 → 50 штук;
(e) by_status: товар с ProductStatusMap status='new' → группа new, без статуса → none;
(f) суммы pct ≈ 100 при total>0;
(g) пустой проект → нули;
(h) фильтры warehouse_ids / product_status (by_status игнорирует product_status).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.cache import invalidate_cache
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.fulfillment import FulfillmentProvider, FulfillmentStock
from backend.models.refs import ProductStatusMap
from backend.services.assembly.stock_distribution import (
    get_stock_distribution,
    get_stock_distribution_history,
    snapshot_stock_distribution,
)

BARCODE = "TEST_BC_SD_001"


@pytest_asyncio.fixture
async def env(db_session, project, other_project):
    """ФФ-склад + второй ФФ-склад + номенклатура с article_wb (nm_id)."""
    wh_id = (
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'SD FF WH', 'FULFILLMENT', 1, true, false, NOW(), NOW()) RETURNING id"
            ),
            {"pid": project.id},
        )
    ).scalar()
    wh2_id = (
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'SD FF WH 2', 'FULFILLMENT', 2, true, false, NOW(), NOW()) RETURNING id"
            ),
            {"pid": project.id},
        )
    ).scalar()
    # nm_id (article_wb) = ключ, по которому резолвится статус товара.
    nm_id = int(uuid.uuid4().int % 1_000_000) + 1
    nom_id = (
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, article_wb, updated_at) "
                "VALUES (:pid, :bc, :nm, NOW()) RETURNING id"
            ),
            {"pid": project.id, "bc": BARCODE, "nm": nm_id},
        )
    ).scalar()
    await db_session.commit()
    return SimpleNamespace(
        project_id=project.id,
        other_project_id=other_project.id,
        wh_id=wh_id,
        wh2_id=wh2_id,
        nom_id=nom_id,
        nm_id=nm_id,
    )


async def _dist(db_session, project_id: int, **kwargs) -> dict:
    """Вызов сервиса со сбросом кэша (данные мутируются внутри теста)."""
    await invalidate_cache("reports:assembly_stock_distribution")
    return await get_stock_distribution(db_session, project_id, **kwargs)


async def _mk_stock(
    db_session,
    env,
    *,
    qty_good: int,
    qty_reserve: int = 0,
    base_barcode: str | None = None,
    units_per_box: int = 1,
    nomenclature_id: int | None = "DEFAULT",
    warehouse_id: int | None = None,
    barcode: str | None = None,
) -> None:
    nom = env.nom_id if nomenclature_id == "DEFAULT" else nomenclature_id
    db_session.add(
        FulfillmentStock(
            project_id=env.project_id,
            warehouse_id=warehouse_id or env.wh_id,
            provider=FulfillmentProvider.SKLADBOT.value,
            barcode=barcode or f"BC-{uuid.uuid4().hex[:10]}",
            nomenclature_id=nom,
            qty_good=qty_good,
            qty_reserve=qty_reserve,
            base_barcode=base_barcode,
            units_per_box=units_per_box,
        )
    )
    await db_session.commit()


async def _mk_request(
    db_session,
    env,
    *,
    status: AssemblyStatus,
    qty: int,
    nomenclature_id: int | None = "DEFAULT",
    warehouse_id: int | None = None,
) -> AssemblyRequest:
    nom = env.nom_id if nomenclature_id == "DEFAULT" else nomenclature_id
    req = AssemblyRequest(
        project_id=env.project_id,
        warehouse_id=warehouse_id or env.wh_id,
        number=f"ASM-SD{uuid.uuid4().hex[:8]}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("100.00"),
    )
    db_session.add(req)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=env.project_id,
            assembly_request_id=req.id,
            nomenclature_id=nom,
            barcode=BARCODE,
            quantity=qty,
        )
    )
    await db_session.commit()
    return req


async def _set_status(db_session, env, nm_id: int, status: str) -> None:
    db_session.add(ProductStatusMap(project_id=env.project_id, nm_id=nm_id, status=status))
    await db_session.commit()


def _wh_group(res: dict, warehouse_id: int) -> dict | None:
    return next((g for g in res["by_warehouse"] if g["key"] == str(warehouse_id)), None)


def _status_group(res: dict, key: str) -> dict | None:
    return next((g for g in res["by_status"] if g["key"] == key), None)


# --- (g) empty project --------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_project_zeros(db_session, env):
    res = await _dist(db_session, env.project_id)
    assert res["total"] == {
        "ff_stock": 0,
        "in_assembly": 0,
        "ready": 0,
        "in_transit": 0,
        "total": 0,
        "ff_stock_pct": 0.0,
        "in_assembly_pct": 0.0,
        "ready_pct": 0.0,
        "in_transit_pct": 0.0,
    }
    assert res["by_warehouse"] == []
    assert res["by_status"] == []


# --- (a) project isolation ----------------------------------------------------


@pytest.mark.asyncio
async def test_project_isolation(db_session, env):
    await _mk_stock(db_session, env, qty_good=50)
    await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, qty=10)

    res_other = await _dist(db_session, env.other_project_id)
    assert res_other["total"]["total"] == 0
    assert res_other["by_warehouse"] == []
    assert res_other["by_status"] == []

    res = await _dist(db_session, env.project_id)
    assert res["total"]["total"] == 60


# --- (b) pipeline buckets by assembly status ----------------------------------


@pytest.mark.asyncio
async def test_pipeline_buckets_by_status(db_session, env):
    await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, qty=10)
    await _mk_request(db_session, env, status=AssemblyStatus.READY, qty=20)
    await _mk_request(db_session, env, status=AssemblyStatus.VEHICLE_ASSIGNED, qty=5)
    await _mk_request(db_session, env, status=AssemblyStatus.SHIPPED, qty=7)
    # Терминальные статусы не считаются.
    await _mk_request(db_session, env, status=AssemblyStatus.DELIVERED, qty=99)
    await _mk_request(db_session, env, status=AssemblyStatus.CANCELLED, qty=99)

    res = await _dist(db_session, env.project_id)
    t = res["total"]
    assert t["in_assembly"] == 10
    assert t["ready"] == 25  # READY 20 + VEHICLE_ASSIGNED 5
    assert t["in_transit"] == 7
    assert t["ff_stock"] == 0
    assert t["total"] == 42


@pytest.mark.asyncio
async def test_soft_deleted_request_excluded(db_session, env):
    req = await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, qty=10)
    req.soft_delete()
    await db_session.commit()

    res = await _dist(db_session, env.project_id)
    assert res["total"]["in_assembly"] == 0


# --- (c) ff_stock minus reserve -----------------------------------------------


@pytest.mark.asyncio
async def test_ff_stock_minus_reserve(db_session, env):
    await _mk_stock(db_session, env, qty_good=100, qty_reserve=30)
    res = await _dist(db_session, env.project_id)
    assert res["total"]["ff_stock"] == 70


@pytest.mark.asyncio
async def test_ff_stock_reserve_clamped_to_zero(db_session, env):
    # reserve > good → max(.,0) = 0, не отрицательное.
    await _mk_stock(db_session, env, qty_good=10, qty_reserve=40)
    res = await _dist(db_session, env.project_id)
    assert res["total"]["ff_stock"] == 0


# --- (d) box row → loose ------------------------------------------------------


@pytest.mark.asyncio
async def test_box_row_expanded_to_loose(db_session, env):
    # Короб-строка: base_barcode задан, units_per_box=10, qty_good=5 → 50 штук.
    await _mk_stock(
        db_session,
        env,
        qty_good=5,
        units_per_box=10,
        base_barcode="EAN_LOOSE_001",
    )
    res = await _dist(db_session, env.project_id)
    assert res["total"]["ff_stock"] == 50


@pytest.mark.asyncio
async def test_box_and_loose_summed_no_double_count(db_session, env):
    # Россыпь-строка 7 + короб-строка 3×10=30 → 37 (физически разные строки).
    await _mk_stock(db_session, env, qty_good=7)
    await _mk_stock(db_session, env, qty_good=3, units_per_box=10, base_barcode="EAN_LOOSE_002")
    res = await _dist(db_session, env.project_id)
    assert res["total"]["ff_stock"] == 37


# --- (e) by_status grouping ---------------------------------------------------


@pytest.mark.asyncio
async def test_by_status_new_and_none(db_session, env):
    # Номенклатура env.nom_id со статусом 'new'.
    await _set_status(db_session, env, env.nm_id, "new")
    await _mk_stock(db_session, env, qty_good=40)  # status new
    # Номенклатура без статуса (article_wb есть, но ProductStatusMap нет).
    nom_none = (
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, article_wb, updated_at) "
                "VALUES (:pid, 'TEST_BC_SD_NONE', :nm, NOW()) RETURNING id"
            ),
            {"pid": env.project_id, "nm": env.nm_id + 777},
        )
    ).scalar()
    await db_session.commit()
    await _mk_stock(db_session, env, qty_good=15, nomenclature_id=nom_none)
    # Строка без nomenclature_id (NULL) → тоже none.
    await _mk_stock(db_session, env, qty_good=5, nomenclature_id=None)

    res = await _dist(db_session, env.project_id)

    g_new = _status_group(res, "new")
    assert g_new is not None
    assert g_new["label"] == "Новинка"
    assert g_new["bucket"]["ff_stock"] == 40

    g_none = _status_group(res, "none")
    assert g_none is not None
    assert g_none["label"] == "Без статуса"
    assert g_none["bucket"]["ff_stock"] == 20  # 15 + 5

    assert res["total"]["ff_stock"] == 60


# --- (f) pct sums ≈ 100 -------------------------------------------------------


@pytest.mark.asyncio
async def test_pct_sums_to_100(db_session, env):
    await _mk_stock(db_session, env, qty_good=30)
    await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, qty=10)
    await _mk_request(db_session, env, status=AssemblyStatus.READY, qty=10)
    await _mk_request(db_session, env, status=AssemblyStatus.SHIPPED, qty=10)

    res = await _dist(db_session, env.project_id)
    t = res["total"]
    pct_sum = t["ff_stock_pct"] + t["in_assembly_pct"] + t["ready_pct"] + t["in_transit_pct"]
    assert pct_sum == pytest.approx(100.0, abs=0.2)
    # ff_stock=30, пайплайн 10+10+10=30 → итого 60 → 30/60 = 50.0%.
    assert t["ff_stock_pct"] == pytest.approx(50.0, abs=0.05)


# --- by_warehouse -------------------------------------------------------------


@pytest.mark.asyncio
async def test_by_warehouse_grouping_and_label(db_session, env):
    await _mk_stock(db_session, env, qty_good=10, warehouse_id=env.wh_id)
    await _mk_stock(db_session, env, qty_good=40, warehouse_id=env.wh2_id)
    await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, qty=5, warehouse_id=env.wh_id)

    res = await _dist(db_session, env.project_id)

    g1 = _wh_group(res, env.wh_id)
    g2 = _wh_group(res, env.wh2_id)
    assert g1 is not None and g2 is not None
    assert g1["label"] == "SD FF WH"
    assert g2["label"] == "SD FF WH 2"
    assert g1["bucket"]["ff_stock"] == 10
    assert g1["bucket"]["in_assembly"] == 5
    assert g1["bucket"]["total"] == 15
    assert g2["bucket"]["ff_stock"] == 40
    # Сортировка по total убыв.: wh2 (40) перед wh (15).
    assert [g["key"] for g in res["by_warehouse"]] == [str(env.wh2_id), str(env.wh_id)]


# --- filters ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warehouse_ids_filter(db_session, env):
    await _mk_stock(db_session, env, qty_good=10, warehouse_id=env.wh_id)
    await _mk_stock(db_session, env, qty_good=40, warehouse_id=env.wh2_id)

    res = await _dist(db_session, env.project_id, warehouse_ids=[env.wh2_id])
    assert res["total"]["ff_stock"] == 40
    assert [g["key"] for g in res["by_warehouse"]] == [str(env.wh2_id)]


@pytest.mark.asyncio
async def test_product_status_filter_scopes_total_but_not_by_status(db_session, env):
    """product_status='new' сужает total/by_warehouse, но by_status — всегда полный."""
    await _set_status(db_session, env, env.nm_id, "new")
    await _mk_stock(db_session, env, qty_good=40)  # new
    # Товар без статуса.
    nom_none = (
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, article_wb, updated_at) "
                "VALUES (:pid, 'TEST_BC_SD_NONE2', :nm, NOW()) RETURNING id"
            ),
            {"pid": env.project_id, "nm": env.nm_id + 555},
        )
    ).scalar()
    await db_session.commit()
    await _mk_stock(db_session, env, qty_good=15, nomenclature_id=nom_none)

    res = await _dist(db_session, env.project_id, product_status="new")
    # total и by_warehouse — только 'new'.
    assert res["total"]["ff_stock"] == 40
    assert sum(g["bucket"]["ff_stock"] for g in res["by_warehouse"]) == 40
    # by_status — полный разрез, обе группы видны.
    assert _status_group(res, "new")["bucket"]["ff_stock"] == 40
    assert _status_group(res, "none")["bucket"]["ff_stock"] == 15


@pytest.mark.asyncio
async def test_product_status_filter_none(db_session, env):
    """product_status='none' оставляет только товары без статуса."""
    await _set_status(db_session, env, env.nm_id, "active")
    await _mk_stock(db_session, env, qty_good=40)  # active
    await _mk_stock(db_session, env, qty_good=12, nomenclature_id=None)  # none

    res = await _dist(db_session, env.project_id, product_status="none")
    assert res["total"]["ff_stock"] == 12


# --- snapshot + history -------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_and_history(db_session, env):
    """Снимок пишет бакеты за день; история суммирует их по дате."""
    await _mk_stock(db_session, env, qty_good=30, qty_reserve=10)  # ff_stock=20
    await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, qty=5)
    await _mk_request(db_session, env, status=AssemblyStatus.SHIPPED, qty=7)

    day = date(2026, 6, 17)
    written = await snapshot_stock_distribution(db_session, env.project_id, day)
    await db_session.commit()
    assert written >= 1

    hist = await get_stock_distribution_history(db_session, env.project_id)
    assert len(hist["daily"]) == 1
    point = hist["daily"][0]
    assert point["date"] == "2026-06-17"
    assert point["ff_stock"] == 20
    assert point["in_assembly"] == 5
    assert point["in_transit"] == 7
    assert point["ready"] == 0


@pytest.mark.asyncio
async def test_snapshot_idempotent(db_session, env):
    """Повторный снимок того же дня перезаписывает, а не дублирует."""
    await _mk_stock(db_session, env, qty_good=40)
    day = date(2026, 6, 17)
    await snapshot_stock_distribution(db_session, env.project_id, day)
    await db_session.commit()
    await snapshot_stock_distribution(db_session, env.project_id, day)
    await db_session.commit()

    hist = await get_stock_distribution_history(db_session, env.project_id)
    assert len(hist["daily"]) == 1
    assert hist["daily"][0]["ff_stock"] == 40


@pytest.mark.asyncio
async def test_history_project_isolation(db_session, env):
    """История одного проекта не видит снимки другого."""
    await _mk_stock(db_session, env, qty_good=40)
    await snapshot_stock_distribution(db_session, env.project_id, date(2026, 6, 17))
    await db_session.commit()

    hist_other = await get_stock_distribution_history(db_session, env.other_project_id)
    assert hist_other["daily"] == []


@pytest.mark.asyncio
async def test_history_empty(db_session, env):
    """Нет снимков → daily пуст (история копится вперёд, без бэкфилла)."""
    hist = await get_stock_distribution_history(db_session, env.project_id)
    assert hist["daily"] == []


@pytest.mark.asyncio
async def test_history_filters_and_date_range(db_session, env):
    """Фильтры склада/статуса и диапазон дат применяются к истории."""
    await _set_status(db_session, env, env.nm_id, "new")
    await _mk_stock(db_session, env, qty_good=40, warehouse_id=env.wh_id)  # new
    await _mk_stock(db_session, env, qty_good=15, warehouse_id=env.wh2_id, nomenclature_id=None)  # none

    d1 = date(2026, 6, 16)
    d2 = date(2026, 6, 17)
    await snapshot_stock_distribution(db_session, env.project_id, d1)
    await db_session.commit()
    await snapshot_stock_distribution(db_session, env.project_id, d2)
    await db_session.commit()

    # Полная история — 2 дня, ff_stock = 55 каждый.
    full = await get_stock_distribution_history(db_session, env.project_id)
    assert [p["date"] for p in full["daily"]] == ["2026-06-16", "2026-06-17"]
    assert all(p["ff_stock"] == 55 for p in full["daily"])

    # Фильтр по складу wh2 → только 15.
    by_wh = await get_stock_distribution_history(db_session, env.project_id, warehouse_ids=[env.wh2_id])
    assert all(p["ff_stock"] == 15 for p in by_wh["daily"])

    # Фильтр по статусу new → только 40.
    by_status = await get_stock_distribution_history(db_session, env.project_id, product_status="new")
    assert all(p["ff_stock"] == 40 for p in by_status["daily"])

    # Диапазон дат: только второй день.
    ranged = await get_stock_distribution_history(
        db_session, env.project_id, date_from=d2, date_to=d2 + timedelta(days=1)
    )
    assert [p["date"] for p in ranged["daily"]] == ["2026-06-17"]
