from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    mode: Literal["documents", "general"] = "documents"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationSummary(BaseModel):
    id: str
    title: str
    chat_mode: Literal["documents", "general"] = "documents"
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class ConversationMessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    status: Literal["completed", "failed", "interrupted"]
    model_id: str | None = None
    usage_metadata: dict[str, Any] | None = None
    citation_metadata: list[dict[str, Any]] | None = None
    request_metadata: dict[str, Any] | None = None
    error_metadata: dict[str, Any] | None = None
    generation_ms: int | None = None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessageOut]
    next_before_id: int | None = None


class ConversationMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    document_ids: list[int] | None = Field(default=None, max_length=50)
    request_id: str | None = Field(default=None, max_length=64)
    mode: Literal["documents", "general"] = "documents"

