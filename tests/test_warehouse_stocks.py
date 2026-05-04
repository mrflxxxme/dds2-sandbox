"""
Tests for warehouse stock analytics — compute_need helper,
and response structure validation for get_warehouse_stocks / get_warehouse_need.
"""

import pytest
from sqlalchemy import delete, select, text

from backend.models import WbStockSnapshot, WbWarehouseStock
from backend.services.stock_analytics_service import compute_need
from backend.services.warehouse_stock_service import sync_warehouse_stocks

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_need(stock_qty, avg_daily, need_days)
# Formula: max(0, round(avg_daily * need_days - stock_qty))
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeNeed:
    """Verify restocking need calculation: avg_daily * need_days - stock_qty."""

    def test_basic_need(self):
        """stock=50, avg=10/day, 14 days → need 10*14-50 = 90."""
        assert compute_need(50, 10.0, 14) == 90

    def test_surplus(self):
        """stock=100, avg=2/day, 7 days → 2*7-100 = -86 → 0 (surplus)."""
        assert compute_need(100, 2.0, 7) == 0

    def test_exact_match(self):
        """stock=50, avg=5/day, 10 days → 5*10-50 = 0."""
        assert compute_need(50, 5.0, 10) == 0

    def test_zero_avg(self):
        """No sales → 0*14-50 = -50 → 0."""
        assert compute_need(50, 0.0, 14) == 0

    def test_zero_stock(self):
        """stock=0, avg=10/day, 7 days → 10*7 = 70."""
        assert compute_need(0, 10.0, 7) == 70

    def test_negative_stock(self):
        """stock=-10 → 5*14 - (-10) = 80."""
        assert compute_need(-10, 5.0, 14) == 80

    def test_large_need_days(self):
        """stock=100, avg=20/day, 30 days → 20*30-100 = 500."""
        assert compute_need(100, 20.0, 30) == 500

    def test_fractional_avg(self):
        """stock=3, avg=0.5/day, 14 days → 0.5*14-3 = 4."""
        assert compute_need(3, 0.5, 14) == 4

    def test_both_zero(self):
        """No sales, no stock → 0."""
        assert compute_need(0, 0.0, 14) == 0

    def test_need_days_7(self):
        """stock=20, avg=8/day, 7 days → 8*7-20 = 36."""
        assert compute_need(20, 8.0, 7) == 36

    def test_need_days_30(self):
        """stock=10, avg=3/day, 30 days → 3*30-10 = 80."""
        assert compute_need(10, 3.0, 30) == 80


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Warehouse stocks response structure
# ═══════════════════════════════════════════════════════════════════════════════


class TestWarehouseResponseStructure:
    """Validate expected response shapes without DB."""

    def test_warehouse_stocks_empty_shape(self):
        """Expected shape when warehouses response is empty."""
        response = {
            "warehouses": [],
            "total_warehouses": 0,
            "total_qty": 0,
            "total_in_way_to_client": 0,
            "total_in_way_from_client": 0,
            "yesterday_total_qty": 0,
            "change_total": 0,
            "last_synced_at": None,
        }
        assert response["total_warehouses"] == 0
        assert response["total_qty"] == 0
        assert response["warehouses"] == []
        assert response["yesterday_total_qty"] == 0
        assert response["change_total"] == 0

    def test_warehouse_stocks_shape(self):
        """Expected shape of a single warehouse entry with extended fields."""
        wh = {
            "name": "Казань",
            "total_qty": 500,
            "in_way_to_client": 50,
            "in_way_from_client": 10,
            "articles_count": 10,
            "yesterday_qty": 480,
            "change": 20,
        }
        assert wh["name"] == "Казань"
        assert isinstance(wh["total_qty"], int)
        assert isinstance(wh["articles_count"], int)
        assert wh["in_way_to_client"] == 50
        assert wh["in_way_from_client"] == 10
        assert wh["change"] == 20

    def test_articles_response_shape(self):
        """Expected shape of stock by article response."""
        article = {
            "nm_id": 12345,
            "vendor_code": "ART-001",
            "subject": "Ковры",
            "brand": "TestBrand",
            "total_qty": 100,
            "in_way_to_client": 15,
            "in_way_from_client": 5,
            "warehouses": [
                {"name": "Казань", "quantity": 60, "in_way_to_client": 10, "in_way_from_client": 3},
                {"name": "Тула", "quantity": 40, "in_way_to_client": 5, "in_way_from_client": 2},
            ],
        }
        assert article["total_qty"] == 100
        assert len(article["warehouses"]) == 2
        assert article["warehouses"][0]["name"] == "Казань"
        assert sum(w["quantity"] for w in article["warehouses"]) == 100

    def test_history_response_shape(self):
        """Expected shape of stock history response."""
        day = {
            "date": "2026-03-21",
            "total_qty": 5000,
            "in_way_to_client": 300,
            "in_way_from_client": 50,
            "total": 5350,
            "articles_count": 100,
        }
        assert day["total"] == day["total_qty"] + day["in_way_to_client"] + day["in_way_from_client"]
        assert day["articles_count"] == 100

    def test_need_response_structure(self):
        """Expected shape of stock need response."""
        response = {
            "need_days": 14,
            "total_warehouses": 3,
            "total_articles": 10,
            "warehouses": [
                {
                    "name": "Коледино",
                    "total_need": 150,
                    "articles": {
                        "12345": {"stock": 50, "avg_daily": 10.0, "need": 90},
                    },
                }
            ],
            "articles": [
                {"nm_id": 12345, "vendor_code": "ART-001"},
            ],
        }
        assert response["need_days"] == 14
        wh = response["warehouses"][0]
        assert wh["name"] == "Коледино"
        assert wh["total_need"] == 150
        art_data = wh["articles"]["12345"]
        assert art_data["need"] == 90

    def test_compute_need_pipeline(self):
        """Simulate computing need for multiple articles in a warehouse."""
        articles = [
            {"nm_id": 1, "avg_daily": 5.0, "stock": 30},
            {"nm_id": 2, "avg_daily": 3.0, "stock": 100},
            {"nm_id": 3, "avg_daily": 10.0, "stock": 20},
        ]
        need_days = 14
        results = []
        total_need = 0
        for a in articles:
            n = compute_need(a["stock"], a["avg_daily"], need_days)
            total_need += n
            results.append({"nm_id": a["nm_id"], "need": n})

        assert results[0]["need"] == 40  # 5*14 - 30 = 40
        assert results[1]["need"] == 0  # 3*14 - 100 = -58 → 0
        assert results[2]["need"] == 120  # 10*14 - 20 = 120
        assert total_need == 160

    def test_multiple_warehouses_need(self):
        """Simulate need computation across warehouses."""
        warehouses = {
            "Коледино": {"nm_1": 50, "nm_2": 30},
            "Казань": {"nm_1": 20, "nm_2": 10},
        }
        avg_daily = {"nm_1": 5.0, "nm_2": 3.0}
        need_days = 14

        results = {}
        for wh_name, stocks in warehouses.items():
            wh_total = 0
            for nm_id, stock in stocks.items():
                n = compute_need(stock, avg_daily[nm_id], need_days)
                wh_total += n
            results[wh_name] = wh_total

        # Коледино: nm_1: 5*14-50=20, nm_2: 3*14-30=12 → 32
        assert results["Коледино"] == 32
        # Казань: nm_1: 5*14-20=50, nm_2: 3*14-10=32 → 82
        assert results["Казань"] == 82


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: sync_warehouse_stocks deduplicates rows with same (nm_id, warehouse_name)
# Regression for CardinalityViolationError on ON CONFLICT (2026-05-04).
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
async def _clean_wb_stocks(db_session, project):
    # Test DB is shared — sync sequence with existing rows so PK INSERTs work.
    await db_session.execute(delete(WbStockSnapshot).where(WbStockSnapshot.project_id == project.id))
    await db_session.execute(delete(WbWarehouseStock).where(WbWarehouseStock.project_id == project.id))
    await db_session.execute(
        text(
            "SELECT setval('wb_stock_snapshots_id_seq', COALESCE((SELECT MAX(id) FROM wb_stock_snapshots), 0) + 1, false)"
        )
    )
    await db_session.execute(
        text(
            "SELECT setval('wb_warehouse_stocks_id_seq', COALESCE((SELECT MAX(id) FROM wb_warehouse_stocks), 0) + 1, false)"
        )
    )
    await db_session.commit()
    yield


class TestSyncWarehouseStocksDedup:
    @pytest.mark.asyncio
    async def test_dedup_same_nm_and_warehouse_aggregates_quantities(self, _clean_wb_stocks, db_session, project):
        # WB API returns one row per barcode/size — same nm_id + warehouseName
        # can repeat. Sync must aggregate, not blow up on UPSERT.
        items = [
            {
                "nmId": 100,
                "warehouseName": "Коледино",
                "supplierArticle": "art-1",
                "subject": "Ноутбуки",
                "brand": "FSPLACE",
                "barcode": "bc-size-S",
                "quantity": 5,
                "quantityFull": 5,
                "inWayToClient": 1,
                "inWayFromClient": 0,
            },
            {
                "nmId": 100,
                "warehouseName": "Коледино",
                "supplierArticle": "art-1",
                "subject": "Ноутбуки",
                "brand": "FSPLACE",
                "barcode": "bc-size-M",
                "quantity": 3,
                "quantityFull": 3,
                "inWayToClient": 0,
                "inWayFromClient": 2,
            },
            {
                "nmId": 100,
                "warehouseName": "Казань",
                "supplierArticle": "art-1",
                "subject": "Ноутбуки",
                "brand": "FSPLACE",
                "barcode": "bc-size-S",
                "quantity": 7,
                "quantityFull": 7,
                "inWayToClient": 0,
                "inWayFromClient": 0,
            },
        ]

        count = await sync_warehouse_stocks(db_session, project.id, items)

        # 3 input rows aggregated to 2 unique (nm_id, warehouse_name) keys
        assert count == 2

        rows = (
            (await db_session.execute(select(WbWarehouseStock).where(WbWarehouseStock.project_id == project.id)))
            .scalars()
            .all()
        )
        by_wh = {r.warehouse_name: r for r in rows}
        assert by_wh["Коледино"].quantity == 8  # 5 + 3
        assert by_wh["Коледино"].quantity_full == 8
        assert by_wh["Коледино"].in_way_to_client == 1
        assert by_wh["Коледино"].in_way_from_client == 2
        assert by_wh["Коледино"].subject == "Ноутбуки"
        assert by_wh["Казань"].quantity == 7

    @pytest.mark.asyncio
    async def test_skips_rows_without_nm_or_warehouse(self, _clean_wb_stocks, db_session, project):
        items = [
            {"nmId": None, "warehouseName": "Коледино", "quantity": 1},
            {"nmId": 200, "warehouseName": "", "quantity": 1},
            {"nmId": 200, "warehouseName": "Коледино", "quantity": 4},
        ]
        count = await sync_warehouse_stocks(db_session, project.id, items)
        assert count == 1
        rows = (
            (await db_session.execute(select(WbWarehouseStock).where(WbWarehouseStock.project_id == project.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].quantity == 4

    @pytest.mark.asyncio
    async def test_first_nonempty_metadata_wins(self, _clean_wb_stocks, db_session, project):
        # vendor_code/subject/brand from first row that has a non-empty value.
        items = [
            {
                "nmId": 300,
                "warehouseName": "Коледино",
                "supplierArticle": "",
                "subject": "",
                "brand": "",
                "quantity": 1,
            },
            {
                "nmId": 300,
                "warehouseName": "Коледино",
                "supplierArticle": "vc-1",
                "subject": "Диваны бескаркасные",
                "brand": "FSPLACE",
                "quantity": 2,
            },
            {
                "nmId": 300,
                "warehouseName": "Коледино",
                "supplierArticle": "vc-2",
                "subject": "OTHER",
                "brand": "OTHER",
                "quantity": 3,
            },
        ]
        await sync_warehouse_stocks(db_session, project.id, items)
        row = (
            await db_session.execute(select(WbWarehouseStock).where(WbWarehouseStock.project_id == project.id))
        ).scalar_one()
        assert row.quantity == 6
        assert row.vendor_code == "vc-1"
        assert row.subject == "Диваны бескаркасные"
        assert row.brand == "FSPLACE"
