"""
Integration schemas.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class IntegrationKeySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    service: str
    label: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_sync_at: Optional[datetime] = None
    key_preview: Optional[str] = None  # masked key, e.g. "***abcd"


class SyncLogSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    integration_id: int
    service: str
    sync_type: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    status: str = "RUNNING"
    rows_fetched: int = 0
    rows_inserted: int = 0
    error_msg: Optional[str] = None
