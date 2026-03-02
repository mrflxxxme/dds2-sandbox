"""
Import schemas.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ImportLogSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    source_type: str
    imported_at: datetime
    rows_raw: int
    rows_inserted: int
    rows_skipped: int
    status: str
    error_msg: Optional[str] = None
    file_url: Optional[str] = None


class ImportResult(BaseModel):
    """Response after file upload."""
    import_id: int
    filename: str
    source_type: str
    rows_raw: int
    rows_inserted: int
    rows_skipped: int
    status: str
    error_msg: Optional[str] = None
