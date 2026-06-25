# ruff: noqa: RUF002, RUF003
"""
FF-портал (Хамза) — изоляция, кросс-проект, флоу сборки/приёмки, блок внешнего юзера.
"""

import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import hash_password
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.auth import Project, ProjectMember, User
from backend.models.cost import Nomenclature
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
