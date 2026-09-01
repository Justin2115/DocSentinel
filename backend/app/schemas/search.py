from pydantic import BaseModel


class SearchHit(BaseModel):
    document_id: int
    document_name: str
    page_number: int | None = None
    snippet: str
    match_field: str
    score: float | None = None


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


class SemanticSearchResponse(BaseModel):
    items: list[SemanticSearchHit]
    total: int
    query: str
