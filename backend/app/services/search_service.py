import logging
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentPage, ExtractedField, OCRResult
from app.schemas.search import SearchHit, SearchResponse

logger = logging.getLogger(__name__)

SNIPPET_RADIUS = 120
_TOKEN_SPLIT = re.compile(r"[^\w\u0900-\u097F]+", re.UNICODE)


def _snippet_needles(query: str, extra_terms: list[str] | None = None) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        token = (value or "").strip()
        key = token.lower()
        if len(token) < 2 or key in seen:
            return
        seen.add(key)
        ordered.append(token)

    add(query)
    for part in _TOKEN_SPLIT.split(query or ""):
        add(part)
    for term in extra_terms or []:
        add(term)
    return ordered


def _earliest_match(source: str, needles: list[str]) -> tuple[int, int]:
    haystack = source.lower()
    best_index = -1
    best_length = 0
    for needle in needles:
        index = haystack.find(needle.lower())
        if index < 0:
            continue
        if best_index < 0 or index < best_index:
            best_index = index
            best_length = len(needle)
    return best_index, best_length


def _line_bounds(source: str, index: int, match_end: int) -> tuple[int, int]:
    line_start = source.rfind("\n", 0, index) + 1
    newline_at_end = source.find("\n", match_end)
    line_end = len(source) if newline_at_end < 0 else newline_at_end

    if line_start > 0:
        previous_break = source.rfind("\n", 0, line_start - 1)
        line_start = 0 if previous_break < 0 else previous_break + 1

    if line_end < len(source):
        next_break = source.find("\n", line_end + 1)
        line_end = len(source) if next_break < 0 else next_break

    return line_start, line_end


def make_snippet(
    text: str,
    query: str,
    radius: int = SNIPPET_RADIUS,
    extra_terms: list[str] | None = None,
) -> str:
    """Keep the matched line(s) in view instead of a long document tail."""
    source = text or ""
    if not source:
        return ""

    needles = _snippet_needles(query, extra_terms)
    query_needles = _snippet_needles(query)
    index, match_len = _earliest_match(source, query_needles)
    if index < 0:
        extra_only = [
            term for term in needles if term.lower() not in {item.lower() for item in query_needles}
        ]
        index, match_len = _earliest_match(source, extra_only)

    if index < 0:
        snippet = source[: radius * 2].strip()
        return snippet + ("…" if len(source) > len(snippet) else "")

    match_end = index + max(match_len, 1)
    line_start, line_end = _line_bounds(source, index, match_end)
    window = source[line_start:line_end].strip()

    if len(window) > radius * 3:
        start = max(0, index - radius)
        end = min(len(source), match_end + radius)
        window = source[start:end].strip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(source) else ""
        return f"{prefix}{window}{suffix}"

    prefix = "…" if line_start > 0 else ""
    suffix = "…" if line_end < len(source) else ""
    return f"{prefix}{window}{suffix}"


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
    _attach_related_highlights(term, page_hits)
    return SearchResponse(items=page_hits, total=total, skip=skip, limit=limit)


def _attach_related_highlights(query: str, hits: list[SearchHit]) -> None:
    if not hits:
        return
    try:
        from app.services.embedding_service import related_terms_in_texts

        related = related_terms_in_texts(
            query,
            [f"{hit.document_name} {hit.snippet}" for hit in hits],
        )
        for hit, terms in zip(hits, related):
            hit.highlight_terms = terms
    except Exception:
        logger.exception("Could not compute related highlight terms")


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
                    highlight_terms=hit.highlight_terms,
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
