from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models.document import Document
from app.models.user import User
from app.schemas.search import SearchHit, SearchResponse
from app.services import answer_service
from app.services.embedding_service import chunk_text
from app.services.search_metrics import ranking_metrics


def test_chunking_keeps_paragraph_boundaries_and_respects_configured_limit():
    text = "First paragraph about invoices.\n\nSecond paragraph about payment dates."
    chunks = chunk_text(text, chunk_size=42, overlap=0)
    assert len(chunks) == 2
    assert all(len(chunk) <= 42 for chunk in chunks)
    assert "\n\n" not in chunks[0]


def test_ranking_metrics_compute_recall_precision_mrr_and_ndcg():
    metrics = ranking_metrics([4, 2, 7], [2, 7], k=3)
    assert metrics["recall@3"] == 1.0
    assert metrics["precision@3"] == 2 / 3
    assert metrics["mrr"] == 0.5
    assert 0 < metrics["ndcg@3"] < 1


def test_answer_response_citations_come_from_authorized_database_documents(monkeypatch):
    db = SessionLocal()
    document = Document(
        original_filename="contract.pdf",
        stored_filename="contract.pdf",
        file_path="uploads/contract.pdf",
        file_type="application/pdf",
        status="indexed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        user = User(id=0, email="admin@test.invalid", name="Admin", role="ADMIN", is_active=True)
        monkeypatch.setattr(answer_service, "authorized_document_ids", lambda _db, _user: [document.id])
        monkeypatch.setattr(
            answer_service,
            "hybrid_search",
            lambda *_args, **_kwargs: SearchResponse(
                items=[SearchHit(
                    document_id=document.id,
                    document_name="forged-name.pdf",
                    page_number=3,
                    snippet="Payment is due within thirty days.",
                    match_field="semantic",
                    score=0.4,
                )],
                total=1,
                skip=0,
                limit=8,
            ),
        )
        monkeypatch.setattr(answer_service.settings, "RAG_LLM_BASE_URL", "")
        with_citations = answer_service.answer_question(db, user, "When is payment due?")
        assert with_citations.evidence_status == "retrieved_only"
        assert with_citations.citations[0].document_title == "contract.pdf"
        assert with_citations.citations[0].page_number == 3
        assert with_citations.citations[0].href == f"/library?preview={document.id}&page=3"

        hidden = answer_service.answer_question(db, user, "When is payment due?", include_citations=False)
        assert hidden.citations == []
        assert "[S1]" not in hidden.answer
    finally:
        db.delete(document)
        db.commit()
        db.close()


def test_answer_without_authorized_sources_abstains(monkeypatch):
    db = SessionLocal()
    user = User(id=0, email="admin@test.invalid", name="Admin", role="ADMIN", is_active=True)
    monkeypatch.setattr(answer_service, "authorized_document_ids", lambda _db, _user: [])
    try:
        response = answer_service.answer_question(db, user, "What is the invoice total?")
        assert response.evidence_status == "insufficient_evidence"
        assert response.citations == []
    finally:
        db.close()


def test_answer_abstains_when_authorized_corpus_has_no_retrieved_evidence(monkeypatch):
    db = SessionLocal()
    document = Document(
        original_filename="unrelated.pdf",
        stored_filename="unrelated.pdf",
        file_path="uploads/unrelated.pdf",
        file_type="application/pdf",
        status="indexed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        user = User(id=0, email="admin@test.invalid", name="Admin", role="ADMIN", is_active=True)
        monkeypatch.setattr(answer_service, "authorized_document_ids", lambda *_args: [document.id])
        monkeypatch.setattr(
            answer_service,
            "hybrid_search",
            lambda *_args, **_kwargs: SearchResponse(items=[], total=0, skip=0, limit=8),
        )
        response = answer_service.answer_question(db, user, "What is the project budget?")
        assert response.evidence_status == "insufficient_evidence"
        assert response.citations == []
    finally:
        db.delete(document)
        db.commit()
        db.close()
