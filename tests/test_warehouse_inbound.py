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

    @pytest.mark.asyncio
    async def test_accept_is_idempotent_does_not_double_stock(self, db_session, project, warehouse, nomenclature_id):
        """Re-accepting a receipt whose stock was already applied must NOT double it.

        Reproduces the prod incident (receipt #123, Газпром): a transparent
        401-retry / double-submit re-ran accept while the receipt still looked
        acceptable (its status snapshot was read before the winning accept
        committed), applying +162 twice → 324. Acceptance must be idempotent
        per receipt.

        The second accept runs on a *fresh* session — a separate request and
        DB connection, with no stale identity-map state — which is how the bug
        actually manifests in prod.
        """
        from tests.conftest_api import TestSessionLocal

        barcode, nom_id = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 162}]},
        )
        await accept_receipt(db_session, project.id, receipt.id)

        # Simulate the race window: the receipt looks acceptable again to a
        # separate caller (status snapshot taken before the winner committed),
        # while INBOUND movements for this receipt already exist.
        await db_session.execute(
            text("UPDATE inbound_receipts SET status = 'EXPECTED' WHERE id = :id"),
            {"id": receipt.id},
        )
        await db_session.commit()

        # The second accept must be rejected, not re-applied.
        async with TestSessionLocal() as other:
            with pytest.raises(ValueError):
                await accept_receipt(other, project.id, receipt.id)

        # Stock stays at the single application — 162, not 324.
        async with TestSessionLocal() as reader:
            qty = await reader.execute(
                text(
                    "SELECT quantity FROM warehouse_stock "
                    "WHERE project_id = :p AND warehouse_id = :w AND nomenclature_id = :n"
                ),
                {"p": project.id, "w": warehouse.id, "n": nom_id},
            )
            assert qty.scalar() == 162


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
