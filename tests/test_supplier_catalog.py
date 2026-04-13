"""
Tests — Supplier Catalog endpoint: GET /supply-chain/suppliers/{id}/catalog
"""

import uuid

import pytest


def _uid() -> str:
    return uuid.uuid4().hex[:6]


async def _make_project(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"SC Catalog Test {_uid()}"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


async def _make_supplier(client, headers, name: str | None = None) -> dict:
    resp = await client.post(
        "/api/v1/supply-chain/suppliers",
        json={"name": name or f"Supplier {_uid()}", "country": "CHINA", "currency": "CNY"},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()


async def _make_factory_order(
    client, headers, supplier_id: int, order_number: str, items: list, order_date: str = "2024-01-15"
) -> dict:
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": order_number,
            "supplier_id": supplier_id,
            "order_date": order_date,
            "items": items,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ─── Happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supplier_catalog_happy_path(client, auth_headers):
    """Two factory orders with overlapping barcodes — check aggregation, weighted avg, grouping."""
    headers = await _make_project(client, auth_headers)
    supplier = await _make_supplier(client, headers)
    sid = supplier["id"]

    # Order 1: BC-AAA (subject=Carpet, qty=100, price=10), BC-BBB (subject=Rug, qty=50, price=20)
    await _make_factory_order(
        client,
        headers,
        sid,
        f"FO-{_uid()}",
        [
            {"barcode": "BC-AAA", "subject": "Carpet", "article_seller": "ART-1", "qty": 100, "price_cny": "10.00"},
            {"barcode": "BC-BBB", "subject": "Rug", "qty": 50, "price_cny": "20.00"},
        ],
    )

    # Order 2: BC-AAA (subject=Carpet, qty=200, price=15) — same barcode, different price
    await _make_factory_order(
        client,
        headers,
        sid,
        f"FO-{_uid()}",
        [
            {"barcode": "BC-AAA", "subject": "Carpet", "qty": 200, "price_cny": "15.00"},
        ],
    )

    resp = await client.get(f"/api/v1/supply-chain/suppliers/{sid}/catalog", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Summary
    summary = data["summary"]
    assert summary["orders_count"] == 2
    assert summary["sku_count"] == 2  # BC-AAA, BC-BBB
    assert summary["total_qty"] == 350  # 100 + 200 + 50
    # total_amount: 100*10 + 200*15 + 50*20 = 1000 + 3000 + 1000 = 5000
    assert float(summary["total_amount"]) == pytest.approx(5000.0)

    # Subjects: Carpet, Rug
    subjects = {g["subject"]: g for g in data["subjects"]}
    assert "Carpet" in subjects
    assert "Rug" in subjects

    carpet = subjects["Carpet"]
    assert carpet["sku_count"] == 1
    assert carpet["total_qty"] == 300  # 100 + 200

    # BC-AAA: weighted avg = (100*10 + 200*15) / 300 = 4000/300 ≈ 13.333
    bc_aaa = carpet["items"][0]
    assert bc_aaa["barcode"] == "BC-AAA"
    assert bc_aaa["total_qty"] == 300
    assert float(bc_aaa["avg_price"]) == pytest.approx(13.333333, rel=1e-4)
    # last_price: order_date = 2024-01-15 for both, so last by id DESC → second order, price=15
    assert float(bc_aaa["last_price"]) == pytest.approx(15.0)
    assert bc_aaa["orders_count"] == 2
    assert len(bc_aaa["order_history"]) == 2

    rug = subjects["Rug"]
    assert rug["sku_count"] == 1
    bc_bbb = rug["items"][0]
    assert bc_bbb["barcode"] == "BC-BBB"
    assert bc_bbb["total_qty"] == 50
    assert float(bc_bbb["avg_price"]) == pytest.approx(20.0)


# ─── Project isolation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supplier_catalog_project_isolation(client, auth_headers):
    """Supplier from another project returns 404."""
    headers_a = await _make_project(client, auth_headers)
    headers_b = await _make_project(client, auth_headers)

    supplier_b = await _make_supplier(client, headers_b)
    sid_b = supplier_b["id"]

    # Project A tries to access supplier from project B → 404
    resp = await client.get(f"/api/v1/supply-chain/suppliers/{sid_b}/catalog", headers=headers_a)
    assert resp.status_code == 404


# ─── is_deleted filter ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supplier_catalog_deleted_item_excluded(client, auth_headers):
    """Soft-deleted factory order item is not included in the catalog."""
    headers = await _make_project(client, auth_headers)
    supplier = await _make_supplier(client, headers)
    sid = supplier["id"]

    order = await _make_factory_order(
        client,
        headers,
        sid,
        f"FO-{_uid()}",
        [
            {"barcode": "BC-LIVE", "subject": "Chair", "qty": 10, "price_cny": "5.00"},
            {"barcode": "BC-DEAD", "subject": "Chair", "qty": 20, "price_cny": "8.00"},
        ],
    )
    order_id = order["id"]

    # Find the item id for BC-DEAD
    item_to_delete = next(i for i in order["items"] if i["barcode"] == "BC-DEAD")
    item_id = item_to_delete["id"]

    # Soft-delete the item
    del_resp = await client.delete(
        f"/api/v1/supply-chain/factory-orders/{order_id}/items/{item_id}",
        headers=headers,
    )
    assert del_resp.status_code == 200, del_resp.text

    resp = await client.get(f"/api/v1/supply-chain/suppliers/{sid}/catalog", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    all_barcodes = [item["barcode"] for grp in data["subjects"] for item in grp["items"]]
    assert "BC-LIVE" in all_barcodes
    assert "BC-DEAD" not in all_barcodes
    assert data["summary"]["sku_count"] == 1
    assert data["summary"]["total_qty"] == 10


# ─── Delivered qty ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supplier_catalog_delivered_qty(client, auth_headers):
    """
    CostOrderItem linked to a vehicle shows delivered_qty=0 before delivery.
    Full DELIVERED transition requires InboundReceipt; we verify the tracking
    logic by confirming delivered_qty stays 0 when status is SHIPPED/CUSTOMS/DISPATCHED.
    """
    headers = await _make_project(client, auth_headers)
    supplier = await _make_supplier(client, headers)
    sid = supplier["id"]

    order = await _make_factory_order(
        client,
        headers,
        sid,
        f"FO-{_uid()}",
        [
            {"barcode": "BC-DEL", "subject": "Table", "qty": 50, "price_cny": "30.00"},
        ],
    )
    order_id = order["id"]
    item_id = order["items"][0]["id"]

    # Create a vehicle
    vehicle_no = f"VH-{_uid()}"
    v_resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={"order_no": vehicle_no, "container_type": "truck1"},
        headers=headers,
    )
    assert v_resp.status_code == 201, v_resp.text

    # Split item to vehicle (30 of 50)
    split_resp = await client.post(
        f"/api/v1/supply-chain/factory-orders/{order_id}/split-to-vehicles",
        json={"assignments": [{"factory_order_item_id": item_id, "qty": 30, "vehicle_order_no": vehicle_no}]},
        headers=headers,
    )
    assert split_resp.status_code == 200, split_resp.text

    # Before any status change: delivered_qty == 0
    resp = await client.get(f"/api/v1/supply-chain/suppliers/{sid}/catalog", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["subjects"]) == 1
    item = data["subjects"][0]["items"][0]
    assert item["barcode"] == "BC-DEL"
    assert item["delivered_qty"] == 0
    assert item["total_qty"] == 50

    # Check order_history entry has vehicle info
    assert len(item["order_history"]) == 1
    hist = item["order_history"][0]
    assert hist["vehicle_order_no"] == vehicle_no
    assert hist["is_delivered"] is False

    # Transition to SHIPPED — still not delivered
    sr = await client.put(
        f"/api/v1/supply-chain/vehicles/{vehicle_no}/status",
        json={"status": "SHIPPED"},
        headers=headers,
    )
    assert sr.status_code == 200, sr.text

    resp2 = await client.get(f"/api/v1/supply-chain/suppliers/{sid}/catalog", headers=headers)
    data2 = resp2.json()
    assert data2["subjects"][0]["items"][0]["delivered_qty"] == 0
    # summary delivered_amount should be 0
    assert float(data2["summary"]["delivered_amount"]) == pytest.approx(0.0)


# ─── Empty supplier ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supplier_catalog_no_orders(client, auth_headers):
    """Supplier with no factory orders returns zero summary and empty subjects list."""
    headers = await _make_project(client, auth_headers)
    supplier = await _make_supplier(client, headers)
    sid = supplier["id"]

    resp = await client.get(f"/api/v1/supply-chain/suppliers/{sid}/catalog", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["subjects"] == []
    summary = data["summary"]
    assert summary["orders_count"] == 0
    assert summary["sku_count"] == 0
    assert summary["total_qty"] == 0
    assert float(summary["total_amount"]) == 0.0
    assert float(summary["delivered_amount"]) == 0.0


# ─── Null subject → "Без предмета" ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_supplier_catalog_null_subject_group(client, auth_headers):
    """Items with null subject are grouped under 'Без предмета'."""
    headers = await _make_project(client, auth_headers)
    supplier = await _make_supplier(client, headers)
    sid = supplier["id"]

    await _make_factory_order(
        client,
        headers,
        sid,
        f"FO-{_uid()}",
        [
            {"barcode": "BC-NOSUBJ", "qty": 5, "price_cny": "1.00"},  # no subject
        ],
    )

    resp = await client.get(f"/api/v1/supply-chain/suppliers/{sid}/catalog", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    subjects = {g["subject"]: g for g in data["subjects"]}
    assert "Без предмета" in subjects
    barcodes = [i["barcode"] for i in subjects["Без предмета"]["items"]]
    assert "BC-NOSUBJ" in barcodes
