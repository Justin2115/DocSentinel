import logging
import re
from pathlib import Path
from typing import Callable

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


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    size = chunk_size or settings.CHUNK_SIZE
    overlap_size = overlap if overlap is not None else settings.CHUNK_OVERLAP
    source = (text or "").strip()
    if not source:
        return []
    if len(source) <= size:
        return [source]

    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", source):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?।])\s+", paragraph)
        for sentence in sentences:
            if len(sentence) <= size:
                units.append(sentence)
                continue
            words = sentence.split()
            fragment = ""
            for word in words:
                if fragment and len(fragment) + len(word) + 1 > size:
                    units.append(fragment)
                    fragment = ""
                if len(word) > size:
                    if fragment:
                        units.append(fragment)
                        fragment = ""
                    units.extend(word[i : i + size] for i in range(0, len(word), size))
                else:
                    fragment = f"{fragment} {word}".strip()
            if fragment:
                units.append(fragment)

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        overlap = current[-overlap_size:].lstrip() if overlap_size and current else ""
        if overlap:
            boundary = overlap.find(" ")
            overlap = overlap[boundary + 1 :] if boundary >= 0 else overlap
        current = f"{overlap}\n\n{unit}" if overlap else unit
        if len(current) > size:
            current = unit
    if current:
        chunks.append(current)
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
            if count and document.status not in ("needs_review", "completed_with_warnings", "failed"):
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
                if document.status not in ("needs_review", "completed_with_warnings", "failed"):
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


def encode_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if _encode_override is not None:
        return _encode_override(texts)
    embeddings = _get_model().encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def get_collection():
    global _chroma_client, _collection, _collection_path
    path = persist_dir()
    if _collection is None or _collection_path != path:
        import chromadb

        _chroma_client = chromadb.PersistentClient(path=path)
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        _collection_path = path
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

    owner_id = document.uploaded_by
    for ocr, page in ocr_rows:
        page_number = page.page_number if page is not None else 1
        chunks = chunk_text(ocr.extracted_text or "")
        try:
            from app.services.ocr_service import detect_language

            language = detect_language(ocr.extracted_text or "")[0]
        except Exception:
            language = "unknown"
        for chunk_index, chunk in enumerate(chunks):
            vector_id = _vector_id(document_id, page_number, chunk_index)
            ids.append(vector_id)
            documents.append(chunk)
            metadatas.append(
                {
                    "document_id": document_id,
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                    "filename": document.original_filename,
                    "title": document.original_filename,
                    "owner_id": owner_id if owner_id is not None else -1,
                    "department": document.department or "",
                    "document_type": document.document_type or "",
                    "content_type": "page_text",
                    "language": language,
                    "chunking_version": "paragraph-v2",
                    "index_version": settings.EMBEDDING_MODEL,
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

    if not ids:
        old_refs = [row.vector_db_ref for row in db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == document_id).all()]
        if old_refs:
            try:
                get_collection().delete(ids=old_refs)
            except Exception:
                logger.exception("Could not remove old vectors for empty document %s", document_id)
        db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == document_id).delete(synchronize_session=False)
        db.commit()
        return 0

    embeddings = encode_texts(documents)
    collection = get_collection()
    old_refs = [row.vector_db_ref for row in db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == document_id).all()]
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )
    stale_refs = [ref for ref in old_refs if ref not in set(ids)]
    if stale_refs:
        collection.delete(ids=stale_refs)
    db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == document_id).delete(synchronize_session=False)
    db.add_all(index_rows)
    db.commit()
    return len(ids)


def semantic_search(
    db: Session,
    query: str,
    top_k: int | None = None,
    min_score: float = 0.2,
    authorized_ids: list[int] | None = None,
) -> SemanticSearchResponse:
    term = (query or "").strip()
    if not term:
        return SemanticSearchResponse(items=[], total=0, query=term)
    if authorized_ids is not None and not authorized_ids:
        return SemanticSearchResponse(items=[], total=0, query=term)

    n_results = top_k or settings.SEMANTIC_TOP_K
    collection = get_collection()
    if collection.count() == 0:
        logger.warning(
            "Semantic search skipped: vector index is empty for query %r",
            term,
        )
        return SemanticSearchResponse(items=[], total=0, query=term)

    query_embedding = encode_texts([term])[0]
    query_args = {}
    if authorized_ids is not None:
        query_args["where"] = {"document_id": {"$in": authorized_ids}}
    raw = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, max(collection.count(), 1)),
        include=["metadatas", "distances", "documents"],
        **query_args,
    )

    documents_out = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]

    hits: list[SemanticSearchHit] = []
    authorized_set = set(authorized_ids) if authorized_ids is not None else None
    for snippet, metadata, distance in zip(documents_out, metadatas, distances):
        similarity = max(0.0, min(1.0, 1.0 - float(distance)))
        if similarity < min_score:
            continue
        meta = metadata or {}
        document_id = int(meta.get("document_id", 0))
        if authorized_set is not None and document_id not in authorized_set:
            continue
        document = db.query(Document).filter(Document.id == document_id).first()
        if document is None:
            continue
        document_name = document.original_filename
        hits.append(
            SemanticSearchHit(
                document_id=document_id,
                document_name=document_name,
                page_number=int(meta.get("page_number") or 1),
                chunk_index=int(meta.get("chunk_index") or 0),
                snippet=snippet or str(meta.get("text_preview") or ""),
                similarity=round(similarity, 4),
                match_type="semantic",
            )
        )

    try:
        related = related_terms_in_texts(
            term,
            [f"{hit.document_name} {hit.snippet}" for hit in hits],
        )
        for hit, terms in zip(hits, related):
            hit.highlight_terms = terms
    except Exception:
        logger.exception("Could not compute related highlight terms")

    from app.services.search_service import make_snippet

    for hit in hits:
        hit.snippet = make_snippet(
            hit.snippet,
            term,
            extra_terms=hit.highlight_terms,
        )

    return SemanticSearchResponse(items=hits, total=len(hits), query=term)


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]+|[\u0900-\u097F]+|\d+")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "at", "to", "for",
    "of", "as", "by", "is", "are", "was", "were", "be", "been", "it", "its",
    "this", "that", "these", "those", "with", "from", "into", "over", "after",
    "before", "not", "no", "so", "than", "then", "too", "very", "can", "will",
    "just", "about", "up", "out", "also", "only", "all", "any", "each", "few",
    "more", "most", "other", "some", "such", "own", "same", "both", "year",
    "years", "day", "days", "time", "one", "two", "new", "old", "first",
    "last", "many", "much", "every", "within", "without", "using", "used",
    "made", "make", "get", "got", "has", "have", "had", "does", "did", "done",
}


def _content_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(text or ""):
        token = match.group(0)
        key = token.lower()
        if len(key) < 4 or key in _STOPWORDS or key in seen or key.isdigit():
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
