import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.dependencies import (
    get_current_user,
    require_admin,
    require_upload_checker,
    require_upload_maker,
    verify_document_review_access,
    verify_document_view_access,
)
from app.core.rbac import Department, DocumentStatus, UserRole
from app.db.session import get_db
from app.models.document import (
    Document,
    DocumentPage,
    ExtractedField,
    ExtractedTable,
    OCRResult,
    ReviewQueue,
)
from app.models.user import User
from app.schemas.document import (
    AssignCheckerRequest,
    DashboardStatsResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentProcessingResponse,
    DocumentResponse,
    ReviewActionRequest,
    UploadQueueItem,
    UploadQueueResponse,
)
from app.services.embedding_service import (
    delete_document_embeddings,
    schedule_document_indexing,
)
from app.services.extraction_service import extract_document_report
from app.services.ocr_service import SUPPORTED_LANGUAGES, extract_document_text
from app.services.search_service import semantic_matching_document_ids
from app.services.upload_service import UPLOAD_DIR, save_uploaded_file

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/documents",
    tags=["Documents"],
)


# ============================================================
# UPLOAD DOCUMENT
# ============================================================

@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=201,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: str = Form("auto"),
    department: str | None = Form(None),
    document_type: str | None = Form(None),
    current_user: User = Depends(require_upload_maker),
    db: Session = Depends(get_db),
):
    """
    Upload a document and return immediately; OCR, extraction and indexing run
    as a background task after the response is sent.
    Language is detected automatically unless explicitly provided.
    Requires UPLOAD_MAKER or ADMIN role.
    """

    norm_lang = (language or "auto").lower().strip()

    if norm_lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported language '{language}'. "
                f"Supported languages are: "
                f"{', '.join(sorted(SUPPORTED_LANGUAGES))}."
            ),
        )

    # Check upload folder permissions before processing if folder/type requested
    from app.services.admin_service import check_folder_permission, evaluate_document_routing
    folder_check_target = document_type or department
    if folder_check_target and not check_folder_permission(db, folder_check_target, current_user.role, action="upload"):
        raise HTTPException(
            status_code=403,
            detail=f"Access forbidden: Role '{current_user.role}' is not permitted to upload '{folder_check_target}' documents.",
        )

    try:
        (
            original_filename,
            stored_filename,
            file_size,
        ) = await save_uploaded_file(file)

        disk_path = UPLOAD_DIR / stored_filename
        content_type = file.content_type or "application/octet-stream"

        # ----------------------------------------------------
        # Persist original file + create document record before
        # any processing work, then acknowledge the upload.
        # ----------------------------------------------------

        document = Document(
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=str(Path("uploads") / stored_filename),
            file_type=content_type,
            file_size=file_size,
            uploaded_by=current_user.id,
            status=DocumentStatus.PROCESSING.value,
        )

        db.add(document)
        db.commit()
        db.refresh(document)

        background_tasks.add_task(
            process_document_task,
            document_id=document.id,
            disk_path=disk_path,
            content_type=content_type,
            norm_lang=norm_lang,
            department=department,
            document_type=document_type,
            user_id=current_user.id,
        )

        return document

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:

        db.rollback()

        logger.error(
            f"Document upload error: {error}",
            exc_info=True,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to upload and process document.",
        )

    finally:

        await file.close()


# ============================================================
# BACKGROUND PROCESSING PIPELINE (OCR -> extraction -> routing -> indexing)
# ============================================================

def _persisted_valid_pages(db: Session, document_id: int) -> dict[int, OCRResult]:
    """Map page numbers that already hold usable OCR text for this document."""
    rows = (
        db.query(OCRResult, DocumentPage)
        .join(DocumentPage, DocumentPage.id == OCRResult.page_id)
        .filter(OCRResult.document_id == document_id)
        .all()
    )
    valid: dict[int, OCRResult] = {}
    for ocr, page in rows:
        if (ocr.extracted_text or "").strip() and page.page_number not in valid:
            valid[page.page_number] = ocr
    return valid


def _document_page_count(disk_path: Path, content_type: str) -> int | None:
    """Page count used to decide which pages still need processing.

    Returns None when the file cannot be inspected, which makes the caller
    process every page instead of silently skipping work.
    """
    try:
        suffix = disk_path.suffix.lower()
        if suffix == ".pdf" or "pdf" in (content_type or "").lower():
            import pymupdf as fitz

            with fitz.open(disk_path) as doc:
                return doc.page_count
        if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"):
            return 1
        return 1
    except Exception:
        logger.exception("Could not count pages for %s", disk_path)
        return None


def _merged_extraction_pages(db: Session, document_id: int):
    """Rebuild the extraction result for a document from persisted OCR rows.

    A resumed run only re-OCRs missing pages, so field extraction and routing
    still have to see every page, including the ones reused from the database.
    """
    from app.services.ocr_service import (
        DocumentExtractionResult,
        ExtractedPage,
    )

    rows = (
        db.query(OCRResult, DocumentPage)
        .outerjoin(DocumentPage, DocumentPage.id == OCRResult.page_id)
        .filter(OCRResult.document_id == document_id)
        .all()
    )
    # Tables are read back as structures, not as their rendered text: field
    # extraction needs rows, columns, headers and per-cell confidence, and a
    # resumed run that lost them would fall back to guessing from the page's prose.
    from app.services.table_structure import structure_from_payload

    def stored_payload(stored: ExtractedTable) -> dict:
        cells = list(stored.cells or [])
        return {
            "page_number": stored.page_number,
            "row_count": stored.row_count,
            "col_count": stored.col_count,
            "bbox": stored.bbox,
            "header_rows": stored.header_rows,
            "engine": stored.engine,
            "source": stored.source,
            "confidence": stored.confidence,
            "uncertain_cells": stored.uncertain_cells,
            "requires_review": stored.requires_review,
            "note": stored.note,
            # Not stored as columns: the cell flags carry it, and the frame is the
            # one fact a note can state - kept so a rebuilt grid cannot claim page
            # coordinates it was never written down as having.
            "page_backed_cells": sum(
                1
                for cell in cells
                if cell.get("from_page") and str(cell.get("text") or "").strip()
            ),
            "frame": "corrected"
            if "deskewed frame" in (stored.note or "")
            else "page",
            "cells": cells,
        }

    tables_by_page: dict[int, list] = {}
    for stored in (
        db.query(ExtractedTable)
        .filter(ExtractedTable.document_id == document_id)
        .order_by(ExtractedTable.page_number, ExtractedTable.id)
        .all()
    ):
        tables_by_page.setdefault(int(stored.page_number), []).append(
            structure_from_payload(stored_payload(stored))
        )

    pages: list[ExtractedPage] = []
    engines: set[str] = set()
    confidences: list[float] = []
    failed_pages = 0
    for ocr, page in sorted(
        rows, key=lambda r: (r[1].page_number if r[1] is not None else 1)
    ):
        if ocr.ocr_engine:
            engines.update(e.strip() for e in ocr.ocr_engine.split(","))
        # NULL confidence means the page was never OCR-scored (digital text
        # layer or blank page); averaging it as 0 would misstate document
        # quality, so only measured pages count.
        conf = float(ocr.confidence) if ocr.confidence is not None else None
        if conf is not None:
            confidences.append(conf)
        failed = "Failed" in (ocr.ocr_engine or "")
        if failed:
            failed_pages += 1
        page_number = page.page_number if page is not None else 1
        pages.append(
            ExtractedPage(
                page_number=page_number,
                text=ocr.extracted_text or "",
                # Stays None: a page nobody scored must not reach field extraction
                # looking like a page scored at zero, which would cap every field
                # read from it at the worst possible confidence.
                confidence=conf,
                raw_text=ocr.raw_text or ocr.extracted_text or "",
                ocr_failed=failed,
                tables=tables_by_page.get(page_number, []),
            )
        )
    combined = "\n\n".join(p.text for p in pages if p.text).strip()
    return DocumentExtractionResult(
        pages=pages,
        combined_text=combined,
        overall_ocr_confidence=(
            round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        ),
        ocr_engine=", ".join(sorted(engines)),
        ocr_failed_pages=failed_pages,
    )


_inflight_lock = threading.Lock()
_inflight_processing: set[int] = set()


def claim_document_processing(document_id: int) -> bool:
    """Reserve a document for this process; False when a worker already holds it."""
    with _inflight_lock:
        if document_id in _inflight_processing:
            return False
        _inflight_processing.add(document_id)
        return True


def release_document_processing(document_id: int) -> None:
    with _inflight_lock:
        _inflight_processing.discard(document_id)


def is_processing_claimed(document_id: int) -> bool:
    with _inflight_lock:
        return document_id in _inflight_processing


# Statuses that mean "this document still expects the pipeline to run".
# Stored values are written by this module (uppercase) but legacy rows and the
# 'failed' marker use lowercase, so compare case-insensitively.
_TASK_ACCEPTED_STATUSES = ("PROCESSING", "UPLOADED")
_RETRYABLE_STATUSES = ("FAILED", "PROCESSING", "UPLOADED")


def process_document_task(
    document_id: int,
    disk_path: Path,
    content_type: str,
    norm_lang: str,
    department: str | None,
    document_type: str | None,
    user_id: int | None,
) -> None:
    """Run the full processing pipeline for one document in a dedicated DB session.

    Executes after the upload response is sent. Marks the document failed on
    error so it can never remain in PROCESSING forever. Pages whose OCR text is
    already persisted are reused rather than re-OCRed, so a retried or resumed
    run stays idempotent and cannot duplicate per-page rows.
    """
    from app.db.session import SessionLocal
    from app.services.extraction_service import extract_document_report
    from app.services.ocr_service import extract_document_text
    from app.services.pipeline_metrics import stage

    if not claim_document_processing(document_id):
        logger.info(
            "Document %s is already being processed in this worker; skipping duplicate task",
            document_id,
        )
        return

    db = SessionLocal()
    t_start = time.perf_counter()
    try:
        document = (
            db.query(Document).filter(Document.id == document_id).first()
        )
        if document is None or (document.status or "").upper() not in _TASK_ACCEPTED_STATUSES:
            # Duplicate delivery or document removed — never reprocess.
            return

        persisted = _persisted_valid_pages(db, document.id)
        total_pages = _document_page_count(disk_path, content_type)
        only_pages: set[int] | None = None
        if persisted and total_pages:
            missing = {
                n for n in range(1, total_pages + 1) if n not in persisted
            }
            only_pages = missing or None

        logger.info(
            "Starting OCR for document %s (%s, lang=%s, pages_done=%s, pages_todo=%s)",
            document.id,
            document.original_filename,
            norm_lang,
            len(persisted),
            len(only_pages) if only_pages is not None else total_pages,
        )
        file_path = document.file_path
        ocr_res = None

        page_obj_map: dict[int, DocumentPage] = {
            page_number: row.page
            for page_number, row in persisted.items()
            if row.page is not None
        }

        def persist_page(page) -> None:
            """Commit one finished page as soon as it is available.

            A scanned page can take minutes, so pages must not sit uncommitted
            until the whole document finishes; a restart would discard them.
            Pages that already hold valid persisted text are left untouched.
            """
            if page.page_number in persisted:
                return
            doc_page = page_obj_map.get(page.page_number)
            if doc_page is None:
                doc_page = DocumentPage(
                    document_id=document.id,
                    page_number=page.page_number,
                    page_path=file_path,
                    width=page.width,
                    height=page.height,
                )
                db.add(doc_page)
                db.flush()
                page_obj_map[page.page_number] = doc_page
            db.add(
                OCRResult(
                    document_id=document.id,
                    page_id=doc_page.id,
                    extracted_text=page.text,
                    raw_text=page.raw_text or page.text,
                    confidence=page.confidence,
                    ocr_engine=page.engine
                    or (ocr_res.ocr_engine if ocr_res is not None else "unknown"),
                )
            )
            # Structured tables ride with the page that produced them: their cells,
            # boxes and per-cell confidence come straight from the engine, and an
            # unreadable cell is stored empty and flagged rather than filled in.
            for structure in getattr(page, "tables", None) or []:
                payload = structure.to_payload()
                db.add(
                    ExtractedTable(
                        document_id=document.id,
                        page_id=doc_page.id,
                        page_number=page.page_number,
                        bbox=payload["bbox"],
                        row_count=payload["row_count"],
                        col_count=payload["col_count"],
                        header_rows=payload["header_rows"],
                        cells=payload["cells"],
                        engine=payload["engine"],
                        source=payload["source"],
                        confidence=payload["confidence"],
                        uncertain_cells=payload["uncertain_cells"],
                        requires_review=payload["requires_review"],
                        note=payload["note"],
                    )
                )
            db.commit()

        if only_pages != set():
            with stage("doc.ocr", document_id=document_id) as metrics:
                ocr_res = extract_document_text(
                    disk_path,
                    content_type,
                    language=norm_lang,
                    only_pages=only_pages,
                    on_page=persist_page,
                )
                metrics["pages"] = len(ocr_res.pages)
                metrics["engine"] = ocr_res.ocr_engine.replace(" ", "_")
            logger.info(
                "OCR finished for document %s engine=%s pages=%s",
                document.id,
                ocr_res.ocr_engine,
                len(ocr_res.pages),
            )
            if not ocr_res.pages and not ocr_res.combined_text.strip():
                # Nothing was extracted and nothing was already persisted: this
                # is a failure, not a document with empty content.
                raise RuntimeError(
                    f"Text extraction produced no content (engine={ocr_res.ocr_engine})"
                )

        if ocr_res is not None:
            # Processors without the per-page callback (single image, plain
            # text, or test doubles) still need their output stored.
            for page in ocr_res.pages:
                if page.page_number not in page_obj_map:
                    persist_page(page)
            if not ocr_res.pages and ocr_res.combined_text.strip() and not persisted:
                db.add(
                    OCRResult(
                        document_id=document.id,
                        page_id=None,
                        extracted_text=ocr_res.combined_text,
                        confidence=ocr_res.overall_ocr_confidence,
                        ocr_engine=ocr_res.ocr_engine,
                    )
                )
                db.commit()

        # ------------------------------------------------
        # Structured extraction
        # ------------------------------------------------

        # autoflush is disabled, so newly added page/OCR rows must be flushed
        # before the merged read can see them.
        db.flush()
        merged = _merged_extraction_pages(db, document.id)
        if merged.pages and merged.ocr_failed_pages >= len(merged.pages):
            # Every page raised inside the OCR engine: this is a failed run, not
            # a document whose text simply looks thin.
            raise RuntimeError(
                f"OCR failed on all {len(merged.pages)} page(s) of the document"
            )
        with stage("doc.field_extraction", document_id=document_id) as metrics:
            report = extract_document_report(merged)
            metrics["fields"] = len(report.fields)
            metrics["confidence"] = report.overall_confidence

        existing_fields = (
            db.query(ExtractedField)
            .filter(ExtractedField.document_id == document.id)
            .count()
        )
        if not existing_fields:
            for field in report.fields:

                page_id = (
                    page_obj_map[field.page_number].id
                    if field.page_number in page_obj_map
                    else None
                )

                field_row = ExtractedField(
                    document_id=document.id,
                    page_id=page_id,
                    field_name=field.field_name,
                    field_value=field.field_value,
                    confidence=field.confidence,
                    original_value=field.original_value,
                    corrected_value=field.corrected_value,
                    is_verified=field.is_verified,
                )

                db.add(field_row)

        # ------------------------------------------------
        # Department Categorization, Routing & Review Queue
        # ------------------------------------------------

        uploader_department = None
        if user_id is not None:
            uploader = db.query(User).filter(User.id == user_id).first()
            uploader_department = uploader.department if uploader else None

        from app.services.admin_service import evaluate_document_routing

        raw_dept = department or report.department or uploader_department or "Operations"
        resolved_dept = Department.normalize(raw_dept) or raw_dept
        resolved_doc_type = document_type or report.document_type or "General Document"

        routed_review, routed_reason, route_to = evaluate_document_routing(
            db, resolved_doc_type, report.overall_confidence
        )

        # Pages that raised inside the OCR engine are a quality problem the
        # reviewer must see, even when the rest of the document scored well.
        failed_pages_note = (
            f"{merged.ocr_failed_pages} of {len(merged.pages)} page(s) failed OCR"
            if merged.ocr_failed_pages
            else None
        )

        # Determine final document status. Both the workflow routing decision
        # and the extraction report's own quality flags must be honoured, or a
        # poor OCR run gets reported as a successful index.
        needs_review = bool(routed_review or report.requires_review or failed_pages_note)
        final_status = (
            DocumentStatus.PENDING_REVIEW.value if needs_review else "completed"
        )
        review_reason = "; ".join(
            part
            for part in (routed_reason, report.review_reason, failed_pages_note)
            if part
        ) or "Pending verification"
        if route_to and route_to not in review_reason:
            review_reason = f"[{route_to}] {review_reason}"

        review_items = (
            db.query(ReviewQueue)
            .filter(ReviewQueue.document_id == document.id)
            .count()
        )
        if needs_review and not review_items:
            review_item = ReviewQueue(
                document_id=document.id,
                reason=review_reason,
                confidence=report.overall_confidence,
                status="pending",
            )
            db.add(review_item)

        # ------------------------------------------------
        # Update document
        # ------------------------------------------------

        document.department = resolved_dept
        document.document_type = resolved_doc_type
        document.overall_confidence = report.overall_confidence
        document.status = final_status
        # func.now() is the transaction start time in PostgreSQL, which makes
        # every stage of a long OCR run look instantaneous.
        document.processed_at = datetime.now(timezone.utc)

        with stage("doc.db_write", document_id=document_id):
            db.commit()
        logger.info(
            "stage=doc.total duration_s=%.3f status=ok document_id=%s pages=%s status=%s",
            time.perf_counter() - t_start,
            document_id,
            len(page_obj_map),
            final_status,
        )
        schedule_document_indexing(document.id)

    except Exception as proc_error:

        logger.error(
            f"Error during document processing pipeline for %s: %s",
            document_id,
            proc_error,
            exc_info=True,
        )
        logger.info(
            "stage=doc.total duration_s=%.3f status=failed document_id=%s error=%s",
            time.perf_counter() - t_start,
            document_id,
            type(proc_error).__name__,
        )

        db.rollback()

        try:
            document = (
                db.query(Document).filter(Document.id == document_id).first()
            )
            if document is not None:
                document.status = "failed"
                document.processed_at = datetime.now(timezone.utc)
                # The reason has to survive the request; the log alone is not a
                # message the library UI can show the uploader.
                if not (
                    db.query(ReviewQueue)
                    .filter(ReviewQueue.document_id == document.id)
                    .count()
                ):
                    db.add(
                        ReviewQueue(
                            document_id=document.id,
                            reason=f"Processing failed: {proc_error}",
                            confidence=None,
                            status="pending",
                        )
                    )
                db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to mark document %s as failed", document_id
            )

    finally:
        db.close()
        release_document_processing(document_id)


# ============================================================
# STARTUP RECOVERY FOR INTERRUPTED JOBS
# ============================================================

_RECOVERABLE_STATUSES = ("PROCESSING", "UPLOADED")


def requeue_interrupted_documents() -> int:
    """Retry documents left mid-pipeline by a shutdown or crash.

    Processing runs in an in-process background task, so a server that stops
    takes its jobs with it and the rows stay PROCESSING until a human notices.
    Documents already claimed by a running task in this process are skipped, so
    re-running them cannot duplicate work. Missing files are marked failed
    instead of staying PROCESSING forever.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    requeued = 0
    try:
        documents = (
            db.query(Document)
            .filter(func.upper(Document.status).in_(_RECOVERABLE_STATUSES))
            .order_by(Document.id)
            .all()
        )
        for document in documents:
            if is_processing_claimed(document.id):
                logger.info(
                    "Document %s already has a task running in this process; leaving it",
                    document.id,
                )
                continue
            disk_path = UPLOAD_DIR / Path(document.file_path).name
            if not disk_path.exists():
                logger.error(
                    "Document %s interrupted and its file is missing (%s); marking failed",
                    document.id,
                    document.file_path,
                )
                document.status = "failed"
                document.processed_at = datetime.now(timezone.utc)
                db.commit()
                continue

            logger.info(
                "Requeueing interrupted document %s (%s)",
                document.id,
                document.original_filename,
            )
            process_document_task(
                document_id=document.id,
                disk_path=disk_path,
                content_type=document.file_type or "application/octet-stream",
                norm_lang="auto",
                department=document.department,
                document_type=document.document_type,
                user_id=document.uploaded_by,
            )
            requeued += 1
    except Exception:
        logger.exception("Interrupted document requeue failed")
    finally:
        db.close()
    return requeued


# ============================================================
# PROCESSING WATCHDOG AND PROGRESS
# ============================================================

# Statuses that mean the pipeline already produced its final outcome. Values
# are compared upper-cased because legacy rows and the failure marker are
# stored in lowercase.
_SETTLED_STATUSES = (
    "PENDING_REVIEW",
    "COMPLETED",
    "INDEXED",
    "APPROVED",
    "REJECTED",
    "NEEDS_REVISION",
)


def _to_utc_datetime(value: datetime | str | None) -> datetime | None:
    """Normalize any datetime, date string, or None to a timezone-aware UTC datetime.

    - If None, returns None.
    - If string, parses ISO or standard datetime formats and attaches UTC.
    - If naive datetime, attaches UTC timezone (preserving calendar values).
    - If aware datetime, converts to UTC.
    """
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            return _to_utc_datetime(parsed)
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(cleaned, fmt)
                return _to_utc_datetime(parsed)
            except ValueError:
                continue
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _db_now(db: Session) -> datetime:
    """The database clock, normalized to timezone-aware UTC.

    Comparing server timestamps with a local ``datetime.now()`` shifts every
    elapsed value by the machine's UTC offset, which can hide a stalled job or
    invent one. SQLite returns the timestamp as text, so it is parsed here.
    """
    try:
        value = db.execute(select(func.now())).scalar()
    except Exception:
        logger.exception("Could not read the database clock")
        return datetime.now(timezone.utc)
    res = _to_utc_datetime(value)
    if res is not None:
        return res
    return datetime.now(timezone.utc)


def _processing_anchor(document: Document) -> datetime | None:
    """Latest server timestamp for the run a document is (or was) in, normalized to UTC."""
    stamps = [
        _to_utc_datetime(value)
        for value in (document.uploaded_at, document.updated_at)
    ]
    valid_stamps = [s for s in stamps if s is not None]
    return max(valid_stamps) if valid_stamps else None


def _processing_error(db: Session, document_id: int) -> str | None:
    """Most recent review-queue note, which is where failures record their reason."""
    item = (
        db.query(ReviewQueue)
        .filter(ReviewQueue.document_id == document_id)
        .order_by(ReviewQueue.id.desc())
        .first()
    )
    return item.reason if item is not None else None


def settle_stalled_processing(
    db: Session,
    document: Document,
    *,
    now: datetime | None = None,
    timeout_seconds: int | None = None,
) -> str | None:
    """Fail a document whose run outlived its budget with no worker attached.

    Deliberately narrow: a document claimed by a task in this process is left
    alone however long it takes, and only the progress endpoint calls this, so
    startup recovery keeps its ability to resume an interrupted run instead of
    writing it off. The reason is stored in the review queue, which is how the
    detail endpoint and the Retry action already explain failures.
    """
    status = (document.status or "").upper()
    if status not in _TASK_ACCEPTED_STATUSES:
        return None
    if is_processing_claimed(document.id):
        return None

    budget = (
        settings.PROCESSING_TIMEOUT_SECONDS
        if timeout_seconds is None
        else timeout_seconds
    )
    anchor = _processing_anchor(document)
    if anchor is None:
        return None
    now_stamp = _to_utc_datetime(now) if now is not None else _db_now(db)
    elapsed = (now_stamp - anchor).total_seconds()
    if elapsed <= budget:
        return None

    reason = (
        f"Processing stalled: no worker has run this document for over "
        f"{int(round(budget / 60))} minutes"
    )
    document.status = "failed"
    document.processed_at = datetime.now(timezone.utc)
    document.updated_at = func.now()

    pending = (
        db.query(ReviewQueue)
        .filter(
            ReviewQueue.document_id == document.id,
            ReviewQueue.status == "pending",
        )
        .order_by(ReviewQueue.id.desc())
        .first()
    )
    if pending is None:
        db.add(
            ReviewQueue(
                document_id=document.id,
                reason=reason,
                confidence=None,
                status="pending",
            )
        )
    else:
        pending.reason = reason
    db.commit()
    logger.warning(
        "Document %s marked stalled after %s s with no worker attached",
        document.id,
        int(elapsed),
    )
    return reason


def processing_progress(
    db: Session,
    document: Document,
    *,
    now: datetime | None = None,
    stalled: bool = False,
) -> DocumentProcessingResponse:
    """Describe one document's pipeline state from rows it already committed.

    Page, field and chunk counts come from DocumentPage/OCRResult,
    ExtractedField and EmbeddingIndex. Nothing is estimated: an unmeasured page
    confidence stays absent and an unknown page total is reported as null.
    """
    from app.models.document import ExtractedField
    from app.models.embedding import EmbeddingIndex

    status = (document.status or "").upper() or "UPLOADED"
    budget = settings.PROCESSING_TIMEOUT_SECONDS

    rows = (
        db.query(OCRResult, DocumentPage)
        .outerjoin(DocumentPage, DocumentPage.id == OCRResult.page_id)
        .filter(OCRResult.document_id == document.id)
        .all()
    )
    completed_pages: set[int] = set()
    failed_pages = 0
    for ocr, page in rows:
        page_number = page.page_number if page is not None else 1
        text = (ocr.extracted_text or "").strip()
        if text:
            completed_pages.add(page_number)
        elif "Failed" in (ocr.ocr_engine or ""):
            failed_pages += 1
    pages_done = len(completed_pages)

    field_count = int(
        db.query(func.count(ExtractedField.id))
        .filter(ExtractedField.document_id == document.id)
        .scalar()
        or 0
    )
    chunk_count = int(
        db.query(func.count(EmbeddingIndex.id))
        .filter(EmbeddingIndex.document_id == document.id)
        .scalar()
        or 0
    )

    unsettled = status not in _SETTLED_STATUSES
    running = unsettled and is_processing_claimed(document.id)

    pages_total: int | None = None
    if unsettled:
        disk_path = UPLOAD_DIR / Path(document.file_path).name
        # A missing file has no page count to report; asking the PDF reader for
        # it would log a traceback on every poll of an already failed upload.
        if disk_path.exists():
            pages_total = _document_page_count(disk_path, document.file_type or "")
        if pages_total is not None:
            pages_total = max(pages_total, pages_done + failed_pages)

    anchor = _processing_anchor(document)
    now_stamp = _to_utc_datetime(now) if now is not None else _db_now(db)
    upload_dt = _to_utc_datetime(document.uploaded_at)

    run_start = upload_dt if upload_dt is not None else anchor
    elapsed: float | None = (
        max(0.0, (now_stamp - run_start).total_seconds()) if run_start is not None else None
    )
    # Elapsed time only means "over budget" while a run is still owed. A
    # document settled hours ago is not timed out, and one the watchdog just
    # failed is reported as timed out even though its status already moved.
    timed_out = stalled or (
        unsettled and anchor is not None and (now_stamp - anchor).total_seconds() > budget
    )
    # Once a run has settled its elapsed time is history, not a live counter:
    # both stamps come from the same server clock, so a job that finished in a
    # minute keeps reporting a minute instead of growing past its own timeout.
    if not unsettled:
        finish = _to_utc_datetime(document.processed_at) or _to_utc_datetime(document.updated_at)
        if upload_dt is not None and finish is not None:
            if finish >= upload_dt:
                elapsed = (finish - upload_dt).total_seconds()
            else:
                elapsed = 0.0
        elif upload_dt is not None and finish is None:
            elapsed = 0.0
        elif upload_dt is None and finish is not None:
            elapsed = 0.0
        else:
            elapsed = None

    note = (
        _processing_error(db, document.id)
        if status in ("FAILED", "PENDING_REVIEW")
        else None
    )
    error = note if status == "FAILED" else None

    if status == "FAILED":
        stage = "failed"
        message = error or "Processing failed before any output could be stored."
    elif not unsettled:
        if status == "PENDING_REVIEW":
            stage = "done"
            message = (
                "Finished with warnings and is waiting for review. " + (note or "")
            ).strip()
        elif chunk_count == 0:
            stage = "making_searchable"
            message = "Text captured. The search index is still being built."
        else:
            stage = "done"
            message = "Processed and searchable."
    elif running:
        # Page counts belong in the numeric fields above, not in a status line the
        # user reads once per poll ("Page 0 of 5" was noise, not progress).
        if pages_total is not None and pages_done >= pages_total:
            stage = "extracting_fields"
            message = "Reading finished; extracting fields and classifying."
        else:
            stage = "reading_text"
            message = "Reading text from the document."
    else:
        stage = "waiting"
        message = "Queued for processing."

    return DocumentProcessingResponse(
        document_id=document.id,
        status=document.status or "UPLOADED",
        stage=stage,
        is_running=running,
        pages_total=pages_total,
        pages_completed=pages_done,
        pages_failed=failed_pages,
        extracted_field_count=field_count,
        indexed_chunks=chunk_count,
        searchable=chunk_count > 0,
        overall_confidence=(
            float(document.overall_confidence)
            if document.overall_confidence is not None
            else None
        ),
        elapsed_seconds=None if elapsed is None else round(elapsed, 1),
        timeout_seconds=budget,
        timed_out=timed_out,
        message=message,
        error=error,
    )


@router.get(
    "/{document_id}/processing",
    response_model=DocumentProcessingResponse,
    summary="Processing progress for one document",
)
def get_document_processing(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Poll-ready status for an upload: which page it is on and what failed.

    Uses the same view permissions as the document detail endpoint, so a
    checker or maker can only ever watch documents they may already read.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document with ID {document_id} not found.",
        )

    verify_document_view_access(document, current_user, db=db)

    # The watchdog may flip this row to failed just now; the payload has to
    # report that it ran out of budget even though the status already moved.
    stalled = settle_stalled_processing(db, document) is not None
    return processing_progress(db, document, stalled=stalled)


# ============================================================
# UPLOAD QUEUE (the uploader's own recent documents)
# ============================================================


@router.get(
    "/uploads/recent",
    response_model=UploadQueueResponse,
    summary="The current user's own uploads with their stored processing state",
)
def list_recent_uploads(
    limit: int = Query(
        10,
        ge=1,
        le=50,
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read the upload queue from persisted rows instead of browser storage.

    Restricted to documents the caller uploaded themselves, so no department or
    role rule is involved, and each row's progress is the same reading the
    single-document endpoint gives. Newest first by id, which is the order they
    were stored in regardless of which timestamp column a legacy row carries.
    """
    documents = (
        db.query(Document)
        .filter(Document.uploaded_by == current_user.id)
        .order_by(Document.id.desc())
        .limit(limit)
        .all()
    )

    items = [
        UploadQueueItem(
            document_id=document.id,
            filename=document.original_filename,
            department=document.department,
            document_type=document.document_type,
            uploaded_at=document.uploaded_at,
            processing=processing_progress(
                db,
                document,
                stalled=settle_stalled_processing(db, document) is not None,
            ),
        )
        for document in documents
    ]

    return UploadQueueResponse(items=items)


# ============================================================
# USER-TRIGGERED RETRY
# ============================================================


@router.post(
    "/{document_id}/retry",
    response_model=DocumentResponse,
    summary="Retry failed or interrupted processing (Upload Checker / Admin)",
)
def retry_document_processing(
    document_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_upload_checker),
    db: Session = Depends(get_db),
):
    """Put one unsettled document back through the processing pipeline.

    Only documents that never reached a settled state can be retried. Because
    every finished page is committed as it lands and the task reuses persisted
    page text, a retry only re-runs the pages that are actually missing.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    current_status = (document.status or "").upper()
    if current_status not in _RETRYABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Document status '{document.status}' is already settled; "
                "only failed or interrupted documents can be retried"
            ),
        )
    if is_processing_claimed(document.id):
        raise HTTPException(
            status_code=409,
            detail="Document is already being processed by a running task",
        )

    disk_path = UPLOAD_DIR / Path(document.file_path).name
    if not disk_path.exists():
        raise HTTPException(
            status_code=400,
            detail="Stored file is missing, so this document cannot be reprocessed",
        )

    document.status = DocumentStatus.PROCESSING.value
    # Server clock, not datetime.now(): the stalled-processing watchdog compares
    # this row against the database clock, and a local timestamp can sit hours
    # ahead of it, which would hide a genuinely abandoned run.
    document.updated_at = func.now()
    db.commit()
    db.refresh(document)

    background_tasks.add_task(
        process_document_task,
        document_id=document.id,
        disk_path=disk_path,
        content_type=document.file_type or "application/octet-stream",
        norm_lang="auto",
        department=document.department,
        document_type=document.document_type,
        user_id=document.uploaded_by,
    )
    return document


# ============================================================
# LIST DOCUMENTS + KEYWORD SEARCH
# ============================================================

@router.get(
    "",
    response_model=DocumentListResponse,
)
def list_documents(
    skip: int = Query(
        0,
        ge=0,
    ),
    limit: int = Query(
        50,
        ge=1,
        le=100,
    ),
    search: str | None = Query(
        None,
    ),
    department: str | None = Query(
        None,
    ),
    document_type: str | None = Query(
        None,
    ),
    file_type: str | None = Query(
        None,
    ),
    status: str | None = Query(
        None,
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List documents with pagination, filtering and hybrid search.
    Enforces department-aware and role-based document access.
    """

    query = (
        db.query(Document)
        .outerjoin(
            OCRResult,
            OCRResult.document_id == Document.id,
        )
        .outerjoin(
            ExtractedField,
            ExtractedField.document_id == Document.id,
        )
    )

    # --------------------------------------------------------
    # RBAC & DEPARTMENT ACCESS FILTERING
    # --------------------------------------------------------
    user_role = UserRole.normalize(current_user.role)
    if user_role == UserRole.ADMIN.value:
        if department:
            query = query.filter(Document.department.ilike(f"%{department.strip()}%"))
    elif user_role == UserRole.UPLOAD_MAKER.value:
        maker_dept = (current_user.department or "").strip()
        if maker_dept:
            query = query.filter(
                or_(
                    Document.department.ilike(f"%{maker_dept}%"),
                    Document.uploaded_by == current_user.id,
                )
            )
        else:
            query = query.filter(Document.uploaded_by == current_user.id)
        if department:
            query = query.filter(Document.department.ilike(f"%{department.strip()}%"))
    elif user_role == UserRole.UPLOAD_CHECKER.value:
        checker_dept = (current_user.department or "").strip()
        query = query.filter(Document.assigned_checker == current_user.id)
        if checker_dept:
            query = query.filter(Document.department.ilike(f"%{checker_dept}%"))
        if department:
            query = query.filter(Document.department.ilike(f"%{department.strip()}%"))



    # --------------------------------------------------------
    # KEYWORD SEARCH
    # --------------------------------------------------------

    if search and search.strip():

        search_term = f"%{search.strip()}%"
        semantic_ids = semantic_matching_document_ids(db, search.strip(), user=current_user)
        keyword_match = or_(
            Document.original_filename.ilike(search_term),
            OCRResult.extracted_text.ilike(search_term),
            ExtractedField.field_name.ilike(search_term),
            ExtractedField.field_value.ilike(search_term),
            ExtractedField.original_value.ilike(search_term),
            ExtractedField.corrected_value.ilike(search_term),
        )
        if semantic_ids:
            query = query.filter(
                or_(
                    keyword_match,
                    Document.id.in_(semantic_ids),
                )
            )
        else:
            query = query.filter(keyword_match)

    # --------------------------------------------------------
    # DOCUMENT TYPE
    # --------------------------------------------------------

    if document_type:
        query = query.filter(
            Document.document_type == document_type
        )

    # --------------------------------------------------------
    # FILE TYPE
    # --------------------------------------------------------

    if file_type:
        query = query.filter(
            Document.file_type == file_type
        )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if status:
        query = query.filter(
            Document.status == status
        )

    # --------------------------------------------------------
    # Remove duplicate documents caused by joins
    # --------------------------------------------------------

    query = query.distinct()

    # --------------------------------------------------------
    # Total results
    # --------------------------------------------------------

    total = query.count()

    # --------------------------------------------------------
    # Pagination
    # --------------------------------------------------------

    documents = (
        query
        .order_by(
            Document.uploaded_at.desc(),
            # Rows that share a timestamp would otherwise page unpredictably and
            # a client walking skip/limit could miss or repeat a document.
            Document.id.desc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )

    return DocumentListResponse(
        items=documents,
        total=total,
        skip=skip,
        limit=limit,
    )


# ============================================================
# DASHBOARD STATS
# ============================================================

@router.get(
    "/stats",
    response_model=DashboardStatsResponse,
)
def dashboard_stats(
    db: Session = Depends(get_db),
):
    total_documents = (
        db.query(
            func.count(Document.id)
        ).scalar()
        or 0
    )

    processed_today = (
        db.query(
            func.count(Document.id)
        )
        .filter(
            Document.processed_at.isnot(None),
            func.date(
                Document.processed_at
            )
            == func.current_date(),
        )
        .scalar()
        or 0
    )

    pending_review = (
        db.query(
            func.count(Document.id)
        )
        .filter(
            or_(
                Document.status.ilike("%PENDING%"),
                Document.status.ilike("%NEEDS%"),
                Document.status == "needs_review",
            )
        )
        .scalar()
        or 0
    )

    average_confidence = (
        db.query(
            func.avg(
                Document.overall_confidence
            )
        )
        .filter(
            Document.overall_confidence.isnot(None)
        )
        .scalar()
    )

    recent_documents = (
        db.query(Document)
        .order_by(
            Document.uploaded_at.desc()
        )
        .limit(5)
        .all()
    )

    return DashboardStatsResponse(
        total_documents=total_documents,
        processed_today=processed_today,
        pending_review=pending_review,
        average_confidence=(
            round(
                float(average_confidence),
                2,
            )
            if average_confidence is not None
            else None
        ),
        recent_documents=recent_documents,
    )


# ============================================================
# GET ONE DOCUMENT
# ============================================================

@router.get(
    "/review/queue",
    response_model=list[DocumentDetailResponse],
    summary="Get documents awaiting review",
)
def get_review_queue(
    current_user: User = Depends(require_upload_checker),
    db: Session = Depends(get_db),
):
    user_role = UserRole.normalize(current_user.role)
    query = (
        db.query(Document)
        .options(
            selectinload(Document.pages),
            selectinload(Document.ocr_results),
            selectinload(Document.extracted_fields),
            selectinload(Document.extracted_tables),
            selectinload(Document.review_items),
        )
        .filter(
            or_(
                Document.status.ilike("%PENDING%"),
                Document.status.ilike("%NEEDS%"),
                Document.status == "needs_review",
            )
        )
    )
    if user_role == UserRole.UPLOAD_CHECKER.value:
        checker_dept = (current_user.department or "").strip()
        query = query.filter(Document.assigned_checker == current_user.id)
        if checker_dept:
            query = query.filter(Document.department.ilike(f"%{checker_dept}%"))
    return query.order_by(Document.uploaded_at.desc()).all()



# ============================================================
# GET ONE DOCUMENT
# ============================================================

@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
)
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get complete document details including:

    - document metadata
    - pages
    - OCR results
    - extracted fields
    - review queue information
    """

    document = (
        db.query(Document)
        .options(
            selectinload(Document.pages),
            selectinload(Document.ocr_results),
            selectinload(Document.extracted_fields),
            selectinload(Document.extracted_tables),
            selectinload(Document.review_items),
        )
        .filter(
            Document.id == document_id
        )
        .first()
    )

    if not document:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Document with ID "
                f"{document_id} not found."
            ),
        )

    verify_document_view_access(document, current_user, db=db)
    return document


# ============================================================
# DOCUMENT REVIEW ACTIONS (UPLOAD_CHECKER / ADMIN)
# ============================================================

@router.post(
    "/{document_id}/approve",
    response_model=DocumentResponse,
    summary="Approve a document (Upload Checker / Admin)",
)
def approve_document(
    document_id: int,
    current_user: User = Depends(require_upload_checker),
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    verify_document_review_access(document, current_user, db=db)

    document.status = DocumentStatus.APPROVED.value
    document.updated_at = func.now()

    review_item = (
        db.query(ReviewQueue)
        .filter(ReviewQueue.document_id == document_id)
        .order_by(ReviewQueue.id.desc())
        .first()
    )
    if review_item:
        review_item.status = "approved"
        review_item.reviewed_by = current_user.id
        review_item.reviewed_at = func.now()

    db.commit()
    db.refresh(document)
    return document


@router.post(
    "/{document_id}/reject",
    response_model=DocumentResponse,
    summary="Reject a document (Upload Checker / Admin)",
)
def reject_document(
    document_id: int,
    payload: ReviewActionRequest = ReviewActionRequest(),
    current_user: User = Depends(require_upload_checker),
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    verify_document_review_access(document, current_user, db=db)

    document.status = DocumentStatus.REJECTED.value
    document.updated_at = func.now()

    review_item = (
        db.query(ReviewQueue)
        .filter(ReviewQueue.document_id == document_id)
        .order_by(ReviewQueue.id.desc())
        .first()
    )
    if review_item:
        review_item.status = "rejected"
        if payload.reason:
            review_item.reason = payload.reason
        review_item.reviewed_by = current_user.id
        review_item.reviewed_at = func.now()

    db.commit()
    db.refresh(document)
    return document


@router.post(
    "/{document_id}/request-revision",
    response_model=DocumentResponse,
    summary="Request revision on a document (Upload Checker / Admin)",
)
def request_document_revision(
    document_id: int,
    payload: ReviewActionRequest = ReviewActionRequest(),
    current_user: User = Depends(require_upload_checker),
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    verify_document_review_access(document, current_user, db=db)

    document.status = DocumentStatus.NEEDS_REVISION.value
    document.updated_at = func.now()

    review_item = (
        db.query(ReviewQueue)
        .filter(ReviewQueue.document_id == document_id)
        .order_by(ReviewQueue.id.desc())
        .first()
    )
    if review_item:
        review_item.status = "needs_revision"
        if payload.notes or payload.reason:
            review_item.reason = payload.notes or payload.reason
        review_item.reviewed_by = current_user.id
        review_item.reviewed_at = func.now()

    db.commit()
    db.refresh(document)
    return document


@router.post(
    "/{document_id}/assign",
    response_model=DocumentResponse,
    summary="Assign an Upload Checker to a document (Admin only)",
)
def assign_document_checker(
    document_id: int,
    payload: AssignCheckerRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    checker = db.query(User).filter(User.id == payload.checker_id).first()
    if not checker:
        raise HTTPException(status_code=404, detail=f"User with ID {payload.checker_id} not found")

    checker_role = UserRole.normalize(checker.role)
    if checker_role not in (UserRole.UPLOAD_CHECKER.value, UserRole.ADMIN.value):
        raise HTTPException(status_code=400, detail="Assigned user must be an Upload Checker or Admin")

    document.assigned_checker = payload.checker_id
    document.updated_at = func.now()

    review_item = (
        db.query(ReviewQueue)
        .filter(ReviewQueue.document_id == document_id)
        .order_by(ReviewQueue.id.desc())
        .first()
    )
    if review_item:
        review_item.assigned_to = payload.checker_id

    db.commit()
    db.refresh(document)
    return document


# ============================================================
# DELETE DOCUMENT
# ============================================================

@router.delete(
    "/{document_id}",
    status_code=204,
)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id
        )
        .first()
    )

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    # Only admin or uploader can delete
    user_role = UserRole.normalize(current_user.role)
    if user_role != UserRole.ADMIN.value and document.uploaded_by != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Access forbidden: Only Administrators or the document uploader can delete this document",
        )


    try:

        delete_document_embeddings(db, document.id)
        db.delete(document)
        db.commit()

        (
            UPLOAD_DIR
            / document.stored_filename
        ).unlink(
            missing_ok=True
        )

    except Exception:

        db.rollback()

        logger.exception(
            "Failed to delete document %s",
            document_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to delete document.",
        )