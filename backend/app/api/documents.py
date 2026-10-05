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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import (
    get_current_user,
    require_admin,
    require_upload_checker,
    require_upload_maker,
    verify_document_review_access,
    verify_document_view_access,
)
from app.core.rbac import Department, DocumentStatus, UserRole
from app.db.session import get_db, recycle_connection
from app.models.document import (
    Document,
    DocumentPage,
    ExtractedField,
    OCRResult,
    ReviewQueue,
)
from app.models.user import User
from app.schemas.document import (
    AssignCheckerRequest,
    DashboardStatsResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
    ReviewActionRequest,
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
    file: UploadFile = File(...),
    language: str = Form("auto"),
    department: str | None = Form(None),
    document_type: str | None = Form(None),
    current_user: User = Depends(require_upload_maker),
    db: Session = Depends(get_db),
):
    """
    Upload and process a document through the document processing pipeline.
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
        file_path = str(Path("uploads") / stored_filename)
        content_type = file.content_type or "application/octet-stream"

        # ----------------------------------------------------
        # Create initial document
        # ----------------------------------------------------

        document = Document(
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=file_path,
            file_type=content_type,
            file_size=file_size,
            uploaded_by=current_user.id if current_user else None,
            status=DocumentStatus.UPLOADED.value,
        )

        db.add(document)
        db.flush()
        document_id = document.id
        db.commit()
        recycle_connection(db)


        try:
            # ------------------------------------------------
            # OCR (do not hold a Neon connection open during this)
            # ------------------------------------------------

            logger.info(
                "Starting OCR for document %s (%s, lang=%s)",
                document_id,
                original_filename,
                norm_lang,
            )
            ocr_res = extract_document_text(
                disk_path,
                content_type,
                language=norm_lang,
            )
            logger.info(
                "OCR finished for document %s engine=%s pages=%s",
                document_id,
                ocr_res.ocr_engine,
                len(ocr_res.pages),
            )

            # ------------------------------------------------
            # Save pages + OCR results
            # ------------------------------------------------

            page_obj_map: dict[int, DocumentPage] = {}
            document = db.get(Document, document_id)
            if document is None:
                raise RuntimeError(f"Document {document_id} missing after OCR")

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

                for p in ocr_res.pages:

                    page_id = (
                        page_obj_map[p.page_number].id
                        if p.page_number in page_obj_map
                        else None
                    )

                    ocr_row = OCRResult(
                        document_id=document.id,
                        page_id=page_id,
                        extracted_text=p.text,
                        confidence=p.confidence,
                        ocr_engine=ocr_res.ocr_engine,
                    )

                    db.add(ocr_row)

            else:

                ocr_row = OCRResult(
                    document_id=document.id,
                    page_id=None,
                    extracted_text=ocr_res.combined_text,
                    confidence=ocr_res.overall_ocr_confidence,
                    ocr_engine=ocr_res.ocr_engine,
                )

                db.add(ocr_row)

            # ------------------------------------------------
            # Structured extraction
            # ------------------------------------------------

            report = extract_document_report(ocr_res)

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

            raw_dept = department or report.department or (current_user.department if current_user else None) or "Operations"
            resolved_dept = Department.normalize(raw_dept) or raw_dept
            resolved_doc_type = document_type or report.document_type or "General Document"

            routed_review, routed_reason, route_to = evaluate_document_routing(
                db, resolved_doc_type, report.overall_confidence
            )

            final_status = DocumentStatus.PENDING_REVIEW.value
            review_reason = routed_reason or report.review_reason or "Pending verification"
            if route_to and route_to not in review_reason:
                review_reason = f"[{route_to}] {review_reason}"

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
            document.processed_at = func.now()

            db.commit()
            db.refresh(document)
            schedule_document_indexing(document.id)


        except Exception as proc_error:

            logger.error(
                f"Error during document processing pipeline: {proc_error}",
                exc_info=True,
            )

            recycle_connection(db)
            document = db.get(Document, document_id)
            if document is not None:
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
        try:
            db.rollback()
        except Exception:
            recycle_connection(db)

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
        semantic_ids = semantic_matching_document_ids(db, search.strip())
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
            Document.uploaded_at.desc()
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
            Document.status == "needs_review"
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