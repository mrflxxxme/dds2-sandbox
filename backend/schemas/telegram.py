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
    measurements_notify_enabled: bool = False
    ff_board_enabled: bool = False
    # NULL = табло по всем складам проекта; иначе — заявки только этого склада ФФ.
    ff_board_warehouse_id: int | None = None
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


class FfBoardConfigRequest(BaseModel):
    """Set the pinned FF-board for a chat: on/off + optional warehouse scope.

    warehouse_id=None with enabled=True → board over all warehouses (default).
    A non-null warehouse_id scopes the board to that single fulfillment warehouse.
    """

    enabled: bool
    warehouse_id: int | None = None
