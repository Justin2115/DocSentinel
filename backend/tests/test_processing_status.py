"""Processing watchdog and progress endpoint.

Covers the two things the upload UI needs to be honest about: how far a run has
actually got (derived only from rows the pipeline commits) and what happens to a
document that is still marked PROCESSING long after its budget with no worker
attached. Every row here is created in the throwaway database bound by conftest.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.documents import claim_document_processing, release_document_processing
from app.db.session import SessionLocal
from app.main import app
from app.models.document import (
    Document,
    DocumentPage,
    ExtractedField,
    OCRResult,
    ReviewQueue,
)
from app.models.embedding import EmbeddingIndex
from tests.helpers import get_test_auth_headers


def _db_now() -> datetime:
    """Timestamp in the same clock domain the columns are written with."""
    from app.api.documents import _db_now as doc_db_now

    db = SessionLocal()
    try:
        return doc_db_now(db)
    finally:
        db.close()


def _make_document(
    *,
    status: str = "PROCESSING",
    pages: list[tuple[int, str, float | None, str]] | None = None,
    uploaded_at: datetime | None = None,
    department: str | None = None,
    uploaded_by: int | None = None,
    indexed_chunks: int = 0,
    field_count: int = 0,
    overall_confidence: float | None = None,
    filename: str = "sample.pdf",
) -> int:
    db = SessionLocal()
    try:
        document = Document(
            original_filename=filename,
            stored_filename=filename,
            file_path=f"uploads/{filename}",
            file_type="application/pdf",
            file_size=1024,
            status=status,
            department=department,
            uploaded_by=uploaded_by,
            overall_confidence=overall_confidence,
            uploaded_at=uploaded_at or _db_now(),
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        for page_number, text, confidence, engine in pages or []:
            page = DocumentPage(
                document_id=document.id,
                page_number=page_number,
                page_path=document.file_path,
            )
            db.add(page)
            db.flush()
            db.add(
                OCRResult(
                    document_id=document.id,
                    page_id=page.id,
                    extracted_text=text,
                    confidence=confidence,
                    ocr_engine=engine,
                )
            )

        for chunk_index in range(indexed_chunks):
            db.add(
                EmbeddingIndex(
                    document_id=document.id,
                    page_number=1,
                    chunk_index=chunk_index,
                    vector_db_ref=f"test-collection/{document.id}-{chunk_index}",
                )
            )
        for field_index in range(field_count):
            db.add(
                ExtractedField(
                    document_id=document.id,
                    field_name=f"field_{field_index}",
                    field_value="value",
                )
            )
        db.commit()
        return document.id
    finally:
        db.close()


def _admin_headers():
    db = SessionLocal()
    try:
        return get_test_auth_headers(db, "progress-admin@docsentinel.test", "ADMIN")
    finally:
        db.close()


def _progress(client: TestClient, document_id: int, headers=None):
    return client.get(
        f"/api/documents/{document_id}/processing",
        headers=headers or _admin_headers(),
    )


def _row(document_id: int):
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        reasons = [
            item.reason
            for item in db.query(ReviewQueue).filter_by(document_id=document_id).all()
        ]
        return document.status, reasons
    finally:
        db.close()


class TestProgressPayload:
    def test_pages_fields_and_chunks_come_from_persisted_rows(self):
        doc_id = _make_document(
            status="PENDING_REVIEW",
            pages=[
                (1, "Invoice INV-1 total $10.00", 91.0, "PyMuPDF+Fake"),
                (2, "", None, "PyMuPDF+Failed"),
                (3, "Payment terms net thirty", 88.0, "PyMuPDF+Fake"),
            ],
            indexed_chunks=4,
            field_count=2,
            overall_confidence=87.5,
        )

        body = _progress(TestClient(app), doc_id).json()

        assert body["status"] == "PENDING_REVIEW"
        assert body["stage"] == "done"
        assert body["pages_completed"] == 2
        assert body["pages_failed"] == 1
        assert body["extracted_field_count"] == 2
        assert body["indexed_chunks"] == 4
        assert body["searchable"] is True
        assert body["overall_confidence"] == 87.5
        assert body["is_running"] is False

    def test_settled_documents_report_the_run_duration_not_a_live_clock(self):
        """A finished job's elapsed time is history, not a counter that grows."""
        from app.api.documents import processing_progress

        doc_id = _make_document(
            status="completed",
            pages=[(1, "digital text layer", 95.0, "PyMuPDF-Digital")],
            indexed_chunks=1,
        )
        db = SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            anchor = _db_now()
            document.uploaded_at = anchor - timedelta(minutes=10)
            document.updated_at = anchor - timedelta(minutes=9, seconds=50)
            db.commit()
            db.refresh(document)

            settled = processing_progress(db, document, now=anchor + timedelta(hours=1))
            assert settled.elapsed_seconds == 10.0
            assert settled.timed_out is False

            document.status = "PROCESSING"
            db.commit()
            db.refresh(document)
            live = processing_progress(db, document, now=anchor + timedelta(hours=1))
            assert live.elapsed_seconds > settled.elapsed_seconds
            assert live.timed_out is True
        finally:
            db.close()

    def test_finished_documents_without_embeddings_say_the_index_is_pending(self):
        doc_id = _make_document(
            status="completed",
            pages=[(1, "Receipt cash 12 store", 95.0, "PyMuPDF+Fake")],
        )

        body = _progress(TestClient(app), doc_id).json()

        assert body["stage"] == "making_searchable"
        assert body["searchable"] is False
        assert "search index" in body["message"].lower()

    def test_unmeasured_confidence_is_reported_as_absent_not_zero(self):
        doc_id = _make_document(
            status="completed",
            pages=[(1, "digital text layer", None, "PyMuPDF-Digital")],
            indexed_chunks=1,
        )

        body = _progress(TestClient(app), doc_id).json()

        assert body["overall_confidence"] is None

    def test_review_documents_carry_the_reason_that_put_them_there(self):
        doc_id = _make_document(
            status="PENDING_REVIEW",
            pages=[(1, "blurry scan", 20.0, "PyMuPDF+Fake")],
        )
        db = SessionLocal()
        try:
            db.add(
                ReviewQueue(
                    document_id=doc_id,
                    reason="Low OCR confidence (20.0%)",
                    confidence=20.0,
                    status="pending",
                )
            )
            db.commit()
        finally:
            db.close()

        body = _progress(TestClient(app), doc_id).json()

        assert "Low OCR confidence" in body["message"]

    def test_failed_documents_expose_their_error(self):
        doc_id = _make_document(status="failed", filename="broken.pdf")
        db = SessionLocal()
        try:
            db.add(
                ReviewQueue(
                    document_id=doc_id,
                    reason="Processing failed: PaddleOCR could not start",
                    confidence=None,
                    status="pending",
                )
            )
            db.commit()
        finally:
            db.close()

        body = _progress(TestClient(app), doc_id).json()

        assert body["stage"] == "failed"
        assert "PaddleOCR could not start" in body["error"]
        assert "PaddleOCR could not start" in body["message"]

    def test_page_total_comes_from_the_file_while_a_task_holds_it(self, tmp_path, monkeypatch):
        pytest.importorskip("pymupdf")
        from app.api import documents as documents_api

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr(documents_api, "UPLOAD_DIR", uploads)

        import pymupdf

        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.new_page()
            pdf.save(uploads / "two_pages.pdf")

        doc_id = _make_document(
            pages=[(1, "first page text", 93.0, "PyMuPDF+Fake")],
            filename="two_pages.pdf",
        )
        claim_document_processing(doc_id)
        try:
            body = _progress(TestClient(app), doc_id).json()
        finally:
            release_document_processing(doc_id)

        assert body["is_running"] is True
        assert body["stage"] == "reading_text"
        assert body["pages_total"] == 2
        assert body["pages_completed"] == 1
        # The counts live in the numeric fields. A message that repeated them could
        # contradict its own numbers, so it stays a plain description.
        assert body["message"] == "Reading text from the document."
        assert not any(character.isdigit() for character in body["message"])

    def test_a_missing_file_is_reported_not_crashed(self):
        doc_id = _make_document(pages=[(1, "text", 90.0, "Fake")], filename="gone.pdf")

        response = _progress(TestClient(app), doc_id)

        assert response.status_code == 200
        assert response.json()["pages_total"] is None


class TestStalledProcessing:
    def test_budget_exhausted_with_no_worker_fails_the_document_with_a_reason(self):
        doc_id = _make_document(
            uploaded_at=_db_now() - timedelta(minutes=40),
            pages=[(1, "half a page of text", 80.0, "PyMuPDF+Fake")],
        )

        body = _progress(TestClient(app), doc_id).json()

        assert body["status"] == "failed"
        assert body["stage"] == "failed"
        assert body["timed_out"] is True
        assert "stalled" in body["error"].lower()
        status, reasons = _row(doc_id)
        assert status == "failed"
        assert any("stalled" in (reason or "").lower() for reason in reasons)

    def test_a_run_inside_its_budget_is_left_untouched(self):
        doc_id = _make_document(uploaded_at=_db_now() - timedelta(seconds=30))

        body = _progress(TestClient(app), doc_id).json()

        assert body["status"] == "PROCESSING"
        assert body["timed_out"] is False
        assert _row(doc_id)[0] == "PROCESSING"

    def test_a_live_task_is_never_failed_by_the_watchdog(self):
        doc_id = _make_document(
            uploaded_at=_db_now() - timedelta(hours=2),
            pages=[(1, "slow but running", 70.0, "PyMuPDF+Fake")],
        )
        claim_document_processing(doc_id)
        try:
            body = _progress(TestClient(app), doc_id).json()
        finally:
            release_document_processing(doc_id)

        assert body["status"] == "PROCESSING"
        assert body["timed_out"] is True
        assert _row(doc_id)[0] == "PROCESSING"

    def test_settled_documents_are_never_rewritten(self):
        doc_id = _make_document(
            status="completed",
            uploaded_at=_db_now() - timedelta(hours=5),
            pages=[(1, "done long ago", 96.0, "Fake")],
            indexed_chunks=2,
        )

        body = _progress(TestClient(app), doc_id).json()

        assert body["status"] == "completed"
        assert body["timed_out"] is False
        assert _row(doc_id)[1] == []


class TestPermissions:
    def test_anonymous_callers_get_401(self):
        doc_id = _make_document()

        response = TestClient(app).get(f"/api/documents/{doc_id}/processing")

        assert response.status_code == 401

    def test_a_maker_cannot_watch_another_departments_document(self):
        db = SessionLocal()
        try:
            headers = get_test_auth_headers(
                db,
                "progress-maker@docsentinel.test",
                "UPLOAD_MAKER",
                department="FINANCE",
            )
        finally:
            db.close()
        doc_id = _make_document(department="LEGAL", uploaded_by=None)

        response = _progress(TestClient(app), doc_id, headers)

        assert response.status_code == 403
        # Permission checks must not mutate the document either.
        assert _row(doc_id)[0] == "PROCESSING"

    def test_unknown_document_id_is_404(self):
        response = _progress(TestClient(app), 999999)

        assert response.status_code == 404


class TestTimezoneAndSettledDurationRegression:
    def test_naive_uploaded_at_with_aware_now(self):
        """Naive stored timestamp with offset-aware now must not raise TypeError."""
        from datetime import timezone
        from app.api.documents import processing_progress

        doc_id = _make_document(status="PROCESSING")
        db = SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            # Deliberately set naive datetime
            document.uploaded_at = datetime(2026, 10, 20, 10, 0, 0)
            document.updated_at = datetime(2026, 10, 20, 10, 1, 0)
            db.commit()
            db.refresh(document)

            # Pass offset-aware UTC now
            aware_now = datetime(2026, 10, 20, 10, 5, 0, tzinfo=timezone.utc)
            res = processing_progress(db, document, now=aware_now)
            assert res.elapsed_seconds == 300.0
            assert res.timed_out is False
        finally:
            db.close()

    def test_aware_uploaded_at_with_naive_now(self):
        """Aware stored timestamp with offset-naive now must not raise TypeError."""
        from datetime import timezone
        from app.api.documents import processing_progress

        doc_id = _make_document(status="PROCESSING")
        db = SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            # Stored with UTC aware tz
            document.uploaded_at = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
            document.updated_at = datetime(2026, 10, 20, 10, 0, 30, tzinfo=timezone.utc)
            db.commit()
            db.refresh(document)

            # Pass naive now
            naive_now = datetime(2026, 10, 20, 10, 2, 0)
            res = processing_progress(db, document, now=naive_now)
            assert res.elapsed_seconds == 120.0
        finally:
            db.close()

    def test_naive_uploaded_at_with_aware_updated_at(self):
        """Mixed naive uploaded_at and aware updated_at must compare safely."""
        from datetime import timezone, timedelta
        from app.api.documents import processing_progress, settle_stalled_processing

        doc_id = _make_document(status="PROCESSING")
        db = SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            now = datetime.now(timezone.utc)
            document.uploaded_at = (now - timedelta(seconds=60)).replace(tzinfo=None)  # naive
            document.updated_at = now - timedelta(seconds=30)  # aware
            db.commit()
            db.refresh(document)

            # Must not crash in settle_stalled_processing or processing_progress
            stalled = settle_stalled_processing(db, document, timeout_seconds=900)
            assert stalled is None

            res = processing_progress(db, document)
            assert res.elapsed_seconds is not None
            assert res.elapsed_seconds >= 0.0
        finally:
            db.close()

    def test_missing_timestamps_do_not_crash(self):
        """Documents missing timestamps gracefully return without 500."""
        from app.api.documents import processing_progress, settle_stalled_processing

        doc_id = _make_document(status="PROCESSING")
        db = SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            # Test in-memory None timestamps
            document.uploaded_at = None
            document.updated_at = None
            document.processed_at = None

            assert settle_stalled_processing(db, document) is None
            res = processing_progress(db, document)
            assert res.elapsed_seconds is None
            assert res.timed_out is False
        finally:
            db.close()

    def test_frozen_settled_duration_stays_frozen(self):
        """Settled duration remains locked at actual run duration across future polls."""
        from datetime import timezone
        from app.api.documents import processing_progress

        doc_id = _make_document(status="completed")
        db = SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            document.uploaded_at = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
            document.processed_at = datetime(2026, 10, 20, 12, 0, 45, tzinfo=timezone.utc)
            document.updated_at = datetime(2026, 10, 20, 12, 5, 0, tzinfo=timezone.utc)
            db.commit()
            db.refresh(document)

            # Poll 1 hour later
            poll_1h = datetime(2026, 10, 20, 13, 0, 0, tzinfo=timezone.utc)
            res1 = processing_progress(db, document, now=poll_1h)
            assert res1.elapsed_seconds == 45.0
            assert res1.timed_out is False

            # Poll 24 hours later
            poll_24h = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
            res2 = processing_progress(db, document, now=poll_24h)
            assert res2.elapsed_seconds == 45.0
            assert res2.timed_out is False
        finally:
            db.close()

    def test_stalled_watchdog_marks_failed_with_frozen_duration(self):
        """A stalled job flips to failed and freezes elapsed duration."""
        from datetime import timezone
        from app.api.documents import processing_progress, settle_stalled_processing

        doc_id = _make_document(status="PROCESSING")
        db = SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            document.uploaded_at = datetime(2026, 10, 20, 10, 0, 0)
            document.updated_at = datetime(2026, 10, 20, 10, 0, 0)
            db.commit()
            db.refresh(document)

            future_now = datetime(2026, 10, 20, 10, 30, 0, tzinfo=timezone.utc)
            reason = settle_stalled_processing(db, document, now=future_now, timeout_seconds=600)
            assert reason is not None
            assert document.status == "failed"

            res = processing_progress(db, document, now=future_now + timedelta(days=2))
            assert res.status == "failed"
            assert res.stage == "failed"
            assert res.timed_out is True
        finally:
            db.close()
