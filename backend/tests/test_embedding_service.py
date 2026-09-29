import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.embedding_service import chunk_text, semantic_search
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
