import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.models.embedding import EmbeddingIndex
from app.services.embedding_service import index_document_embeddings
from tests.helpers import use_test_embeddings

client = TestClient(app)


def test_semantic_search_api_english_and_hindi(tmp_path):
    use_test_embeddings(tmp_path)
    db = SessionLocal()
    document = Document(
        original_filename="mixed_language.pdf",
        stored_filename="mixed_language.pdf",
        file_path="uploads/mixed_language.pdf",
        file_type="application/pdf",
        status="indexed",
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
                extracted_text=(
                    "This invoice is pending payment. "
                    "यह चालान अभी तक भुगतान नहीं हुआ है।"
                ),
            )
        )
        db.commit()
        index_document_embeddings(db, document.id)

        english = client.get(
            "/api/search/semantic",
            params={"q": "pending invoice payment", "min_score": 0.0},
        )
        assert english.status_code == 200
        english_ids = [item["document_id"] for item in english.json()["items"]]
        assert document.id in english_ids

        hindi = client.get(
            "/api/search/semantic",
            params={"q": "चालान भुगतान", "min_score": 0.0},
        )
        assert hindi.status_code == 200
        hindi_ids = [item["document_id"] for item in hindi.json()["items"]]
        assert document.id in hindi_ids
    finally:
        db.query(EmbeddingIndex).filter(
            EmbeddingIndex.document_id == document.id
        ).delete()
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()
        from app.services.embedding_service import reset_chroma_client, set_encode_override

        set_encode_override(None)
        reset_chroma_client()


def test_keyword_search_api():
    db = SessionLocal()
    document = Document(
        original_filename="receipt.txt",
        stored_filename="receipt.txt",
        file_path="uploads/receipt.txt",
        file_type="text/plain",
        status="completed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        db.add(
            OCRResult(
                document_id=document.id,
                extracted_text="Grocery receipt banana milk bread",
            )
        )
        db.commit()

        response = client.get("/api/search", params={"q": "banana"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] >= 1
        assert any(item["document_id"] == document.id for item in payload["items"])

        hybrid = client.get(
            "/api/search",
            params={"q": "banana", "mode": "hybrid", "min_score": 0.0},
        )
        assert hybrid.status_code == 200
        assert any(
            item["document_id"] == document.id
            for item in hybrid.json()["items"]
        )
    finally:
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()
