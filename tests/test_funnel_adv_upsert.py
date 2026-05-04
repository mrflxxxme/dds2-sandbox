"""
Regression guard: ad upsert in run_funnel_sync must use GREATEST for adv_* fields.

Incident: 2026-05-04 — Vyatkin (project 15) had wb_funnel_daily.adv_sum=0 for
30.04, 01.05, 02.05 because partial fetch_ad_stats (chunks 429-throttled or
time-budget exceeded) returned data for SOME nm_ids of a day. Then has_ad_data
was True (day_ads non-empty) but for many nm_ids ad={} → excluded.adv_sum=0,
and ON CONFLICT UPDATE wiped previously-synced real values with 0.

Fix: replace bare `update_fields[..] = stmt.excluded.adv_*` with
`sa_func.greatest(stmt.excluded.adv_*, WbFunnelDaily.adv_*)` so a real value
is never silently overwritten with 0 by a partial sync.
"""

import inspect

from backend.services.funnel import sync


def test_run_funnel_sync_uses_greatest_for_adv_fields():
    """All three adv_* fields must be upserted via GREATEST(excluded, current)."""
    src = inspect.getsource(sync.run_funnel_sync)
    assert (
        "sa_func.greatest(stmt.excluded.adv_views" in src
    ), "adv_views upsert must use GREATEST to avoid overwriting real values with 0"
    assert (
        "sa_func.greatest(stmt.excluded.adv_clicks" in src
    ), "adv_clicks upsert must use GREATEST to avoid overwriting real values with 0"
    assert (
        "sa_func.greatest(stmt.excluded.adv_sum" in src
    ), "adv_sum upsert must use GREATEST to avoid overwriting real values with 0"


def test_run_funnel_sync_no_bare_excluded_assignment_for_adv():
    """Bare `update_fields[adv_*] = stmt.excluded.adv_*` is the regression pattern."""
    src = inspect.getsource(sync.run_funnel_sync)
    bad_patterns = [
        'update_fields["adv_views"] = stmt.excluded.adv_views',
        'update_fields["adv_clicks"] = stmt.excluded.adv_clicks',
        'update_fields["adv_sum"] = stmt.excluded.adv_sum',
    ]
    for pat in bad_patterns:
        assert pat not in src, (
            f"Regression: bare excluded assignment found: {pat!r}. "
            "Use sa_func.greatest(...) — see incident 2026-05-04 (Vyatkin)."
        )
