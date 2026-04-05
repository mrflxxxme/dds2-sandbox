"""
Tests for warehouse_inbound.py — Inbound receipts (priemka).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import InboundStatus
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import (
    accept_receipt,
    cancel_receipt,
    create_receipt,
    get_receipt,
    list_receipts,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def warehouse(db_session: AsyncSession, project):
    """Create a test warehouse for inbound tests."""
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"WH-IN-{_uid()}", "warehouse_type": "EXTERNAL"},
    )


@pytest_asyncio.fixture
async def nomenclature_id(db_session: AsyncSession, project):
    """Create a test nomenclature record and return its barcode."""
    barcode = f"BC-{_uid()}"
    result = await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
            "VALUES (:pid, :bc, :subj, NOW()) RETURNING id"
        ),
        {"pid": project.id, "bc": barcode, "subj": "Test Item"},
    )
    nom_id = result.scalar()
    await db_session.commit()
    return barcode, nom_id


# ═══════════════════════════════════════════════════════════════════════════════
# create_receipt
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateReceipt:
    @pytest.mark.asyncio
    async def test_create_receipt_with_items(self, db_session, project, warehouse, nomenclature_id):
        barcode, nom_id = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {
                "comment": "Test receipt",
                "items": [
                    {"barcode": barcode, "expected_qty": 100, "actual_qty": 0},
                ],
            },
        )
        assert receipt.id is not None
        assert receipt.project_id == project.id
        assert receipt.warehouse_id == warehouse.id
        assert receipt.status == InboundStatus.EXPECTED
        assert receipt.number.startswith("IN-")
        assert len(receipt.items) == 1
        assert receipt.items[0].expected_qty == 100
        assert receipt.items[0].barcode == barcode

    @pytest.mark.asyncio
    async def test_create_receipt_warehouse_not_found(self, db_session, project):
        with pytest.raises(ValueError, match="Warehouse not found"):
            await create_receipt(db_session, project.id, 999999, {"items": []})

    @pytest.mark.asyncio
    async def test_create_receipt_barcode_not_found(self, db_session, project, warehouse):
        with pytest.raises(ValueError, match="Barcode not found"):
            await create_receipt(
                db_session,
                project.id,
                warehouse.id,
                {"items": [{"barcode": "NONEXISTENT", "expected_qty": 10}]},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# list_receipts
# ═══════════════════════════════════════════════════════════════════════════════


class TestListReceipts:
    @pytest.mark.asyncio
    async def test_list_empty(self, db_session, project, warehouse):
        receipts = await list_receipts(db_session, project.id, warehouse.id)
        assert isinstance(receipts, list)

    @pytest.mark.asyncio
    async def test_list_returns_created(self, db_session, project, warehouse, nomenclature_id):
        barcode, _ = nomenclature_id
        await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 50}]},
        )
        receipts = await list_receipts(db_session, project.id, warehouse.id)
        assert len(receipts) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# accept_receipt
# ═══════════════════════════════════════════════════════════════════════════════


class TestAcceptReceipt:
    @pytest.mark.asyncio
    async def test_accept_receipt_auto_fills_actual_qty(self, db_session, project, warehouse, nomenclature_id):
        barcode, _ = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 50, "actual_qty": 0}]},
        )
        accepted = await accept_receipt(db_session, project.id, receipt.id)
        assert accepted.status == InboundStatus.ACCEPTED
        assert accepted.items[0].actual_qty == 50
        assert accepted.actual_date is not None

    @pytest.mark.asyncio
    async def test_accept_empty_receipt_fails(self, db_session, project, warehouse):
        receipt = await create_receipt(db_session, project.id, warehouse.id, {"items": []})
        with pytest.raises(ValueError, match="no items"):
            await accept_receipt(db_session, project.id, receipt.id)

    @pytest.mark.asyncio
    async def test_accept_not_found(self, db_session, project):
        with pytest.raises(ValueError, match="not found"):
            await accept_receipt(db_session, project.id, 999999)

    @pytest.mark.asyncio
    async def test_accept_already_accepted(self, db_session, project, warehouse, nomenclature_id):
        barcode, _ = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 10}]},
        )
        await accept_receipt(db_session, project.id, receipt.id)
        with pytest.raises(ValueError, match="Cannot accept"):
            await accept_receipt(db_session, project.id, receipt.id)


# ═══════════════════════════════════════════════════════════════════════════════
# cancel_receipt
# ═══════════════════════════════════════════════════════════════════════════════


class TestCancelReceipt:
    @pytest.mark.asyncio
    async def test_cancel_accepted_receipt(self, db_session, project, warehouse, nomenclature_id):
        barcode, _ = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 20}]},
        )
        await accept_receipt(db_session, project.id, receipt.id)
        cancelled = await cancel_receipt(db_session, project.id, receipt.id)
        assert cancelled.status == InboundStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_non_accepted_fails(self, db_session, project, warehouse, nomenclature_id):
        barcode, _ = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 10}]},
        )
        with pytest.raises(ValueError, match="ACCEPTED"):
            await cancel_receipt(db_session, project.id, receipt.id)


# ═══════════════════════════════════════════════════════════════════════════════
# project_id isolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestInboundProjectIsolation:
    @pytest.mark.asyncio
    async def test_get_receipt_project_isolation(self, db_session, project, other_project, warehouse, nomenclature_id):
        barcode, _ = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 5}]},
        )
        # Should not be visible from other project
        found = await get_receipt(db_session, other_project.id, receipt.id)
        assert found is None
