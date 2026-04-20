"""
API + service tests — Supply Chain Russian vehicles.

Russian vehicles (country=RUSSIA) use a flat RUB cost model:
- No FX rates (rate_cny/usd/eur forced to 1)
- Delivery stored in delivery_cost_rub (not CNY/USD)
- No duty/vat/util on items
- container_type forced to 'gazelle'
- invoice_no/dt_number/payment_ref forced to None
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select


def _uid() -> str:
    """Short unique suffix for test data."""
    return uuid.uuid4().hex[:6]


async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Supply Chain RU Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Vehicle creation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_russian_vehicle(client, auth_headers, db_session):
    """Creating RU vehicle saves country=RUSSIA, forces gazelle + zeroed FX/CNY legs."""
    from backend.models.cost import CostOrder

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    vno = f"V-RU-{_uid()}"

    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={
            "order_no": vno,
            "container_type": "truck1",  # should be overridden to gazelle
            "country": "RUSSIA",
            "delivery_cost_rub": "5000.00",
            # These MUST be ignored / overwritten for RU
            "delivery_cost_cny": "999",
            "delivery_cost_usd": "999",
            "rate_cny": "12.5",
            "rate_usd": "92",
            "rate_eur": "98",
            "invoice_no": "INV-IGNORED",
            "payment_ref": "PAY-IGNORED",
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Create RU vehicle failed: {resp.text}"

    result = await db_session.execute(
        select(CostOrder).where(
            CostOrder.order_no == vno,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle = result.scalar_one()

    assert vehicle.country == "RUSSIA"
    assert vehicle.container_type == "gazelle"
    assert Decimal(str(vehicle.rate_cny)) == Decimal("1")
    assert Decimal(str(vehicle.rate_usd)) == Decimal("1")
    assert Decimal(str(vehicle.rate_eur)) == Decimal("1")
    assert Decimal(str(vehicle.delivery_cost_cny)) == Decimal("0")
    assert Decimal(str(vehicle.delivery_cost_usd)) == Decimal("0")
    assert Decimal(str(vehicle.delivery_cost_rub)) == Decimal("5000.00")
    assert vehicle.invoice_no is None
    assert vehicle.payment_ref is None


@pytest.mark.asyncio
async def test_create_russian_vehicle_free_delivery(client, auth_headers, db_session):
    """RU vehicle with delivery_cost_rub=0 creates successfully (free-delivery edge case)."""
    from backend.models.cost import CostOrder

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    vno = f"V-RUZ-{_uid()}"

    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={
            "order_no": vno,
            "country": "RUSSIA",
            "delivery_cost_rub": "0",
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Create free-delivery RU failed: {resp.text}"

    result = await db_session.execute(
        select(CostOrder).where(
            CostOrder.order_no == vno,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle = result.scalar_one()
    assert vehicle.country == "RUSSIA"
    assert Decimal(str(vehicle.delivery_cost_rub)) == Decimal("0")
    assert vehicle.container_type == "gazelle"


# ─── Recalculation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recalc_russian_vehicle_no_duty_no_vat(client, auth_headers, db_session):
    """
    RU vehicle with one item: recalculate_order_items must NOT apply duty/vat/util.
    total_rub = price_cny (RUB) + share of delivery_rub_total.
    """
    from backend.models.cost import CostOrder, CostOrderItem
    from backend.services.cost.items import recalculate_order_items

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    vno = f"V-RURC-{_uid()}"

    # Create RU vehicle with 5000 RUB delivery
    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={
            "order_no": vno,
            "country": "RUSSIA",
            "delivery_cost_rub": "5000",
        },
        headers=headers,
    )
    assert resp.status_code == 201

    # Insert CostOrderItem directly (price_cny stores RUB here because rate_cny=1)
    item = CostOrderItem(
        project_id=project_id,
        order_no=vno,
        barcode=f"BC-RU-{_uid()}",
        subject="Carpet",  # even with matching DutyRule, RU branch should skip it
        article_seller="ART-RU-1",
        qty=10,
        price_cny=Decimal("100.00"),
        weight_kg=Decimal("2.0"),
        volume_m3=Decimal("0.1"),
    )
    db_session.add(item)
    await db_session.commit()

    # Recalculate
    updated, err = await recalculate_order_items(db_session, project_id, vno)
    assert err is None
    assert updated == 1

    # Re-fetch item
    result = await db_session.execute(
        select(CostOrderItem).where(
            CostOrderItem.order_no == vno,
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
    )
    updated_item = result.scalar_one()

    # RU: no duty, no vat, no util
    assert Decimal(str(updated_item.duty_rub or 0)) == Decimal("0")
    assert Decimal(str(updated_item.vat_rub or 0)) == Decimal("0")
    assert Decimal(str(updated_item.util_rub or 0)) == Decimal("0")
    # cost_rub == price_cny (flat RUB, rate_cny=1)
    assert Decimal(str(updated_item.cost_rub)) == Decimal("100.00")
    # delivery_rub stored PER UNIT. total_volume = volume_m3 * qty = 0.1 * 10 = 1.0.
    # per-unit = 5000 * 0.1 / 1.0 = 500.
    assert Decimal(str(updated_item.delivery_rub)) == Decimal("500.00")
    # total_per_unit = cost (100) + delivery (500) = 600
    assert Decimal(str(updated_item.total_rub)) == Decimal("600.00")
    # rate_cny=1, so total_cny_per_unit == total_rub_per_unit
    assert Decimal(str(updated_item.total_cny)) == Decimal("600.00")

    # Sanity: the vehicle itself is still RU
    res = await db_session.execute(select(CostOrder).where(CostOrder.order_no == vno))
    order = res.scalar_one()
    assert order.country == "RUSSIA"


@pytest.mark.asyncio
async def test_recalc_chinese_vehicle_unchanged(client, auth_headers, db_session):
    """
    Regression: a default Chinese vehicle still computes duty/vat via the existing path.
    We use an INVOICE-basis DutyRule which is independent of area/weight and easy to verify.
    """
    from backend.models import CostOrderItem, DutyBasis, DutyRule
    from backend.services.cost.items import recalculate_order_items

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    vno = f"V-CN-{_uid()}"

    # Create a standard China vehicle with fixed FX
    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={
            "order_no": vno,
            "container_type": "truck1",
            "country": "CHINA",
            "rate_cny": "10",
            "rate_usd": "90",
            "rate_eur": "100",
            "delivery_cost_cny": "500",
            "delivery_cost_usd": "0",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    # Add INVOICE-basis duty rule (10% of base)
    rule = DutyRule(
        project_id=project_id,
        subject="Carpet",
        basis=DutyBasis.INVOICE,
        rate=Decimal("10"),  # percent
        util_collect_rub=Decimal("0"),
    )
    db_session.add(rule)

    # Single CostOrderItem with subject=Carpet
    item = CostOrderItem(
        project_id=project_id,
        order_no=vno,
        barcode=f"BC-CN-{_uid()}",
        subject="Carpet",
        article_seller="ART-CN-1",
        qty=1,
        price_cny=Decimal("100"),
        weight_kg=Decimal("1"),
        volume_m3=Decimal("0.1"),
    )
    db_session.add(item)
    await db_session.commit()

    updated, err = await recalculate_order_items(db_session, project_id, vno)
    assert err is None
    assert updated == 1

    result = await db_session.execute(
        select(CostOrderItem).where(
            CostOrderItem.order_no == vno,
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
    )
    updated_item = result.scalar_one()

    # cost_rub = 100 CNY * 10 = 1000 RUB
    assert Decimal(str(updated_item.cost_rub)) == Decimal("1000")
    # delivery_rub_total = 500 CNY * 10 = 5000 RUB, single item → whole amount
    assert Decimal(str(updated_item.delivery_rub)) == Decimal("5000")
    # duty (INVOICE 10%) = (1000 + 5000/2) * 0.1 = 350
    assert Decimal(str(updated_item.duty_rub)) == Decimal("350")
    # vat base = cost + duty + delivery/2 = 1000 + 350 + 2500 = 3850; vat = 3850 * DEFAULT_VAT
    assert Decimal(str(updated_item.vat_rub)) > Decimal("0")


# ─── Multi-tenancy isolation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_russian_vehicle_multitenancy(client, auth_headers, db_session):
    """RU vehicles from project A must not be visible in project B listings."""
    from backend.models.cost import CostOrder

    # Project A
    headers_a = await _project_headers(client, auth_headers)
    project_id_a = int(headers_a["X-Project-Id"])
    vno_a = f"V-RUA-{_uid()}"

    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={
            "order_no": vno_a,
            "country": "RUSSIA",
            "delivery_cost_rub": "1234",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201

    # Project B (fresh)
    headers_b = await _project_headers(client, auth_headers)
    project_id_b = int(headers_b["X-Project-Id"])
    assert project_id_a != project_id_b

    # Listing in B must NOT include project A's vehicle
    resp = await client.get("/api/v1/supply-chain/vehicles", headers=headers_b)
    assert resp.status_code == 200
    order_nos = [v["order_no"] for v in resp.json()]
    assert vno_a not in order_nos

    # And direct DB check: scoped by project_id in the model
    result = await db_session.execute(
        select(CostOrder).where(
            CostOrder.order_no == vno_a,
            CostOrder.project_id == project_id_b,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    assert result.scalar_one_or_none() is None


# ─── Regression: stale volume_m3 → delivery allocation ──────────────────────


@pytest.mark.asyncio
async def test_recalc_refreshes_stale_volume_from_override(client, auth_headers, db_session):
    """
    Regression: when pcs_per_box_override differs from FO's pcs_per_box, the stored
    volume_m3 may be stale (computed with factory ppb). recalculate_order_items must
    re-derive volume_m3 from effective override × box_size, otherwise delivery_rub
    allocation is under-/over-estimated for that item.
    """
    from backend.models import CostOrderItem
    from backend.models.supply_chain import FactoryOrder, FactoryOrderItem
    from backend.services.cost.items import recalculate_order_items

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    vno = f"V-STALE-{_uid()}"

    # China vehicle: delivery 1000 CNY × rate 10 = 10 000 RUB split by volume.
    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={
            "order_no": vno,
            "container_type": "truck1",
            "country": "CHINA",
            "rate_cny": "10",
            "rate_usd": "90",
            "rate_eur": "100",
            "delivery_cost_cny": "1000",
            "delivery_cost_usd": "0",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    # Factory order with ppb=9 (box 40×36×40 → 0.0064 m³/шт at ppb=9)
    fo = FactoryOrder(
        project_id=project_id,
        order_number=f"FO-STALE-{_uid()}",
        factory_name="Test",
        status="FORMING",
    )
    db_session.add(fo)
    await db_session.flush()

    foi = FactoryOrderItem(
        project_id=project_id,
        factory_order_id=fo.id,
        barcode=f"BC-STALE-{_uid()}",
        qty=120,
        price_cny=Decimal("80"),
        box_size="40*36*40",
        pcs_per_box=9,
        weight_kg=Decimal("9"),
    )
    db_session.add(foi)

    # CostOrderItem with STALE volume_m3 (=0.0064 per unit, as if ppb=9 was used)
    # and override pcs_per_box=4 (actual packing in this vehicle).
    # Effective vol should be 0.0576/4 = 0.0144.
    item = CostOrderItem(
        project_id=project_id,
        order_no=vno,
        barcode=foi.barcode,
        subject=None,
        qty=120,
        price_cny=Decimal("80"),
        weight_kg=Decimal("9"),
        volume_m3=Decimal("0.006400"),  # stale
        factory_order_item_id=None,  # set after flush
        pcs_per_box_override=4,
    )
    await db_session.flush()
    item.factory_order_item_id = foi.id
    db_session.add(item)
    await db_session.commit()

    updated, err = await recalculate_order_items(db_session, project_id, vno)
    assert err is None
    assert updated == 1

    result = await db_session.execute(
        select(CostOrderItem).where(
            CostOrderItem.order_no == vno,
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
    )
    refreshed = result.scalar_one()

    # Volume must be recomputed from effective (override ppb=4, box from FO).
    assert Decimal(str(refreshed.volume_m3)) == Decimal(
        "0.014400"
    ), f"volume_m3 not refreshed from override: got {refreshed.volume_m3}"
    # Sole item → delivery_rub_unit == delivery_rub_total / qty (1000 * 10 / 120).
    expected_delivery = Decimal("10000") / Decimal("120")
    assert abs(Decimal(str(refreshed.delivery_rub)) - expected_delivery) < Decimal("0.01")
