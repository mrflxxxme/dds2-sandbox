"""
API tests — Cost endpoints (nomenclature, duty rules, cost orders).
"""

import uuid

import pytest

# ─── Helper ──────────────────────────────────────────────────────────────────


async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Cost Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Nomenclature ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nomenclature_empty(client, auth_headers):
    """Nomenclature list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/cost/nomenclature", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_nomenclature_requires_auth(client):
    """Nomenclature endpoint requires authentication."""
    resp = await client.get("/api/v1/cost/nomenclature")
    assert resp.status_code in (401, 403, 422)


# ─── Duty Rules ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duty_rules_empty(client, auth_headers):
    """Duty rules list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/cost/duty_rules", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_duty_rules_crud(client, auth_headers):
    """Create, list, and delete a duty rule."""
    headers = await _project_headers(client, auth_headers)

    # Create
    resp = await client.post(
        "/api/v1/cost/duty_rules",
        json={
            "subject": "Ковры",
            "rate": 10.0,
            "basis": "INVOICE",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # List
    resp = await client.get("/api/v1/cost/duty_rules", headers=headers)
    assert resp.status_code == 200
    rules = resp.json()
    assert len(rules) >= 1
    rule = next(r for r in rules if r["subject"] == "Ковры")

    # Delete
    resp = await client.delete(
        f"/api/v1/cost/duty_rules/{rule['id']}",
        headers=headers,
    )
    assert resp.status_code == 200


# ─── Cost Orders ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_orders_empty(client, auth_headers):
    """Cost orders list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/cost/orders", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_cost_orders_create(client, auth_headers):
    """Create a cost order."""
    headers = await _project_headers(client, auth_headers)

    uid = uuid.uuid4().hex[:6]
    order_no = f"COST-{uid}"
    resp = await client.post(
        "/api/v1/cost/orders",
        json={
            "order_no": order_no,
            "ship_date": "2024-06-01",
            "supplier": "China Supplier Co",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    order = resp.json()
    assert order.get("order_no") == order_no or order.get("ok")


@pytest.mark.asyncio
async def test_cost_order_items_empty(client, auth_headers):
    """Cost order items should return empty for nonexistent order."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/cost/orders/NONEXISTENT/items",
        headers=headers,
    )
    # Should return 200 with empty list or 404
    assert resp.status_code in (200, 404)


def _divandek_excel(barcodes: list[int]) -> bytes:
    """Build a minimal дивандек-format Excel (штрихкод/количество/цена) for upload tests."""
    import io

    import pandas as pd

    df = pd.DataFrame(
        {
            "Штрихкод": barcodes,
            "Количество": [1] * len(barcodes),
            "Цена": [10] * len(barcodes),
        }
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_reupload_soft_deletes_old_items(client, auth_headers, db_session):
    """Re-uploading an order soft-deletes prior items (Iron rule 3) instead of hard-deleting them.

    Read-path (get_cost_order_items) must show only the latest batch, while the previous
    rows still exist physically with is_deleted=True (recoverable, not physically removed).
    """
    from sqlalchemy import select

    from backend.models import CostOrderItem
    from backend.services.cost.items import get_cost_order_items, upload_order_items

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    order_no = f"COST-{uuid.uuid4().hex[:6]}"

    # Create the order so upload_order_items finds it.
    resp = await client.post(
        "/api/v1/cost/orders",
        json={"order_no": order_no, "ship_date": "2024-06-01", "supplier": "China Supplier Co"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    # First upload: two barcodes.
    inserted, _unrec, err = await upload_order_items(db_session, project_id, order_no, _divandek_excel([1001, 1002]))
    assert err is None
    assert inserted == 2

    # Re-upload with a different barcode set: old rows must be soft-deleted, not dropped.
    inserted2, _unrec2, err2 = await upload_order_items(db_session, project_id, order_no, _divandek_excel([2001]))
    assert err2 is None
    assert inserted2 == 1

    # Read-path sees ONLY the latest batch (old rows filtered by is_deleted).
    items, _nom = await get_cost_order_items(db_session, project_id, order_no)
    visible_barcodes = sorted(i.barcode for i in items)
    assert visible_barcodes == ["2001"]

    # Old rows still exist physically, flagged is_deleted=True with a deleted_at stamp.
    all_rows = (
        (await db_session.execute(select(CostOrderItem).where(CostOrderItem.order_no == order_no))).scalars().all()
    )
    deleted_barcodes = sorted(r.barcode for r in all_rows if r.is_deleted)
    assert deleted_barcodes == ["1001", "1002"]
    assert all(r.deleted_at is not None for r in all_rows if r.is_deleted)
    # Nothing was physically removed: 2 old + 1 new == 3 total rows.
    assert len(all_rows) == 3
