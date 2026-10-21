import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.models.embedding import EmbeddingIndex
from app.services.embedding_service import index_document_embeddings
from tests.helpers import get_test_auth_headers, use_test_embeddings

client = TestClient(app)


def test_search_api_requires_authentication():
    response = client.get("/api/search", params={"q": "invoice"})
    assert response.status_code == 401
    assert "Authentication required" in response.text

    semantic_response = client.get("/api/search/semantic", params={"q": "invoice"})
    assert semantic_response.status_code == 401


def test_semantic_search_api_english_and_hindi(tmp_path):
    use_test_embeddings(tmp_path, multilingual=True)
    db = SessionLocal()
    headers = get_test_auth_headers(db)
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
            headers=headers,
        )
        assert english.status_code == 200
        english_ids = [item["document_id"] for item in english.json()["items"]]
        assert document.id in english_ids

        hindi = client.get(
            "/api/search/semantic",
            params={"q": "चालान भुगतान", "min_score": 0.0},
            headers=headers,
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


def test_search_endpoints_use_calibrated_default_min_score(monkeypatch):
    """Omitting min_score must apply the calibrated floor, not an ad-hoc value."""
    import pytest

    from app.api import search as search_api
    from app.core.config import settings
    from app.schemas.search import SemanticSearchResponse

    captured: list = []

    def fake_semantic_search(db, query, top_k=None, min_score=None, **kwargs):
        captured.append(min_score)
        return SemanticSearchResponse(items=[], total=0, query=query)

    monkeypatch.setattr(search_api, "semantic_search", fake_semantic_search)
    db = SessionLocal()
    try:
        headers = get_test_auth_headers(db)
    finally:
        db.close()

    r1 = client.get(
        "/api/search", params={"q": "invoice", "mode": "semantic"}, headers=headers
    )
    assert r1.status_code == 200
    assert captured[-1] == pytest.approx(settings.SEMANTIC_MIN_SCORE)

    r2 = client.get("/api/search/semantic", params={"q": "invoice"}, headers=headers)
    assert r2.status_code == 200
    assert captured[-1] == pytest.approx(settings.SEMANTIC_MIN_SCORE)

    r3 = client.get(
        "/api/search/semantic",
        params={"q": "invoice", "min_score": "0.1"},
        headers=headers,
    )
    assert r3.status_code == 200
    assert captured[-1] == pytest.approx(0.1)


def test_calibrated_threshold_value_pinned():
    """The 0.30 floor came from labeled measurements (hard negatives maxed at
    0.252, relevant median 0.459). Pin it so an ad-hoc retune fails loudly."""
    from app.core.config import settings

    assert settings.SEMANTIC_MIN_SCORE == 0.30


def test_keyword_search_api():
    db = SessionLocal()
    headers = get_test_auth_headers(db)
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

        response = client.get(
            "/api/search",
            params={"q": "banana"},
            headers=headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] >= 1
        assert any(item["document_id"] == document.id for item in payload["items"])

        hybrid = client.get(
            "/api/search",
            params={"q": "banana", "mode": "hybrid", "min_score": 0.0},
            headers=headers,
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
