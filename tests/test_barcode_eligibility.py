# ruff: noqa: RUF001, RUF002, RUF003
"""Tests for barcode_eligibility_service.get_barcode_eligibility.

Eligibility (WB acceptance) is mocked at the service boundary
(`check_acceptance_and_redistribute`) — same approach as other suites that keep
the live WB `get_acceptance_options` integration out of unit tests. FF stock is
seeded into the DB (WarehouseStock ledger + active assembly reserve) and asserted
end-to-end.
"""

import pytest

import backend.services.barcode_eligibility_service as svc
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import Nomenclature
from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType


def _availability(can_box=True, free=10, paid=0):
    """One AcceptanceFlags-shaped entry for «Коледино» (eligible)."""
    return {
        "Коледино": {
            "warehouse_id": 507,
            "can_box": can_box,
            "can_monopallet": False,
            "can_supersafe": False,
            "box_meta": {"free_days_14": free, "paid_days_14": paid, "min_coefficient": 0},
            "mono_meta": None,
            "super_meta": None,
        }
    }


async def _seed_nomenclature(db, project_id, *, barcode, nm_id, vendor):
    nom = Nomenclature(
        project_id=project_id,
        barcode=barcode,
        article_wb=nm_id,
        article_seller=vendor,
    )
    db.add(nom)
    await db.flush()
    return nom


async def _seed_ff_warehouse(db, project_id, name, *, wtype=WarehouseType.FULFILLMENT):
    wh = Warehouse(
        project_id=project_id,
        name=name,
        warehouse_type=wtype.value,
        is_active=True,
        sort_order=0,
    )
    db.add(wh)
    await db.flush()
    return wh


async def _seed_stock(db, project_id, *, warehouse_id, nomenclature_id, barcode, qty):
    db.add(
        WarehouseStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            quantity=qty,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_resolved_barcode_targets_and_ff_stock(db_session, project, monkeypatch):
    """Резолвленный баркод → targets (eligible WB) + ff_stock с available."""
    nom = await _seed_nomenclature(db_session, project.id, barcode="BC-1", nm_id=1001, vendor="ART-1")
    ff = await _seed_ff_warehouse(db_session, project.id, "Хамза")
    await _seed_stock(
        db_session, project.id, warehouse_id=ff.id, nomenclature_id=nom.id, barcode="BC-1", qty=50
    )
    await db_session.commit()

    async def fake_accept(db, project_id, items, force=False):
        return {
            "items": [{"nm_id": it["nm_id"], "barcode": it["barcode"], "availability": _availability()} for it in items],
            "moves": [],
            "checked_at": None,
            "cache_hit": False,
        }

    monkeypatch.setattr(svc, "check_acceptance_and_redistribute", fake_accept)

    resp = await svc.get_barcode_eligibility(db_session, project.id, ["BC-1"])

    assert resp.unknown == []
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.barcode == "BC-1"
    assert item.nm_id == 1001
    assert item.vendor_code == "ART-1"

    # targets shape
    assert len(item.targets) == 1
    tgt = item.targets[0]
    assert tgt.wb_name == "Коледино"
    assert tgt.can_box is True
    assert tgt.free_days_14 == 10
    assert tgt.no_limit is False

    # ff_stock shape
    assert len(item.ff_stock) == 1
    fs = item.ff_stock[0]
    assert fs.ff_id == ff.id
    assert fs.ff_name == "Хамза"
    assert fs.available == 50


@pytest.mark.asyncio
async def test_unknown_barcode_does_not_fail(db_session, project, monkeypatch):
    """Неизвестный баркод (нет Nomenclature) → попадает в unknown, запрос ОК."""
    nom = await _seed_nomenclature(db_session, project.id, barcode="BC-OK", nm_id=2002, vendor="ART-2")
    ff = await _seed_ff_warehouse(db_session, project.id, "апл")
    await _seed_stock(
        db_session, project.id, warehouse_id=ff.id, nomenclature_id=nom.id, barcode="BC-OK", qty=7
    )
    await db_session.commit()

    async def fake_accept(db, project_id, items, force=False):
        return {
            "items": [{"nm_id": it["nm_id"], "barcode": it["barcode"], "availability": _availability()} for it in items],
            "moves": [],
            "checked_at": None,
            "cache_hit": False,
        }

    monkeypatch.setattr(svc, "check_acceptance_and_redistribute", fake_accept)

    resp = await svc.get_barcode_eligibility(db_session, project.id, ["BC-OK", "BC-MISSING"])

    assert resp.unknown == ["BC-MISSING"]
    assert len(resp.items) == 1
    assert resp.items[0].barcode == "BC-OK"
    assert resp.items[0].ff_stock[0].available == 7


@pytest.mark.asyncio
async def test_ff_stock_subtracts_active_assembly_reserve(db_session, project, monkeypatch):
    """available = quantity − резерв активных сборок с того же ФФ-склада."""
    nom = await _seed_nomenclature(db_session, project.id, barcode="BC-R", nm_id=3003, vendor="ART-3")
    ff = await _seed_ff_warehouse(db_session, project.id, "ФФ-Главный")
    await _seed_stock(
        db_session, project.id, warehouse_id=ff.id, nomenclature_id=nom.id, barcode="BC-R", qty=30
    )

    req = AssemblyRequest(
        project_id=project.id,
        warehouse_id=ff.id,
        number="ASM-T1",
        status=AssemblyStatus.IN_PROGRESS.value,
        pallets_count=1,
        pallet_weight_kg=10,
        package_type="BOX",
    )
    db_session.add(req)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=project.id,
            assembly_request_id=req.id,
            nomenclature_id=nom.id,
            barcode="BC-R",
            quantity=12,
        )
    )
    await db_session.commit()

    async def fake_accept(db, project_id, items, force=False):
        return {
            "items": [{"nm_id": it["nm_id"], "barcode": it["barcode"], "availability": _availability()} for it in items],
            "moves": [],
            "checked_at": None,
            "cache_hit": False,
        }

    monkeypatch.setattr(svc, "check_acceptance_and_redistribute", fake_accept)

    resp = await svc.get_barcode_eligibility(db_session, project.id, ["BC-R"])

    assert len(resp.items) == 1
    assert resp.items[0].ff_stock[0].available == 30 - 12  # 18


@pytest.mark.asyncio
async def test_no_limit_flag_when_all_days_closed(db_session, project, monkeypatch):
    """Eligible по options, но free=paid=0 → no_limit=True (предзаявка ⌛)."""
    await _seed_nomenclature(db_session, project.id, barcode="BC-NL", nm_id=4004, vendor="ART-4")
    await db_session.commit()

    async def fake_accept(db, project_id, items, force=False):
        avail = _availability(can_box=True, free=0, paid=0)
        return {
            "items": [{"nm_id": it["nm_id"], "barcode": it["barcode"], "availability": avail} for it in items],
            "moves": [],
            "checked_at": None,
            "cache_hit": False,
        }

    monkeypatch.setattr(svc, "check_acceptance_and_redistribute", fake_accept)

    resp = await svc.get_barcode_eligibility(db_session, project.id, ["BC-NL"])

    assert len(resp.items) == 1
    tgt = resp.items[0].targets[0]
    assert tgt.no_limit is True
    assert resp.items[0].ff_stock == []  # нет остатка на ФФ


@pytest.mark.asyncio
async def test_external_warehouse_excluded_from_ff_stock(db_session, project, monkeypatch):
    """EXTERNAL-склад не считается ФФ-источником (только FULFILLMENT)."""
    nom = await _seed_nomenclature(db_session, project.id, barcode="BC-EXT", nm_id=5005, vendor="ART-5")
    ext = await _seed_ff_warehouse(db_session, project.id, "Внешний", wtype=WarehouseType.EXTERNAL)
    await _seed_stock(
        db_session, project.id, warehouse_id=ext.id, nomenclature_id=nom.id, barcode="BC-EXT", qty=99
    )
    await db_session.commit()

    async def fake_accept(db, project_id, items, force=False):
        return {
            "items": [{"nm_id": it["nm_id"], "barcode": it["barcode"], "availability": _availability()} for it in items],
            "moves": [],
            "checked_at": None,
            "cache_hit": False,
        }

    monkeypatch.setattr(svc, "check_acceptance_and_redistribute", fake_accept)

    resp = await svc.get_barcode_eligibility(db_session, project.id, ["BC-EXT"])

    assert len(resp.items) == 1
    assert resp.items[0].ff_stock == []  # EXTERNAL не учитывается


@pytest.mark.asyncio
async def test_empty_and_dedup_barcodes(db_session, project, monkeypatch):
    """Пустой/дублирующийся ввод нормализуется; пустой список → пустой ответ."""
    await _seed_nomenclature(db_session, project.id, barcode="BC-D", nm_id=6006, vendor="ART-6")
    await db_session.commit()

    calls = {"n": 0}

    async def fake_accept(db, project_id, items, force=False):
        calls["n"] += 1
        # дедуп: один item на уникальный баркод
        assert len(items) == 1
        return {
            "items": [{"nm_id": it["nm_id"], "barcode": it["barcode"], "availability": _availability()} for it in items],
            "moves": [],
            "checked_at": None,
            "cache_hit": False,
        }

    monkeypatch.setattr(svc, "check_acceptance_and_redistribute", fake_accept)

    # empty input — без WB-вызова
    empty = await svc.get_barcode_eligibility(db_session, project.id, ["", "  "])
    assert empty.items == [] and empty.unknown == []
    assert calls["n"] == 0

    # dedupe + trim
    resp = await svc.get_barcode_eligibility(db_session, project.id, [" BC-D ", "BC-D"])
    assert len(resp.items) == 1
    assert resp.items[0].barcode == "BC-D"
