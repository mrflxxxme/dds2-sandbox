"""
Common response schemas.
"""

from typing import Optional
from pydantic import BaseModel


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str


class StatusResponse(BaseModel):
    """Generic status response."""
    status: str


class DeleteResponse(BaseModel):
    """Response for delete operations."""
    deleted: bool
    id: Optional[int] = None
