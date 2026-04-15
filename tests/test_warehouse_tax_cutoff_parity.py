"""Regression test: unified-stock tax_info must match BDR's heuristic.

Bug found 2026-04-15: `_load_bdr_metrics` called `load_tax_settings(project_id,
anchor, anchor)` — keying off the window END (yesterday). On a real project
with March tax rates set to 15%/7% but April rates still at placeholder 0%,
this made Unified use April's zero rates while БДР used March's real ones.
Result: Unified profit = БДР profit + full БДР tax (~+11M ₽ on 120M revenue,
+9 p.p. margin).

Fix: load tax_info per-period using each window's START (`cutoff_X`), which
mirrors BDR's `load_tax_settings(d_from, d_to)` → `d_from.month` heuristic.

This test monkeypatches `load_tax_settings` to capture the date_from it was
called with and asserts that Unified uses the period-start month — i.e. for
the 30-day window ending yesterday, the tax lookup lands on (yesterday - 29
days).month, not yesterday.month.
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.warehouse_stock_engine import _load_bdr_metrics


@pytest.mark.asyncio
async def test_tax_settings_keyed_off_period_start():
    """For each period, load_tax_settings must be called with the period
    START (cutoff), not the anchor — otherwise Unified picks the wrong month
    when the user has month-level tax overrides."""
    # Return empty maps so _compute_period_metrics short-circuits and we can
    # observe the tax_info calls without needing real DB data.
    captured: list[tuple[date, date]] = []

    async def fake_load_tax(db, pid, d_from, d_to):
        captured.append((d_from, d_to))
        return {"tax_regime": "usn_income_expense_vat", "usn_rate": 15.0, "nds_rate": 7.0, "cost_as_expense": True}

    # _compute_period_metrics issues a DB select. Patch it to a no-op.
    async def fake_compute(db, pid, cutoff, today, nm_to_nom, cost_map, tax_info):
        return {}

    # `load_tax_settings` is imported inside the function (`from
    # bdr_loaders import ...`), so the binding lives in the source module.
    with (
        patch("backend.services.bdr_loaders.load_tax_settings", new=fake_load_tax),
        patch(
            "backend.services.warehouse_stock_engine._compute_period_metrics", new=AsyncMock(side_effect=fake_compute)
        ),
    ):
        await _load_bdr_metrics(db=None, project_id=1, nm_id_to_nom_id={}, cost_map={})

    # Expect 3 calls — one per trend window.
    assert len(captured) == 3, f"expected 3 load_tax_settings calls, got {len(captured)}"

    # Anchor is always yesterday.
    from backend.utils.time import utcnow

    anchor = utcnow().date() - timedelta(days=1)

    # The three cutoffs (period STARTS) — these are the months that must drive
    # the tax lookup, not the anchor.
    expected_cutoffs = {
        anchor - timedelta(days=29),  # 30d
        anchor - timedelta(days=13),  # 14d
        anchor - timedelta(days=6),  # 7d
    }
    actual_d_froms = {d_from for d_from, _ in captured}
    assert actual_d_froms == expected_cutoffs, (
        f"tax_info must be keyed on period start (cutoff). expected d_from in "
        f"{expected_cutoffs}, got {actual_d_froms}"
    )

    # Sanity: none of the calls use the anchor as d_from — that's the bug
    # signature we're guarding against.
    assert anchor not in actual_d_froms, (
        "tax_info was loaded with d_from=anchor (yesterday). This picks the "
        "current month's rates while БДР uses the period-start month, so "
        "profits diverge by the full tax amount. Use the cutoff instead."
    )
