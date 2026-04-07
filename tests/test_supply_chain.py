"""
API tests — Supply Chain endpoints (factory orders, split to vehicles, vehicle status, overview).
"""

import uuid

import pytest


def _uid() -> str:
    """Short unique suffix for test data to avoid collisions."""
    return uuid.uuid4().hex[:6]


# ─── Helper ──────────────────────────────────────────────────────────────────


async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Supply Chain Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Factory Orders CRUD ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_factory_order_with_items(client, auth_headers):
    """Create a factory order with items and verify response."""
    headers = await _project_headers(client, auth_headers)

    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": "FO-001",
            "factory_name": "Test Factory",
            "note": "Test order",
            "items": [
                {
                    "barcode": "BC001",
                    "subject": "Carpet",
                    "article_seller": "ART-1",
                    "qty": 100,
                    "price_cny": "15.50",
                },
                {
                    "barcode": "BC002",
                    "subject": "Rug",
                    "qty": 200,
                    "price_cny": "25.00",
                },
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["order_number"] == "FO-001"
    assert data["factory_name"] == "Test Factory"
    assert len(data["items"]) == 2
    assert data["items"][0]["barcode"] == "BC001"
    assert data["items"][0]["qty"] == 100
    assert data["items"][0]["assigned_qty"] == 0


@pytest.mark.asyncio
async def test_list_factory_orders(client, auth_headers):
    """List factory orders returns empty for new project, then populated."""
    headers = await _project_headers(client, auth_headers)

    # Empty initially
    resp = await client.get("/api/v1/supply-chain/factory-orders", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []

    # Create one
    await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={"order_number": "FO-LIST-1", "items": []},
        headers=headers,
    )

    # Now list returns it
    resp = await client.get("/api/v1/supply-chain/factory-orders", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_get_factory_order_not_found(client, auth_headers):
    """Getting nonexistent factory order returns 404."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/supply-chain/factory-orders/99999", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_factory_order(client, auth_headers):
    """Update factory order fields."""
    headers = await _project_headers(client, auth_headers)

    # Create
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={"order_number": "FO-UPD-1", "factory_name": "Old Factory"},
        headers=headers,
    )
    order_id = resp.json()["id"]

    # Update
    resp = await client.put(
        f"/api/v1/supply-chain/factory-orders/{order_id}",
        json={"factory_name": "New Factory"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["factory_name"] == "New Factory"


@pytest.mark.asyncio
async def test_delete_factory_order(client, auth_headers):
    """Soft delete factory order."""
    headers = await _project_headers(client, auth_headers)

    # Create
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={"order_number": "FO-DEL-1"},
        headers=headers,
    )
    order_id = resp.json()["id"]

    # Delete
    resp = await client.delete(
        f"/api/v1/supply-chain/factory-orders/{order_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify not returned in list
    resp = await client.get("/api/v1/supply-chain/factory-orders", headers=headers)
    ids = [o["id"] for o in resp.json()]
    assert order_id not in ids


# ─── Split to Vehicles ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_split_to_vehicles(client, auth_headers):
    """Split factory order items to vehicles (creates CostOrders)."""
    headers = await _project_headers(client, auth_headers)

    # Create factory order with items
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": "FO-SPLIT-1",
            "items": [
                {"barcode": "BC-S1", "qty": 100, "price_cny": "10.00"},
                {"barcode": "BC-S2", "qty": 50, "price_cny": "20.00"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    order = resp.json()
    order_id = order["id"]
    item1_id = order["items"][0]["id"]
    item2_id = order["items"][1]["id"]

    # Split: 60 of item1 to v1, 40 of item1 to v2, 50 of item2 to v1
    v1 = f"V-{_uid()}"
    v2 = f"V-{_uid()}"
    resp = await client.post(
        f"/api/v1/supply-chain/factory-orders/{order_id}/split-to-vehicles",
        json={
            "assignments": [
                {"factory_order_item_id": item1_id, "qty": 60, "vehicle_order_no": v1},
                {"factory_order_item_id": item1_id, "qty": 40, "vehicle_order_no": v2},
                {"factory_order_item_id": item2_id, "qty": 50, "vehicle_order_no": v1},
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200, f"Split failed: {resp.text}"
    data = resp.json()
    assert data["ok"] is True
    assert data["created_cost_items"] == 3

    # Verify assigned_qty updated
    resp = await client.get(
        f"/api/v1/supply-chain/factory-orders/{order_id}",
        headers=headers,
    )
    items = resp.json()["items"]
    item1 = next(i for i in items if i["id"] == item1_id)
    item2 = next(i for i in items if i["id"] == item2_id)
    assert item1["assigned_qty"] == 100  # 60 + 40
    assert item2["assigned_qty"] == 50


@pytest.mark.asyncio
async def test_split_to_vehicles_exceeds_qty(client, auth_headers):
    """Split should fail if assigned qty exceeds total qty."""
    headers = await _project_headers(client, auth_headers)

    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": "FO-SPLIT-FAIL",
            "items": [{"barcode": "BC-FAIL", "qty": 10, "price_cny": "5.00"}],
        },
        headers=headers,
    )
    order = resp.json()
    order_id = order["id"]
    item_id = order["items"][0]["id"]

    # Try to assign more than available
    resp = await client.post(
        f"/api/v1/supply-chain/factory-orders/{order_id}/split-to-vehicles",
        json={
            "assignments": [
                {"factory_order_item_id": item_id, "qty": 15, "vehicle_order_no": f"V-FAIL-{_uid()}"},
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 400


# ─── Vehicle Status ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_vehicle_status_shipped(client, auth_headers):
    """Update vehicle status to SHIPPED (triggers cost recalc)."""
    headers = await _project_headers(client, auth_headers)
    vno = f"V-ST-{_uid()}"

    # Create factory order and split to vehicle
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": f"FO-STATUS-{_uid()}",
            "items": [{"barcode": "BC-ST1", "qty": 50, "price_cny": "10.00"}],
        },
        headers=headers,
    )
    order = resp.json()
    item_id = order["items"][0]["id"]

    # Split to create vehicle
    await client.post(
        f"/api/v1/supply-chain/factory-orders/{order['id']}/split-to-vehicles",
        json={
            "assignments": [
                {"factory_order_item_id": item_id, "qty": 50, "vehicle_order_no": vno},
            ]
        },
        headers=headers,
    )

    # Update status to SHIPPED
    resp = await client.put(
        f"/api/v1/supply-chain/vehicles/{vno}/status",
        json={"status": "SHIPPED"},
        headers=headers,
    )
    assert resp.status_code == 200, f"Status update failed: {resp.text}"
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "SHIPPED"


@pytest.mark.asyncio
async def test_invalid_status_transition(client, auth_headers):
    """Cannot skip statuses — e.g. FORMING -> CUSTOMS should fail."""
    headers = await _project_headers(client, auth_headers)

    vno = f"V-INV-{_uid()}"
    # Create factory order and split to vehicle
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": f"FO-INV-{_uid()}",
            "items": [{"barcode": "BC-INV", "qty": 10, "price_cny": "5.00"}],
        },
        headers=headers,
    )
    order = resp.json()
    item_id = order["items"][0]["id"]

    await client.post(
        f"/api/v1/supply-chain/factory-orders/{order['id']}/split-to-vehicles",
        json={
            "assignments": [
                {"factory_order_item_id": item_id, "qty": 10, "vehicle_order_no": vno},
            ]
        },
        headers=headers,
    )

    # Try FORMING -> CUSTOMS (should fail)
    resp = await client.put(
        f"/api/v1/supply-chain/vehicles/{vno}/status",
        json={"status": "CUSTOMS"},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ship_empty_vehicle_fails(client, auth_headers):
    """Cannot ship a vehicle with no items."""
    headers = await _project_headers(client, auth_headers)
    vno = f"V-EMPTY-{_uid()}"

    # Create empty vehicle
    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={"order_no": vno, "container_type": "truck1"},
        headers=headers,
    )
    assert resp.status_code == 201

    # Try to ship — should fail
    resp = await client.put(
        f"/api/v1/supply-chain/vehicles/{vno}/status",
        json={"status": "SHIPPED"},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_vehicle_status_delivered_creates_receipt(client, auth_headers, db_session):
    """
    When vehicle status is set to DELIVERED with target_warehouse_id,
    an InboundReceipt should be auto-created.
    """
    from sqlalchemy import text

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])

    # Create a warehouse for the project
    result = await db_session.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, created_at, updated_at) "
            "VALUES (:pid, :name, :wtype, NOW(), NOW()) RETURNING id"
        ),
        {"pid": project_id, "name": "Test WH", "wtype": "EXTERNAL"},
    )
    warehouse_id = result.scalar()

    # Create nomenclature for the barcode (needed for InboundReceipt items)
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, subject, updated_at) " "VALUES (:pid, :bc, :subj, NOW())"),
        {"pid": project_id, "bc": "BC-DELIV", "subj": "Test Item"},
    )
    await db_session.commit()

    vno = f"V-DLV-{_uid()}"
    # Create factory order and split to vehicle
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": f"FO-DLV-{_uid()}",
            "items": [{"barcode": "BC-DELIV", "qty": 30, "price_cny": "10.00"}],
        },
        headers=headers,
    )
    order = resp.json()
    item_id = order["items"][0]["id"]

    await client.post(
        f"/api/v1/supply-chain/factory-orders/{order['id']}/split-to-vehicles",
        json={
            "assignments": [
                {"factory_order_item_id": item_id, "qty": 30, "vehicle_order_no": vno},
            ]
        },
        headers=headers,
    )

    # Set invoice_no (required for DISPATCHED transition)
    resp = await client.put(
        f"/api/v1/supply-chain/vehicles/{vno}",
        json={"invoice_no": "INV-TEST", "target_warehouse_id": warehouse_id},
        headers=headers,
    )
    assert resp.status_code == 200, f"Update vehicle failed: {resp.text}"

    # Go through full status flow: FORMING → SHIPPED → CUSTOMS → DISPATCHED
    resp = await client.put(
        f"/api/v1/supply-chain/vehicles/{vno}/status",
        json={"status": "SHIPPED"},
        headers=headers,
    )
    assert resp.status_code == 200, f"SHIPPED failed: {resp.text}"

    resp = await client.put(
        f"/api/v1/supply-chain/vehicles/{vno}/status",
        json={"status": "CUSTOMS", "dt_number": "DT-TEST-001"},
        headers=headers,
    )
    assert resp.status_code == 200, f"CUSTOMS failed: {resp.text}"

    # CUSTOMS → DISPATCHED creates InboundReceipt
    resp = await client.put(
        f"/api/v1/supply-chain/vehicles/{vno}/status",
        json={"status": "DISPATCHED"},
        headers=headers,
    )
    assert resp.status_code == 200, f"DISPATCHED failed: {resp.text}"
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "DISPATCHED"
    assert "inbound_receipt_id" in data
    assert data["inbound_receipt_id"] is not None


# ─── Overview ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overview_empty(client, auth_headers):
    """Overview returns zero counts for new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/supply-chain/overview", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["factory_orders_count"] == 0
    assert data["total_items_in_transit"] == 0
    assert isinstance(data["vehicles_by_status"], dict)


@pytest.mark.asyncio
async def test_overview_with_data(client, auth_headers):
    """Overview reflects created factory orders and vehicles."""
    headers = await _project_headers(client, auth_headers)

    vno = f"V-OV-{_uid()}"
    # Create a factory order
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": f"FO-OV-{_uid()}",
            "items": [{"barcode": "BC-OV1", "qty": 100, "price_cny": "10.00"}],
        },
        headers=headers,
    )
    order = resp.json()
    item_id = order["items"][0]["id"]

    # Split to vehicle
    await client.post(
        f"/api/v1/supply-chain/factory-orders/{order['id']}/split-to-vehicles",
        json={
            "assignments": [
                {"factory_order_item_id": item_id, "qty": 100, "vehicle_order_no": vno},
            ]
        },
        headers=headers,
    )

    # Overview
    resp = await client.get("/api/v1/supply-chain/overview", headers=headers)
    data = resp.json()
    assert data["factory_orders_count"] == 1
    # Vehicle is in FORMING status by default, so items should be counted
    assert data["total_items_in_transit"] == 100


# ─── Vehicle Create & Recalc ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_vehicle_with_container_type(client, auth_headers):
    """Create vehicle with container_type, ship_date, invoice_no."""
    headers = await _project_headers(client, auth_headers)

    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={
            "order_no": f"V-NEW-{_uid()}",
            "container_type": "40ft",
            "delivery_cost_cny": 15000,
            "rate_cny": 12.5,
            "rate_usd": 92,
            "rate_eur": 98,
            "ship_date": "2026-04-15",
            "invoice_no": "INV-123",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["container_type"] == "40ft"
    assert data["transport_type"] == "CONTAINER"
    assert data["ship_date"] == "2026-04-15"
    assert data["invoice_no"] == "INV-123"
    assert data["status"] == "FORMING"


@pytest.mark.asyncio
async def test_recalc_vehicle(client, auth_headers):
    """Recalculate vehicle costs returns summary."""
    headers = await _project_headers(client, auth_headers)

    # Create order + items
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": "FO-RECALC",
            "items": [{"barcode": "BC-RC1", "qty": 10, "price_cny": "100.00"}],
        },
        headers=headers,
    )
    order = resp.json()
    item_id = order["items"][0]["id"]

    # Create vehicle and add items
    vno = f"V-RC-{_uid()}"
    await client.post(
        "/api/v1/supply-chain/vehicles",
        json={"order_no": vno, "container_type": "truck1", "rate_cny": 12.5},
        headers=headers,
    )
    await client.post(
        f"/api/v1/supply-chain/vehicles/{vno}/items",
        json={"items": [{"factory_order_item_id": item_id, "qty": 10}]},
        headers=headers,
    )

    # Recalc
    resp = await client.post(
        f"/api/v1/supply-chain/vehicles/{vno}/recalc",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "total_cost_rub" in data
    assert "total_rub" in data
    assert float(data["total_cost_rub"]) > 0


# ─── Auth Required ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_requires_auth(client):
    """Supply chain endpoints require authentication."""
    resp = await client.get("/api/v1/supply-chain/factory-orders")
    assert resp.status_code in (401, 403, 422)

    resp = await client.get("/api/v1/supply-chain/overview")
    assert resp.status_code in (401, 403, 422)
