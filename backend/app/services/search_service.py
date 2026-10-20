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

    if len(window) > radius:
        context = max(radius // 2, 1)
        start = max(line_start, index - context)
        end = min(line_end, max(match_end + context, start + radius))
        if end - start > radius:
            end = start + radius
            if end < match_end:
                start = max(line_start, match_end - radius)
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
    authorized_ids: list[int] | None = None,
) -> SearchResponse:
    term = (query or "").strip()
    if not term:
        return SearchResponse(items=[], total=0, skip=skip, limit=limit)
    if authorized_ids is not None and not authorized_ids:
        return SearchResponse(items=[], total=0, skip=skip, limit=limit)

    pattern = f"%{term}%"
    hits: list[SearchHit] = []
    seen: set[tuple] = set()

    filename_query = db.query(Document).filter(Document.original_filename.ilike(pattern))
    if authorized_ids is not None:
        filename_query = filename_query.filter(Document.id.in_(authorized_ids))
    filename_docs = filename_query.all()
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

    ocr_query = (
        db.query(OCRResult, Document, DocumentPage)
        .join(Document, Document.id == OCRResult.document_id)
        .outerjoin(DocumentPage, DocumentPage.id == OCRResult.page_id)
        .filter(OCRResult.extracted_text.ilike(pattern))
    )
    if authorized_ids is not None:
        ocr_query = ocr_query.filter(Document.id.in_(authorized_ids))
    ocr_rows = ocr_query.all()
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

    field_query = (
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
    )
    if authorized_ids is not None:
        field_query = field_query.filter(Document.id.in_(authorized_ids))
    field_rows = field_query.all()
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
    authorized_ids: list[int] | None = None,
) -> SearchResponse:
    """Fuse independent lexical and vector rankings with reciprocal rank fusion."""
    candidate_limit = min(max(limit * 5, 30), 200)
    keyword_result = keyword_search(
        db, query, skip=0, limit=candidate_limit, authorized_ids=authorized_ids
    )
    fused: dict[tuple[int, int | None, str], dict[str, object]] = {}

    def add(hit: SearchHit, rank: int, source: str) -> None:
        key = (hit.document_id, hit.page_number, " ".join((hit.snippet or "").lower().split()))
        entry = fused.setdefault(key, {"hit": hit, "rrf": 0.0, "sources": set()})
        entry["rrf"] = float(entry["rrf"]) + 1.0 / (60 + rank)
        entry["sources"].add(source)

    for rank, hit in enumerate(keyword_result.items, start=1):
        add(hit, rank, "lexical")

    try:
        from app.services.embedding_service import semantic_search

        semantic_result = semantic_search(
            db,
            query,
            top_k=candidate_limit,
            min_score=min_score,
            authorized_ids=authorized_ids,
        )
    except Exception:
        logger.exception(
            "Semantic search failed; returning keyword results only"
        )
        semantic_result = None

    if semantic_result is not None:
        for rank, hit in enumerate(semantic_result.items, start=1):
            add(
                SearchHit(
                    document_id=hit.document_id,
                    document_name=hit.document_name,
                    page_number=hit.page_number,
                    chunk_index=hit.chunk_index,
                    snippet=hit.snippet,
                    match_field="semantic",
                    score=None,
                    highlight_terms=hit.highlight_terms,
                ),
                rank,
                "semantic",
            )

    max_rrf = 2 / 61
    merged: list[SearchHit] = []
    for entry in fused.values():
        hit = entry["hit"]
        sources = entry["sources"]
        if sources == {"lexical", "semantic"}:
            hit.match_field = "hybrid"
        hit.score = round(float(entry["rrf"]) / max_rrf, 4)
        merged.append(hit)
    merged.sort(key=lambda hit: hit.score or 0, reverse=True)
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
