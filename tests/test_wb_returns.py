"""
Tests for WB Goods Returns module — service, helpers, router.

Covers:
1. Sync from WB API (mock client): upsert idempotency, camelCase mapping, ISO datetime parsing
2. Tab filters: pvz / in_transit / history
3. Summary counters
4. classify_ui_state — 7 branches
5. create_receipt_from_returns — happy path, error paths
6. project_id isolation, soft_delete filtering
"""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from backend.models.warehouse import InboundReceipt, InboundStatus, Warehouse
from backend.models.wb_returns import WbGoodsReturn
from backend.services.wb_returns_service import (
    _parse_wb_datetime,
    classify_ui_state,
    create_receipt_from_returns,
    get_summary,
    list_returns,
    sync_project_returns,
)
from backend.utils.time import utcnow

# ─── Fixture: clean DB + project ────────────────────────────────────────────

# allow-fixed-project-id: 991/992 sit below projects_id_seq on the long-lived dev
# DB — the collision window has already passed, so the sequence never re-issues
# these ids. See check 21 in scripts/check_conventions.sh.
TEST_PROJECT_ID = 991  # isolated from other suites
OTHER_PROJECT_ID = 992


@pytest_asyncio.fixture(autouse=True)
async def ensure_returns_project(db_session):
    """Prepare isolated project + warehouse for each test; clean between runs."""
    # Clean returns + receipts for both projects
    await db_session.execute(
        text("DELETE FROM wb_goods_returns WHERE project_id IN (:p1, :p2)"),
        {"p1": TEST_PROJECT_ID, "p2": OTHER_PROJECT_ID},
    )
    await db_session.execute(
        text(
            "DELETE FROM inbound_receipt_items WHERE receipt_id IN "
            "(SELECT id FROM inbound_receipts WHERE project_id IN (:p1, :p2))"
        ),
        {"p1": TEST_PROJECT_ID, "p2": OTHER_PROJECT_ID},
    )
    await db_session.execute(
        text("DELETE FROM inbound_receipts WHERE project_id IN (:p1, :p2)"),
        {"p1": TEST_PROJECT_ID, "p2": OTHER_PROJECT_ID},
    )
    # stock_movements / warehouse_stock тоже ссылаются на warehouses.id. Чистим их
    # ДО самих складов — иначе остаточная строка (другой suite потрогал склад
    # 991/992 раньше в общем прогоне, либо движение из чужого проекта на наш склад)
    # роняет DELETE FROM warehouses на FK. Инцидент: Tests #1188 / commit 1f186da.
    # Скоупим по warehouse_id-подзапросу, чтобы поймать и кросс-проектные строки.
    await db_session.execute(
        text(
            "DELETE FROM stock_movements WHERE warehouse_id IN "
            "(SELECT id FROM warehouses WHERE project_id IN (:p1, :p2))"
        ),
        {"p1": TEST_PROJECT_ID, "p2": OTHER_PROJECT_ID},
    )
    await db_session.execute(
        text(
            "DELETE FROM warehouse_stock WHERE warehouse_id IN "
            "(SELECT id FROM warehouses WHERE project_id IN (:p1, :p2))"
        ),
        {"p1": TEST_PROJECT_ID, "p2": OTHER_PROJECT_ID},
    )
    await db_session.execute(
        text("DELETE FROM warehouses WHERE project_id IN (:p1, :p2)"),
        {"p1": TEST_PROJECT_ID, "p2": OTHER_PROJECT_ID},
    )
    await db_session.execute(
        text("DELETE FROM nomenclature WHERE project_id IN (:p1, :p2)"),
        {"p1": TEST_PROJECT_ID, "p2": OTHER_PROJECT_ID},
    )
    await db_session.commit()

    # Ensure BOTH projects exist — идемпотентно КАЖДЫЙ тест (не гейтить по 991).
    # Тесты изоляции вставляют данные и для 991, и для 992; при параллельном CI
    # один из проектов мог отсутствовать → FK на wb_goods_returns.project_id.
    result_u = await db_session.execute(text("SELECT id FROM users WHERE username = 'wb_returns_test_user'"))
    user_id = result_u.scalar()
    if user_id is None:
        await db_session.execute(
            text(
                "INSERT INTO users (username, email, password_hash, is_active, created_at) "
                "VALUES (:u, :e, :p, true, NOW())"
            ),
            {"u": "wb_returns_test_user", "e": "wbret_test@test.com", "p": "nohash"},
        )
        await db_session.commit()
        result_u = await db_session.execute(text("SELECT id FROM users WHERE username = 'wb_returns_test_user'"))
        user_id = result_u.scalar()
    for pid, slug in [(TEST_PROJECT_ID, "wb-ret-test"), (OTHER_PROJECT_ID, "wb-ret-other")]:
        await db_session.execute(
            text(
                "INSERT INTO projects (id, name, slug, owner_id, created_at) "
                "VALUES (:id, :n, :s, :o, NOW()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": pid, "n": f"WB Ret {pid}", "s": slug, "o": user_id},
        )
    await db_session.commit()

    yield


@pytest_asyncio.fixture
async def warehouse(db_session):
    """Create a warehouse for receipt creation tests."""
    wh = Warehouse(
        project_id=TEST_PROJECT_ID,
        name="Test WH",
        warehouse_type="EXTERNAL",
        is_active=True,
    )
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


# ─── Pure helpers ──────────────────────────────────────────────────────────


class TestHelpers:
    def test_parse_wb_datetime_with_z(self):
        result = _parse_wb_datetime("2026-04-22T08:35:53Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 22
        assert result.hour == 8
        assert result.tzinfo is None

    def test_parse_wb_datetime_with_offset(self):
        result = _parse_wb_datetime("2026-04-22T11:35:53+03:00")
        assert result is not None
        # 11:35 MSK == 08:35 UTC
        assert result.hour == 8
        assert result.tzinfo is None

    def test_parse_wb_datetime_empty(self):
        assert _parse_wb_datetime(None) is None
        assert _parse_wb_datetime("") is None
        assert _parse_wb_datetime("   ") is None

    def test_parse_wb_datetime_invalid(self):
        assert _parse_wb_datetime("not-a-date") is None


class TestClassifyUiState:
    """Cover all 7 branches of classify_ui_state."""

    def test_received(self):
        ret = WbGoodsReturn(
            project_id=1,
            srid="X",
            is_status_active=True,
            inbound_receipt_id=5,
        )
        assert classify_ui_state(ret, "ACCEPTED") == "received"

    def test_expired(self):
        past = utcnow() - timedelta(days=1)
        ret = WbGoodsReturn(
            project_id=1,
            srid="X",
            is_status_active=False,
            expired_dt=past,
            completed_dt=None,
        )
        assert classify_ui_state(ret, None) == "expired"

    def test_pickup_planned(self):
        ret = WbGoodsReturn(
            project_id=1,
            srid="X",
            is_status_active=True,
            inbound_receipt_id=5,
            completed_dt=None,
        )
        assert classify_ui_state(ret, "EXPECTED") == "pickup_planned"

    def test_picked_up_pending_receipt(self):
        ret = WbGoodsReturn(
            project_id=1,
            srid="X",
            is_status_active=False,
            inbound_receipt_id=5,
            completed_dt=utcnow(),
        )
        assert classify_ui_state(ret, "EXPECTED") == "picked_up_pending_receipt"

    def test_picked_without_receipt(self):
        ret = WbGoodsReturn(
            project_id=1,
            srid="X",
            is_status_active=False,
            inbound_receipt_id=None,
            completed_dt=utcnow(),
        )
        assert classify_ui_state(ret, None) == "picked_without_receipt"

    def test_ready_for_pickup(self):
        ret = WbGoodsReturn(
            project_id=1,
            srid="X",
            is_status_active=True,
            ready_to_return_dt=utcnow(),
            completed_dt=None,
            inbound_receipt_id=None,
        )
        assert classify_ui_state(ret, None) == "ready_for_pickup"

    def test_in_transit_to_pvz(self):
        ret = WbGoodsReturn(
            project_id=1,
            srid="X",
            is_status_active=True,
            ready_to_return_dt=None,
            completed_dt=None,
            inbound_receipt_id=None,
        )
        assert classify_ui_state(ret, None) == "in_transit_to_pvz"


# ─── Sync tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSync:
    async def test_sync_upserts_returns(self, db_session):
        """Two sync calls with same srid — first INSERTs, second UPDATEs."""
        mock = AsyncMock()
        mock.get_goods_returns.return_value = [
            {
                "srid": "SR-1",
                "shkId": "123",
                "stickerId": "st-1",
                "barcode": "999000",
                "nmId": 5555,
                "subjectName": "Футболка",
                "brand": "Nike",
                "techSize": "M",
                "orderId": 42,
                "orderDt": "2026-04-01",
                "readyToReturnDt": "2026-04-10T10:00:00Z",
                "expiredDt": "2026-04-20T10:00:00Z",
                "completedDt": None,
                "status": "Готов к выдаче",
                "returnType": "Возврат брака",
                "reason": "defect",
                "isStatusActive": True,
                "dstOfficeId": 100,
                "dstOfficeAddress": "ул. Ленина 1",
            }
        ]
        result1 = await sync_project_returns(
            db_session,
            TEST_PROJECT_ID,
            "FAKE_KEY",
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 22),
            api_client=mock,
        )
        assert result1["rows_fetched"] == 1
        assert result1["rows_upserted"] == 1

        # Second sync — status changed
        mock.get_goods_returns.return_value = [
            {
                "srid": "SR-1",
                "isStatusActive": False,
                "completedDt": "2026-04-15T12:00:00Z",
                "status": "Забран",
            }
        ]
        result2 = await sync_project_returns(
            db_session,
            TEST_PROJECT_ID,
            "FAKE_KEY",
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 22),
            api_client=mock,
        )
        assert result2["rows_upserted"] == 1

        # Verify update (not duplicate)
        rows = (
            await db_session.execute(
                text("SELECT COUNT(*) FROM wb_goods_returns WHERE project_id=:p AND srid='SR-1'"),
                {"p": TEST_PROJECT_ID},
            )
        ).scalar()
        assert rows == 1

        # Verify updated fields
        row = (
            await db_session.execute(
                text(
                    "SELECT is_status_active, completed_dt, status "
                    "FROM wb_goods_returns WHERE project_id=:p AND srid='SR-1'"
                ),
                {"p": TEST_PROJECT_ID},
            )
        ).first()
        assert row[0] is False
        assert row[1] is not None
        assert row[2] == "Забран"

    async def test_sync_camel_case_conversion(self, db_session):
        """All camelCase fields are correctly mapped to snake_case."""
        mock = AsyncMock()
        mock.get_goods_returns.return_value = [
            {
                "srid": "SR-2",
                "shkId": "SHK-2",
                "stickerId": "ST-2",
                "barcode": "111",
                "nmId": 999,
                "subjectName": "Куртка",
                "brand": "Adidas",
                "techSize": "L",
                "orderId": 100,
                "orderDt": "2026-04-01",
                "readyToReturnDt": "2026-04-10T10:00:00Z",
                "expiredDt": "2026-04-20T10:00:00Z",
                "isStatusActive": True,
                "dstOfficeId": 200,
                "dstOfficeAddress": "addr-200",
            }
        ]
        await sync_project_returns(db_session, TEST_PROJECT_ID, "K", api_client=mock)
        row = (
            await db_session.execute(
                text(
                    "SELECT shk_id, sticker_id, barcode, nm_id, subject_name, brand, "
                    "tech_size, order_id, order_dt, ready_to_return_dt, expired_dt, "
                    "dst_office_id, dst_office_address "
                    "FROM wb_goods_returns WHERE project_id=:p AND srid='SR-2'"
                ),
                {"p": TEST_PROJECT_ID},
            )
        ).first()
        assert row[0] == "SHK-2"
        assert row[1] == "ST-2"
        assert row[2] == "111"
        assert row[3] == 999
        assert row[4] == "Куртка"
        assert row[5] == "Adidas"
        assert row[6] == "L"
        assert row[7] == 100
        assert str(row[8]) == "2026-04-01"
        assert row[11] == 200
        assert row[12] == "addr-200"

    async def test_sync_datetime_iso_parsing(self, db_session):
        """ISO datetimes with Z and offset are correctly parsed to naive UTC."""
        mock = AsyncMock()
        mock.get_goods_returns.return_value = [
            {
                "srid": "SR-3",
                "readyToReturnDt": "2026-04-22T08:35:53Z",
                "expiredDt": "2026-04-25T11:35:53+03:00",
                "completedDt": "2026-04-30T00:00:00Z",
                "isStatusActive": True,
            }
        ]
        await sync_project_returns(db_session, TEST_PROJECT_ID, "K", api_client=mock)
        row = (
            await db_session.execute(
                text(
                    "SELECT ready_to_return_dt, expired_dt, completed_dt "
                    "FROM wb_goods_returns WHERE project_id=:p AND srid='SR-3'"
                ),
                {"p": TEST_PROJECT_ID},
            )
        ).first()
        assert row[0] == datetime(2026, 4, 22, 8, 35, 53)
        # 11:35 MSK == 08:35 UTC
        assert row[1] == datetime(2026, 4, 25, 8, 35, 53)
        assert row[2] == datetime(2026, 4, 30, 0, 0, 0)


# ─── List filter tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestListFilters:
    async def test_tab_pvz_returns_only_active_no_receipt(self, db_session):
        """tab=pvz returns only active returns without a linked receipt."""
        now = utcnow()
        # active, no receipt → should appear
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="PVZ-1",
                is_status_active=True,
                ready_to_return_dt=now,
            )
        )
        # active, WITH receipt → must NOT appear in pvz
        receipt = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=1,  # fake, will be orphan but OK for query
            number="VZ-1",
            status=InboundStatus.EXPECTED,
            is_defect=True,
        )
        db_session.add(receipt)
        await db_session.flush()
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="PVZ-2",
                is_status_active=True,
                inbound_receipt_id=receipt.id,
            )
        )
        # inactive → must NOT appear
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="PVZ-3",
                is_status_active=False,
            )
        )
        await db_session.commit()

        items, total = await list_returns(db_session, TEST_PROJECT_ID, tab="pvz")
        srids = {i["srid"] for i in items}
        assert "PVZ-1" in srids
        assert "PVZ-2" not in srids
        assert "PVZ-3" not in srids
        assert total == 1

    async def test_tab_in_transit_returns_expected_receipts(self, db_session, warehouse):
        """tab=in_transit returns only returns linked to an EXPECTED receipt."""
        receipt_expected = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            number="VZ-2",
            status=InboundStatus.EXPECTED,
            is_defect=True,
        )
        receipt_accepted = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            number="VZ-3",
            status=InboundStatus.ACCEPTED,
            is_defect=True,
        )
        db_session.add_all([receipt_expected, receipt_accepted])
        await db_session.flush()
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="IT-1",
                is_status_active=True,
                inbound_receipt_id=receipt_expected.id,
            )
        )
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="IT-2",
                is_status_active=False,
                inbound_receipt_id=receipt_accepted.id,
            )
        )
        await db_session.commit()

        items, _ = await list_returns(db_session, TEST_PROJECT_ID, tab="in_transit")
        srids = {i["srid"] for i in items}
        assert "IT-1" in srids
        assert "IT-2" not in srids

    async def test_tab_history_contains_accepted_and_expired(self, db_session, warehouse):
        """tab=history returns ACCEPTED/CANCELLED receipts or expired returns."""
        receipt = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            number="VZ-4",
            status=InboundStatus.ACCEPTED,
            is_defect=True,
        )
        db_session.add(receipt)
        await db_session.flush()
        past = utcnow() - timedelta(days=10)
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="HIST-1",
                is_status_active=False,
                inbound_receipt_id=receipt.id,
            )
        )
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="HIST-2-expired",
                is_status_active=False,
                expired_dt=past,
            )
        )
        # active without receipt → should NOT be in history
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="HIST-3-active",
                is_status_active=True,
            )
        )
        await db_session.commit()

        items, _ = await list_returns(db_session, TEST_PROJECT_ID, tab="history")
        srids = {i["srid"] for i in items}
        assert "HIST-1" in srids
        assert "HIST-2-expired" in srids
        assert "HIST-3-active" not in srids


@pytest.mark.asyncio
class TestProjectIsolationAndSoftDelete:
    async def test_list_filters_by_project(self, db_session):
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="MINE",
                is_status_active=True,
            )
        )
        db_session.add(
            WbGoodsReturn(
                project_id=OTHER_PROJECT_ID,
                srid="OTHER",
                is_status_active=True,
            )
        )
        await db_session.commit()
        items, _ = await list_returns(db_session, TEST_PROJECT_ID, tab="all")
        srids = {i["srid"] for i in items}
        assert "MINE" in srids
        assert "OTHER" not in srids

    async def test_list_hides_soft_deleted(self, db_session):
        ret = WbGoodsReturn(
            project_id=TEST_PROJECT_ID,
            srid="DELME",
            is_status_active=True,
        )
        db_session.add(ret)
        await db_session.commit()
        ret.soft_delete()
        await db_session.commit()
        items, total = await list_returns(db_session, TEST_PROJECT_ID, tab="all")
        assert total == 0
        assert items == []


@pytest.mark.asyncio
class TestGetSummary:
    async def test_summary_counts_all_states(self, db_session, warehouse):
        now = utcnow()
        past = now - timedelta(days=2)
        future = now + timedelta(days=1)
        # ready_for_pickup + soon_expires (within 3 days)
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="R1",
                is_status_active=True,
                ready_to_return_dt=now,
                expired_dt=future,
            )
        )
        # ready_for_pickup, not soon_expires
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="R2",
                is_status_active=True,
                ready_to_return_dt=now,
                expired_dt=now + timedelta(days=30),
            )
        )
        # in_transit
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="T1",
                is_status_active=True,
            )
        )
        # expired
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="E1",
                is_status_active=False,
                expired_dt=past,
                completed_dt=None,
            )
        )
        # received
        receipt_acc = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            number="VZ-5",
            status=InboundStatus.ACCEPTED,
            is_defect=True,
        )
        db_session.add(receipt_acc)
        await db_session.flush()
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="RC1",
                is_status_active=True,
                inbound_receipt_id=receipt_acc.id,
            )
        )
        # pickup_planned
        receipt_exp = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            number="VZ-6",
            status=InboundStatus.EXPECTED,
            is_defect=True,
        )
        db_session.add(receipt_exp)
        await db_session.flush()
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="PP1",
                is_status_active=True,
                inbound_receipt_id=receipt_exp.id,
                completed_dt=None,
            )
        )
        # picked_up_pending_receipt
        receipt_exp2 = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            number="VZ-7",
            status=InboundStatus.EXPECTED,
            is_defect=True,
        )
        db_session.add(receipt_exp2)
        await db_session.flush()
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="PUP1",
                is_status_active=False,
                inbound_receipt_id=receipt_exp2.id,
                completed_dt=now,
            )
        )
        # picked_without_receipt
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="PW1",
                is_status_active=False,
                completed_dt=now,
                inbound_receipt_id=None,
            )
        )
        await db_session.commit()

        summary = await get_summary(db_session, TEST_PROJECT_ID)
        assert summary["ready_for_pickup"] == 2
        assert summary["soon_expires"] == 1  # only R1 (future < 3 days)
        assert summary["in_transit_to_pvz"] == 1
        assert summary["expired"] == 1
        assert summary["received"] == 1
        assert summary["pickup_planned"] == 1
        assert summary["picked_up_pending_receipt"] == 1
        assert summary["picked_without_receipt"] == 1


# ─── Create receipt from returns tests ─────────────────────────────────────


@pytest.mark.asyncio
class TestCreateReceiptFromReturns:
    async def test_create_receipt_success(self, db_session, warehouse):
        """Happy path: create receipt from N srids, all linked, items created."""
        for srid in ["CR-1", "CR-2", "CR-3"]:
            db_session.add(
                WbGoodsReturn(
                    project_id=TEST_PROJECT_ID,
                    srid=srid,
                    barcode=f"BC-{srid}",
                    nm_id=12345,
                    subject_name="Test",
                    is_status_active=True,
                )
            )
        await db_session.commit()

        result = await create_receipt_from_returns(
            db_session,
            TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            srids=["CR-1", "CR-2", "CR-3"],
            comment="test",
        )
        assert result["items_count"] == 3
        assert set(result["linked_srids"]) == {"CR-1", "CR-2", "CR-3"}
        assert result["status"] == InboundStatus.EXPECTED
        assert result["warehouse_id"] == warehouse.id
        assert result["receipt_number"].startswith("ВЗ-")

        # Check that all returns are linked
        rows = (
            await db_session.execute(
                text("SELECT COUNT(*) FROM wb_goods_returns " "WHERE project_id=:p AND inbound_receipt_id=:rid"),
                {"p": TEST_PROJECT_ID, "rid": result["receipt_id"]},
            )
        ).scalar()
        assert rows == 3

        # Receipt exists with is_defect=true
        r = (
            await db_session.execute(
                text("SELECT status, is_defect, defect_reason FROM inbound_receipts " "WHERE id = :rid"),
                {"rid": result["receipt_id"]},
            )
        ).first()
        assert r[0] == InboundStatus.EXPECTED
        assert r[1] is True
        assert "Возврат" in r[2]

    async def test_create_receipt_rejects_already_linked(self, db_session, warehouse):
        # Create existing receipt first
        receipt = InboundReceipt(
            project_id=TEST_PROJECT_ID,
            warehouse_id=warehouse.id,
            number="VZ-PRE",
            status=InboundStatus.EXPECTED,
            is_defect=True,
        )
        db_session.add(receipt)
        await db_session.flush()
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="LINKED",
                is_status_active=True,
                barcode="BC-L",
                inbound_receipt_id=receipt.id,
            )
        )
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await create_receipt_from_returns(
                db_session,
                TEST_PROJECT_ID,
                warehouse_id=warehouse.id,
                srids=["LINKED"],
            )
        assert exc.value.status_code == 400
        assert "already linked" in exc.value.detail.lower() or "not found" in exc.value.detail.lower()

    async def test_create_receipt_rejects_unknown_srid(self, db_session, warehouse):
        with pytest.raises(HTTPException) as exc:
            await create_receipt_from_returns(
                db_session,
                TEST_PROJECT_ID,
                warehouse_id=warehouse.id,
                srids=["NEVER-EXISTED"],
            )
        assert exc.value.status_code == 400
        assert "not found" in exc.value.detail.lower()

    async def test_create_receipt_rejects_wrong_project_warehouse(self, db_session, warehouse):
        """Warehouse belongs to a different project → 404."""
        db_session.add(
            WbGoodsReturn(
                project_id=OTHER_PROJECT_ID,
                srid="OP1",
                barcode="BC-OP",
                is_status_active=True,
            )
        )
        await db_session.commit()

        with pytest.raises(HTTPException) as exc:
            await create_receipt_from_returns(
                db_session,
                OTHER_PROJECT_ID,  # different project — warehouse belongs to TEST_PROJECT_ID
                warehouse_id=warehouse.id,
                srids=["OP1"],
            )
        assert exc.value.status_code == 404

    async def test_create_receipt_rejects_soft_deleted_warehouse(self, db_session, warehouse):
        """Soft-deleted warehouse → 404."""
        warehouse.soft_delete()
        await db_session.commit()
        db_session.add(
            WbGoodsReturn(
                project_id=TEST_PROJECT_ID,
                srid="SD1",
                barcode="BC-SD",
                is_status_active=True,
            )
        )
        await db_session.commit()
        with pytest.raises(HTTPException) as exc:
            await create_receipt_from_returns(
                db_session,
                TEST_PROJECT_ID,
                warehouse_id=warehouse.id,
                srids=["SD1"],
            )
        assert exc.value.status_code == 404
