"""
Stock Analytics Service — backward-compatible re-export.

Split into:
- stock_forecast_service.py: forecast, trend, traffic-light
- warehouse_stock_service.py: sync, stocks view, restocking need
"""

# Re-export all public names for backward compatibility
from backend.services.stock_forecast_service import (
    build_traffic_light_counts,
    classify_traffic_light,
    compute_days_left,
    compute_trend_pct,
    get_stock_analytics,
)
from backend.services.warehouse_need_service import (
    get_warehouse_need,
)
from backend.services.warehouse_stock_service import (
    compute_need,
    get_warehouse_stocks,
    sync_warehouse_stocks,
)

__all__ = [
    "get_stock_analytics",
    "compute_days_left",
    "compute_trend_pct",
    "classify_traffic_light",
    "build_traffic_light_counts",
    "compute_need",
    "sync_warehouse_stocks",
    "get_warehouse_stocks",
    "get_warehouse_need",
]
