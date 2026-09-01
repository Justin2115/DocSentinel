import logging
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

    chunks: list[str] = []
    start = 0
    while start < len(source):
        end = min(len(source), start + size)
        chunk = source[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(source):
            break
        start = max(0, end - overlap_size)
        if start >= end:
            start = end
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

    for ocr, page in ocr_rows:
        page_number = page.page_number if page is not None else 1
        chunks = chunk_text(ocr.extracted_text or "")
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


def semantic_search(
    db: Session,
    query: str,
    top_k: int | None = None,
    min_score: float = 0.2,
) -> SemanticSearchResponse:
    term = (query or "").strip()
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

    query_embedding = encode_texts([term])[0]
    raw = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, max(collection.count(), 1)),
        include=["metadatas", "distances", "documents"],
    )

    documents_out = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]

    hits: list[SemanticSearchHit] = []
    for snippet, metadata, distance in zip(documents_out, metadatas, distances):
        similarity = max(0.0, min(1.0, 1.0 - float(distance)))
        if similarity < min_score:
            continue
        meta = metadata or {}
        document_id = int(meta.get("document_id", 0))
        document = db.query(Document).filter(Document.id == document_id).first()
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
                snippet=snippet or str(meta.get("text_preview") or ""),
                similarity=round(similarity, 4),
                match_type="semantic",
            )
        )

    return SemanticSearchResponse(items=hits, total=len(hits), query=term)
