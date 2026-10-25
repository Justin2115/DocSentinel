"""OCR text corrections in Review Queue must persist extracted_text.

raw_text stays the original engine output. extracted_text is the canonical
value returned by GET and used by search/RAG after a supported reindex.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import documents as documents_api
from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.models.user import User
from tests.helpers import get_test_auth_headers


ORIGINAL_OCR = "c0rrected 1nvoice t0tal 12500"
CORRECTED_OCR = "corrected invoice total 12500"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def stub_reindex(monkeypatch):
    scheduled: list[int] = []
    monkeypatch.setattr(
        documents_api,
        "schedule_document_indexing",
        lambda document_id: scheduled.append(document_id),
    )
    return scheduled


def _headers(email: str, role: str, department: str | None):
    db = SessionLocal()
    try:
        return get_test_auth_headers(
            db, email=email, role=role, department=department
        )
    finally:
        db.close()


def _seed_document(*, department: str = "FINANCE", original: str = ORIGINAL_OCR):
    db = SessionLocal()
    try:
        get_test_auth_headers(
            db,
            email="ocr-maker@docsentinel.test",
            role="DEPARTMENT_MAKER",
            department=department,
        )
        maker = (
            db.query(User)
            .filter(User.email == "ocr-maker@docsentinel.test")
            .first()
        )
        document = Document(
            original_filename="invoice.pdf",
            stored_filename="invoice.pdf",
            file_path="uploads/invoice.pdf",
            file_type="application/pdf",
            file_size=1024,
            status="needs_review",
            department=department,
            document_type="Invoice",
            uploaded_by=maker.id,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        page = DocumentPage(
            document_id=document.id,
            page_number=1,
            page_path=document.file_path,
        )
        db.add(page)
        db.commit()
        db.refresh(page)
        ocr = OCRResult(
            document_id=document.id,
            page_id=page.id,
            extracted_text=original,
            raw_text=None,
            confidence=71.5,
            ocr_engine="PaddleOCR",
        )
        db.add(ocr)
        db.commit()
        db.refresh(ocr)
        return document.id, ocr.id, original
    finally:
        db.close()


def test_patch_returns_updated_extracted_text_and_preserves_raw(client, stub_reindex):
    document_id, ocr_id, original = _seed_document()
    headers = _headers(
        "ocr-checker@docsentinel.test", "DEPARTMENT_CHECKER", "FINANCE"
    )

    response = client.patch(
        f"/api/documents/{document_id}/ocr/{ocr_id}",
        json={"extracted_text": CORRECTED_OCR},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["extracted_text"] == CORRECTED_OCR
    assert body["raw_text"] == original
    assert stub_reindex == [document_id]


def test_database_and_refetch_return_corrected_text(client, stub_reindex):
    document_id, ocr_id, original = _seed_document()
    headers = _headers(
        "ocr-checker@docsentinel.test", "DEPARTMENT_CHECKER", "FINANCE"
    )

    patch = client.patch(
        f"/api/documents/{document_id}/ocr/{ocr_id}",
        json={"extracted_text": CORRECTED_OCR},
        headers=headers,
    )
    assert patch.status_code == 200

    db = SessionLocal()
    try:
        row = db.query(OCRResult).filter(OCRResult.id == ocr_id).one()
        assert row.extracted_text == CORRECTED_OCR
        assert row.raw_text == original
    finally:
        db.close()

    refetch = client.get(f"/api/documents/{document_id}", headers=headers)
    assert refetch.status_code == 200
    ocr = refetch.json()["ocr_results"][0]
    assert ocr["extracted_text"] == CORRECTED_OCR
    assert ocr["raw_text"] == original

    queue = client.get("/api/documents/review/queue", headers=headers)
    assert queue.status_code == 200
    queued = next(item for item in queue.json() if item["id"] == document_id)
    assert queued["ocr_results"][0]["extracted_text"] == CORRECTED_OCR
    assert queued["ocr_results"][0]["raw_text"] == original


def test_raw_text_is_not_overwritten_on_second_edit(client, stub_reindex):
    document_id, ocr_id, original = _seed_document()
    headers = _headers(
        "ocr-checker@docsentinel.test", "DEPARTMENT_CHECKER", "FINANCE"
    )

    client.patch(
        f"/api/documents/{document_id}/ocr/{ocr_id}",
        json={"extracted_text": CORRECTED_OCR},
        headers=headers,
    )
    second = client.patch(
        f"/api/documents/{document_id}/ocr/{ocr_id}",
        json={"extracted_text": "second correction"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["extracted_text"] == "second correction"
    assert second.json()["raw_text"] == original


def test_other_department_checker_cannot_edit_ocr(client, stub_reindex):
    document_id, ocr_id, original = _seed_document(department="FINANCE")
    headers = _headers(
        "ocr-hr-checker@docsentinel.test", "DEPARTMENT_CHECKER", "HR"
    )

    response = client.patch(
        f"/api/documents/{document_id}/ocr/{ocr_id}",
        json={"extracted_text": "Hacked OCR Text"},
        headers=headers,
    )
    assert response.status_code == 403

    db = SessionLocal()
    try:
        row = db.query(OCRResult).filter(OCRResult.id == ocr_id).one()
        assert row.extracted_text == original
    finally:
        db.close()
    assert stub_reindex == []


def test_correction_reindexes_extracted_text_without_duplicate_chunks(
    client, monkeypatch
):
    from app.models.embedding import EmbeddingIndex
    from app.services.embedding_service import (
        get_collection,
        index_document_embeddings,
        semantic_search,
    )

    document_id, ocr_id, original = _seed_document()
    headers = _headers(
        "ocr-checker@docsentinel.test", "DEPARTMENT_CHECKER", "FINANCE"
    )

    db = SessionLocal()
    try:
        first_count = index_document_embeddings(db, document_id)
        assert first_count >= 1
        before = (
            db.query(EmbeddingIndex)
            .filter(EmbeddingIndex.document_id == document_id)
            .count()
        )
    finally:
        db.close()

    def _sync_reindex(scheduled_id: int):
        session = SessionLocal()
        try:
            index_document_embeddings(session, scheduled_id)
        finally:
            session.close()

    monkeypatch.setattr(documents_api, "schedule_document_indexing", _sync_reindex)

    response = client.patch(
        f"/api/documents/{document_id}/ocr/{ocr_id}",
        json={"extracted_text": CORRECTED_OCR},
        headers=headers,
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(EmbeddingIndex)
            .filter(EmbeddingIndex.document_id == document_id)
            .all()
        )
        assert len(rows) == before
        assert len({(row.page_number, row.chunk_index) for row in rows}) == len(rows)
        chroma_docs = get_collection().get(ids=[row.vector_db_ref for row in rows])
        stored_text = " ".join(chroma_docs.get("documents") or [])
        assert CORRECTED_OCR in stored_text
        assert original not in stored_text
        hits = semantic_search(db, "corrected invoice total", top_k=5, min_score=0.0)
        assert any(item.document_id == document_id for item in hits.items)
    finally:
        db.close()


def test_missing_text_is_rejected_and_does_not_change_row(client, stub_reindex):
    document_id, ocr_id, original = _seed_document()
    headers = _headers(
        "ocr-checker@docsentinel.test", "DEPARTMENT_CHECKER", "FINANCE"
    )

    response = client.patch(
        f"/api/documents/{document_id}/ocr/{ocr_id}",
        json={},
        headers=headers,
    )
    assert response.status_code == 400
    assert "missing" in response.json()["detail"].lower()

    db = SessionLocal()
    try:
        row = db.query(OCRResult).filter(OCRResult.id == ocr_id).one()
        assert row.extracted_text == original
    finally:
        db.close()
    assert stub_reindex == []
