from datetime import datetime

from pydantic import BaseModel, Field


class PreprocessedPage(BaseModel):
    document_id: str
    page_number: int
    image_path: str
    original_filename: str
    dpi: int


class PageResult(BaseModel):
    page_number: int
    extracted_text: str
    confidence_score: float = Field(ge=0.0, le=1.0)


class OCRResponse(BaseModel):
    document_id: str
    pages: list[PageResult]
    overall_confidence: float = Field(ge=0.0, le=1.0)
    ocr_engine: str
    processed_at: datetime