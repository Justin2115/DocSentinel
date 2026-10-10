from typing import Optional
from pydantic import BaseModel, Field


class SourceCitation(BaseModel):
    document_id: int
    document_name: str
    page_number: Optional[int] = None
    snippet: str
    similarity: float


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class ChatQueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    document_id: Optional[int] = None
    history: list[ChatMessage] = []


class ChatQueryResponse(BaseModel):
    answer: str
    sources: list[SourceCitation]
    confidence: float
    question: str
    has_evidence: bool = True
    engine: str = "extractive_fallback"
    notes: Optional[str] = None
