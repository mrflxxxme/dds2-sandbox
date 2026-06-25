# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for logistics analytics extensions (Лист логиста → История отправок):
- расширенная сводка (заявки, штуки, SKU, склады, подрядчики)
- агрегаты по подрядчикам (top по перевозчикам)
- кривая «объём → ₽/паллета» (pallet buckets)
- аномальные отгрузки (без стоимости / завышенная / заниженная ₽/паллета)
- построчная выдача отгрузок (get_logistics_shipments) с фильтрами и флагом аномалии

Данные сидируются напрямую (AssemblyRequest + OutboundShipment + Counterparty +
OutboundShipmentItem) — точный контроль стоимости/паллет/склада/перевозчика.
"""

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.counterparty import Counterparty
from backend.models.warehouse import OutboundShipment, OutboundShipmentItem, OutboundStatus
from backend.services.assembly.analytics import (
    get_cost_forecast,
    get_logistics_analytics,
    get_logistics_shipments,
)

pytestmark = pytest.mark.asyncio


async def _ensure_warehouse(db_session, pid: int) -> int:
    row = await db_session.execute(
        text(
            "SELECT id FROM warehouses WHERE project_id = :pid AND (is_deleted = false OR is_deleted IS NULL) LIMIT 1"
        ),
        {"pid": pid},
    )
    wh_id = row.scalar()
    if wh_id is None:
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'Logi Test WH', 'FULFILLMENT', 1, true, false, NOW(), NOW())"
            ),
            {"pid": pid},
        )
        await db_session.commit()
        row = await db_session.execute(
            text("SELECT id FROM warehouses WHERE project_id = :pid LIMIT 1"),
            {"pid": pid},
        )
        wh_id = row.scalar()
    return wh_id


async def _ensure_nom(db_session, pid: int, barcode: str) -> int:
    row = await db_session.execute(
        text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
        {"pid": pid, "bc": barcode},
    )
    nid = row.scalar()
    if nid is None:
        row = await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, brand, updated_at) "
                "VALUES (:pid, :bc, 'TestBrand', NOW()) RETURNING id"
            ),
            {"pid": pid, "bc": barcode},
        )
        nid = row.scalar()
        await db_session.commit()
    return nid


@pytest_asyncio.fixture
async def seeded(db_session, project):
    """Сидируем контролируемый набор отгрузок для project.

    Кластер «Электросталь»: 6 нормальных (cpp=2000) + 1 завышенная (cpp=20000)
    + 1 заниженная (cpp=400). Плюс отгрузка без стоимости и второй склад/перевозчик.
    """
    pid = project.id
    wh_id = await _ensure_warehouse(db_session, pid)
    nom_id = await _ensure_nom(db_session, pid, "LOGI_BC_1")

    carrier_a = Counterparty(project_id=pid, inn="7700000001", name="ТК Альфа")
    carrier_b = Counterparty(project_id=pid, inn="7700000002", name="ИП Браво")
    db_session.add_all([carrier_a, carrier_b])
    await db_session.flush()

    specs = [
        # (number, dest, pallets, cost, carrier_id, status, items_qty)
        ("LG-1", "Электросталь", 5, Decimal("10000"), carrier_a.id, AssemblyStatus.DELIVERED, 100),
        ("LG-2", "Электросталь", 5, Decimal("10000"), carrier_a.id, AssemblyStatus.DELIVERED, 100),
        ("LG-3", "Электросталь", 5, Decimal("10000"), carrier_a.id, AssemblyStatus.SHIPPED, 100),
        ("LG-4", "Электросталь", 5, Decimal("10000"), carrier_b.id, AssemblyStatus.SHIPPED, 100),
        ("LG-5", "Электросталь", 5, Decimal("10000"), carrier_b.id, AssemblyStatus.SHIPPED, 100),
        ("LG-6", "Электросталь", 5, Decimal("10000"), carrier_b.id, AssemblyStatus.SHIPPED, 100),
        ("LG-HI", "Электросталь", 5, Decimal("100000"), carrier_a.id, AssemblyStatus.SHIPPED, 50),  # cpp=20000 → overpriced
        ("LG-LO", "Электросталь", 5, Decimal("2000"), carrier_b.id, AssemblyStatus.SHIPPED, 50),  # cpp=400 → underpriced
        ("LG-NOCOST", "Коледино", 3, None, carrier_a.id, AssemblyStatus.SHIPPED, 30),  # no_cost
        ("LG-BIG", "Коледино", 12, Decimal("36000"), carrier_b.id, AssemblyStatus.DELIVERED, 200),  # bucket 11+
    ]
    for number, dest, pallets, cost, cp_id, status, qty in specs:
        req = AssemblyRequest(
            project_id=pid,
            warehouse_id=wh_id,
            number=number,
            status=status,
            pallets_count=pallets,
            pallet_weight_kg=Decimal("100"),
            wb_warehouse_name_manual=dest,
            counterparty_id=cp_id,
        )
        db_session.add(req)
        await db_session.flush()
        # Состав заявки — источник brands в построчной выдаче.
        db_session.add(
            AssemblyRequestItem(
                project_id=pid,
                assembly_request_id=req.id,
                nomenclature_id=nom_id,
                barcode="LOGI_BC_1",
                quantity=qty,
            )
        )
        ship = OutboundShipment(
            project_id=pid,
            warehouse_id=wh_id,
            number=f"O-{number}",
            status=OutboundStatus.DELIVERED if status == AssemblyStatus.DELIVERED else OutboundStatus.SHIPPED,
            destination=dest,
            assembly_request_id=req.id,
            attempt_no=1,
            pickup_cost=cost,
            pallets_count=pallets,
            pallet_weight_kg=Decimal("100"),
            counterparty_id=cp_id,
            shipped_date=date(2026, 6, 10),
        )
        db_session.add(ship)
        await db_session.flush()
        db_session.add(
            OutboundShipmentItem(
                project_id=pid,
                shipment_id=ship.id,
                nomenclature_id=nom_id,
                barcode="LOGI_BC_1",
                quantity=qty,
            )
        )
    await db_session.commit()
    return project


class TestLogisticsSummary:
    async def test_extended_summary_counts(self, db_session, seeded):
        a = await get_logistics_analytics(db_session, seeded.id)
        s = a["summary"]
        assert s["total_shipments"] == 10
        assert s["total_requests"] == 10
        # 6*100 + 50 + 50 + 30 + 200 = 930 штук
        assert s["total_items"] == 930
        assert s["total_skus"] == 1
        assert s["total_destinations"] == 2  # Электросталь + Коледино
        assert s["total_carriers"] == 2
        # вес: каждая паллета 100 кг
        total_pallets = 5 * 8 + 3 + 12
        assert int(s["total_pallets"]) == total_pallets
        assert float(s["total_weight_kg"]) == float(total_pallets * 100)


class TestLogisticsCarriers:
    async def test_by_carrier_aggregation(self, db_session, seeded):
        a = await get_logistics_analytics(db_session, seeded.id)
        by_carrier = {c["carrier_name"]: c for c in a["by_carrier"]}
        assert "ТК Альфа" in by_carrier
        assert "ИП Браво" in by_carrier
        # Альфа: LG-1,2,3,HI,NOCOST = 5 отгрузок; Браво: LG-4,5,6,LO,BIG = 5
        assert by_carrier["ТК Альфа"]["shipments_count"] == 5
        assert by_carrier["ИП Браво"]["shipments_count"] == 5
        assert by_carrier["ТК Альфа"]["destinations_count"] == 2


class TestLogisticsPalletBuckets:
    async def test_buckets_present_and_ordered(self, db_session, seeded):
        a = await get_logistics_analytics(db_session, seeded.id)
        buckets = {b["bucket"]: b for b in a["pallet_buckets"]}
        # все «4-5» отгрузки (8 шт на 5 паллет) + одна «11+» (12 паллет)
        assert "4-5" in buckets
        assert "11+" in buckets
        assert buckets["11+"]["shipments_count"] == 1
        # порядок по sort_order возрастающий
        orders = [b["sort_order"] for b in a["pallet_buckets"]]
        assert orders == sorted(orders)

    async def test_dest_pallet_cells_per_warehouse(self, db_session, seeded):
        a = await get_logistics_analytics(db_session, seeded.id)
        cells = {(c["dest_warehouse"], c["bucket"]): c for c in a["dest_pallet_cells"]}
        # Электросталь: 8 отгрузок по 5 паллет → корзина «4-5»
        es = cells.get(("Электросталь", "4-5"))
        assert es is not None and es["shipments_count"] == 8
        # Коледино: BIG 12 паллет → «11+» (NOCOST без стоимости в корзины не идёт)
        kol = cells.get(("Коледино", "11+"))
        assert kol is not None and kol["shipments_count"] == 1
        assert ("Коледино", "1") not in cells  # no_cost не образует ячейку


class TestLogisticsAnomalies:
    async def test_no_cost_detected(self, db_session, seeded):
        a = await get_logistics_analytics(db_session, seeded.id)
        no_cost = [x for x in a["anomalies"] if x["anomaly_type"] == "no_cost"]
        assert any(x["assembly_number"] == "LG-NOCOST" for x in no_cost)

    async def test_overpriced_and_underpriced_detected(self, db_session, seeded):
        a = await get_logistics_analytics(db_session, seeded.id)
        by_num = {x["assembly_number"]: x for x in a["anomalies"]}
        assert by_num.get("LG-HI", {}).get("anomaly_type") == "overpriced"
        assert by_num.get("LG-LO", {}).get("anomaly_type") == "underpriced"
        # нормальные не помечены
        assert "LG-1" not in by_num

    async def test_project_isolation(self, db_session, seeded, other_project):
        a = await get_logistics_analytics(db_session, other_project.id)
        assert a["summary"]["total_shipments"] == 0
        assert a["anomalies"] == []


class TestLogisticsShipmentsList:
    async def test_full_set_returned_for_sorting(self, db_session, seeded):
        s = await get_logistics_shipments(db_session, seeded.id)
        assert s["total"] == 10
        assert len(s["items"]) == 10  # весь период, не страница
        assert s["truncated"] is False

    async def test_rows_carry_carrier_and_anomaly(self, db_session, seeded):
        s = await get_logistics_shipments(db_session, seeded.id)
        by_num = {r["assembly_number"]: r for r in s["items"]}
        assert by_num["LG-1"]["carrier_name"] == "ТК Альфа"
        assert by_num["LG-1"]["brands"] == "TestBrand"
        assert by_num["LG-HI"]["anomaly_type"] == "overpriced"
        assert by_num["LG-NOCOST"]["anomaly_type"] == "no_cost"
        assert by_num["LG-1"]["anomaly_type"] is None
        # cost_per_pallet вычислен
        assert float(by_num["LG-1"]["cost_per_pallet"]) == 2000.0

    async def test_carrier_filter(self, db_session, seeded):
        # carrier_id берём из агрегата
        a = await get_logistics_analytics(db_session, seeded.id)
        alpha = next(c for c in a["by_carrier"] if c["carrier_name"] == "ТК Альфа")
        s = await get_logistics_shipments(db_session, seeded.id, carrier_id=alpha["counterparty_id"])
        assert s["total"] == 5
        assert all(r["carrier_name"] == "ТК Альфа" for r in s["items"])

    async def test_dest_filter(self, db_session, seeded):
        s = await get_logistics_shipments(db_session, seeded.id, dest_warehouse="Коледино")
        assert s["total"] == 2
        assert all(r["dest_warehouse"] == "Коледино" for r in s["items"])

    async def test_date_filter_excludes_out_of_period(self, db_session, seeded):
        s = await get_logistics_shipments(
            db_session, seeded.id, date_from=date(2026, 7, 1), date_to=date(2026, 7, 31)
        )
        assert s["total"] == 0


class TestCostForecast:
    async def test_forecast_per_warehouse_bucket(self, db_session, seeded):
        f = await get_cost_forecast(db_session, seeded.id)
        assert f["sample_size"] == 9  # 8 Электросталь + 1 Коледино BIG (LG-NOCOST исключён)
        whs = {w["dest_warehouse"]: w for w in f["warehouses"]}
        es = whs["Электросталь"]
        # все 8 при 5 паллетах → корзина «4-5», медиана ₽/пал = 2000 (6×2000,20000,400)
        assert es["sample_size"] == 8
        assert float(es["cpp"]) == 2000.0
        b45 = next(b for b in es["buckets"] if b["bucket"] == "4-5")
        assert b45["sample_size"] == 8
        assert float(b45["cpp"]) == 2000.0
        # коридор устойчив к выбросам (p25..p75 уже самих экстремумов)
        assert float(b45["low"]) <= 2000.0 <= float(b45["high"])

    async def test_forecast_project_isolation(self, db_session, seeded, other_project):
        f = await get_cost_forecast(db_session, other_project.id)
        assert f["sample_size"] == 0
        assert f["warehouses"] == []
