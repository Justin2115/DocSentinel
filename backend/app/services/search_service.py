import logging

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentPage, ExtractedField, OCRResult
from app.schemas.search import SearchHit, SearchResponse

logger = logging.getLogger(__name__)

SNIPPET_RADIUS = 100


def make_snippet(text: str, query: str, radius: int = SNIPPET_RADIUS) -> str:
    source = text or ""
    if not source:
        return ""

    needle = query.strip().lower()
    haystack = source.lower()
    index = haystack.find(needle) if needle else -1

    if index < 0:
        snippet = source[: radius * 2]
        return snippet.strip() + ("…" if len(source) > len(snippet) else "")

    start = max(0, index - radius)
    end = min(len(source), index + len(query.strip()) + radius)
    snippet = source[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(source) else ""
    return f"{prefix}{snippet}{suffix}"


def _occurrence_score(text: str | None, query: str, base: float) -> float:
    if not text:
        return base
    count = text.lower().count(query.strip().lower())
    return round(min(1.0, base + min(count, 5) * 0.02), 4)


def keyword_search(
    db: Session,
    query: str,
    skip: int = 0,
    limit: int = 20,
) -> SearchResponse:
    term = (query or "").strip()
    if not term:
        return SearchResponse(items=[], total=0, skip=skip, limit=limit)

    pattern = f"%{term}%"
    hits: list[SearchHit] = []
    seen: set[tuple] = set()

    filename_docs = (
        db.query(Document)
        .filter(Document.original_filename.ilike(pattern))
        .all()
    )
    for document in filename_docs:
        key = (document.id, None, "filename")
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            SearchHit(
                document_id=document.id,
                document_name=document.original_filename,
                page_number=None,
                snippet=document.original_filename,
                match_field="filename",
                score=_occurrence_score(document.original_filename, term, 1.0),
            )
        )

    ocr_rows = (
        db.query(OCRResult, Document, DocumentPage)
        .join(Document, Document.id == OCRResult.document_id)
        .outerjoin(DocumentPage, DocumentPage.id == OCRResult.page_id)
        .filter(OCRResult.extracted_text.ilike(pattern))
        .all()
    )
    for ocr, document, page in ocr_rows:
        page_number = page.page_number if page is not None else None
        key = (document.id, page_number, "extracted_text")
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            SearchHit(
                document_id=document.id,
                document_name=document.original_filename,
                page_number=page_number,
                snippet=make_snippet(ocr.extracted_text or "", term),
                match_field="extracted_text",
                score=_occurrence_score(ocr.extracted_text, term, 0.8),
            )
        )

    field_rows = (
        db.query(ExtractedField, Document, DocumentPage)
        .join(Document, Document.id == ExtractedField.document_id)
        .outerjoin(DocumentPage, DocumentPage.id == ExtractedField.page_id)
        .filter(
            or_(
                ExtractedField.field_name.ilike(pattern),
                ExtractedField.field_value.ilike(pattern),
                ExtractedField.original_value.ilike(pattern),
                ExtractedField.corrected_value.ilike(pattern),
            )
        )
        .all()
    )
    for field, document, page in field_rows:
        value = (
            field.corrected_value
            or field.field_value
            or field.original_value
            or field.field_name
        )
        page_number = page.page_number if page is not None else None
        key = (document.id, field.id, "field_value")
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            SearchHit(
                document_id=document.id,
                document_name=document.original_filename,
                page_number=page_number,
                snippet=make_snippet(value or "", term),
                match_field="field_value",
                score=_occurrence_score(value, term, 0.6),
            )
        )

    hits.sort(key=lambda hit: hit.score or 0, reverse=True)
    total = len(hits)
    page_hits = hits[skip : skip + limit]
    return SearchResponse(items=page_hits, total=total, skip=skip, limit=limit)


def hybrid_search(
    db: Session,
    query: str,
    skip: int = 0,
    limit: int = 20,
    min_score: float = 0.2,
) -> SearchResponse:
    """Keyword matches first, then semantic matches that keyword search missed."""
    keyword_result = keyword_search(db, query, skip=0, limit=10_000)
    merged: list[SearchHit] = list(keyword_result.items)
    seen: set[tuple] = {
        (hit.document_id, hit.page_number, hit.match_field)
        for hit in merged
    }

    try:
        from app.services.embedding_service import semantic_search

        semantic_result = semantic_search(
            db,
            query,
            top_k=max(limit * 2, 10),
            min_score=min_score,
        )
    except Exception:
        logger.exception(
            "Semantic search failed; returning keyword results only"
        )
        semantic_result = None

    if semantic_result is not None:
        for hit in semantic_result.items:
            already_on_page = any(
                existing.document_id == hit.document_id
                and existing.page_number == hit.page_number
                and existing.match_field != "semantic"
                for existing in merged
            )
            key = (hit.document_id, hit.page_number, "semantic")
            if already_on_page or key in seen:
                continue
            seen.add(key)
            merged.append(
                SearchHit(
                    document_id=hit.document_id,
                    document_name=hit.document_name,
                    page_number=hit.page_number,
                    snippet=hit.snippet,
                    match_field="semantic",
                    score=hit.similarity,
                )
            )

    merged.sort(
        key=lambda hit: (
            0 if hit.match_field != "semantic" else 1,
            -(hit.score or 0),
        )
    )
    total = len(merged)
    return SearchResponse(
        items=merged[skip : skip + limit],
        total=total,
        skip=skip,
        limit=limit,
    )


def semantic_matching_document_ids(
    db: Session,
    query: str,
    min_score: float = 0.2,
) -> list[int]:
    try:
        from app.services.embedding_service import semantic_search

        result = semantic_search(db, query, min_score=min_score)
        ids: list[int] = []
        seen: set[int] = set()
        for hit in result.items:
            if hit.document_id not in seen:
                seen.add(hit.document_id)
                ids.append(hit.document_id)
        return ids
    except Exception:
        logger.exception(
            "Semantic document-id lookup failed; using keyword filter only"
        )
        return []
