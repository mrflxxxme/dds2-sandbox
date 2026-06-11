"""
Tests for backend/services/funnel/stock_costs.py — расширенные столбцы воронки.

Covers:
- get_stock_cost_map — себестоимость остатков WB (quantity_full × цена единицы)
- get_stock_cost_map — остатки своих складов: резерв включён, брак исключён
- Приоритет цены: CostOrderItem avg → WbCostOverride → WarehouseStock.cost_price
- Прогноз days_left по средним заказам за 7 дней (якорь на вчера) + тренд 7д-vs-7д
- project_id изоляция
- apply_min_orders — серверный порог по заказам
- merge_stock_costs — мёрж в sku-строки и агрегация для групп (tag children / brand)
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.funnel.stock_costs import (
    apply_min_orders,
    get_stock_cost_map,
    merge_stock_costs,
)
from backend.utils.time import utcnow


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_nomenclature(
    db: AsyncSession,
    pid: int,
    nm_id: int,
    article_seller: str | None = None,
    brand: str = "BrandX",
    subject: str = "Ковры",
) -> tuple[int, str]:
    """Insert nomenclature, return (nomenclature_id, barcode)."""
    barcode = f"FSC-{_uid()}"
    result = await db.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_seller, article_wb, brand, subject, updated_at) "
            "VALUES (:pid, :bc, :art, :nm, :brand, :subj, NOW()) RETURNING id"
        ),
        {"pid": pid, "bc": barcode, "art": article_seller, "nm": nm_id, "brand": brand, "subj": subject},
    )
    nom_id = result.scalar()
    await db.commit()
    return nom_id, barcode


async def _make_warehouse(db: AsyncSession, pid: int) -> int:
    result = await db.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, is_deleted, created_at, updated_at) "
            "VALUES (:pid, :name, 'FULFILLMENT', false, NOW(), NOW()) RETURNING id"
        ),
        {"pid": pid, "name": f"FSC-WH-{_uid()}"},
    )
    wh_id = result.scalar()
    await db.commit()
    return wh_id


async def _add_own_stock(
    db: AsyncSession,
    pid: int,
    wh_id: int,
    nom_id: int,
    barcode: str,
    qty: int,
    defect: int = 0,
    cost_price: float | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO warehouse_stock "
            "(project_id, warehouse_id, nomenclature_id, barcode, quantity, in_transit, "
            " defect_quantity, defect_in_transit, cost_price, updated_at) "
            "VALUES (:pid, :wh, :nom, :bc, :qty, 0, :defect, 0, :cost, NOW())"
        ),
        {"pid": pid, "wh": wh_id, "nom": nom_id, "bc": barcode, "qty": qty, "defect": defect, "cost": cost_price},
    )
    await db.commit()


async def _add_wb_stock(
    db: AsyncSession, pid: int, nm_id: int, qty_full: int, warehouse_name: str = "Коледино"
) -> None:
    await db.execute(
        text(
            "INSERT INTO wb_warehouse_stocks "
            "(project_id, nm_id, warehouse_name, quantity, quantity_full, in_way_to_client, in_way_from_client, updated_at) "
            "VALUES (:pid, :nm, :wh, :qty, :qty_full, 0, 0, NOW())"
        ),
        {"pid": pid, "nm": nm_id, "wh": warehouse_name, "qty": qty_full, "qty_full": qty_full},
    )
    await db.commit()


async def _add_avg_cost(db: AsyncSession, pid: int, article_seller: str, unit_cost: float, qty: int = 10) -> None:
    """Insert a cost order + item so load_avg_costs returns unit_cost for the article."""
    order_no = f"FSC-{_uid()}"
    await db.execute(
        text(
            "INSERT INTO cost_orders "
            "(project_id, order_no, delivery_cost_cny, delivery_cost_usd, rate_cny, rate_eur, rate_usd, "
            " is_deleted, created_at) "
            "VALUES (:pid, :no, 0, 0, 1, 1, 1, false, NOW())"
        ),
        {"pid": pid, "no": order_no},
    )
    await db.execute(
        text(
            "INSERT INTO cost_order_items "
            "(project_id, order_no, barcode, article_seller, qty, price_cny, total_rub, unrecognized, is_deleted) "
            "VALUES (:pid, :no, :bc, :art, :qty, 0, :total, false, false)"
        ),
        {"pid": pid, "no": order_no, "bc": f"FSC-{_uid()}", "art": article_seller, "qty": qty, "total": unit_cost},
    )
    await db.commit()


async def _add_cost_override(db: AsyncSession, pid: int, nm_id: int, cost: float) -> None:
    await db.execute(
        text(
            "INSERT INTO wb_cost_override (project_id, nm_id, cost_price, updated_at) "
            "VALUES (:pid, :nm, :cost, NOW())"
        ),
        {"pid": pid, "nm": nm_id, "cost": cost},
    )
    await db.commit()


async def _add_funnel_orders(db: AsyncSession, pid: int, nm_id: int, day_offsets: dict[int, int]) -> None:
    """Insert wb_funnel_daily rows: {days_ago: orders_count}. Day 1 = вчера."""
    today = utcnow().date()
    for days_ago, orders in day_offsets.items():
        await db.execute(
            text(
                "INSERT INTO wb_funnel_daily "
                "(project_id, date, nm_id, open_card, add_to_cart, orders_count, orders_sum_rub, "
                " stocks_wb, stocks_mp, adv_views, adv_clicks, adv_sum) "
                "VALUES (:pid, :d, :nm, 0, 0, :cnt, :cnt * 100, 0, 0, 0, 0, 0)"
            ),
            {"pid": pid, "d": today - timedelta(days=days_ago), "nm": nm_id, "cnt": orders},
        )
    await db.commit()


# ─── get_stock_cost_map ──────────────────────────────────────────────────────


class TestGetStockCostMap:
    @pytest.mark.asyncio
    async def test_wb_stock_cost_from_avg_cost(self, db_session, project):
        """WB остаток = Σ quantity_full по складам × средневзвешенная себестоимость."""
        nm_id = 910001
        art = f"fsc-art-{_uid()}"
        await _make_nomenclature(db_session, project.id, nm_id, article_seller=art)
        await _add_wb_stock(db_session, project.id, nm_id, qty_full=6, warehouse_name="Коледино")
        await _add_wb_stock(db_session, project.id, nm_id, qty_full=4, warehouse_name="Казань")
        await _add_avg_cost(db_session, project.id, art, unit_cost=200.0)

        stock_map = await get_stock_cost_map(db_session, project.id)

        info = stock_map[nm_id]
        assert info["wb_qty"] == 10
        assert info["wb_cost_rub"] == pytest.approx(2000.0)

    @pytest.mark.asyncio
    async def test_own_stock_includes_reserve_excludes_defect(self, db_session, project):
        """Свои склады: quantity (включая резерв) считается, defect_quantity — нет."""
        nm_id = 910002
        art = f"fsc-art-{_uid()}"
        nom_id, barcode = await _make_nomenclature(db_session, project.id, nm_id, article_seller=art)
        wh1 = await _make_warehouse(db_session, project.id)
        wh2 = await _make_warehouse(db_session, project.id)
        await _add_own_stock(db_session, project.id, wh1, nom_id, barcode, qty=8, defect=3)
        await _add_own_stock(db_session, project.id, wh2, nom_id, barcode, qty=2, defect=1)
        await _add_avg_cost(db_session, project.id, art, unit_cost=100.0)

        stock_map = await get_stock_cost_map(db_session, project.id)

        info = stock_map[nm_id]
        assert info["own_qty"] == 10  # 8 + 2, брак (4 шт) не входит
        assert info["own_cost_rub"] == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_cost_priority_override_then_stock_price(self, db_session, project):
        """Без CostOrderItem: WbCostOverride → потом WarehouseStock.cost_price."""
        # nm с override
        nm_override = 910003
        nom_id, barcode = await _make_nomenclature(db_session, project.id, nm_override)
        wh = await _make_warehouse(db_session, project.id)
        await _add_own_stock(db_session, project.id, wh, nom_id, barcode, qty=5)
        await _add_cost_override(db_session, project.id, nm_override, 150.0)

        # nm только с cost_price на складской строке
        nm_stock_price = 910004
        nom_id2, barcode2 = await _make_nomenclature(db_session, project.id, nm_stock_price)
        await _add_own_stock(db_session, project.id, wh, nom_id2, barcode2, qty=4, cost_price=50.0)

        stock_map = await get_stock_cost_map(db_session, project.id)

        assert stock_map[nm_override]["own_cost_rub"] == pytest.approx(750.0)  # 5 × 150
        assert stock_map[nm_stock_price]["own_cost_rub"] == pytest.approx(200.0)  # 4 × 50

    @pytest.mark.asyncio
    async def test_days_left_and_trend(self, db_session, project):
        """days_left = (WB + свои) ÷ ср. заказы/день за 7 дн; тренд 7д-vs-7д."""
        nm_id = 910005
        art = f"fsc-art-{_uid()}"
        nom_id, barcode = await _make_nomenclature(db_session, project.id, nm_id, article_seller=art)
        wh = await _make_warehouse(db_session, project.id)
        await _add_wb_stock(db_session, project.id, nm_id, qty_full=10)
        await _add_own_stock(db_session, project.id, wh, nom_id, barcode, qty=10)
        await _add_avg_cost(db_session, project.id, art, unit_cost=100.0)
        # последние 7 дней (1..7 дней назад): 2 заказа/день; предыдущие 7 (8..14): 4/день
        offsets = {d: 2 for d in range(1, 8)}
        offsets.update({d: 4 for d in range(8, 15)})
        await _add_funnel_orders(db_session, project.id, nm_id, offsets)

        stock_map = await get_stock_cost_map(db_session, project.id)

        info = stock_map[nm_id]
        assert info["avg_daily"] == pytest.approx(2.0)
        assert info["days_left"] == 10  # 20 шт ÷ 2/день
        assert info["trend_pct"] == pytest.approx(-50.0)
        expected_date = (utcnow().date() + timedelta(days=10)).isoformat()
        assert info["out_date"] == expected_date

    @pytest.mark.asyncio
    async def test_no_sales_gives_999_days(self, db_session, project):
        """Сток есть, продаж нет → days_left = 999, out_date = None."""
        nm_id = 910006
        await _make_nomenclature(db_session, project.id, nm_id)
        await _add_wb_stock(db_session, project.id, nm_id, qty_full=5)

        stock_map = await get_stock_cost_map(db_session, project.id)

        assert stock_map[nm_id]["days_left"] == 999
        assert stock_map[nm_id]["out_date"] is None

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Данные чужого проекта не попадают в карту."""
        nm_id = 910007
        await _make_nomenclature(db_session, other_project.id, nm_id)
        await _add_wb_stock(db_session, other_project.id, nm_id, qty_full=99)

        stock_map = await get_stock_cost_map(db_session, project.id)

        assert nm_id not in stock_map


# ─── apply_min_orders ────────────────────────────────────────────────────────


class TestApplyMinOrders:
    def test_filters_rows_below_threshold(self):
        rows = [
            {"nm_id": 1, "orders_count": 0},
            {"nm_id": 2, "orders_count": 5},
            {"nm_id": 3, "orders_count": 12},
        ]
        assert [r["nm_id"] for r in apply_min_orders(rows, 5)] == [2, 3]

    def test_zero_threshold_keeps_all(self):
        rows = [{"nm_id": 1, "orders_count": 0}]
        assert apply_min_orders(rows, 0) == rows


# ─── merge_stock_costs ───────────────────────────────────────────────────────


def _info(wb_qty=0, wb_cost=0.0, own_qty=0, own_cost=0.0, avg_daily=0.0, avg_daily_prev=0.0, brand="B", subject="S"):
    return {
        "wb_qty": wb_qty,
        "wb_cost_rub": wb_cost,
        "own_qty": own_qty,
        "own_cost_rub": own_cost,
        "avg_daily": avg_daily,
        "avg_daily_prev": avg_daily_prev,
        "days_left": 0,
        "out_date": None,
        "trend_pct": 0.0,
        "brand": brand,
        "subject": subject,
    }


class TestMergeStockCosts:
    def test_sku_rows_merge_by_nm_id(self):
        rows = [{"nm_id": 1, "orders_count": 5}, {"nm_id": 2, "orders_count": 3}]
        stock_map = {1: _info(wb_qty=10, wb_cost=2000.0, own_qty=4, own_cost=400.0, avg_daily=2.0)}

        merge_stock_costs(rows, stock_map, "sku")

        assert rows[0]["wb_stock_cost"] == pytest.approx(2000.0)
        assert rows[0]["own_stock_cost"] == pytest.approx(400.0)
        assert rows[0]["stock_days_left"] == 7  # (10+4) ÷ 2
        # товар без стока — нули, без падения
        assert rows[1]["wb_stock_cost"] == 0
        assert rows[1]["stock_days_left"] == 0

    def test_group_rows_sum_children(self):
        rows = [
            {
                "tag": "Хиты",
                "orders_count": 8,
                "children": [{"nm_id": 1}, {"nm_id": 2}],
            }
        ]
        stock_map = {
            1: _info(wb_qty=10, wb_cost=1000.0, own_qty=0, avg_daily=1.0),
            2: _info(wb_qty=20, wb_cost=3000.0, own_qty=10, own_cost=500.0, avg_daily=2.0),
        }

        merge_stock_costs(rows, stock_map, "tag")

        row = rows[0]
        assert row["wb_stock_qty"] == 30
        assert row["wb_stock_cost"] == pytest.approx(4000.0)
        assert row["own_stock_cost"] == pytest.approx(500.0)
        assert row["stock_days_left"] == 13  # 40 шт ÷ 3/день
        # children тоже получают свои значения
        assert row["children"][0]["wb_stock_cost"] == pytest.approx(1000.0)

    def test_brand_rows_aggregate_by_attribution(self):
        rows = [{"brand": "BrandA", "orders_count": 5}]
        stock_map = {
            1: _info(wb_qty=5, wb_cost=500.0, brand="BrandA"),
            2: _info(wb_qty=7, wb_cost=700.0, brand="BrandB"),
        }

        merge_stock_costs(rows, stock_map, "brand")

        assert rows[0]["wb_stock_cost"] == pytest.approx(500.0)
        assert rows[0]["wb_stock_qty"] == 5
