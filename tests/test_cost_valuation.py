# ruff: noqa: RUF002, RUF003
"""
Tests for the cost valuation engine — backend/services/cost/valuation.py.

Two layers:
  * Pure-sequence (_walk / _Batch / _SkuRaw) — deterministic, no DB. Pins the
    golden FIFO numbers, returns-restore, and oversell→seed shortfall.
  * DB-backed (compute_project_valuation / slice_window) — builds real
    CostOrder/CostOrderItem/WbFinanceRow/Nomenclature rows in the test session,
    asserts lifetime_avg parity (Σ global_avg·net_qty) and the empty case.

The golden numbers below are verified-correct fixtures for the engine's current
behaviour; any change to them signals a regression in the valuation walk.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.services.cost.valuation import (
    _Batch,
    _SkuRaw,
    _walk,
    compute_project_valuation,
    slice_window,
)

# ── golden fixture (ascending avail_date batches + net monthly sales) ──────────

GOLDEN_BATCHES = [
    (36, "1971.19"),
    (144, "1947.99"),
    (108, "1953.20"),
    (48, "1988.07"),
    (16, "1826.21"),
    (8, "1813.50"),
    (225, "1641.24"),
    (27, "1626.72"),
]
# net sales per month (sold − returned); the window covers all of them
GOLDEN_SALES = {
    (2024, 12): 207,
    (2025, 1): 90,
    (2025, 2): 61,
    (2025, 3): 105,
    (2025, 4): 67,
    (2025, 5): 36,
    (2025, 6): 7,
}


def _build_golden() -> tuple[list[_Batch], _SkuRaw, Decimal]:
    """Synthetic batches (one per month, ascending avail_date) + monthly sales."""
    batches: list[_Batch] = []
    base = date(2024, 11, 1)
    for i, (qty, cost) in enumerate(GOLDEN_BATCHES):
        batches.append(
            _Batch(
                order_no=f"B{i:02d}",
                avail_date=base + timedelta(days=i),
                qty=qty,
                unit_cost=Decimal(cost),
                arrival_known=True,
            )
        )
    raw = _SkuRaw()
    for (y, m), q in GOLDEN_SALES.items():
        raw.sales.append((date(y, m, 15), q))

    recv_qty = sum(q for q, _ in GOLDEN_BATCHES)
    recv_sum = sum(Decimal(q) * Decimal(c) for q, c in GOLDEN_BATCHES)
    global_avg = recv_sum / Decimal(recv_qty)
    return batches, raw, global_avg


def _approx(a: Decimal, b: str, tol: str = "0.01") -> bool:
    return abs(Decimal(a) - Decimal(b)) <= Decimal(tol)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Golden sequence — pure _walk, no DB
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoldenSequence:
    def test_golden_numbers(self):
        batches, raw, global_avg = _build_golden()
        seed = global_avg
        res = _walk(batches, raw, global_avg, seed)

        # lifetime (global) average over all received units
        assert _approx(global_avg, "1821.53")

        # ending inventory: 612 received − 573 net sold = 39 on hand
        assert res["on_hand_qty"] == 39

        # FIFO ending value = leftover layers at their own cost.
        # 39 units survive: 12 from the 1641.24 layer + 27 @ 1626.72
        # 12*1641.24 + 27*1626.72 = 19694.88 + 43921.44 = 63616.32
        assert _approx(res["on_hand_value"]["fifo"], "63616.32")

        # current effective FIFO cost = first still-on-shelf layer (1641.24)
        assert _approx(res["eff_now"]["fifo"], "1641.24")

        # December FIFO effective cost ≈ 1952.71 (207 units off the oldest layers)
        dec = res["monthly"]["2024-12"]
        assert dec["sold"] == 207
        dec_eff = dec["cogs_fifo"] / Decimal(dec["sold"])
        assert abs(dec_eff - Decimal("1952.71")) <= Decimal("0.01")

        # total FIFO COGS over the whole window
        window_fifo = sum(m["cogs_fifo"] for m in res["monthly"].values())
        assert _approx(window_fifo, "1051157.84")

        # sanity: 573 net units sold across the window
        assert sum(m["sold"] for m in res["monthly"].values()) == 573

    def test_no_warnings_when_arrival_known(self):
        batches, raw, global_avg = _build_golden()
        res = _walk(batches, raw, global_avg, global_avg)
        assert res["warnings"] == []
        assert res["is_estimated"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Returns restore a consumed layer and reduce that month's cogs_fifo
# ═══════════════════════════════════════════════════════════════════════════════


class TestReturns:
    def test_return_restores_layer_and_reduces_cogs(self):
        unit = Decimal("100")
        no_return = _walk(
            [_Batch("B0", date(2025, 1, 1), 10, unit, True)],
            _SkuRaw(sales=[(date(2025, 1, 10), 5)]),
            unit,
            unit,
        )
        with_return = _walk(
            [_Batch("B0", date(2025, 1, 1), 10, unit, True)],
            _SkuRaw(sales=[(date(2025, 1, 10), 5)], returns=[(date(2025, 1, 20), 2)]),
            unit,
            unit,
        )

        jan_no = no_return["monthly"]["2025-01"]
        jan_ret = with_return["monthly"]["2025-01"]

        # 5 sold @100 = 500; a return of 2 restores 2 units → 300
        assert jan_no["cogs_fifo"] == Decimal("500")
        assert jan_ret["cogs_fifo"] == Decimal("300")
        assert jan_ret["returned"] == 2

        # the restored units land back on the shelf: 5 → 7 on hand
        assert no_return["on_hand_qty"] == 5
        assert with_return["on_hand_qty"] == 7
        # and the restored layer keeps its original cost
        assert _approx(with_return["on_hand_value"]["fifo"], "700")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Opening-balance / shortfall — oversell consumes at seed, flags estimated
# ═══════════════════════════════════════════════════════════════════════════════


class TestShortfall:
    def test_oversell_beyond_batches_uses_seed(self):
        seed = Decimal("100")
        res = _walk(
            [_Batch("B0", date(2025, 1, 1), 3, Decimal("100"), True)],
            _SkuRaw(sales=[(date(2025, 1, 10), 5)]),
            Decimal("100"),
            seed,
        )
        jan = res["monthly"]["2025-01"]
        # 3 real units @100 + 2 oversold @ seed 100 = 500
        assert jan["cogs_fifo"] == Decimal("500")
        assert res["is_estimated"] is True
        assert res["on_hand_qty"] == 0

    def test_opening_balance_layer_covers_early_sales(self):
        """An OPENING batch dated before any cost order behaves as the oldest
        FIFO layer; sales fully covered by it are NOT estimated."""
        opening = _Batch("OPENING", date(2024, 12, 1), 50, Decimal("90"), True)
        later = _Batch("B1", date(2025, 1, 1), 10, Decimal("110"), True)
        res = _walk(
            [opening, later],
            _SkuRaw(sales=[(date(2024, 12, 5), 20)]),
            Decimal("100"),
            Decimal("90"),
        )
        dec = res["monthly"]["2024-12"]
        # 20 units off the opening layer @90 = 1800, not estimated
        assert dec["cogs_fifo"] == Decimal("1800")
        assert res["is_estimated"] is False
        assert res["on_hand_qty"] == 40  # 30 opening left + 10 later


# ═══════════════════════════════════════════════════════════════════════════════
# DB-backed scenarios — real rows in the test session
# ═══════════════════════════════════════════════════════════════════════════════


async def _seed_batch_order(db, pid, *, order_no, article, barcode, qty, total_rub, arrival: date):
    """One DELIVERED cost order with a single item priced at total_rub/unit."""
    from backend.models import CostOrder, CostOrderItem, Nomenclature, VehicleStatus

    db.add(
        CostOrder(
            order_no=order_no,
            project_id=pid,
            country="CHINA",
            status=VehicleStatus.DELIVERED,
            actual_arrival_date=arrival,
            rate_cny=Decimal("1"),
            rate_eur=Decimal("1"),
            rate_usd=Decimal("1"),
            delivery_cost_cny=Decimal("0"),
            delivery_cost_usd=Decimal("0"),
        )
    )
    db.add(
        Nomenclature(
            project_id=pid,
            barcode=barcode,
            subject="Ковры",
            brand="ACME",
            article_seller=article,
            article_wb=None,
        )
    )
    db.add(
        CostOrderItem(
            project_id=pid,
            order_no=order_no,
            barcode=barcode,
            subject="Ковры",
            article_seller=article,
            qty=qty,
            price_cny=Decimal("0"),
            volume_m3=Decimal("0"),
            duty_rub=Decimal("0"),
            total_rub=Decimal(str(total_rub)),
        )
    )


async def _seed_sale(db, pid, *, rrd_id, article, d: date, qty: int, doc="Продажа"):
    from backend.models.wb_finance import WbFinanceRow

    db.add(
        WbFinanceRow(
            project_id=pid,
            realizationreport_id=0,
            rrd_id=rrd_id,
            date_from=d,
            date_to=d,
            sa_name=article,
            nm_id=0,
            doc_type_name=doc,
            supplier_oper_name=doc,
            sale_dt=d,
            quantity=qty,
        )
    )


class TestComputeProjectValuationDB:
    @pytest.mark.asyncio
    async def test_lifetime_avg_parity(self, db_session, project):
        """slice_window(lifetime_avg) == Σ(global_avg · net_qty) per window —
        matches the legacy load_avg_costs · qty contract."""
        pid = project.id
        art = f"ART-PAR-{pid}"
        bc = f"BC-PAR-{pid}"
        # two batches: 100 @ 200.00 and 100 @ 300.00 → global_avg 250.00
        await _seed_batch_order(
            db_session, pid, order_no=f"PAR-A-{pid}", article=art, barcode=bc,
            qty=100, total_rub="200.00", arrival=date(2025, 1, 1),
        )
        from backend.models import CostOrder, CostOrderItem

        db_session.add(
            CostOrder(
                order_no=f"PAR-B-{pid}", project_id=pid, country="CHINA",
                actual_arrival_date=date(2025, 2, 1),
                rate_cny=Decimal("1"), rate_eur=Decimal("1"), rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"), delivery_cost_usd=Decimal("0"),
            )
        )
        db_session.add(
            CostOrderItem(
                project_id=pid, order_no=f"PAR-B-{pid}", barcode=bc, subject="Ковры",
                article_seller=art, qty=100, price_cny=Decimal("0"),
                volume_m3=Decimal("0"), duty_rub=Decimal("0"), total_rub=Decimal("300.00"),
            )
        )
        # sales: 30 in Feb, 20 in Mar (net 50)
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 1, article=art, d=date(2025, 2, 10), qty=30)
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 2, article=art, d=date(2025, 3, 10), qty=20)
        await db_session.commit()

        val = await compute_project_valuation(db_session, pid, skus={art})
        v = val[art.lower()]
        assert _approx(v["lifetime_avg"], "250.00")

        sliced = slice_window(val, date(2025, 2, 1), date(2025, 3, 31), "lifetime_avg")
        row = sliced[art.lower()]
        # net 50 units · 250.00 = 12500.00
        assert row["qty"] == 50
        assert _approx(row["cogs"], "12500.00")
        assert _approx(row["eff_cost"], "250.00")
        # parity: Σ over months of global_avg · net_qty
        expected = sum(
            Decimal("250.00") * Decimal(m["qty"]) for m in row["monthly"].values()
        )
        assert _approx(row["cogs"], str(expected))

    @pytest.mark.asyncio
    async def test_fifo_vs_lifetime_differ_per_window(self, db_session, project):
        """FIFO window COGS draws from the oldest layer first, so it differs
        from the flat lifetime average when batches have distinct costs."""
        pid = project.id
        art = f"ART-FIFO-{pid}"
        bc = f"BC-FIFO-{pid}"
        await _seed_batch_order(
            db_session, pid, order_no=f"FIFO-A-{pid}", article=art, barcode=bc,
            qty=10, total_rub="200.00", arrival=date(2025, 1, 1),
        )
        await db_session.commit()
        # add a pricier second layer
        from backend.models import CostOrder, CostOrderItem

        db_session.add(
            CostOrder(
                order_no=f"FIFO-B-{pid}", project_id=pid, country="CHINA",
                actual_arrival_date=date(2025, 2, 1),
                rate_cny=Decimal("1"), rate_eur=Decimal("1"), rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"), delivery_cost_usd=Decimal("0"),
            )
        )
        db_session.add(
            CostOrderItem(
                project_id=pid, order_no=f"FIFO-B-{pid}", barcode=bc, subject="Ковры",
                article_seller=art, qty=10, price_cny=Decimal("0"),
                volume_m3=Decimal("0"), duty_rub=Decimal("0"), total_rub=Decimal("400.00"),
            )
        )
        # sell 5 in March — all should come off the 200.00 layer under FIFO
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 9, article=art, d=date(2025, 3, 5), qty=5)
        await db_session.commit()

        val = await compute_project_valuation(db_session, pid, skus={art})
        w = date(2025, 3, 1), date(2025, 3, 31)
        fifo = slice_window(val, *w, "fifo")[art.lower()]
        life = slice_window(val, *w, "lifetime_avg")[art.lower()]
        # FIFO: 5 @ 200 = 1000; lifetime: 5 @ 300 (avg of 200/400) = 1500
        assert _approx(fifo["cogs"], "1000.00")
        assert _approx(life["cogs"], "1500.00")

    @pytest.mark.asyncio
    async def test_empty_sku_not_in_window(self, db_session, project):
        """A SKU with a batch but no sales has no monthly buckets → dropped by
        slice_window; valuation still reports zero sold and full on-hand."""
        pid = project.id
        art = f"ART-EMPTY-{pid}"
        bc = f"BC-EMPTY-{pid}"
        await _seed_batch_order(
            db_session, pid, order_no=f"EMPTY-A-{pid}", article=art, barcode=bc,
            qty=10, total_rub="150.00", arrival=date(2025, 1, 1),
        )
        await db_session.commit()

        val = await compute_project_valuation(db_session, pid, skus={art})
        v = val[art.lower()]
        assert v["total_sold"] == 0
        assert v["on_hand_qty"] == 10
        assert v["monthly"] == {}

        sliced = slice_window(val, date(2025, 1, 1), date(2025, 12, 31), "fifo")
        assert art.lower() not in sliced

    @pytest.mark.asyncio
    async def test_unknown_sku_absent_from_result(self, db_session, project):
        """A SKU with neither batches nor sales never appears in the valuation."""
        pid = project.id
        val = await compute_project_valuation(db_session, pid, skus={f"GHOST-{pid}"})
        assert f"ghost-{pid}".lower() not in val
        assert val == {} or f"ghost-{pid}" not in val

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Sales/batches of one project never leak into another's valuation."""
        pid = project.id
        art = f"ART-ISO-{pid}"
        bc = f"BC-ISO-{pid}"
        await _seed_batch_order(
            db_session, pid, order_no=f"ISO-A-{pid}", article=art, barcode=bc,
            qty=10, total_rub="100.00", arrival=date(2025, 1, 1),
        )
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 50, article=art, d=date(2025, 2, 1), qty=4)
        await db_session.commit()

        other_val = await compute_project_valuation(db_session, other_project.id, skus={art})
        assert art.lower() not in other_val

    @pytest.mark.asyncio
    async def test_batch_barcode_bridge_mismatched_article(self, db_session, project):
        """Cost batch booked under the PURCHASE article still feeds sales booked
        under the WB article when they share a barcode.

        Prod расихрон: cost_order_items carried a factory article ('Кас_наб_...')
        while WB sales/nomenclature use 'K-147'. Batches were keyed by the purchase
        article and sales by the WB article, so under FIFO the SKU had sales but
        NO cost layer → COGS silently 0. The barcode bridge re-keys batches to the
        WB article so cost and sales meet.
        """
        pid = project.id
        purchase_art = f"ФАБ-{pid}"  # factory article on the purchase order
        wb_art = f"WB-{pid}"  # WB seller article — sales + nomenclature use this
        bc = f"BCBR-{pid}"
        from backend.models import CostOrder, CostOrderItem, Nomenclature, VehicleStatus

        db_session.add(
            CostOrder(
                order_no=f"BR-A-{pid}", project_id=pid, country="CHINA",
                status=VehicleStatus.DELIVERED, actual_arrival_date=date(2025, 1, 1),
                rate_cny=Decimal("1"), rate_eur=Decimal("1"), rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"), delivery_cost_usd=Decimal("0"),
            )
        )
        # nomenclature maps the shared barcode → WB article (differs from purchase)
        db_session.add(
            Nomenclature(
                project_id=pid, barcode=bc, subject="Ковры", brand="ACME",
                article_seller=wb_art, article_wb=None,
            )
        )
        # cost item carries the PURCHASE article + the shared barcode
        db_session.add(
            CostOrderItem(
                project_id=pid, order_no=f"BR-A-{pid}", barcode=bc, subject="Ковры",
                article_seller=purchase_art, qty=10, price_cny=Decimal("0"),
                volume_m3=Decimal("0"), duty_rub=Decimal("0"), total_rub=Decimal("500.00"),
            )
        )
        await db_session.commit()
        # sale booked under the WB article
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 77, article=wb_art, d=date(2025, 2, 10), qty=4)
        await db_session.commit()

        val = await compute_project_valuation(db_session, pid)
        # keyed by the WB article; the purchase-article batch reaches it via barcode
        v = val[wb_art.lower()]
        assert v["total_received"] == 10
        assert _approx(v["lifetime_avg"], "500.00")
        fifo = slice_window(val, date(2025, 2, 1), date(2025, 2, 28), "fifo")[wb_art.lower()]
        assert fifo["qty"] == 4
        assert _approx(fifo["cogs"], "2000.00")  # 4 × 500, not 0


class TestBdrSubMonthWindowCogs:
    """Regression: FIFO/moving COGS in the BDR must span the SAME days as the
    revenue it is compared against.

    The engine buckets COGS by calendar MONTH; ``slice_window`` can only filter
    on month boundaries. For a sub-month BDR window (e.g. 15–20 June) the engine
    returns the WHOLE month's COGS, while revenue/units come from the exact
    day-range SQL. Charging a full month of COGS against a few days of revenue
    inflated COGS to ~135 % of revenue → a phantom multi-million-rouble loss
    (prod, 2026-06: −16 М ₽ instead of +7 % profit). The fix applies the engine's
    per-unit cost to the window's actual units, exactly like the lifetime path."""

    @pytest.mark.asyncio
    async def test_partial_month_cogs_scales_with_window_units(self, db_session, project):
        from unittest.mock import AsyncMock, patch

        from backend.services.settings_service import set_valuation_method
        from backend.services.wb_bdr_service import get_wb_bdr

        pid = project.id
        art = f"ART-WIN-{pid}"
        bc = f"BC-WIN-{pid}"
        # 1000 units @ unit cost 100.00 (total_rub is per-unit) — covers all sales.
        await _seed_batch_order(
            db_session, pid, order_no=f"WIN-A-{pid}", article=art, barcode=bc,
            qty=1000, total_rub="100.00", arrival=date(2026, 6, 1),
        )
        # 100 units sold 5 June (OUTSIDE the 15–20 window), 10 units 17 June (INSIDE).
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 61, article=art, d=date(2026, 6, 5), qty=100)
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 62, article=art, d=date(2026, 6, 17), qty=10)
        await set_valuation_method(db_session, pid, "fifo")
        await db_session.commit()

        with (
            patch(
                "backend.services.wb_finance_sync.get_sync_status",
                new_callable=AsyncMock,
                return_value={"status": "ok"},
            ),
            patch("backend.cache._redis_client", None),
        ):
            result = await get_wb_bdr(
                db_session,
                project_id=pid,
                date_from=date(2026, 6, 15),
                date_to=date(2026, 6, 20),
                group_by="article",
            )

        # Window holds 10 net units → COGS must be 10 × 100 = 1000, NOT the whole
        # June bucket (110 × 100 = 11000) the engine reports for "2026-06".
        cost_total = result["summary"]["cost_total"]
        assert cost_total == pytest.approx(1000.0), cost_total

    @pytest.mark.asyncio
    async def test_opiu_partial_month_cogs_scales_with_window_units(self, db_session, project):
        """ОПиУ shares the engine consumer path and the same month-bucket trap:
        a sub-month window must prorate, not charge the whole month."""
        from unittest.mock import patch

        from backend.services.opiu_service import get_opiu
        from backend.services.settings_service import set_valuation_method

        pid = project.id
        art = f"ART-OWIN-{pid}"
        bc = f"BC-OWIN-{pid}"
        await _seed_batch_order(
            db_session, pid, order_no=f"OWIN-A-{pid}", article=art, barcode=bc,
            qty=1000, total_rub="100.00", arrival=date(2026, 6, 1),
        )
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 71, article=art, d=date(2026, 6, 5), qty=100)
        await _seed_sale(db_session, pid, rrd_id=pid * 1000 + 72, article=art, d=date(2026, 6, 17), qty=10)
        await set_valuation_method(db_session, pid, "fifo")
        await db_session.commit()

        with patch("backend.cache._redis_client", None):
            result = await get_opiu(
                db_session,
                project_id=pid,
                date_from=date(2026, 6, 15),
                date_to=date(2026, 6, 20),
            )

        cost_row = next(r for r in result["rows"] if r["key"] == "cost_price")
        assert cost_row["total"] == pytest.approx(1000.0), cost_row["total"]
