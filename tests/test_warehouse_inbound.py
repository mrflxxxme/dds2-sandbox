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
    async def test_create_receipt_dedupes_duplicate_barcode(self, db_session, project, warehouse, nomenclature_id):
        """Duplicate barcode lines collapse into one (summed). The receipt model
        assumes one line per barcode; without dedup a repeated barcode doubles
        stock on accept (prod bug: vehicle items arriving from 2+ sources)."""
        barcode, nom_id = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {
                "items": [
                    {"barcode": barcode, "expected_qty": 8, "actual_qty": 8},
                    {"barcode": barcode, "expected_qty": 154, "actual_qty": 154},
                ],
            },
        )
        assert len(receipt.items) == 1
        assert receipt.items[0].expected_qty == 162
        assert receipt.items[0].actual_qty == 162

        # Accept applies the merged qty once — stock is 162, not 324.
        await accept_receipt(db_session, project.id, receipt.id)
        result = await db_session.execute(
            text(
                "SELECT quantity FROM warehouse_stock WHERE project_id = :pid "
                "AND warehouse_id = :wid AND nomenclature_id = :nid"
            ),
            {"pid": project.id, "wid": warehouse.id, "nid": nom_id},
        )
        assert result.scalar() == 162

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
# accept_receipt — defect receipts (WB-возвраты с ПВЗ)
# ═══════════════════════════════════════════════════════════════════════════════


async def _mark_defect(db_session: AsyncSession, receipt) -> None:
    """Пометить приёмку как брак — как делает create_receipt_from_returns для возвратов ПВЗ.

    Мутируем ORM-объект, а не сырой UPDATE: тест-сессия с expire_on_commit=False
    иначе вернула бы из identity-map устаревший is_defect=False (в проде accept
    идёт отдельным запросом/сессией и грузит приёмку заново).
    """
    receipt.is_defect = True
    receipt.defect_reason = "Возврат WB с ПВЗ"
    await db_session.commit()


async def _read_stock(db_session: AsyncSession, project_id: int, warehouse_id: int, nom_id: int) -> tuple[int, int]:
    """(quantity, defect_quantity) для строки остатка; (0, 0) если строки нет."""
    row = (
        await db_session.execute(
            text(
                "SELECT quantity, defect_quantity FROM warehouse_stock "
                "WHERE project_id = :p AND warehouse_id = :w AND nomenclature_id = :n"
            ),
            {"p": project_id, "w": warehouse_id, "n": nom_id},
        )
    ).first()
    return (0, 0) if row is None else (row[0], row[1])


class TestAcceptDefectReceipt:
    @pytest.mark.asyncio
    async def test_accept_defect_receipt_books_into_defect_not_good(
        self, db_session, project, warehouse, nomenclature_id
    ):
        """Возврат с ПВЗ (is_defect=True): Accept пополняет брак, а не годный сток."""
        barcode, nom_id = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 7, "actual_qty": 0}]},
        )
        await _mark_defect(db_session, receipt)

        await accept_receipt(db_session, project.id, receipt.id)

        quantity, defect_quantity = await _read_stock(db_session, project.id, warehouse.id, nom_id)
        assert defect_quantity == 7, "возврат-брак должен попасть в defect_quantity"
        assert quantity == 0, "годный сток не должен расти от приёмки брака"

    @pytest.mark.asyncio
    async def test_cancel_accepted_defect_receipt_rolls_back_defect(
        self, db_session, project, warehouse, nomenclature_id
    ):
        """Отмена подтверждённой брак-приёмки откатывает именно defect_quantity (симметрия accept↔cancel)."""
        barcode, nom_id = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 7}]},
        )
        await _mark_defect(db_session, receipt)
        await accept_receipt(db_session, project.id, receipt.id)

        await cancel_receipt(db_session, project.id, receipt.id)

        quantity, defect_quantity = await _read_stock(db_session, project.id, warehouse.id, nom_id)
        assert defect_quantity == 0
        assert quantity == 0

    @pytest.mark.asyncio
    async def test_accept_defect_receipt_is_idempotent(self, db_session, project, warehouse, nomenclature_id):
        """Повторный Accept брак-приёмки (гонка double-submit) не задваивает defect_quantity."""
        from tests.conftest_api import TestSessionLocal

        barcode, nom_id = nomenclature_id
        receipt = await create_receipt(
            db_session,
            project.id,
            warehouse.id,
            {"items": [{"barcode": barcode, "expected_qty": 7}]},
        )
        await _mark_defect(db_session, receipt)
        await accept_receipt(db_session, project.id, receipt.id)

        # Снова делаем приёмку "подтверждаемой" — снимок статуса до победившего accept.
        await db_session.execute(
            text("UPDATE inbound_receipts SET status = 'EXPECTED' WHERE id = :id"),
            {"id": receipt.id},
        )
        await db_session.commit()

        async with TestSessionLocal() as other:
            with pytest.raises(ValueError):
                await accept_receipt(other, project.id, receipt.id)

        async with TestSessionLocal() as reader:
            _, defect_quantity = await _read_stock(reader, project.id, warehouse.id, nom_id)
            assert defect_quantity == 7


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


# ═══════════════════════════════════════════════════════════════════════════════
# reconcile_receipt_from_vehicle — vehicle→receipt item sync (anti-doubling)
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconcileReceiptFromVehicle:
    @pytest.mark.asyncio
    async def test_reconcile_collapses_stale_duplicate_lines(self, db_session, project, warehouse, nomenclature_id):
        """Editing vehicle items (soft-delete old + re-add) left stale duplicate
        receipt lines that doubled stock on accept (prod bug, receipt IN-111 →
        324 instead of 162). Reconcile rebuilds the EXPECTED receipt from the
        vehicle's ACTIVE cost items → exactly one line per barcode."""
        from backend.models.cost import CostOrder, CostOrderItem
        from backend.models.enums import VehicleStatus
        from backend.models.warehouse import InboundReceipt, InboundReceiptItem
        from backend.services.supply_chain.vehicle_delivery import reconcile_receipt_from_vehicle

        barcode, nom_id = nomenclature_id
        order_no = f"V-{_uid()}"
        vehicle = CostOrder(
            project_id=project.id,
            order_no=order_no,
            status=VehicleStatus.DISPATCHED,
            target_warehouse_id=warehouse.id,
        )
        db_session.add(vehicle)
        await db_session.flush()

        # Active cost items: same barcode as two sub-lots (8 + 154) = 162.
        # Plus a soft-deleted duplicate pair (the replaced ones) — must be ignored.
        db_session.add_all(
            [
                CostOrderItem(project_id=project.id, order_no=order_no, barcode=barcode, qty=8, is_deleted=False),
                CostOrderItem(project_id=project.id, order_no=order_no, barcode=barcode, qty=154, is_deleted=False),
                CostOrderItem(project_id=project.id, order_no=order_no, barcode=barcode, qty=8, is_deleted=True),
                CostOrderItem(project_id=project.id, order_no=order_no, barcode=barcode, qty=154, is_deleted=True),
            ]
        )

        # Linked EXPECTED receipt carrying the doubled lines (8,154,8,154 = 324).
        receipt = InboundReceipt(
            project_id=project.id,
            warehouse_id=warehouse.id,
            number=f"IN-{_uid()}",
            status=InboundStatus.EXPECTED,
            cost_order_id=vehicle.id,
        )
        db_session.add(receipt)
        await db_session.flush()
        db_session.add_all(
            [
                InboundReceiptItem(
                    project_id=project.id,
                    receipt_id=receipt.id,
                    nomenclature_id=nom_id,
                    barcode=barcode,
                    expected_qty=q,
                    actual_qty=0,
                )
                for q in (8, 154, 8, 154)
            ]
        )
        await db_session.flush()

        changed = await reconcile_receipt_from_vehicle(db_session, project.id, vehicle.id)
        assert changed > 0
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT expected_qty FROM inbound_receipt_items WHERE receipt_id = :rid ORDER BY id"),
            {"rid": receipt.id},
        )
        qtys = [row[0] for row in result.all()]
        # One line per barcode, expected_qty = sum of ACTIVE cost items (162).
        assert qtys == [162]

    @pytest.mark.asyncio
    async def test_reconcile_noop_without_expected_receipt(self, db_session, project, warehouse):
        """No linked EXPECTED receipt → reconcile is a safe no-op (returns 0)."""
        from backend.models.cost import CostOrder
        from backend.models.enums import VehicleStatus
        from backend.services.supply_chain.vehicle_delivery import reconcile_receipt_from_vehicle

        vehicle = CostOrder(
            project_id=project.id,
            order_no=f"V-{_uid()}",
            status=VehicleStatus.FORMING,
            target_warehouse_id=warehouse.id,
        )
        db_session.add(vehicle)
        await db_session.flush()

        changed = await reconcile_receipt_from_vehicle(db_session, project.id, vehicle.id)
        assert changed == 0
