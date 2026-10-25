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
    raw_text: str | None = None
    confidence: float | None = None
    ocr_engine: str | None = None
    processed_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True
    )


class OCRTextUpdateRequest(BaseModel):
    """Checker correction for page OCR. Empty string is a valid correction."""

    extracted_text: str | None = None
    text: str | None = None

    def resolved_text(self) -> str | None:
        if self.extracted_text is not None:
            return self.extracted_text
        if self.text is not None:
            return self.text
        return None


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


class ExtractedTableCellResponse(BaseModel):
    """One cell as the engine measured it: grid position, box and span."""

    row: int
    col: int
    text: str = ""
    bbox: list[float] | None = None
    colspan: int = 1
    rowspan: int = 1
    header: bool = False
    # None means the engine had no measurable confidence (a digital text layer was
    # never OCR'd). It is not a score of 0.
    confidence: float | None = None
    uncertain: bool = False
    # Whether the page's own recognised text landed inside this box. A cell without
    # page backing holds text no page confirmed, which is worth showing separately
    # from a cell that was read and scored.
    from_page: bool = False

    model_config = ConfigDict(from_attributes=True)


class ExtractedTableResponse(BaseModel):
    id: int
    document_id: int
    page_id: int | None = None
    page_number: int
    row_count: int | None = None
    col_count: int | None = None
    bbox: list[float] | None = None
    header_rows: list[int] = []
    engine: str | None = None
    source: str
    confidence: float | None = None
    uncertain_cells: int = 0
    requires_review: bool = False
    note: str | None = None
    # How many cells hold text the page itself produced. A rejected grid carries no
    # cells, so it reports 0 of its own rather than the count it measured before
    # being refused.
    page_backed_cells: int = 0
    # "page" or "corrected": which image the cell boxes belong to. An overlay drawn
    # in the wrong frame points at the wrong part of the page.
    frame: str = "page"
    cells: list[ExtractedTableCellResponse] = []

    model_config = ConfigDict(from_attributes=True)


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
    department: str | None = None
    status: str | None = None
    uploaded_by: int | None = None
    assigned_checker: int | None = None
    overall_confidence: float | None = None
    uploaded_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    processed_at: datetime | None = None
    uploader_name: str | None = None
    checker_name: str | None = None

    model_config = ConfigDict(
        from_attributes=True
    )


class DocumentDetailResponse(DocumentResponse):
    pages: list[DocumentPageResponse] = []
    ocr_results: list[OCRResultResponse] = []
    extracted_fields: list[ExtractedFieldResponse] = []
    extracted_tables: list[ExtractedTableResponse] = []
    review_items: list[ReviewQueueResponse] = []

    model_config = ConfigDict(
        from_attributes=True
    )


class DocumentProcessingResponse(BaseModel):
    """Progress of one document, derived only from persisted pipeline rows.

    There is no job table and no invented percentage: ``pages_completed`` counts
    OCR rows that hold text, ``indexed_chunks`` counts embedding rows, and
    ``overall_confidence`` is whatever the pipeline actually measured.
    """

    document_id: int
    status: str
    # waiting | reading_text | extracting_fields | making_searchable |
    # done | failed
    stage: str
    is_running: bool
    pages_total: int | None = None
    pages_completed: int = 0
    pages_failed: int = 0
    extracted_field_count: int = 0
    indexed_chunks: int = 0
    searchable: bool = False
    overall_confidence: float | None = None
    elapsed_seconds: float | None = None
    timeout_seconds: int
    timed_out: bool = False
    message: str
    error: str | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    skip: int
    limit: int


class UploadQueueItem(BaseModel):
    """One of the current user's own uploads and the state stored for it.

    The upload screen rebuilds its status cards from these rows, so a reload or a
    second tab on the same account sees the same queue without any browser storage.
    """

    document_id: int
    filename: str
    department: str | None = None
    document_type: str | None = None
    uploaded_at: datetime | None = None
    processing: DocumentProcessingResponse


class UploadQueueResponse(BaseModel):
    items: list[UploadQueueItem]


class DashboardStatsResponse(BaseModel):
    total_documents: int
    processed_today: int
    pending_review: int
    average_confidence: float | None = None
    recent_documents: list[DocumentResponse]


class ReviewActionRequest(BaseModel):
    reason: str | None = None
    notes: str | None = None


class AssignCheckerRequest(BaseModel):
    checker_id: int

