"""Pydantic schemas for AI Chat conversations and messages."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ── Conversations ─────────────────────────────────────────────────────


class AiConversationCreate(BaseModel):
    brand: str | None = None
    title: str = "Новый чат"


class AiConversationUpdate(BaseModel):
    title: str | None = None
    brand: str | None = None


class AiConversationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    user_id: int
    brand: str | None = None
    title: str
    created_at: datetime
    updated_at: datetime | None = None


class AiConversationList(BaseModel):
    """Lightweight schema for sidebar list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    brand: str | None = None
    title: str
    created_at: datetime
    updated_at: datetime | None = None


# ── Messages ──────────────────────────────────────────────────────────


class AiMessageCreate(BaseModel):
    content: str
    files: list[dict] | None = None  # [{name, type, size, url}]
    brand: str | None = None  # override conversation brand for this message


class AiMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    files: list[dict] | None = None
    tokens_used: int = 0
    tools_used: list[str] | None = None
    created_at: datetime


# ── File Upload ───────────────────────────────────────────────────────


class AiFileUploadResponse(BaseModel):
    name: str
    type: str  # "excel" | "image"
    size: int
    content: str  # extracted text for excel, base64 for images
