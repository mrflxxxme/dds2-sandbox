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
# ─── Analysis ────────────────────────────────────────────────────────────────
from backend.services.funnel.analysis import (
    get_day_analysis,
    get_product_trends,
)

# ─── Anomalies ────────────────────────────────────────────────────────────────
from backend.services.funnel.anomalies import (
    get_anomalies,
)

# ─── Capital ─────────────────────────────────────────────────────────────────
from backend.services.funnel.capital import (
    get_capital_analysis,
)

# ─── Queries ─────────────────────────────────────────────────────────────────
from backend.services.funnel.queries import (
    bulk_set_cost_overrides,
    get_cost_overrides,
    get_filters,
    get_funnel_aggregated,
    get_funnel_by_brand,
    get_funnel_by_sku,
    get_funnel_by_subject,
    get_funnel_detailed,
    get_missing_costs,
    get_summary,
    set_cost_override,
)

# ─── Sync ────────────────────────────────────────────────────────────────────
from backend.services.funnel.sync import (
    batch_resync_ads,
    run_backfill_bg,
    run_funnel_sync,
)
from backend.services.funnel.wb_api_client import (
    fetch_ad_campaigns,
    fetch_ad_stats,
    fetch_funnel,
    get_wb_key,
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
    "get_funnel_by_brand",
    "get_funnel_by_sku",
    "get_funnel_by_subject",
    "get_funnel_detailed",
    "get_summary",
    "get_filters",
    "get_cost_overrides",
    "get_missing_costs",
    "set_cost_override",
    "bulk_set_cost_overrides",
    # analysis
    "get_day_analysis",
    "get_product_trends",
    # anomalies
    "get_anomalies",
    # capital
    "get_capital_analysis",
]
