"""Tests: раскладка по паллетам (pallet_manifest) + расчётный вес товаров.

Covers:
  compute_goods_weight — Σ(qty×weight), недостающие ШК, изоляция project_id.
  update_pallet_manifest — сохранение, сброс (null), строгий инвариант (409),
    ШК не из состава (400).
  build_pallet_layout_xlsx — валидные .xlsx для internal и wb.
"""

from decimal import Decimal
from io import BytesIO

import pytest
import pytest_asyncio
from openpyxl import load_workbook
from sqlalchemy import text

from backend.schemas.assembly import (
    AssemblyItemCreate,
    AssemblyRequestCreate,
    BoxContent,
    PalletBox,
    PalletManifest,
)
from backend.services.assembly_service import (
    PalletManifestConflict,
    build_pallet_layout_xlsx,
    compute_goods_weight,
    create_assembly_request,
    get_assembly_request,
    update_pallet_manifest,
)

PROJECT_ID = 0
OTHER_PROJECT_ID = 0
BC1 = "TEST_BC_PAL_001"
BC2 = "TEST_BC_PAL_002"


@pytest_asyncio.fixture(autouse=True)
async def setup(db_session, project, other_project):
    """Свежий проект + FULFILLMENT-склад + номенклатура с весом/кратностью."""
    global PROJECT_ID, OTHER_PROJECT_ID
    PROJECT_ID = project.id
    OTHER_PROJECT_ID = other_project.id

    await db_session.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
            "VALUES (:pid, 'PalTest FF', 'FULFILLMENT', 1, true, false, NOW(), NOW())"
        ),
        {"pid": PROJECT_ID},
    )
    # bc1: вес 0.5кг, кратность 10; bc2: без веса (недостающий), кратность 10.
    await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, article_seller, weight_kg, box_qty_override, use_box_multiplicity, updated_at) "
            "VALUES (:pid, :bc, 'Товар 1', 'ART-1', 0.5, 10, true, NOW())"
        ),
        {"pid": PROJECT_ID, "bc": BC1},
    )
    await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, article_seller, weight_kg, box_qty_override, use_box_multiplicity, updated_at) "
            "VALUES (:pid, :bc, 'Товар 2', 'ART-2', NULL, 10, true, NOW())"
        ),
        {"pid": PROJECT_ID, "bc": BC2},
    )
    await db_session.commit()

    # Сток на складе-источнике (create_assembly_request проверяет доступное кол-во).
    wh_id = await _wh_id(db_session)
    for bc in (BC1, BC2):
        nom_id = (
            await db_session.execute(
                text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
                {"pid": PROJECT_ID, "bc": bc},
            )
        ).scalar()
        await db_session.execute(
            text(
                "INSERT INTO warehouse_stock (project_id, warehouse_id, nomenclature_id, barcode, quantity, in_transit, updated_at) "
                "VALUES (:pid, :wid, :nid, :bc, 500, 0, NOW())"
            ),
            {"pid": PROJECT_ID, "wid": wh_id, "nid": nom_id, "bc": bc},
        )
    await db_session.commit()
    yield


async def _wh_id(db_session) -> int:
    return (
        await db_session.execute(
            text("SELECT id FROM warehouses WHERE project_id = :pid AND name = 'PalTest FF' LIMIT 1"),
            {"pid": PROJECT_ID},
        )
    ).scalar()


async def _make_request(db_session, items: list[tuple[str, int]]):
    payload = AssemblyRequestCreate(
        warehouse_id=await _wh_id(db_session),
        wb_warehouse_name_manual="Электросталь",
        pallets_count=2,
        pallet_weight_kg=Decimal("100.00"),
        items=[AssemblyItemCreate(barcode=bc, quantity=q) for bc, q in items],
    )
    return await create_assembly_request(db_session, PROJECT_ID, payload)


# ─── compute_goods_weight ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_goods_weight_sum_and_missing(db_session):
    req = await _make_request(db_session, [(BC1, 20), (BC2, 3)])
    req = await get_assembly_request(db_session, PROJECT_ID, req.id)
    weight, missing = await compute_goods_weight(db_session, PROJECT_ID, req)
    assert weight == Decimal("10.000")  # 20 × 0.5, у bc2 веса нет
    assert missing == [BC2]


@pytest.mark.asyncio
async def test_goods_weight_none_when_no_weights(db_session):
    req = await _make_request(db_session, [(BC2, 5)])
    req = await get_assembly_request(db_session, PROJECT_ID, req.id)
    weight, missing = await compute_goods_weight(db_session, PROJECT_ID, req)
    assert weight is None
    assert missing == [BC2]


@pytest.mark.asyncio
async def test_goods_weight_project_isolation(db_session):
    """Вес bc1 задан только в НАШЕМ проекте — из другого не подтягивается и наоборот."""
    # Дать bc1 вес в другом проекте (без веса он там), затем убрать у нас →
    # compute для другого проекта не должен видеть наш вес.
    await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, weight_kg, updated_at) VALUES (:pid, :bc, 9.0, NOW())"
        ),
        {"pid": OTHER_PROJECT_ID, "bc": BC1},
    )
    await db_session.commit()
    req = await _make_request(db_session, [(BC1, 2)])
    req = await get_assembly_request(db_session, PROJECT_ID, req.id)
    weight, _ = await compute_goods_weight(db_session, PROJECT_ID, req)
    assert weight == Decimal("1.000")  # 2 × 0.5 (наш проект), а не 9.0 из чужого


# ─── update_pallet_manifest ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manifest_save_and_reset(db_session):
    req = await _make_request(db_session, [(BC1, 20)])
    manifest = PalletManifest(
        pallets=[PalletBox(pallet_no=1, boxes=[BoxContent(barcode=BC1, box_count=2, loose_units=0)])]
    )
    saved = await update_pallet_manifest(db_session, PROJECT_ID, req.id, manifest)
    assert saved.pallet_manifest is not None
    assert saved.pallet_manifest[0]["pallet_no"] == 1

    reset = await update_pallet_manifest(db_session, PROJECT_ID, req.id, None)
    assert reset.pallet_manifest is None


@pytest.mark.asyncio
async def test_manifest_loose_tail(db_session):
    """box_count×box_qty + loose == quantity (хвост-россыпь)."""
    req = await _make_request(db_session, [(BC1, 23)])  # 2 короба по 10 + 3 россыпью
    manifest = PalletManifest(
        pallets=[PalletBox(pallet_no=1, boxes=[BoxContent(barcode=BC1, box_count=2, loose_units=3)])]
    )
    saved = await update_pallet_manifest(db_session, PROJECT_ID, req.id, manifest)
    assert saved.pallet_manifest[0]["boxes"][0]["loose_units"] == 3


@pytest.mark.asyncio
async def test_manifest_invariant_violation_409(db_session):
    req = await _make_request(db_session, [(BC1, 20)])
    bad = PalletManifest(
        pallets=[PalletBox(pallet_no=1, boxes=[BoxContent(barcode=BC1, box_count=1, loose_units=0)])]
    )  # 1×10 = 10 ≠ 20
    with pytest.raises(PalletManifestConflict):
        await update_pallet_manifest(db_session, PROJECT_ID, req.id, bad)


@pytest.mark.asyncio
async def test_manifest_unknown_barcode_400(db_session):
    req = await _make_request(db_session, [(BC1, 20)])
    bad = PalletManifest(
        pallets=[PalletBox(pallet_no=1, boxes=[BoxContent(barcode="NOPE", box_count=2, loose_units=0)])]
    )
    with pytest.raises(ValueError):
        await update_pallet_manifest(db_session, PROJECT_ID, req.id, bad)


@pytest.mark.asyncio
async def test_manifest_project_isolation(db_session):
    """Чужой проект не может тронуть манифест нашей заявки (get вернёт None → ValueError)."""
    req = await _make_request(db_session, [(BC1, 20)])
    manifest = PalletManifest(
        pallets=[PalletBox(pallet_no=1, boxes=[BoxContent(barcode=BC1, box_count=2, loose_units=0)])]
    )
    with pytest.raises(ValueError):
        await update_pallet_manifest(db_session, OTHER_PROJECT_ID, req.id, manifest)


# ─── build_pallet_layout_xlsx ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xlsx_internal_and_wb(db_session):
    req = await _make_request(db_session, [(BC1, 23)])  # 2 короба + 3 россыпь
    req = await get_assembly_request(db_session, PROJECT_ID, req.id)

    internal = await build_pallet_layout_xlsx(db_session, PROJECT_ID, req, fmt="internal")
    ws = load_workbook(BytesIO(internal)).active
    assert ws["A1"].value == "Паллета"  # заголовок
    assert any(row and row[0] == "ИТОГО" for row in ws.iter_rows(values_only=True))

    wb_bytes = await build_pallet_layout_xlsx(db_session, PROJECT_ID, req, fmt="wb")
    ws2 = load_workbook(BytesIO(wb_bytes)).active
    header = [c.value for c in ws2[1]]
    assert header == ["Баркод товара", "Кол-во товаров", "ШК короба", "Срок годности"]
    rows = [r for r in ws2.iter_rows(min_row=2, values_only=True) if r[0]]
    # 2 полных короба (по 10) + 1 россыпь (3) = 3 строки; ШК короба/срок — пусто.
    assert len(rows) == 3
    assert all(r[2] in (None, "") and r[3] in (None, "") for r in rows)
    assert sorted(r[1] for r in rows) == [3, 10, 10]
