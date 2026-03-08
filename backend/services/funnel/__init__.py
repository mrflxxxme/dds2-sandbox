"""
Funnel service package — WB sales funnel business logic.

This package was refactored from the monolithic funnel_service.py (1,304 lines)
into 4 focused modules:

- wb_api_client: WB API key lookup + all Wildberries API calls
- sync:          Core sync, backfill, batch ad resync
- queries:       Data aggregation, filtering, cost overrides
- analysis:      Day analysis, anomaly detection, product trends

All public functions are re-exported here for backward compatibility.
"""

# ─── WB API Client ──────────────────────────────────────────────────────────
from backend.services.funnel.wb_api_client import (
    get_wb_key,
    fetch_funnel,
    fetch_ad_campaigns,
    fetch_ad_stats,
)

# ─── Sync ────────────────────────────────────────────────────────────────────
from backend.services.funnel.sync import (
    run_funnel_sync,
    run_backfill_bg,
    batch_resync_ads,
)

# ─── Queries ─────────────────────────────────────────────────────────────────
from backend.services.funnel.queries import (
    get_funnel_aggregated,
    get_funnel_detailed,
    get_summary,
    get_filters,
    get_cost_overrides,
    set_cost_override,
    bulk_set_cost_overrides,
)

# ─── Analysis ────────────────────────────────────────────────────────────────
from backend.services.funnel.analysis import (
    get_day_analysis,
    get_product_trends,
)

__all__ = [
    # wb_api_client
    "get_wb_key",
    "fetch_funnel",
    "fetch_ad_campaigns",
    "fetch_ad_stats",
    # sync
    "run_funnel_sync",
    "run_backfill_bg",
    "batch_resync_ads",
    # queries
    "get_funnel_aggregated",
    "get_funnel_detailed",
    "get_summary",
    "get_filters",
    "get_cost_overrides",
    "set_cost_override",
    "bulk_set_cost_overrides",
    # analysis
    "get_day_analysis",
    "get_product_trends",
]
