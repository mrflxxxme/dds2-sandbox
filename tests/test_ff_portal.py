# ruff: noqa: RUF002, RUF003
"""
FF-портал (Хамза) — изоляция, кросс-проект, флоу сборки/приёмки, блок внешнего юзера.
"""

import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import hash_password
from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.auth import Project, ProjectMember, User
from backend.models.cost import Nomenclature
from backend.models.counterparty import Counterparty
from backend.models.gazelka import GazelkaOrder, GazelkaOrderStatus
from backend.models.refs import ProjectSetting
from backend.models.wb_fbo import WbFboSupply
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    Warehouse,
    WarehouseStock,
    WarehouseType,
)
from backend.utils.time import utcnow

FF_PASSWORD = "ffpass123"


async def _stock(db: AsyncSession, warehouse_id: int, nom_id: int) -> WarehouseStock | None:
    db.expire_all()
    return (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.nomenclature_id == nom_id,
            )
        )
    ).scalar_one_or_none()


@pytest_asyncio.fixture
async def ff_env(db_session: AsyncSession, client: AsyncClient):
    """Two projects, an external FF operator scoped to one warehouse in each, plus a
    foreign warehouse (NOT his) and assemblies/receipt for the flow tests."""
    s = uuid.uuid4().hex[:8]
    db = db_session

    owner = User(username=f"owner_{s}", password_hash="x", is_active=True)
    db.add(owner)
    await db.flush()

    pA = Project(name="ПроектA", slug=f"ffa-{s}", owner_id=owner.id)
    pB = Project(name="ПроектB", slug=f"ffb-{s}", owner_id=owner.id)
    db.add_all([pA, pB])
    await db.flush()

    ff_user = User(username=f"hamza_{s}", password_hash=hash_password(FF_PASSWORD), is_active=True, is_external=True)
    db.add(ff_user)
    await db.flush()
    db.add_all(
        [
            ProjectMember(project_id=pA.id, user_id=ff_user.id, role="fulfillment"),
            ProjectMember(project_id=pB.id, user_id=ff_user.id, role="fulfillment"),
        ]
    )

    whA = Warehouse(project_id=pA.id, name="Хамза", warehouse_type=WarehouseType.FULFILLMENT.value)
    whB = Warehouse(project_id=pB.id, name="Хамза", warehouse_type=WarehouseType.FULFILLMENT.value)
    wh_other = Warehouse(project_id=pA.id, name="Чужой ФФ", warehouse_type=WarehouseType.FULFILLMENT.value)
    db.add_all([whA, whB, wh_other])
    await db.flush()

    db.add_all(
        [
            ProjectSetting(project_id=pA.id, key=f"ff_warehouses_{ff_user.id}", value=json.dumps([whA.id])),
            ProjectSetting(project_id=pB.id, key=f"ff_warehouses_{ff_user.id}", value=json.dumps([whB.id])),
        ]
    )

    nomA = Nomenclature(project_id=pA.id, barcode=f"A{s}", subject="ТоварA")
    nomB = Nomenclature(project_id=pB.id, barcode=f"B{s}", subject="ТоварB")
    nom_o = Nomenclature(project_id=pA.id, barcode=f"O{s}", subject="ТоварO")
    db.add_all([nomA, nomB, nom_o])
    await db.flush()

    db.add_all(
        [
            WarehouseStock(project_id=pA.id, warehouse_id=whA.id, nomenclature_id=nomA.id, barcode=nomA.barcode, quantity=100),
            WarehouseStock(project_id=pB.id, warehouse_id=whB.id, nomenclature_id=nomB.id, barcode=nomB.barcode, quantity=100),
        ]
    )

    fbo = WbFboSupply(project_id=pA.id, wb_supply_id=f"FBW-{s}", created_at_wb=utcnow(), warehouse_name="Коледино")
    db.add(fbo)
    await db.flush()

    asmA = AssemblyRequest(
        project_id=pA.id, warehouse_id=whA.id, number=f"ASM-A-{s}", status=AssemblyStatus.PENDING.value,
        pallets_count=1, pallet_weight_kg=1, wb_fbo_supply_id=fbo.id, package_type="BOX",
    )
    asmB = AssemblyRequest(
        project_id=pB.id, warehouse_id=whB.id, number=f"ASM-B-{s}", status=AssemblyStatus.IN_PROGRESS.value,
        pallets_count=1, pallet_weight_kg=1, package_type="BOX",
    )
    asm_other = AssemblyRequest(
        project_id=pA.id, warehouse_id=wh_other.id, number=f"ASM-O-{s}", status=AssemblyStatus.PENDING.value,
        pallets_count=1, pallet_weight_kg=1, package_type="BOX",
    )
    db.add_all([asmA, asmB, asm_other])
    await db.flush()
    db.add_all(
        [
            AssemblyRequestItem(project_id=pA.id, assembly_request_id=asmA.id, nomenclature_id=nomA.id, barcode=nomA.barcode, quantity=10),
            AssemblyRequestItem(project_id=pB.id, assembly_request_id=asmB.id, nomenclature_id=nomB.id, barcode=nomB.barcode, quantity=5),
            AssemblyRequestItem(project_id=pA.id, assembly_request_id=asm_other.id, nomenclature_id=nom_o.id, barcode=nom_o.barcode, quantity=3),
        ]
    )

    rcpt = InboundReceipt(
        project_id=pA.id, warehouse_id=whA.id, number=f"IN-A-{s}", status=InboundStatus.EXPECTED.value, planned_date=date.today()
    )
    db.add(rcpt)
    await db.flush()
    rcpt_item = InboundReceiptItem(
        project_id=pA.id, receipt_id=rcpt.id, nomenclature_id=nomA.id, barcode=nomA.barcode, expected_qty=10
    )
    db.add(rcpt_item)
    await db.flush()

    await db.commit()

    resp = await client.post("/api/v1/auth/login", json={"username": ff_user.username, "password": FF_PASSWORD})
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    return SimpleNamespace(
        db=db, headers=headers, ff_user_id=ff_user.id,
        pA=pA, pB=pB, whA=whA.id, whB=whB.id, wh_other=wh_other.id,
        nomA=nomA.id, nomB=nomB.id,
        asmA=asmA.id, asmB=asmB.id, asm_other=asm_other.id,
        rcpt=rcpt.id, rcpt_item=rcpt_item.id,
    )


# ─── /ff/me + cross-project + scope ──────────────────────────────────────────


async def test_ff_me_lists_both_projects(ff_env, client: AsyncClient):
    resp = await client.get("/api/v1/ff/me", headers=ff_env.headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    slugs = {p["slug"] for p in data["projects"]}
    assert slugs == {ff_env.pA.slug, ff_env.pB.slug}
    assert {w["warehouse_id"] for w in data["warehouses"]} == {ff_env.whA, ff_env.whB}


async def test_assemblies_cross_project_excludes_foreign_warehouse(ff_env, client: AsyncClient):
    resp = await client.get("/api/v1/ff/assemblies", headers=ff_env.headers)
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()["items"]}
    assert ff_env.asmA in ids and ff_env.asmB in ids  # both projects
    assert ff_env.asm_other not in ids  # foreign warehouse hidden


async def test_cannot_act_on_foreign_warehouse_assembly(ff_env, client: AsyncClient):
    resp = await client.post(f"/api/v1/ff/assemblies/{ff_env.asm_other}/start", headers=ff_env.headers)
    assert resp.status_code == 404


# ─── Assembly flow: start → ready → ship (blocked before vehicle) ────────────


async def test_assembly_flow_ship_blocked_until_vehicle(ff_env, client: AsyncClient):
    h = ff_env.headers
    # start PENDING → IN_PROGRESS
    r = await client.post(f"/api/v1/ff/assemblies/{ff_env.asmA}/start", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == AssemblyStatus.IN_PROGRESS.value

    # ready with pallets → READY
    r = await client.post(
        f"/api/v1/ff/assemblies/{ff_env.asmA}/ready",
        headers=h,
        json={"pallets_count": 2, "pallet_weight_kg": 120.5},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == AssemblyStatus.READY.value
    assert r.json()["pallets_count"] == 2

    # ship before vehicle assigned → 400
    r = await client.post(f"/api/v1/ff/assemblies/{ff_env.asmA}/ship", headers=h)
    assert r.status_code == 400

    # we assign the vehicle (simulated) → VEHICLE_ASSIGNED
    db = ff_env.db
    req = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmA))).scalar_one()
    req.status = AssemblyStatus.VEHICLE_ASSIGNED.value
    req.vehicle_assigned_at = utcnow()
    await db.commit()

    # now ship works → SHIPPED, stock deducted 100 → 90
    r = await client.post(f"/api/v1/ff/assemblies/{ff_env.asmA}/ship", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == AssemblyStatus.SHIPPED.value
    st = await _stock(db, ff_env.whA, ff_env.nomA)
    assert st.quantity == 90


# ─── Acceptance with discrepancy + defect ────────────────────────────────────


async def test_acceptance_with_defect_updates_stock(ff_env, client: AsyncClient):
    h = ff_env.headers
    # take in work
    r = await client.post(f"/api/v1/ff/acceptances/{ff_env.rcpt}/start", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["in_work"] is True and r.json()["assigned_to_me"] is True

    # accept: expected 10 → 8 good + 1 defect (1 недовоз)
    r = await client.post(
        f"/api/v1/ff/acceptances/{ff_env.rcpt}/accept",
        headers=h,
        json={"items": [{"item_id": ff_env.rcpt_item, "actual_qty": 8, "defect_qty": 1, "defect_reason": "бой"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == InboundStatus.ACCEPTED.value

    st = await _stock(ff_env.db, ff_env.whA, ff_env.nomA)
    assert st.quantity == 108  # 100 + 8 good
    assert st.defect_quantity == 1  # +1 defect


async def test_double_accept_is_rejected(ff_env, client: AsyncClient):
    h = ff_env.headers
    body = {"items": [{"item_id": ff_env.rcpt_item, "actual_qty": 10, "defect_qty": 0}]}
    r1 = await client.post(f"/api/v1/ff/acceptances/{ff_env.rcpt}/accept", headers=h, json=body)
    assert r1.status_code == 200, r1.text
    r2 = await client.post(f"/api/v1/ff/acceptances/{ff_env.rcpt}/accept", headers=h, json=body)
    assert r2.status_code == 400  # already accepted


# ─── Stock ───────────────────────────────────────────────────────────────────


async def test_stock_cross_project_with_reserved(ff_env, client: AsyncClient):
    resp = await client.get("/api/v1/ff/stock", headers=ff_env.headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    by_wh = {row["warehouse_id"]: row for row in data["items"]}
    assert ff_env.whA in by_wh and ff_env.whB in by_wh
    a = by_wh[ff_env.whA]
    # asmA reserves 10 of nomA (active PENDING) → available = 100 - 10
    assert a["quantity"] == 100
    assert a["reserved"] == 10
    assert a["available"] == 90


# ─── External-user middleware isolation ──────────────────────────────────────


async def test_external_user_blocked_on_main_api(ff_env, client: AsyncClient):
    # FF (external) token must be 403 on any non-/ff, non-/auth API path.
    resp = await client.get("/api/v1/projects", headers=ff_env.headers)
    assert resp.status_code == 403


async def test_normal_user_blocked_from_ff_portal(auth_headers, client: AsyncClient):
    # A regular (non-external) user must NOT access the FF portal.
    resp = await client.get("/api/v1/ff/me", headers=auth_headers)
    assert resp.status_code == 403


async def test_normal_user_allowed_on_main_api(auth_headers, client: AsyncClient):
    resp = await client.get("/api/v1/projects", headers=auth_headers)
    assert resp.status_code == 200


# ─── WB-возврат на портальный склад: переотгрузка заблокирована до приёмки ────


async def test_reship_blocked_until_portal_return_accepted(ff_env):
    """Возврат на портальный ФФ-склад создаётся EXPECTED (сток не вернулся) →
    reopen_for_reship должен падать, пока оператор не примет возврат."""
    from backend.services.assembly.status import reopen_for_reship

    db = ff_env.db
    req = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmA))).scalar_one()
    req.status = AssemblyStatus.RETURNED.value
    ret = InboundReceipt(
        project_id=ff_env.pA.id, warehouse_id=ff_env.whA, number=f"IN-RET-{req.id}",
        status=InboundStatus.EXPECTED.value, assembly_request_id=req.id, assembly_attempt_no=1,
    )
    db.add(ret)
    await db.commit()

    with pytest.raises(ValueError, match="не принят"):
        await reopen_for_reship(db, ff_env.pA.id, ff_env.asmA)


# ─── Assembly detail: vehicle / driver / carrier / history / via_gazelka ─────


async def test_assembly_detail_vehicle_history_gazelka(ff_env, client: AsyncClient):
    db = ff_env.db
    # Enrich assembly A with logistics + carrier + gazelka order + status history.
    cp = Counterparty(project_id=ff_env.pA.id, name="ООО Перевозчик", inn="7700000000", primary_type="CARRIER")
    db.add(cp)
    await db.flush()
    req = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmA))).scalar_one()
    req.vehicle_info = "А123ВС 77"
    req.vehicle_brand = "ГАЗель"
    req.driver_phone = "+79001234567"
    req.counterparty_id = cp.id
    req.pickup_time_slot = "10:00-12:00"
    req.vehicle_assigned_at = utcnow()
    # FBO supply: give it a human name (wb_supply_id уже задан фикстурой как FBW-<s>).
    fbo = (
        await db.execute(select(WbFboSupply).where(WbFboSupply.id == req.wb_fbo_supply_id))
    ).scalar_one()
    fbo.name = "Поставка Коледино"
    # Артикул продавца для проверки поля article на позиции заявки.
    nom = (await db.execute(select(Nomenclature).where(Nomenclature.id == ff_env.nomA))).scalar_one()
    nom.article_seller = "ART-A-001"
    db.add_all(
        [
            AssemblyStatusHistory(
                project_id=ff_env.pA.id, assembly_request_id=req.id,
                old_status=None, new_status=AssemblyStatus.PENDING.value, comment="создана",
            ),
            AssemblyStatusHistory(
                project_id=ff_env.pA.id, assembly_request_id=req.id,
                old_status=AssemblyStatus.PENDING.value, new_status=AssemblyStatus.IN_PROGRESS.value,
            ),
            GazelkaOrder(
                project_id=ff_env.pA.id, assembly_request_id=req.id, status=GazelkaOrderStatus.SENT,
            ),
        ]
    )
    await db.commit()

    r = await client.get(f"/api/v1/ff/assemblies/{ff_env.asmA}", headers=ff_env.headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == ff_env.asmA
    assert d["vehicle_info"] == "А123ВС 77"
    assert d["vehicle_brand"] == "ГАЗель"
    assert d["driver_phone"] == "+79001234567"
    assert d["carrier_name"] == "ООО Перевозчик"
    assert d["pickup_time_slot"] == "10:00-12:00"
    assert d["vehicle_assigned_at"] is not None
    assert d["via_gazelka"] is True
    assert d["editable"] is True  # PENDING
    statuses = [h["status"] for h in d["history"]]
    assert statuses == [AssemblyStatus.PENDING.value, AssemblyStatus.IN_PROGRESS.value]
    # FBO supply fields populated from the linked WbFboSupply.
    assert d["fbo_supply_name"] == "Поставка Коледино"
    assert d["fbo_supply_wb_id"] == f"FBW-{_suffix(ff_env)}"
    assert d["fbo_supply_status"] is not None  # wb_status (default ACTIVE)
    # Item carries the seller article (артикул продавца).
    item = next(it for it in d["items"] if it["barcode"] == f"A{_suffix(ff_env)}")
    assert item["article"] == "ART-A-001"
    # Never expose our logistics cost.
    assert "pickup_cost" not in d and "cost" not in d


async def test_assembly_detail_foreign_warehouse_404(ff_env, client: AsyncClient):
    r = await client.get(f"/api/v1/ff/assemblies/{ff_env.asm_other}", headers=ff_env.headers)
    assert r.status_code == 404


async def test_assembly_detail_item_box_qty(ff_env, client: AsyncClient):
    """Detail items carry box_qty: SKU with a кратность (box_qty_override) → >0;
    SKU без кратности → null."""
    db = ff_env.db
    s = _suffix(ff_env)
    # nomA gets a SKU-level box multiple (source=default).
    nomA = (await db.execute(select(Nomenclature).where(Nomenclature.id == ff_env.nomA))).scalar_one()
    nomA.box_qty_override = 12
    # A second SKU + item on asmA with NO box multiplicity anywhere.
    nom_nobox = Nomenclature(project_id=ff_env.pA.id, barcode=f"NB{s}", subject="БезКратности")
    db.add(nom_nobox)
    await db.flush()
    db.add(
        AssemblyRequestItem(
            project_id=ff_env.pA.id, assembly_request_id=ff_env.asmA,
            nomenclature_id=nom_nobox.id, barcode=nom_nobox.barcode, quantity=4,
        )
    )
    await db.commit()

    r = await client.get(f"/api/v1/ff/assemblies/{ff_env.asmA}", headers=ff_env.headers)
    assert r.status_code == 200, r.text
    by_bc = {it["barcode"]: it for it in r.json()["items"]}
    assert by_bc[f"A{s}"]["box_qty"] == 12  # кратность задана → >0
    assert by_bc[f"NB{s}"]["box_qty"] is None  # без кратности → null


async def test_assembly_detail_item_stock(ff_env, client: AsyncClient):
    """Detail item.stock = WarehouseStock.quantity на складе заявки (current + proposed)."""
    db = ff_env.db
    s = _suffix(ff_env)
    # Set a distinct stock for nomA on the assembly's warehouse (fixture had 100).
    st = await _stock(db, ff_env.whA, ff_env.nomA)
    assert st is not None
    st.quantity = 250
    await db.commit()

    # Current item reflects warehouse stock.
    r = await client.get(f"/api/v1/ff/assemblies/{ff_env.asmA}", headers=ff_env.headers)
    assert r.status_code == 200, r.text
    cur = {it["barcode"]: it for it in r.json()["items"]}
    assert cur[f"A{s}"]["stock"] == 250

    # A proposal for the same SKU carries the same warehouse stock on proposed_items.
    body = {"items": [{"barcode": f"A{s}", "quantity": 3}]}
    r = await client.put(f"/api/v1/ff/assemblies/{ff_env.asmA}/items", headers=ff_env.headers, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    prop = {it["barcode"]: it for it in d["proposed_items"]}
    assert prop[f"A{s}"]["stock"] == 250
    # current items still carry stock too
    assert {it["barcode"]: it["stock"] for it in d["items"]}[f"A{s}"] == 250


async def test_assembly_list_items_have_zero_stock(ff_env, client: AsyncClient):
    """List rows must NOT resolve per-item stock (detail-only) — stock stays 0."""
    db = ff_env.db
    st = await _stock(db, ff_env.whA, ff_env.nomA)
    assert st is not None
    st.quantity = 250
    await db.commit()
    r = await client.get("/api/v1/ff/assemblies", headers=ff_env.headers)
    assert r.status_code == 200, r.text
    for row in r.json()["items"]:
        for it in row["items"]:
            assert it["stock"] == 0


async def test_assembly_list_items_have_no_box_qty(ff_env, client: AsyncClient):
    """List rows must NOT resolve box_qty (avoid N+1) — items keep box_qty None."""
    db = ff_env.db
    nomA = (await db.execute(select(Nomenclature).where(Nomenclature.id == ff_env.nomA))).scalar_one()
    nomA.box_qty_override = 12
    await db.commit()
    r = await client.get("/api/v1/ff/assemblies", headers=ff_env.headers)
    assert r.status_code == 200, r.text
    for row in r.json()["items"]:
        for it in row["items"]:
            assert it["box_qty"] is None


async def test_assembly_list_fbo_number_and_brands(ff_env, client: AsyncClient):
    """List rows carry fbo_supply_number (FBW-…) for FBO-linked assemblies and
    brands = comma-separated unique Nomenclature.brand of the items."""
    db = ff_env.db
    s = _suffix(ff_env)
    nomA = (await db.execute(select(Nomenclature).where(Nomenclature.id == ff_env.nomA))).scalar_one()
    nomA.brand = "БрендА"
    await db.commit()

    r = await client.get("/api/v1/ff/assemblies", headers=ff_env.headers)
    assert r.status_code == 200, r.text
    by_id = {row["id"]: row for row in r.json()["items"]}
    # asmA is FBO-linked (fixture sets wb_supply_id=FBW-<s>) and its item is nomA.
    a = by_id[ff_env.asmA]
    assert a["fbo_supply_number"] == f"FBW-{s}"
    assert a["brands"] == "БрендА"
    # asmB has no FBO supply linked → fbo_supply_number is None.
    assert by_id[ff_env.asmB]["fbo_supply_number"] is None


# ─── Edit наполнения: разрешено до сборки, запрещено после отгрузки ──────────


async def test_edit_items_stores_proposal_not_applied(ff_env, client: AsyncClient):
    # asmB is IN_PROGRESS in project B; current canonical item = nomB qty 5.
    # FF edit is now a PROPOSAL «на согласование» — canonical items stay untouched.
    s = _suffix(ff_env)
    body = {"items": [{"barcode": f"B{s}", "quantity": 7}]}
    r = await client.put(f"/api/v1/ff/assemblies/{ff_env.asmB}/items", headers=ff_env.headers, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    # Proposal is recorded and pending DDS review.
    assert d["ff_review_pending"] is True
    assert d["ff_proposed_at"] is not None
    proposed = {it["barcode"]: it["quantity"] for it in d["proposed_items"]}
    assert proposed == {f"B{s}": 7}
    # CURRENT canonical items are UNCHANGED (still qty 5, not applied).
    current = {it["barcode"]: it["quantity"] for it in d["items"]}
    assert current == {f"B{s}": 5}
    # DB confirms: canonical items untouched, proposal stored on ff_proposed_*.
    db = ff_env.db
    db.expire_all()
    item_rows = (
        await db.execute(
            select(AssemblyRequestItem.barcode, AssemblyRequestItem.quantity).where(
                AssemblyRequestItem.assembly_request_id == ff_env.asmB
            )
        )
    ).all()
    assert {bc: q for bc, q in item_rows} == {f"B{s}": 5}
    req = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmB))).scalar_one()
    assert req.ff_proposed_items == [{"barcode": f"B{s}", "quantity": 7}]
    assert req.ff_proposed_by is not None


async def test_edit_items_proposal_ignores_stock_deficit(ff_env, client: AsyncClient):
    # Over-reserve no longer 400s at the FF edit: stock/deficit is now checked at
    # APPROVAL (main app). The proposal is stored regardless of available stock.
    s = _suffix(ff_env)
    body = {"items": [{"barcode": f"B{s}", "quantity": 100000}]}
    r = await client.put(f"/api/v1/ff/assemblies/{ff_env.asmB}/items", headers=ff_env.headers, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ff_review_pending"] is True
    assert {it["barcode"]: it["quantity"] for it in d["proposed_items"]} == {f"B{s}": 100000}
    # canonical items unchanged
    assert {it["barcode"]: it["quantity"] for it in d["items"]} == {f"B{s}": 5}


async def test_edit_items_unknown_barcode_400(ff_env, client: AsyncClient):
    body = {"items": [{"barcode": "NOPE-XYZ", "quantity": 1}]}
    r = await client.put(f"/api/v1/ff/assemblies/{ff_env.asmB}/items", headers=ff_env.headers, json=body)
    assert r.status_code == 400
    assert "не найден" in r.json()["error"]["message"]


async def test_edit_items_empty_list_400(ff_env, client: AsyncClient):
    r = await client.put(f"/api/v1/ff/assemblies/{ff_env.asmB}/items", headers=ff_env.headers, json={"items": []})
    assert r.status_code == 400


async def test_edit_items_rejected_when_shipped(ff_env, client: AsyncClient):
    db = ff_env.db
    req = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmB))).scalar_one()
    req.status = AssemblyStatus.SHIPPED.value
    await db.commit()
    body = {"items": [{"barcode": f"B{_suffix(ff_env)}", "quantity": 3}]}
    r = await client.put(f"/api/v1/ff/assemblies/{ff_env.asmB}/items", headers=ff_env.headers, json=body)
    assert r.status_code == 400
    assert "только до сборки" in r.json()["error"]["message"]


async def test_edit_items_foreign_warehouse_404(ff_env, client: AsyncClient):
    body = {"items": [{"barcode": "X", "quantity": 1}]}
    r = await client.put(f"/api/v1/ff/assemblies/{ff_env.asm_other}/items", headers=ff_env.headers, json=body)
    assert r.status_code == 404


# ─── Правка паллет/веса независимо от «Готово» ───────────────────────────────


async def test_set_pallets_on_ready_persists(ff_env, client: AsyncClient):
    db = ff_env.db
    req = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmB))).scalar_one()
    req.status = AssemblyStatus.READY.value
    await db.commit()

    r = await client.put(
        f"/api/v1/ff/assemblies/{ff_env.asmB}/pallets",
        headers=ff_env.headers,
        json={"pallets_count": 4, "pallet_weight_kg": 333.75},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pallets_count"] == 4
    assert float(d["pallet_weight_kg"]) == 333.75
    # persisted in DB
    st = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmB))).scalar_one()
    await db.refresh(st)
    assert st.pallets_count == 4
    assert float(st.pallet_weight_kg) == 333.75


async def test_set_pallets_rejected_when_shipped(ff_env, client: AsyncClient):
    db = ff_env.db
    req = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmB))).scalar_one()
    req.status = AssemblyStatus.SHIPPED.value
    await db.commit()

    r = await client.put(
        f"/api/v1/ff/assemblies/{ff_env.asmB}/pallets",
        headers=ff_env.headers,
        json={"pallets_count": 2, "pallet_weight_kg": 50},
    )
    assert r.status_code == 400
    assert "только до отгрузки" in r.json()["error"]["message"]


# ─── Архив: прячет из активного списка, виден под archived=True ──────────────


async def test_archive_toggle_hides_and_reappears(ff_env, client: AsyncClient):
    h = ff_env.headers
    # baseline counts (total must track the page filter, not the unfiltered set)
    base_default = (await client.get("/api/v1/ff/assemblies", headers=h)).json()
    base_archived = (await client.get("/api/v1/ff/assemblies?archived=true", headers=h)).json()
    assert ff_env.asmA in {row["id"] for row in base_default["items"]}

    # archive asmA
    r = await client.post(f"/api/v1/ff/assemblies/{ff_env.asmA}/archive", headers=h, json={"archived": True})
    assert r.status_code == 200, r.text
    assert r.json()["is_archived"] is True

    # default list no longer shows it — AND total drops by exactly one
    r = await client.get("/api/v1/ff/assemblies", headers=h)
    assert ff_env.asmA not in {row["id"] for row in r.json()["items"]}
    assert r.json()["total"] == base_default["total"] - 1

    # archived=True list shows it (and only archived) — AND total grew by one
    r = await client.get("/api/v1/ff/assemblies?archived=true", headers=h)
    arch = r.json()
    arch_ids = {row["id"] for row in arch["items"]}
    assert ff_env.asmA in arch_ids
    assert ff_env.asmB not in arch_ids  # still active
    assert arch["total"] == base_archived["total"] + 1

    # un-archive
    r = await client.post(f"/api/v1/ff/assemblies/{ff_env.asmA}/archive", headers=h, json={"archived": False})
    assert r.status_code == 200 and r.json()["is_archived"] is False
    r = await client.get("/api/v1/ff/assemblies", headers=h)
    assert ff_env.asmA in {row["id"] for row in r.json()["items"]}


async def test_archive_assembly_foreign_404(ff_env, client: AsyncClient):
    r = await client.post(
        f"/api/v1/ff/assemblies/{ff_env.asm_other}/archive", headers=ff_env.headers, json={"archived": True}
    )
    assert r.status_code == 404


async def test_delivered_and_cancelled_auto_archive(ff_env, client: AsyncClient):
    """«Принята WB» (DELIVERED) и «Отменена» (CANCELLED) авто-уходят в архив FF:
    исчезают из активного списка и появляются под archived=True без ручного флага."""
    db = ff_env.db
    a = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmA))).scalar_one()
    b = (await db.execute(select(AssemblyRequest).where(AssemblyRequest.id == ff_env.asmB))).scalar_one()
    a.status = AssemblyStatus.DELIVERED.value
    b.status = AssemblyStatus.CANCELLED.value
    await db.commit()

    h = ff_env.headers
    active = {row["id"] for row in (await client.get("/api/v1/ff/assemblies", headers=h)).json()["items"]}
    assert ff_env.asmA not in active  # delivered — не в активном списке
    assert ff_env.asmB not in active  # cancelled — не в активном списке

    arch = {row["id"] for row in (await client.get("/api/v1/ff/assemblies?archived=true", headers=h)).json()["items"]}
    assert ff_env.asmA in arch  # delivered авто-архивирован
    assert ff_env.asmB in arch  # cancelled авто-архивирован


# ─── project_slug фильтр ─────────────────────────────────────────────────────


async def test_project_slug_filter_scopes_assemblies(ff_env, client: AsyncClient):
    h = ff_env.headers
    # only project B
    r = await client.get(f"/api/v1/ff/assemblies?project_slug={ff_env.pB.slug}", headers=h)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()["items"]}
    assert ff_env.asmB in ids and ff_env.asmA not in ids

    # slug not among operator's projects → empty
    r = await client.get("/api/v1/ff/assemblies?project_slug=nope-not-mine", headers=h)
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_project_slug_filter_scopes_stock(ff_env, client: AsyncClient):
    r = await client.get(f"/api/v1/ff/stock?project_slug={ff_env.pA.slug}", headers=ff_env.headers)
    assert r.status_code == 200, r.text
    whs = {row["warehouse_id"] for row in r.json()["items"]}
    assert ff_env.whA in whs and ff_env.whB not in whs


# ─── Acceptance detail + archive ─────────────────────────────────────────────


async def test_acceptance_detail_and_archive(ff_env, client: AsyncClient):
    h = ff_env.headers
    # detail
    r = await client.get(f"/api/v1/ff/acceptances/{ff_env.rcpt}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == ff_env.rcpt
    assert r.json()["is_archived"] is False

    # baseline counts (total must track the page filter)
    base_default = (await client.get("/api/v1/ff/acceptances", headers=h)).json()
    base_archived = (await client.get("/api/v1/ff/acceptances?archived=true", headers=h)).json()

    # archive hides from default list, shows under archived=True — totals track it
    r = await client.post(f"/api/v1/ff/acceptances/{ff_env.rcpt}/archive", headers=h, json={"archived": True})
    assert r.status_code == 200 and r.json()["is_archived"] is True
    r = await client.get("/api/v1/ff/acceptances", headers=h)
    assert ff_env.rcpt not in {row["id"] for row in r.json()["items"]}
    assert r.json()["total"] == base_default["total"] - 1
    r = await client.get("/api/v1/ff/acceptances?archived=true", headers=h)
    arch = r.json()
    assert ff_env.rcpt in {row["id"] for row in arch["items"]}
    assert arch["total"] == base_archived["total"] + 1


def _suffix(ff_env) -> str:
    """Extract the random suffix used by the ff_env fixture from project A slug (ffa-<s>)."""
    return ff_env.pA.slug.split("-", 1)[1]
