from pydantic import BaseModel, Field


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    include_citations: bool = True
    top_k: int = Field(default=8, ge=1, le=20)


class AnswerCitation(BaseModel):
    source_id: str
    document_id: int
    document_title: str
    filename: str
    page_number: int | None = None
    excerpt: str
    chunk_index: int | None = None
    href: str


class AnswerResponse(BaseModel):
    answer: str
    citations: list[AnswerCitation] = Field(default_factory=list)
    evidence_status: str
    query: str


class SearchHit(BaseModel):
    document_id: int
    document_name: str
    page_number: int | None = None
    chunk_index: int | None = None
    snippet: str
    match_field: str
    score: float | None = None
    highlight_terms: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    items: list[SearchHit]
    total: int
    skip: int
    limit: int


class SemanticSearchHit(BaseModel):
    document_id: int
    document_name: str
    page_number: int
    chunk_index: int
    snippet: str
    similarity: float
    match_type: str = "semantic"
    highlight_terms: list[str] = Field(default_factory=list)


class SemanticSearchResponse(BaseModel):
    items: list[SemanticSearchHit]
    total: int
    query: str
