"""
Telegram bot schemas.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TelegramChatBindingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    chat_id: int
    project_id: int
    brand: str | None = None
    notify_enabled: bool = True
    ff_notify_enabled: bool = False
    created_by_id: int
    created_at: datetime


class TelegramLinkResponse(BaseModel):
    deep_link_url: str


class BrandNoteSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    brand: str
    note: str
    created_at: datetime


class ToggleNotifyRequest(BaseModel):
    enabled: bool
