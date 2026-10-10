import logging
import re
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.models.document import Document, DocumentPage, OCRResult
from app.models.embedding import EmbeddingIndex
from app.schemas.search import SemanticSearchHit, SemanticSearchResponse

logger = logging.getLogger(__name__)

COLLECTION_NAME = "doc_sentinel_chunks"

EncodeFn = Callable[[list[str]], list[list[float]]]

_model = None
_encode_override: EncodeFn | None = None
_chroma_client = None
_collection = None
_collection_path: str | None = None


import unicodedata

def normalize_multilingual_text(text: str) -> str:
    """Normalize Unicode to canonical NFKC form and standardize whitespace."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    return normalized.strip()


_CONTENT_CHAR_RE = re.compile(r"[\w\u0900-\u097F\uA8E0-\uA8FF]")


def has_meaningful_content(text: str) -> bool:
    """Return True if text contains at least 3 alphanumeric/Indic characters, or 2 Indic characters.

    Excludes empty, punctuation-only (e.g. '.'), or low-entropy noise without excluding
    legitimate short identifiers (e.g. 'INV-3337', 'ANZ', 'ID1').
    """
    if not text:
        return False
    chars = _CONTENT_CHAR_RE.findall(text)
    if len(chars) >= 3:
        return True
    if len(chars) >= 2 and any("\u0900" <= c <= "\u097F" or "\uA8E0" <= c <= "\uA8FF" for c in chars):
        return True
    return False


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """Chunk multilingual text respecting paragraph, sentence (English and Indic .!?।॥),

    and word boundaries with configurable overlap.
    """
    size = chunk_size or settings.CHUNK_SIZE
    overlap_size = overlap if overlap is not None else settings.CHUNK_OVERLAP
    source = normalize_multilingual_text(text)
    if not source:
        return []
    if len(source) <= size:
        return [source]

    break_patterns = [
        re.compile(r"\n\n+"),
        re.compile(r"[\.\?\!\u0964\u0965](\s+|$)"),
        re.compile(r"\n+"),
        re.compile(r"\s+"),
    ]

    chunks: list[str] = []
    start = 0
    search_window = max(20, min(size // 4, 120))

    while start < len(source):
        raw_end = min(len(source), start + size)
        end = raw_end

        if raw_end < len(source):
            lookback_start = max(start, raw_end - search_window)
            window_slice = source[lookback_start:raw_end]
            for pat in break_patterns:
                matches = list(pat.finditer(window_slice))
                if matches:
                    best_match = matches[-1]
                    end = lookback_start + best_match.end()
                    break

        chunk = source[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(source):
            break

        next_start = max(0, end - overlap_size)
        if next_start > 0 and next_start < end:
            space_pos = source.find(
                " ",
                next_start,
                min(end, next_start + min(overlap_size // 2, 15)),
            )
            if space_pos >= 0 and space_pos < end:
                next_start = space_pos + 1

        if next_start <= start:
            next_start = end
        start = next_start

    return chunks


def set_encode_override(fn: EncodeFn | None) -> None:
    global _encode_override
    _encode_override = fn


def reset_chroma_client() -> None:
    global _chroma_client, _collection, _collection_path
    _chroma_client = None
    _collection = None
    _collection_path = None


def persist_dir() -> str:
    path = Path(settings.CHROMA_PERSIST_DIR)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model %s", settings.EMBEDDING_MODEL)
        try:
            _model = SentenceTransformer(
                settings.EMBEDDING_MODEL,
                local_files_only=True,
            )
        except Exception:
            logger.info(
                "Embedding model not in local cache; downloading %s",
                settings.EMBEDDING_MODEL,
            )
            _model = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _model


def schedule_document_indexing(document_id: int) -> None:
    """Index embeddings after OCR without blocking the upload HTTP response."""
    import threading

    from app.db.session import SessionLocal
    from app.models.document import Document

    def _run() -> None:
        db = SessionLocal()
        try:
            count = index_document_embeddings(db, document_id)
            document = db.query(Document).filter(Document.id == document_id).first()
            if document is None:
                return
            if count and document.status not in ("needs_review", "failed"):
                document.status = "indexed"
                db.commit()
                logger.info(
                    "Indexed document %s (%s chunks)",
                    document_id,
                    count,
                )
        except Exception:
            logger.exception(
                "Background embedding index failed for document %s",
                document_id,
            )
        finally:
            db.close()

    threading.Thread(
        target=_run,
        name=f"index-doc-{document_id}",
        daemon=True,
    ).start()


def reindex_pending_documents() -> int:
    """Index documents that have OCR text but no embedding_index rows."""
    from sqlalchemy import exists

    from app.db.session import SessionLocal
    from app.models.document import Document, OCRResult

    db = SessionLocal()
    indexed = 0
    try:
        documents = (
            db.query(Document)
            .filter(
                exists().where(OCRResult.document_id == Document.id),
                ~exists().where(EmbeddingIndex.document_id == Document.id),
            )
            .all()
        )
        for document in documents:
            count = index_document_embeddings(db, document.id)
            if count:
                indexed += 1
                if document.status not in ("needs_review", "failed"):
                    document.status = "indexed"
                    db.commit()
                logger.info(
                    "Backfilled embeddings for document %s (%s chunks)",
                    document.id,
                    count,
                )
    except Exception:
        logger.exception("Pending document reindex failed")
    finally:
        db.close()
    return indexed


def reindex_all_documents(db: Session, force: bool = False) -> int:
    """Reindex embeddings for all documents in the database with OCR text.

    If force is True, purges existing embeddings and re-embeds from scratch.
    """
    from sqlalchemy import exists
    from app.models.document import Document, OCRResult

    documents = (
        db.query(Document)
        .filter(exists().where(OCRResult.document_id == Document.id))
        .all()
    )
    indexed = 0
    for document in documents:
        try:
            if force:
                delete_document_embeddings(db, document.id)
            count = index_document_embeddings(db, document.id)
            if count:
                indexed += 1
                if document.status not in ("needs_review", "failed"):
                    document.status = "indexed"
                    db.commit()
                logger.info(
                    "Reindexed document %s (%s chunks)",
                    document.id,
                    count,
                )
        except Exception:
            logger.exception("Failed to reindex document %s", document.id)
            db.rollback()

    return indexed


def encode_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    cleaned = [normalize_multilingual_text(t) for t in texts]
    if _encode_override is not None:
        return _encode_override(cleaned)
    embeddings = _get_model().encode(cleaned, normalize_embeddings=True)
    return embeddings.tolist()


def get_collection_name(model_name: str | None = None) -> str:
    """Return model-specific collection name to avoid mixing incompatible vectors."""
    model = model_name or settings.EMBEDDING_MODEL
    if "paraphrase-multilingual-MiniLM-L12-v2" in model:
        return COLLECTION_NAME
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", model).strip("_")
    return f"{COLLECTION_NAME}_{sanitized[:40]}"


def get_collection():
    global _chroma_client, _collection, _collection_path
    path = persist_dir()
    col_name = get_collection_name()
    if _collection is None or _collection_path != path:
        import chromadb

        _chroma_client = chromadb.PersistentClient(path=path)
        _collection = _chroma_client.get_or_create_collection(
            name=col_name,
            metadata={"hnsw:space": "cosine", "embedding_model": settings.EMBEDDING_MODEL},
        )
        _collection_path = path

        # Check existing collection metadata for incompatible model warning
        existing_meta = getattr(_collection, "metadata", None) or {}
        existing_model = existing_meta.get("embedding_model")
        if existing_model and existing_model != settings.EMBEDDING_MODEL:
            logger.warning(
                "ChromaDB collection '%s' was embedded with '%s', but active model is '%s'. "
                "Trigger reindexing to prevent mixing incompatible vector spaces.",
                col_name,
                existing_model,
                settings.EMBEDDING_MODEL,
            )
    return _collection


def _vector_id(document_id: int, page_number: int, chunk_index: int) -> str:
    return f"doc-{document_id}-p{page_number}-c{chunk_index}"


def delete_document_embeddings(db: Session, document_id: int) -> None:
    rows = (
        db.query(EmbeddingIndex)
        .filter(EmbeddingIndex.document_id == document_id)
        .all()
    )
    refs = [row.vector_db_ref for row in rows]
    if refs:
        try:
            get_collection().delete(ids=refs)
        except Exception:
            logger.exception(
                "Failed to delete Chroma vectors for document %s",
                document_id,
            )
    (
        db.query(EmbeddingIndex)
        .filter(EmbeddingIndex.document_id == document_id)
        .delete(synchronize_session=False)
    )


def index_document_embeddings(db: Session, document_id: int) -> int:
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        return 0

    ocr_rows = (
        db.query(OCRResult, DocumentPage)
        .outerjoin(DocumentPage, DocumentPage.id == OCRResult.page_id)
        .filter(OCRResult.document_id == document_id)
        .all()
    )

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    index_rows: list[EmbeddingIndex] = []

    for ocr, page in ocr_rows:
        page_number = page.page_number if page is not None else 1
        chunks = chunk_text(ocr.extracted_text or "")
        for chunk_index, chunk in enumerate(chunks):
            if not has_meaningful_content(chunk):
                continue
            vector_id = _vector_id(document_id, page_number, chunk_index)
            ids.append(vector_id)
            documents.append(chunk)
            metadatas.append(
                {
                    "document_id": document_id,
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                    "filename": document.original_filename,
                    "text_preview": chunk[:200],
                }
            )
            index_rows.append(
                EmbeddingIndex(
                    document_id=document_id,
                    page_number=page_number,
                    chunk_index=chunk_index,
                    vector_db_ref=vector_id,
                )
            )

    delete_document_embeddings(db, document_id)

    if not ids:
        db.commit()
        return 0

    embeddings = encode_texts(documents)
    get_collection().upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )
    db.add_all(index_rows)
    db.commit()
    return len(ids)


def is_document_viewable(db: Session, document: Document, user: Any) -> bool:
    """Enforce complete RBAC and folder permission checks for document viewing."""
    if user is None:
        return False
    from app.core.rbac import can_view_document
    if not can_view_document(user, document):
        return False
    try:
        from app.services.admin_service import check_folder_permission

        folder = getattr(document, "document_type", None) or getattr(document, "department", None)
        if folder and not check_folder_permission(db, folder, user.role, action="view"):
            return False
    except Exception:
        logger.exception(
            "Error checking folder permission for document %s",
            getattr(document, "id", None),
        )
    return True


def semantic_search(
    db: Session,
    query: str,
    top_k: int | None = None,
    min_score: float = 0.2,
    user: Any | None = None,
    aggregate_by_document: bool = True,
) -> SemanticSearchResponse:
    term = normalize_multilingual_text(query or "")
    if not term:
        return SemanticSearchResponse(items=[], total=0, query=term)

    n_results = top_k or settings.SEMANTIC_TOP_K
    collection = get_collection()
    if collection.count() == 0:
        logger.warning(
            "Semantic search skipped: vector index is empty for query %r",
            term,
        )
        return SemanticSearchResponse(items=[], total=0, query=term)

    try:
        query_embedding = encode_texts([term])[0]
    except Exception:
        logger.exception("Failed to encode query %r", term)
        return SemanticSearchResponse(items=[], total=0, query=term)

    # Fetch wider candidate pool so valid documents are not crowded out before filtering
    fetch_k = min(max(n_results * 5, 50), max(collection.count(), 1))
    try:
        raw = collection.query(
            query_embeddings=[query_embedding],
            n_results=fetch_k,
            include=["metadatas", "distances", "documents"],
        )
    except Exception:
        logger.exception("Chroma vector query failed for query %r", term)
        return SemanticSearchResponse(items=[], total=0, query=term)

    documents_out = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]

    hits: list[SemanticSearchHit] = []
    seen_document_ids: set[int] = set()

    for snippet, metadata, distance in zip(documents_out, metadatas, distances):
        similarity = max(0.0, min(1.0, 1.0 - float(distance)))
        if similarity < min_score:
            continue

        raw_snippet = snippet or str((metadata or {}).get("text_preview") or "")
        # Filter out empty or punctuation-only OCR artifacts without excluding short identifiers
        if not has_meaningful_content(raw_snippet):
            continue

        meta = metadata or {}
        document_id = int(meta.get("document_id", 0))
        if not document_id:
            continue

        # If aggregate_by_document is True, retain only the best (first) chunk per document
        if aggregate_by_document and document_id in seen_document_ids:
            continue

        document = db.query(Document).filter(Document.id == document_id).first()
        if document is None:
            continue

        # RBAC and folder permission check
        if user is not None and not is_document_viewable(db, document, user):
            continue

        seen_document_ids.add(document_id)
        document_name = (
            document.original_filename
            if document is not None
            else str(meta.get("filename") or "")
        )
        hits.append(
            SemanticSearchHit(
                document_id=document_id,
                document_name=document_name,
                page_number=int(meta.get("page_number") or 1),
                chunk_index=int(meta.get("chunk_index") or 0),
                snippet=raw_snippet,
                similarity=round(similarity, 4),
                match_type="semantic",
            )
        )
        if len(hits) >= n_results:
            break

    try:
        related = related_terms_in_texts(
            term,
            [f"{hit.document_name} {hit.snippet}" for hit in hits],
        )
        for hit, terms in zip(hits, related):
            hit.highlight_terms = terms
    except Exception:
        logger.exception("Could not compute related highlight terms")

    if aggregate_by_document:
        from app.services.search_service import make_snippet

        for hit in hits:
            hit.snippet = make_snippet(
                hit.snippet,
                term,
                extra_terms=hit.highlight_terms,
            )

    return SemanticSearchResponse(items=hits, total=len(hits), query=term)


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]+|[\u0900-\u097F\uA8E0-\uA8FF]+|\d+")
_STOPWORDS = {
    # English stopwords
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "at", "to", "for",
    "of", "as", "by", "is", "are", "was", "were", "be", "been", "it", "its",
    "this", "that", "these", "those", "with", "from", "into", "over", "after",
    "before", "not", "no", "so", "than", "then", "too", "very", "can", "will",
    "just", "about", "up", "out", "also", "only", "all", "any", "each", "few",
    "more", "most", "other", "some", "such", "own", "same", "both", "year",
    "years", "day", "days", "time", "one", "two", "new", "old", "first",
    "last", "many", "much", "every", "within", "without", "using", "used",
    "made", "make", "get", "got", "has", "have", "had", "does", "did", "done",
    # Hindi/Marathi common functional particles
    "है", "हैं", "था", "थी", "थे", "का", "की", "के", "को", "में", "पर", "से",
    "और", "या", "यह", "वह", "इस", "उस", "एक", "होता", "होती", "होते",
    "आहे", "आहीत", "होता", "होती", "होते", "चे", "च्या", "ची", "चा", "मध्ये",
    "वरून", "आणि", "किंवा", "हे", "ती", "या", "त्या",
}


def _content_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(text or ""):
        token = match.group(0)
        key = token.lower()
        is_indic = any("\u0900" <= ch <= "\u097F" or "\uA8E0" <= ch <= "\uA8FF" for ch in key)
        min_len = 2 if is_indic else 4
        if len(key) < min_len or key in _STOPWORDS or key in seen or key.isdigit():
            continue
        seen.add(key)
        tokens.append(token)
    return tokens


def related_terms_in_texts(
    query: str,
    texts: list[str],
    min_sim: float = 0.58,
    max_terms: int = 2,
) -> list[list[str]]:
    """Return only high-confidence snippet words close in meaning to the query."""
    if not query.strip() or not texts:
        return [[] for _ in texts]

    candidates_per_text: list[list[str]] = []
    unique: list[str] = []
    unique_keys: set[str] = set()
    for text in texts:
        items = _content_tokens(text)
        candidates_per_text.append(items)
        for item in items:
            key = item.lower()
            if key not in unique_keys and len(unique) < 250:
                unique_keys.add(key)
                unique.append(item)

    if not unique:
        return [[] for _ in texts]

    try:
        vectors = encode_texts([query.strip(), *unique])
    except Exception:
        logger.exception("Failed to embed terms for search highlighting")
        return [[] for _ in texts]

    query_vec = vectors[0]
    scores: dict[str, float] = {}
    for term, vector in zip(unique, vectors[1:]):
        scores[term.lower()] = sum(a * b for a, b in zip(query_vec, vector))

    result: list[list[str]] = []
    for items in candidates_per_text:
        ranked = sorted(
            (
                (item, scores[item.lower()])
                for item in items
                if item.lower() in scores
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        if not ranked or ranked[0][1] < min_sim:
            result.append([])
            continue

        best = ranked[0][1]
        picked: list[str] = []
        seen: set[str] = set()
        for item, score in ranked:
            if score < min_sim or best - score > 0.04:
                break
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append(item)
            if len(picked) >= max_terms:
                break
        result.append(picked)
    return result
