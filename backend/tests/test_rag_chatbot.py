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
from app.services.rag_service import answer_question, _extract_single_answer
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


def test_rag_chatbot_hindi_invoice_qa(tmp_path):
    """Verify that Hindi invoice questions retrieve grounded factual answers in both Hindi and English."""
    from tests.helpers import use_test_embeddings
    use_test_embeddings(tmp_path, multilingual=True)

    db = SessionLocal()
    doc_hi = Document(
        original_filename="hindi_invoice.pdf",
        stored_filename="hindi_invoice.pdf",
        file_path="uploads/hindi_invoice.pdf",
        file_type="application/pdf",
        status="completed",
        department="Finance",
    )
    db.add(doc_hi)
    db.commit()
    db.refresh(doc_hi)

    page = DocumentPage(document_id=doc_hi.id, page_number=1)
    db.add(page)
    db.commit()
    db.refresh(page)

    ocr = OCRResult(
        document_id=doc_hi.id,
        page_id=page.id,
        extracted_text="चालान संख्या: INV-9988\nदिनांक: 15/08/2026\nएकूण देयक रक्कम: ₹4,500\nविलंब शुल्क: 5%\n",
    )
    db.add(ocr)
    db.commit()

    index_document_embeddings(db, doc_hi.id)

    try:
        # Hindi question about invoice number
        resp_inv = answer_question(db, ChatQueryRequest(question="चालान संख्या क्या है?"))
        assert resp_inv.has_evidence is True
        assert "INV-9988" in resp_inv.answer

        # Hindi question about amount
        resp_amt = answer_question(db, ChatQueryRequest(question="कुल देय राशि कितनी है?"))
        assert resp_amt.has_evidence is True
        assert "4,500" in resp_amt.answer

        # English cross-lingual question about invoice number
        resp_en = answer_question(db, ChatQueryRequest(question="What is the invoice number?"))
        assert resp_en.has_evidence is True
        assert "INV-9988" in resp_en.answer
    finally:
        from app.services.embedding_service import delete_document_embeddings
        delete_document_embeddings(db, doc_hi.id)
        db.query(OCRResult).filter(OCRResult.document_id == doc_hi.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == doc_hi.id).delete()
        db.query(Document).filter(Document.id == doc_hi.id).delete()
        db.commit()
        db.close()


def test_rag_chatbot_marathi_bill_qa(tmp_path):
    """Verify that Marathi bill questions retrieve grounded factual answers in both Marathi and English."""
    from tests.helpers import use_test_embeddings
    use_test_embeddings(tmp_path, multilingual=True)

    db = SessionLocal()
    doc_mr = Document(
        original_filename="marathi_bill.pdf",
        stored_filename="marathi_bill.pdf",
        file_path="uploads/marathi_bill.pdf",
        file_type="application/pdf",
        status="completed",
        department="Finance",
    )
    db.add(doc_mr)
    db.commit()
    db.refresh(doc_mr)

    page = DocumentPage(document_id=doc_mr.id, page_number=1)
    db.add(page)
    db.commit()
    db.refresh(page)

    ocr = OCRResult(
        document_id=doc_mr.id,
        page_id=page.id,
        extracted_text="बिल क्रमांक: MH-7788\nअंतिम तारीख: 28/08/2026\nएकूण देयक रक्कम: ₹3,200\nविलंब दंड: 10%\n",
    )
    db.add(ocr)
    db.commit()

    index_document_embeddings(db, doc_mr.id)

    try:
        # Marathi question about bill number
        resp_num = answer_question(db, ChatQueryRequest(question="बिलाचा क्रमांक काय आहे?"))
        assert resp_num.has_evidence is True
        assert "MH-7788" in resp_num.answer

        # Marathi question about amount
        resp_amt = answer_question(db, ChatQueryRequest(question="एकूण रक्कम किती आहे?"))
        assert resp_amt.has_evidence is True
        assert "3,200" in resp_amt.answer

        # Marathi question about due date
        resp_date = answer_question(db, ChatQueryRequest(question="पैसे भरण्याची शेवटची तारीख कोणती?"))
        assert resp_date.has_evidence is True
        assert "28/08/2026" in resp_date.answer
    finally:
        from app.services.embedding_service import delete_document_embeddings
        delete_document_embeddings(db, doc_mr.id)
        db.query(OCRResult).filter(OCRResult.document_id == doc_mr.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == doc_mr.id).delete()
        db.query(Document).filter(Document.id == doc_mr.id).delete()
        db.commit()
        db.close()


def test_extract_single_answer_applies_boosts_per_sentence():
    """Regression: identifier/date/currency boosts must run per sentence, not only the last sentence of a chunk.

    Before the fix, the identifier/late-fee/bank boost blocks and the
    best-score comparison were indented outside the per-sentence loop, so
    only the last sentence of each chunk received those boosts. A chunk whose
    answer-bearing sentence is not the last one could be scored incorrectly.
    """
    chunks = [
        {
            "document_id": 1,
            "document_name": "multi_sentence.pdf",
            "page_number": 1,
            "chunk_index": 0,
            "snippet": (
                "Invoice Number: INV-3337\n"
                "Some unrelated trailing sentence without identifiers.\n"
            ),
            "similarity": 0.9,
        }
    ]
    answer, best_chunk = _extract_single_answer("What is the invoice number?", chunks)
    assert "INV-3337" in answer
    assert best_chunk is not None
    assert best_chunk["document_name"] == "multi_sentence.pdf"


def test_extract_single_answer_amount_boost_with_nonmatching_item_token():
    """Regression: the generic amount-keyword boost must apply even when an
    item token (e.g. "customer") is present but does not match the sentence.

    Previously the amount-keyword branch was an ``elif`` attached to the outer
    ``if item_tokens:`` rather than the inner ``if matched_item:``.  For
    "How much does the customer owe?" the sentence "Total Due: $93.50" had no
    "customer" token, so the "total"/"owe" boost was skipped and the answer
    was rejected.
    """
    chunks = [
        {
            "document_id": 1,
            "document_name": "invoice.pdf",
            "page_number": 1,
            "chunk_index": 0,
            "snippet": (
                "Invoice Number: INV-3337\n"
                "Total Due: $93.50\n"
                "Late payment is subject to fees.\n"
            ),
            "similarity": 0.62,
        }
    ]
    answer, best_chunk = _extract_single_answer("How much does the customer owe?", chunks)
    assert answer
    assert "$93.50" in answer or "Total Due" in answer
    assert best_chunk is not None


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    """Keep these unit tests independent of a running Ollama server.

    Real-model coverage lives in test_ollama_e2e.py, which is skipped when the
    provider is not reachable.
    """
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "is_usable", lambda: False)


def test_sub_floor_similarity_alone_does_not_answer(
    test_invoice_document, monkeypatch
):
    """A weak hit on wording the page never uses must abstain.

    Ranking and similarity are recall aids. Answerability needs the calibrated raw
    floor or text that really matches the question, which is exactly the
    false-positive class the boosted-similarity gate used to let through.
    """
    from app.schemas.search import SemanticSearchHit, SemanticSearchResponse
    from app.services import embedding_service

    hit = SemanticSearchHit(
        document_id=test_invoice_document.id,
        document_name=test_invoice_document.original_filename,
        page_number=1,
        chunk_index=0,
        snippet="Total Due\n$93.50\nLate payment is subject to fees of 5% per month.",
        similarity=0.28,
        match_type="semantic",
    )
    monkeypatch.setattr(
        embedding_service,
        "semantic_search",
        lambda *a, **k: SemanticSearchResponse(items=[hit], total=1, query="q"),
    )
    db = SessionLocal()
    try:
        resp = answer_question(
            db,
            ChatQueryRequest(
                question="Which settlement arrangement covers the outstanding liability?"
            ),
        )
        assert resp.has_evidence is False
        assert "do not contain" in resp.answer
        assert resp.sources == []
    finally:
        db.close()


def test_raw_hit_above_floor_grounds_a_paraphrase(test_invoice_document, monkeypatch):
    """Control: the same question is answered once raw similarity clears the floor."""
    from app.schemas.search import SemanticSearchHit, SemanticSearchResponse
    from app.services import embedding_service

    hit = SemanticSearchHit(
        document_id=test_invoice_document.id,
        document_name=test_invoice_document.original_filename,
        page_number=1,
        chunk_index=0,
        snippet="Total Due\n$93.50\nLate payment is subject to fees of 5% per month.",
        similarity=0.35,
        match_type="semantic",
    )
    monkeypatch.setattr(
        embedding_service,
        "semantic_search",
        lambda *a, **k: SemanticSearchResponse(items=[hit], total=1, query="q"),
    )
    db = SessionLocal()
    try:
        resp = answer_question(
            db,
            ChatQueryRequest(
                question="Which settlement arrangement covers the outstanding liability?"
            ),
        )
        assert resp.has_evidence is True
        assert any(
            s.document_id == test_invoice_document.id for s in resp.sources
        )
        assert resp.sources[0].support.startswith("semantic")
    finally:
        db.close()


def test_exact_line_wording_grounds_an_amount_without_similarity(
    test_invoice_document, monkeypatch
):
    """Damaged or absent embeddings must not hide text the question quotes exactly.

    Retrieval returns nothing semantic here, yet "total due" appears in one OCR
    line next to its amount, so the lexical signal answers it and the citation
    says which signal supported the answer.
    """
    from app.schemas.search import SemanticSearchResponse
    from app.services import embedding_service

    monkeypatch.setattr(
        embedding_service,
        "semantic_search",
        lambda *a, **k: SemanticSearchResponse(items=[], total=0, query="q"),
    )
    db = SessionLocal()
    try:
        resp = answer_question(
            db, ChatQueryRequest(question="What is the total due amount?")
        )
        assert resp.has_evidence is True
        assert "$93.50" in resp.answer
        # Nothing scored this passage in the vector store, so there is no
        # similarity to report; 0.0 would claim a measurement instead.
        assert resp.sources[0].similarity is None
        assert resp.sources[0].support.startswith("lexical")
    finally:
        db.close()



def test_numbers_supported_by_evidence_guards_fabrication():
    from app.services.rag_service import _numbers_supported_by_evidence

    chunks = [{"snippet": "Total Due $93.50; Road laying 120 at rate 1,250 = 150000"}]
    assert _numbers_supported_by_evidence("The total is $93.50.", chunks)
    assert _numbers_supported_by_evidence("Amount 150000 split at 1250 each.", chunks)
    assert _numbers_supported_by_evidence("There is no number here.", chunks)
    assert not _numbers_supported_by_evidence("The total is $94.50.", chunks)
    assert not _numbers_supported_by_evidence("Rate was 125000 per unit.", chunks)


def test_a_number_must_share_its_evidence_line_with_the_word_beside_it():
    """A page holding "MH" and "198" on separate lines does not state "MH 198"."""
    from app.services.rag_service import _numbers_supported_by_evidence

    scrambled = [{"snippet": "Vehicle:\nMH\n198 bags.\nNet 14320 kg"}]

    assert not _numbers_supported_by_evidence("The vehicle used was MH 198.", scrambled)
    # The same figure beside the wording of its own line is evidence, not invention.
    assert _numbers_supported_by_evidence("Bags: 198", scrambled)
    # A neighbour the model chose itself (not borrowed from the page) binds nothing.
    assert _numbers_supported_by_evidence("Total weight 14320 kilograms.", scrambled)


def test_rag_chatbot_lowercase_identifier_retrieval(test_invoice_document):
    """Regression: lowercase hyphenated identifiers (e.g. inv-3337) must trigger keyword-assisted retrieval.

    The key_phrases extraction previously only matched uppercase identifiers
    (INV-3337) and CamelCase phrases, so a user asking with a lowercase
    identifier would miss the exact-match keyword path.
    """
    db = SessionLocal()
    try:
        req = ChatQueryRequest(question="What is the total for inv-3337?")
        resp = answer_question(db, req)
        assert resp.has_evidence is True
        assert "$93.50" in resp.answer or "Total Due" in resp.answer
    finally:
        db.close()
