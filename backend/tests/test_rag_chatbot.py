import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.models.user import User
from app.schemas.query import ChatQueryRequest
from app.services.embedding_service import (
    has_meaningful_content,
    index_document_embeddings,
    semantic_search,
    set_encode_override,
)
from app.services.rag_service import answer_question
from app.services.search_service import keyword_search
from tests.helpers import get_test_auth_headers, mock_multilingual_encode


@pytest.fixture(autouse=True)
def setup_mock_encoder():
    """Use fast concept-based mock encoder for unit test isolation."""
    set_encode_override(mock_multilingual_encode)
    yield
    set_encode_override(None)


@pytest.fixture
def test_invoice_document():
    db = SessionLocal()
    # Create test document
    doc = Document(
        original_filename="sample_invoice_test.pdf",
        stored_filename="sample_invoice_test.pdf",
        file_path="uploads/sample_invoice_test.pdf",
        file_type="application/pdf",
        file_size=2048,
        status="completed",
        department="Finance",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    page1 = DocumentPage(document_id=doc.id, page_number=1)
    db.add(page1)
    db.commit()
    db.refresh(page1)

    ocr_text = (
        "Invoice Number\nINV-3337\n"
        "Due Date\nJanuary 31, 2016\n"
        "Total Due\n$93.50\n"
        "Late payment is subject to fees of 5% per month.\n"
        "ANZ Bank\nACC # 1234 1234\n"
    )
    ocr = OCRResult(document_id=doc.id, page_id=page1.id, extracted_text=ocr_text)
    db.add(ocr)
    db.commit()

    # Index embeddings
    index_document_embeddings(db, doc.id)

    yield doc

    # Cleanup
    from app.models.embedding import EmbeddingIndex
    from app.services.embedding_service import delete_document_embeddings

    delete_document_embeddings(db, doc.id)
    db.query(OCRResult).filter(OCRResult.document_id == doc.id).delete()
    db.query(DocumentPage).filter(DocumentPage.document_id == doc.id).delete()
    db.query(Document).filter(Document.id == doc.id).delete()
    db.commit()
    db.close()


def test_has_meaningful_content_filter():
    """Verify that punctuation-only or empty strings are excluded, but valid identifiers pass."""
    assert not has_meaningful_content("")
    assert not has_meaningful_content(".")
    assert not has_meaningful_content("...")
    assert not has_meaningful_content("   \n\r  ")
    assert not has_meaningful_content("- - -")
    # Legitimate short identifiers and words
    assert has_meaningful_content("INV-3337")
    assert has_meaningful_content("ANZ")
    assert has_meaningful_content("ID1")
    assert has_meaningful_content("Total Due")
    # Indic words
    assert has_meaningful_content("चलान")
    assert has_meaningful_content("देय")


def test_document_library_deduplication(test_invoice_document):
    """Verify that Document Library search returns at most 1 hit per document."""
    db = SessionLocal()
    try:
        # With aggregation (default for library search)
        res_agg = semantic_search(db, "invoice payment", top_k=10, aggregate_by_document=True)
        doc_ids = [h.document_id for h in res_agg.items if h.document_id == test_invoice_document.id]
        assert len(doc_ids) == 1, "Expected exactly 1 hit for the document when aggregated"

        # Check snippet and page number are present
        hit = next(h for h in res_agg.items if h.document_id == test_invoice_document.id)
        assert hit.page_number == 1
        assert "invoice" in hit.snippet.lower() or "due" in hit.snippet.lower()

        # Keyword search with aggregation
        kw_agg = keyword_search(db, "INV-3337", limit=10, aggregate_by_document=True)
        kw_doc_ids = [h.document_id for h in kw_agg.items if h.document_id == test_invoice_document.id]
        assert len(kw_doc_ids) == 1
    finally:
        db.close()


def test_rag_chatbot_single_part_question(test_invoice_document):
    """Verify that the RAG chatbot extracts grounded factual answers and cites source doc and page."""
    db = SessionLocal()
    try:
        req = ChatQueryRequest(question="How much does the customer owe?")
        resp = answer_question(db, req)
        assert resp.has_evidence is True
        assert resp.confidence > 0.0
        assert len(resp.sources) >= 1
        assert resp.sources[0].document_id == test_invoice_document.id
        assert resp.sources[0].page_number == 1
        assert "$93.50" in resp.answer or "Total Due" in resp.answer
        assert test_invoice_document.original_filename in resp.answer
    finally:
        db.close()


def test_rag_chatbot_multipart_question(test_invoice_document):
    """Verify that multi-part compound questions return answers for each part."""
    db = SessionLocal()
    try:
        req = ChatQueryRequest(question="What is the invoice number and when is the payment due?")
        resp = answer_question(db, req)
        assert resp.has_evidence is True
        assert len(resp.sources) >= 1
        # Checks that both the invoice ID and due date are answered
        assert "INV-3337" in resp.answer
        assert "January 31, 2016" in resp.answer or "Due Date" in resp.answer
    finally:
        db.close()


def test_rag_chatbot_missing_evidence():
    """Verify that out-of-domain or unsupported queries clearly state evidence is missing."""
    db = SessionLocal()
    try:
        req = ChatQueryRequest(question="What ingredients are needed to bake a chocolate cake?")
        resp = answer_question(db, req)
        assert resp.has_evidence is False
        assert resp.confidence == 0.0
        assert len(resp.sources) == 0
        assert "available authorized documents do not contain" in resp.answer
    finally:
        db.close()


def test_rag_chatbot_rbac_enforcement(test_invoice_document):
    """Verify that a user without access to a department's documents cannot retrieve answers from them."""
    db = SessionLocal()
    try:
        # User in HR department (doc is in Finance) with UPLOAD_CHECKER role
        unauth_user = User(
            name="HR Checker",
            email="hr_checker@example.com",
            role="UPLOAD_CHECKER",
            department="HR",
            is_active=True,
        )
        req = ChatQueryRequest(question="What is the invoice number?")
        resp = answer_question(db, req, user=unauth_user)
        # Should NOT return the finance document
        assert not any(s.document_id == test_invoice_document.id for s in resp.sources)
    finally:
        db.close()


def test_rag_chatbot_api_endpoint(test_invoice_document):
    """Verify the FastAPI POST /api/query/ask endpoint requires auth and returns ChatQueryResponse."""
    client = TestClient(app)

    # 1. Unauthenticated request should fail with 401
    unauth_resp = client.post("/api/query/ask", json={"question": "What is the invoice number?"})
    assert unauth_resp.status_code == 401

    # 2. Authenticated request with valid admin headers
    db = SessionLocal()
    try:
        headers = get_test_auth_headers(db, role="ADMIN")
        auth_resp = client.post(
            "/api/query/ask",
            json={"question": "What is the invoice number?"},
            headers=headers,
        )
        assert auth_resp.status_code == 200
        data = auth_resp.json()
        assert "answer" in data
        assert "sources" in data
        assert "has_evidence" in data
        assert "confidence" in data
    finally:
        db.close()
