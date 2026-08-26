from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentPageResponse(BaseModel):
    id: int
    document_id: int
    page_number: int
    page_path: str | None = None
    width: int | None = None
    height: int | None = None

    model_config = ConfigDict(
        from_attributes=True
    )


class OCRResultResponse(BaseModel):
    id: int
    document_id: int
    page_id: int | None = None
    extracted_text: str | None = None
    confidence: float | None = None
    ocr_engine: str | None = None
    processed_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True
    )


class ExtractedFieldResponse(BaseModel):
    id: int
    document_id: int
    page_id: int | None = None
    field_name: str
    field_value: str | None = None
    confidence: float | None = None
    original_value: str | None = None
    corrected_value: str | None = None
    is_verified: bool | None = False
    created_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True
    )


class ReviewQueueResponse(BaseModel):
    id: int
    document_id: int
    reason: str | None = None
    confidence: float | None = None
    status: str | None = "pending"
    assigned_to: int | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True
    )


class DocumentResponse(BaseModel):
    id: int
    original_filename: str
    stored_filename: str
    file_path: str
    file_type: str
    file_size: int | None = None
    document_type: str | None = None
    status: str | None = None
    uploaded_by: int | None = None
    overall_confidence: float | None = None
    uploaded_at: datetime | None = None
    processed_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True
    )


class DocumentDetailResponse(DocumentResponse):
    pages: list[DocumentPageResponse] = []
    ocr_results: list[OCRResultResponse] = []
    extracted_fields: list[ExtractedFieldResponse] = []
    review_items: list[ReviewQueueResponse] = []

    model_config = ConfigDict(
        from_attributes=True
    )