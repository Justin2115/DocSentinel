import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.embedding_service import (
    chunk_text,
    is_numeric_only_query,
    reindex_pending_documents,
    semantic_search,
)
from app.db.session import SessionLocal
from app.models.document import Document, DocumentPage, OCRResult
from app.models.embedding import EmbeddingIndex
from app.services.embedding_service import index_document_embeddings
from tests.helpers import dummy_encode, use_test_embeddings


def test_chunk_text_splits_with_overlap():
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=512, overlap=64)
    assert len(chunks) >= 2
    assert chunks[0] == "a" * 512
    assert chunks[1].startswith("a" * 64)


def test_chunk_text_skips_blank():
    assert chunk_text("   ") == []
    assert chunk_text("short") == ["short"]


def test_index_and_query_round_trip(tmp_path):
    use_test_embeddings(tmp_path)
    db = SessionLocal()
    document = Document(
        original_filename="contract.pdf",
        stored_filename="contract.pdf",
        file_path="uploads/contract.pdf",
        file_type="application/pdf",
        status="completed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        page = DocumentPage(document_id=document.id, page_number=1)
        db.add(page)
        db.flush()
        db.add(
            OCRResult(
                document_id=document.id,
                page_id=page.id,
                extracted_text="The vendor must submit the unpaid invoice before Friday.",
            )
        )
        db.commit()

        count = index_document_embeddings(db, document.id)
        assert count >= 1
        rows = (
            db.query(EmbeddingIndex)
            .filter(EmbeddingIndex.document_id == document.id)
            .all()
        )
        assert rows

        result = semantic_search(db, "unpaid invoice", top_k=5, min_score=0.0)
        assert result.total >= 1
        assert result.items[0].document_id == document.id
        assert "invoice" in result.items[0].snippet.lower()
    finally:
        db.query(EmbeddingIndex).filter(
            EmbeddingIndex.document_id == document.id
        ).delete()
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()
        from app.services.embedding_service import set_encode_override, reset_chroma_client

        set_encode_override(None)
        reset_chroma_client()


def test_dummy_encode_is_deterministic():
    first = dummy_encode(["hello world"])
    second = dummy_encode(["hello world"])
    assert first == second
    assert len(first[0]) == 32


def test_reindex_pending_documents_reindexes_when_collection_empty(tmp_path):
    """Regression: reindex_pending_documents must reindex OCR-bearing documents when the active collection is empty.

    After an embedding-model switch the model-specific collection is empty
    while documents still carry stale EmbeddingIndex rows from the previous
    model. reindex_pending_documents must detect the empty collection and
    rebuild vectors for all OCR-bearing documents.
    """
    use_test_embeddings(tmp_path)
    db = SessionLocal()
    document = Document(
        original_filename="reindex_pending.pdf",
        stored_filename="reindex_pending.pdf",
        file_path="uploads/reindex_pending.pdf",
        file_type="application/pdf",
        status="completed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        page = DocumentPage(document_id=document.id, page_number=1)
        db.add(page)
        db.flush()
        db.add(
            OCRResult(
                document_id=document.id,
                page_id=page.id,
                extracted_text="The vendor must submit the unpaid invoice before Friday.",
            )
        )
        db.commit()

        # Index once so EmbeddingIndex rows exist (simulating a prior model's index).
        index_document_embeddings(db, document.id)

        # Wipe the vector collection to simulate a model switch that created
        # a new empty collection while stale EmbeddingIndex rows remain.
        from app.services.embedding_service import get_collection, delete_document_embeddings
        delete_document_embeddings(db, document.id)
        # Re-create stale rows to simulate the pre-switch state.
        from app.models.embedding import EmbeddingIndex
        db.add(EmbeddingIndex(
            document_id=document.id,
            page_number=1,
            chunk_index=0,
            vector_db_ref="doc-stale-ref",
        ))
        db.commit()

        assert get_collection().count() == 0

        reindexed = reindex_pending_documents()
        assert reindexed >= 1
        assert get_collection().count() >= 1
    finally:
        from app.services.embedding_service import delete_document_embeddings, reset_chroma_client, set_encode_override
        delete_document_embeddings(db, document.id)
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()
        set_encode_override(None)
        reset_chroma_client()


def test_is_numeric_only_query_detects_pure_numeric_input():
    """Numeric-only queries (digits, currency, punctuation) have no semantic
    meaning for embedding models and should be detected so semantic retrieval
    is skipped in favor of keyword search."""
    assert is_numeric_only_query("20") is True
    assert is_numeric_only_query("$1,200") is True
    assert is_numeric_only_query("12-31-2024") is True
    assert is_numeric_only_query("93.50") is True
    assert is_numeric_only_query("  42  ") is True
    # Queries with alphabetic characters must NOT be treated as numeric-only.
    assert is_numeric_only_query("invoice 20") is False
    assert is_numeric_only_query("self care") is False
    assert is_numeric_only_query("INV-3337") is False
    assert is_numeric_only_query("") is False


def test_semantic_search_returns_empty_for_numeric_only_query(tmp_path):
    """Semantic search must not return noisy false positives for a purely
    numeric query such as '20'.  Keyword search is the correct channel for
    exact numeric lookups."""
    use_test_embeddings(tmp_path)
    db = SessionLocal()
    document = Document(
        original_filename="numeric_noise.pdf",
        stored_filename="numeric_noise.pdf",
        file_path="uploads/numeric_noise.pdf",
        file_type="application/pdf",
        status="completed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        page = DocumentPage(document_id=document.id, page_number=1)
        db.add(page)
        db.flush()
        db.add(
            OCRResult(
                document_id=document.id,
                page_id=page.id,
                extracted_text="The meeting is on page 20 of the quarterly report.",
            )
        )
        db.commit()
        index_document_embeddings(db, document.id)

        result = semantic_search(db, "20", top_k=5, min_score=0.0)
        assert result.total == 0
    finally:
        from app.services.embedding_service import delete_document_embeddings, reset_chroma_client, set_encode_override
        delete_document_embeddings(db, document.id)
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()
        set_encode_override(None)
        reset_chroma_client()
