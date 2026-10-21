from typing import Optional
from pydantic import BaseModel, Field


class SourceCitation(BaseModel):
    document_id: int
    document_name: str
    page_number: Optional[int] = None
    snippet: str
    # Raw cosine similarity from the vector store. None means the passage was
    # never scored by it: it reached the answer through the lexical or structural
    # pass over persisted OCR text, so there is no similarity to report. A 0.0
    # here would claim a measurement that says the passage is unrelated.
    similarity: Optional[float] = None
    # How this passage earned its place in the answer. "semantic" means it
    # cleared the calibrated raw-similarity floor on its own; the lexical and
    # structural values mean the window itself contains the matching text.
    support: Optional[str] = None
    source_kind: Optional[str] = None
    table_columns: Optional[list[str]] = None
    line_index: Optional[int] = None
    # Present when the cited passage contains a value the OCR could not verify.
    value_state: Optional[str] = None


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class ChatQueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    document_id: Optional[int] = None
    # Optional multi-document scope from the "+" selector. Empty or absent means
    # the whole authorized library, so older clients keep working unchanged.
    document_ids: Optional[list[int]] = None
    history: list[ChatMessage] = []


class ChatQueryResponse(BaseModel):
    answer: str
    sources: list[SourceCitation]
    confidence: float
    question: str
    has_evidence: bool = True
    engine: str = "extractive_fallback"
    notes: Optional[str] = None
