"""
Monitoring schemas — sync health, scheduler status.
"""

from datetime import datetime

from pydantic import BaseModel


class SyncTypeStatus(BaseModel):
    """Status of a single sync type (e.g. funnel_auto, wb_finance_weekly)."""

    service: str
    sync_type: str
    last_ok_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_msg: str | None = None
    last_status: str | None = None
    total_24h: int = 0
    ok_24h: int = 0
    error_24h: int = 0
    avg_duration_sec: float | None = None
    is_running: bool = False
    running_since: datetime | None = None


class SchedulerJobInfo(BaseModel):
    """Info about a scheduled job."""

    id: str
    name: str
    next_run: str | None = None
    is_active: bool = True


class SchedulerStatus(BaseModel):
    """Scheduler overall status."""

    running: bool
    jobs: list[SchedulerJobInfo]


class MonitoringOverview(BaseModel):
    """Full monitoring overview response."""

    sync_types: list[SyncTypeStatus]
    scheduler: SchedulerStatus
    total_syncs_24h: int = 0
    total_errors_24h: int = 0
    health: dict[str, str] = {}


class SyncLogEntry(BaseModel):
    """Extended sync log entry for monitoring."""

    id: int
    integration_id: int
    service: str
    sync_type: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str
    rows_fetched: int = 0
    rows_inserted: int = 0
    error_msg: str | None = None
    duration_sec: float | None = None


class SyncLogFilter(BaseModel):
    """Filters for sync log query."""

    service: str | None = None
    sync_type: str | None = None
    status: str | None = None
    limit: int = 50
    offset: int = 0
