"""
Custom Prometheus metrics exposed via /metrics (prometheus_fastapi_instrumentator
attaches to the default REGISTRY, so Gauge/Counter defined here are scraped
automatically from both backend and worker containers).

Scheduler jobs call the `.set(time.time())` pattern after a successful run —
alert_rules compare `time() - gauge` against an SLO to detect missed runs.
"""

from prometheus_client import Gauge

# ─── Scheduler: last-success timestamps (unix epoch, seconds) ─────────────
# Written by worker scheduler jobs on success. Used by Prometheus alerts via
#   (time() - dds_*_last_success_timestamp) > <SLO>
# to page when a sync is silently missing. Set to 0 at import time so the
# metric is registered even before the first successful run.

wb_goods_returns_last_success = Gauge(
    "dds_wb_goods_returns_last_success_timestamp",
    "Unix timestamp of the last successful WB goods-returns (возвраты на ПВЗ) sync run",
)
wb_goods_returns_last_success.set(0)
