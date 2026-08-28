import logging
from pathlib import Path
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.document import (
    Document,
    DocumentPage,
    ExtractedField,
    OCRResult,
    ReviewQueue,
)
from app.schemas.document import (
    DashboardStatsResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
)
from app.services.extraction_service import extract_document_report
from app.services.ocr_service import SUPPORTED_LANGUAGES, extract_document_text
from app.services.upload_service import UPLOAD_DIR, save_uploaded_file

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/documents",
    tags=["Documents"],
)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=201,
)
async def upload_document(
    file: UploadFile = File(...),
    language: str = Form("en"),
    db: Session = Depends(get_db),
):
    """
    Upload and process a document through the document processing pipeline:
    1. Validate language (en, hi, mr) and file format
    2. Save file to local uploads directory
    3. Create Document record in DB with status 'processing'
    4. Run language-aware OCR / text extraction (PDFs, images)
    5. Clean extracted text and extract structured fields
    6. Score confidences and route low-confidence documents to review queue
    7. Persist pages, OCR results, and extracted fields
    8. Update Document status to 'completed' or 'needs_review'
    """
    norm_lang = (language or "en").lower().strip()
    if norm_lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{language}'. Supported languages are: {', '.join(sorted(SUPPORTED_LANGUAGES))}.",
        )

    try:
        (
            original_filename,
            stored_filename,
            file_size,
        ) = await save_uploaded_file(file)

        disk_path = UPLOAD_DIR / stored_filename
        file_path = str(Path("uploads") / stored_filename)
        content_type = file.content_type or "application/octet-stream"

        # 1. Create initial Document record in DB
        document = Document(
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=file_path,
            file_type=content_type,
            file_size=file_size,
            status="processing",
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        try:
            # 2. Run Language-Aware OCR / Text Extraction
            ocr_res = extract_document_text(disk_path, content_type, language=norm_lang)


            # 3. Save DocumentPage records
            page_obj_map: dict[int, DocumentPage] = {}
            if ocr_res.pages:
                for p in ocr_res.pages:
                    doc_page = DocumentPage(
                        document_id=document.id,
                        page_number=p.page_number,
                        page_path=file_path,
                        width=p.width,
                        height=p.height,
                    )
                    db.add(doc_page)
                    page_obj_map[p.page_number] = doc_page
                db.flush()

                # Save per-page OCR results
                for p in ocr_res.pages:
                    page_id = page_obj_map.get(p.page_number).id if p.page_number in page_obj_map else None
                    ocr_row = OCRResult(
                        document_id=document.id,
                        page_id=page_id,
                        extracted_text=p.text,
                        confidence=p.confidence,
                        ocr_engine=ocr_res.ocr_engine,
                    )
                    db.add(ocr_row)
            else:
                # Document-level OCR result fallback
                ocr_row = OCRResult(
                    document_id=document.id,
                    page_id=None,
                    extracted_text=ocr_res.combined_text,
                    confidence=ocr_res.overall_ocr_confidence,
                    ocr_engine=ocr_res.ocr_engine,
                )
                db.add(ocr_row)

            # 4. Run Structured Field Extraction
            report = extract_document_report(ocr_res)

            # Save Extracted Fields
            for f in report.fields:
                page_id = page_obj_map.get(f.page_number).id if f.page_number in page_obj_map else None
                field_row = ExtractedField(
                    document_id=document.id,
                    page_id=page_id,
                    field_name=f.field_name,
                    field_value=f.field_value,
                    confidence=f.confidence,
                    original_value=f.original_value,
                    corrected_value=f.corrected_value,
                    is_verified=f.is_verified,
                )
                db.add(field_row)

            # 5. Handle Confidence & Review Queue
            final_status = "completed"
            if report.requires_review:
                final_status = "needs_review"
                review_item = ReviewQueue(
                    document_id=document.id,
                    reason=report.review_reason,
                    confidence=report.overall_confidence,
                    status="pending",
                )
                db.add(review_item)

            # 6. Update Document status and metrics
            document.document_type = report.document_type
            document.overall_confidence = report.overall_confidence
            document.status = final_status
            document.processed_at = func.now()

            db.commit()
            db.refresh(document)

        except Exception as proc_error:
            logger.error(f"Error during document processing pipeline: {proc_error}", exc_info=True)
            db.rollback()
            # Mark document as failed
            document.status = "failed"
            document.processed_at = func.now()
            db.add(document)
            db.commit()
            db.refresh(document)

        return document

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:
        db.rollback()
        logger.error(f"Document upload error: {error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to upload and process document.",
        )

    finally:
        await file.close()


@router.get("", response_model=DocumentListResponse)
def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(None),
    document_type: str | None = Query(None),
    file_type: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(Document)
    if search:
        query = query.filter(Document.original_filename.ilike(f"%{search.strip()}%"))
    if document_type:
        query = query.filter(Document.document_type == document_type)
    if file_type:
        query = query.filter(Document.file_type == file_type)
    if status:
        query = query.filter(Document.status == status)

    total = query.count()
    documents = query.order_by(Document.uploaded_at.desc()).offset(skip).limit(limit).all()
    return DocumentListResponse(items=documents, total=total, skip=skip, limit=limit)


@router.get("/stats", response_model=DashboardStatsResponse)
def dashboard_stats(db: Session = Depends(get_db)):
    total_documents = db.query(func.count(Document.id)).scalar() or 0
    processed_today = (
        db.query(func.count(Document.id))
        .filter(Document.processed_at.isnot(None), func.date(Document.processed_at) == func.current_date())
        .scalar()
        or 0
    )
    pending_review = db.query(func.count(Document.id)).filter(Document.status == "needs_review").scalar() or 0
    average_confidence = db.query(func.avg(Document.overall_confidence)).filter(
        Document.overall_confidence.isnot(None)
    ).scalar()
    recent_documents = db.query(Document).order_by(Document.uploaded_at.desc()).limit(5).all()

    return DashboardStatsResponse(
        total_documents=total_documents,
        processed_today=processed_today,
        pending_review=pending_review,
        average_confidence=round(float(average_confidence), 2) if average_confidence is not None else None,
        recent_documents=recent_documents,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
):
    """
    Get detailed document information including pages, OCR text, extracted fields, and review queue items.
    """
    document = (
        db.query(Document)
        .options(
            selectinload(Document.pages),
            selectinload(Document.ocr_results),
            selectinload(Document.extracted_fields),
            selectinload(Document.review_items),
        )
        .filter(Document.id == document_id)
        .first()
    )

    if not document:
        raise HTTPException(
            status_code=404,
            detail=f"Document with ID {document_id} not found.",
        )

    return document


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        db.delete(document)
        db.commit()
        (UPLOAD_DIR / document.stored_filename).unlink(missing_ok=True)
    except Exception:
        db.rollback()
        logger.exception("Failed to delete document %s", document_id)
        raise HTTPException(status_code=500, detail="Failed to delete document.")
