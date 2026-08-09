from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    id: int
    original_filename: str
    stored_filename: str
    file_path: str
    file_type: str
    file_size: int | None
    document_type: str | None
    status: str | None
    uploaded_by: int | None
    overall_confidence: float | None
    uploaded_at: datetime | None
    processed_at: datetime | None

    model_config = ConfigDict(
        from_attributes=True
    )