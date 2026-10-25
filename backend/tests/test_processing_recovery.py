"""Regression tests for interruptible, resumable and idempotent processing.

Everything here is mocked or synthetic: no real OCR model is loaded, and the
suite runs against the throwaway SQLite database and temporary Chroma directory
that conftest.py installs. The two real-runtime tests at the bottom run Paddle
and torch in subprocesses and are skipped unless DOCSENTINEL_REAL_ML_TESTS=1.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pymupdf as fitz
import pytest

from app.api import documents as documents_api
from app.core.dependencies import require_upload_checker
from app.core.rbac import DocumentStatus
from app.models.document import Document, DocumentPage, OCRResult
from app.models.embedding import EmbeddingIndex
from app.services import ml_runtime, ocr_service
from app.services.embedding_service import (
    reindex_pending_documents,
    schedule_document_indexing,
)
from app.services.ocr_service import DocumentExtractionResult, ExtractedPage
from app.services import upload_service

import app.db.session as db_session


def _pdf(path: Path, pages_text: list[str | None]) -> None:
    """Build a PDF; a None entry becomes a page with no text layer (scanned)."""
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page(width=595, height=842)
        if text is not None:
            page.insert_textbox(fitz.Rect(40, 40, 555, 800), text, fontsize=11)
        else:
            img = fitz.open()
            ip = img.new_page(width=595, height=842)
            ip.insert_textbox(fitz.Rect(40, 40, 500, 200), "scan", fontsize=24)
            page.insert_image(page.rect, stream=ip.get_pixmap(dpi=60).tobytes("png"))
            img.close()
    doc.save(str(path))
    doc.close()


def _make_document(db, tmp_path: Path, pages: list[str | None], status: str):
    name = f"doc{len(pages)}.pdf"
    pdf = tmp_path / name
    _pdf(pdf, pages)
    document = Document(
        original_filename=name,
        stored_filename=name,
        file_path=str(pdf),
        file_type="application/pdf",
        status=status,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document, pdf


def _page_result(n: int, text: str) -> ExtractedPage:
    return ExtractedPage(
        page_number=n,
        text=text,
        confidence=90.0,
        raw_text=text,
        page_type="scanned",
        engine="PyMuPDF+FakeOCR",
    )


def _result(pages: list[ExtractedPage]) -> DocumentExtractionResult:
    return DocumentExtractionResult(
        pages=pages,
        combined_text="\n\n".join(p.text for p in pages),
        overall_ocr_confidence=90.0,
        ocr_engine="PyMuPDF+FakeOCR",
    )


@pytest.fixture
def isolated_uploads(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(upload_service, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(documents_api, "UPLOAD_DIR", uploads)
    return uploads


@pytest.fixture
def no_index_thread(monkeypatch):
    """Capture indexing requests instead of spawning threads."""
    requested: list[int] = []
    monkeypatch.setattr(
        documents_api, "schedule_document_indexing", lambda did: requested.append(did)
    )
    return requested


@pytest.fixture
def checker_client():
    """TestClient whose Upload Checker dependency is satisfied by a stub user."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.main import app

    app.dependency_overrides[require_upload_checker] = lambda: SimpleNamespace(
        id=None, role="UPLOAD_CHECKER", department=None
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestResumeAfterInterruption:
    def test_crash_then_retry_resumes_from_committed_pages(
        self, tmp_path, monkeypatch, isolated_uploads, no_index_thread, checker_client
    ):
        """A killed worker keeps its committed pages and the retry does only the rest."""
        name = "crashy.pdf"
        pdf = isolated_uploads / name
        _pdf(pdf, [None, None, None])

        db = db_session.SessionLocal()
        try:
            document = Document(
                original_filename=name,
                stored_filename=name,
                file_path=str(Path("uploads") / name),
                file_type="application/pdf",
                status=DocumentStatus.PROCESSING.value,
            )
            db.add(document)
            db.commit()
            doc_id = document.id
        finally:
            db.close()

        def first_run(file_path, file_type, language="auto", only_pages=None, on_page=None):
            assert only_pages is None
            for n in (1, 2):
                on_page(_page_result(n, f"committed page {n}"))
            raise RuntimeError("worker died on page 3")

        monkeypatch.setattr(ocr_service, "extract_document_text", first_run)
        documents_api.process_document_task(
            document_id=doc_id,
            disk_path=pdf,
            content_type="application/pdf",
            norm_lang="auto",
            department=None,
            document_type=None,
            user_id=None,
        )

        db = db_session.SessionLocal()
        try:
            assert db.query(Document).filter(Document.id == doc_id).first().status == "failed"
            assert (
                db.query(OCRResult).filter(OCRResult.document_id == doc_id).count() == 2
            ), "pages finished before the crash must survive it"
        finally:
            db.close()

        seen: dict[str, object] = {}

        def second_run(file_path, file_type, language="auto", only_pages=None, on_page=None):
            seen["only_pages"] = only_pages
            page = _page_result(3, "committed page 3")
            on_page(page)
            return _result([page])

        monkeypatch.setattr(ocr_service, "extract_document_text", second_run)
        # 'failed' is outside the startup requeue set: recovery must not
        # silently reprocess it, the user has to ask for a retry.
        assert documents_api.requeue_interrupted_documents() == 0
        resp = checker_client.post(f"/api/documents/{doc_id}/retry")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "PROCESSING"

        assert seen["only_pages"] == {3}, "retry must not re-OCR finished pages"

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document.status in ("completed", "PENDING_REVIEW")
            pages = db.query(DocumentPage).filter(DocumentPage.document_id == doc_id).all()
            rows = db.query(OCRResult).filter(OCRResult.document_id == doc_id).all()
            assert len(pages) == 3 and len(rows) == 3, "retry must not duplicate rows"
            assert sorted(r.extracted_text for r in rows) == [
                "committed page 1",
                "committed page 2",
                "committed page 3",
            ]
        finally:
            db.close()

    def test_resume_only_processes_missing_pages_and_keeps_existing_text(
        self, tmp_path, monkeypatch, no_index_thread
    ):
        """Reprocessing a partly done document must not re-OCR or overwrite valid pages."""
        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(
                db, tmp_path, [None, None], DocumentStatus.PROCESSING.value
            )
            page1 = DocumentPage(
                document_id=document.id, page_number=1, page_path=str(pdf), width=595, height=842
            )
            db.add(page1)
            db.flush()
            db.add(
                OCRResult(
                    document_id=document.id,
                    page_id=page1.id,
                    extracted_text="ALREADY PERSISTED PAGE ONE",
                    raw_text="ALREADY PERSISTED PAGE ONE",
                    confidence=91.0,
                    ocr_engine="PyMuPDF+OldFake",
                )
            )
            db.commit()
            doc_id = document.id
        finally:
            db.close()

        seen: dict[str, object] = {}

        def fake_extract(file_path, file_type, language="auto", only_pages=None, on_page=None):
            seen["only_pages"] = only_pages
            page = _page_result(2, "newly recognised page two")
            if on_page is not None:
                on_page(page)
            return _result([page])

        monkeypatch.setattr(ocr_service, "extract_document_text", fake_extract)

        documents_api.process_document_task(
            document_id=doc_id,
            disk_path=pdf,
            content_type="application/pdf",
            norm_lang="auto",
            department=None,
            document_type=None,
            user_id=None,
        )

        assert seen["only_pages"] == {2}

        db = db_session.SessionLocal()
        try:
            pages = (
                db.query(DocumentPage).filter(DocumentPage.document_id == doc_id).all()
            )
            rows = db.query(OCRResult).filter(OCRResult.document_id == doc_id).all()
            assert len(pages) == 2, "resume must not duplicate page rows"
            assert len(rows) == 2, "resume must not duplicate OCR rows"
            by_page = {
                p.page_number: r.extracted_text
                for p, r in db.query(DocumentPage, OCRResult)
                .join(OCRResult, OCRResult.page_id == DocumentPage.id)
                .filter(DocumentPage.document_id == doc_id)
                .all()
            }
            assert by_page[1] == "ALREADY PERSISTED PAGE ONE"
            assert by_page[2] == "newly recognised page two"
            assert DocumentStatus.PROCESSING.value != db.query(Document).get(doc_id).status
        finally:
            db.close()

    def test_interruption_leaves_document_requeueable_not_indexed(
        self, tmp_path, monkeypatch, no_index_thread
    ):
        """A mid-run exception marks the document failed while keeping page rows."""
        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(
                db, tmp_path, [None, None], DocumentStatus.PROCESSING.value
            )
            doc_id = document.id
        finally:
            db.close()

        def extract_then_crash(file_path, file_type, language="auto", on_page=None, **_kw):
            if on_page is not None:
                on_page(_page_result(1, "page one before crash"))
            raise RuntimeError("worker killed mid-page-two")

        monkeypatch.setattr(ocr_service, "extract_document_text", extract_then_crash)

        documents_api.process_document_task(
            document_id=doc_id,
            disk_path=pdf,
            content_type="application/pdf",
            norm_lang="auto",
            department=None,
            document_type=None,
            user_id=None,
        )

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document.status == "failed"
            assert (
                db.query(OCRResult).filter(OCRResult.document_id == doc_id).count() == 1
            ), "the finished page must survive the failure"
            assert (
                db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == doc_id).count()
                == 0
            )
        finally:
            db.close()


class TestRetryEndpoint:
    def test_retry_rejects_unknown_document(self, checker_client):
        assert checker_client.post("/api/documents/999999/retry").status_code == 404

    def test_retry_rejects_settled_document(
        self, tmp_path, monkeypatch, isolated_uploads, no_index_thread, checker_client
    ):
        """A document that already passed review must never be reprocessed by a retry."""
        db = db_session.SessionLocal()
        try:
            document, _pdf_path = _make_document(db, tmp_path, [None], "PENDING_REVIEW")
            doc_id = document.id
        finally:
            db.close()

        def fail_extract(*a, **k):
            raise AssertionError("retry must not run the pipeline for a settled document")

        monkeypatch.setattr(ocr_service, "extract_document_text", fail_extract)
        resp = checker_client.post(f"/api/documents/{doc_id}/retry")
        assert resp.status_code == 409
        assert "settled" in resp.json()["detail"]

    def test_retry_rejects_missing_file(
        self, isolated_uploads, no_index_thread, checker_client
    ):
        db = db_session.SessionLocal()
        try:
            document = Document(
                original_filename="missing.pdf",
                stored_filename="missing.pdf",
                file_path=str(Path("uploads") / "missing.pdf"),
                file_type="application/pdf",
                status="failed",
            )
            db.add(document)
            db.commit()
            doc_id = document.id
        finally:
            db.close()

        resp = checker_client.post(f"/api/documents/{doc_id}/retry")
        assert resp.status_code == 400
        db = db_session.SessionLocal()
        try:
            assert db.query(Document).get(doc_id).status == "failed"
        finally:
            db.close()

    def test_retry_rejects_document_already_running(
        self, tmp_path, monkeypatch, isolated_uploads, no_index_thread, checker_client
    ):
        """A second retry while a task holds the document is refused, not queued."""
        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(db, tmp_path, [None], "failed")
            doc_id = document.id
            name = document.original_filename
        finally:
            db.close()
        shutil.copyfile(pdf, isolated_uploads / name)

        assert documents_api.claim_document_processing(doc_id)
        try:
            resp = checker_client.post(f"/api/documents/{doc_id}/retry")
            assert resp.status_code == 409
            db = db_session.SessionLocal()
            try:
                # The refusal must not leave the document marked PROCESSING.
                assert db.query(Document).get(doc_id).status == "failed"
            finally:
                db.close()
        finally:
            documents_api.release_document_processing(doc_id)

    def test_concurrent_task_deliveries_run_the_pipeline_once(
        self, tmp_path, monkeypatch, no_index_thread
    ):
        """Two threads handed the same document: only one may reach the extractor."""
        import threading

        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(
                db, tmp_path, [None], DocumentStatus.PROCESSING.value
            )
            doc_id = document.id
        finally:
            db.close()

        runs: list[int] = []

        def slow_extract(file_path, file_type, language="auto", only_pages=None, on_page=None):
            runs.append(1)
            # Long enough that the second delivery arrives while this one runs.
            time.sleep(0.5)
            page = _page_result(1, "single flight page")
            if on_page is not None:
                on_page(page)
            return _result([page])

        monkeypatch.setattr(ocr_service, "extract_document_text", slow_extract)

        threads = [
            threading.Thread(
                target=documents_api.process_document_task,
                kwargs=dict(
                    document_id=doc_id,
                    disk_path=pdf,
                    content_type="application/pdf",
                    norm_lang="auto",
                    department=None,
                    document_type=None,
                    user_id=None,
                ),
            )
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(runs) == 1, "duplicate delivery must be claimed away"
        db = db_session.SessionLocal()
        try:
            assert db.query(OCRResult).filter(OCRResult.document_id == doc_id).count() == 1
        finally:
            db.close()
        assert documents_api.is_processing_claimed(doc_id) is False


class TestStartupRecovery:
    def test_requeue_processes_interrupted_document(self, tmp_path, monkeypatch, isolated_uploads, no_index_thread):
        name = "interrupted.pdf"
        pdf = tmp_path / name
        _pdf(pdf, [None])
        stored = isolated_uploads / name
        stored.write_bytes(pdf.read_bytes())

        db = db_session.SessionLocal()
        try:
            document = Document(
                original_filename=name,
                stored_filename=name,
                file_path=str(Path("uploads") / name),
                file_type="application/pdf",
                status=DocumentStatus.PROCESSING.value,
            )
            db.add(document)
            db.commit()
            doc_id = document.id
        finally:
            db.close()

        calls: list[bool] = []

        def fake_extract(file_path, file_type, language="auto", only_pages=None, on_page=None):
            calls.append(only_pages is None)
            page = _page_result(1, "recovered page text")
            if on_page is not None:
                on_page(page)
            return _result([page])

        monkeypatch.setattr(ocr_service, "extract_document_text", fake_extract)

        assert documents_api.requeue_interrupted_documents() == 1
        assert calls == [True]
        assert no_index_thread == [doc_id]

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document.status in ("completed", "PENDING_REVIEW", "indexed")
        finally:
            db.close()

    def test_requeue_marks_missing_file_failed_instead_of_stuck(
        self, isolated_uploads, no_index_thread
    ):
        db = db_session.SessionLocal()
        try:
            document = Document(
                original_filename="gone.pdf",
                stored_filename="gone.pdf",
                file_path=str(Path("uploads") / "gone.pdf"),
                file_type="application/pdf",
                status=DocumentStatus.PROCESSING.value,
            )
            db.add(document)
            db.commit()
            doc_id = document.id
        finally:
            db.close()

        assert documents_api.requeue_interrupted_documents() == 0

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document.status == "failed"
            assert document.processed_at is not None
        finally:
            db.close()

    def test_requeue_leaves_finished_documents_alone(
        self, tmp_path, monkeypatch, isolated_uploads, no_index_thread
    ):
        db = db_session.SessionLocal()
        try:
            document = Document(
                original_filename="done.pdf",
                stored_filename="done.pdf",
                file_path=str(Path("uploads") / "done.pdf"),
                file_type="application/pdf",
                status="indexed",
            )
            db.add(document)
            db.commit()
        finally:
            db.close()

        def fail_extract(*a, **k):
            raise AssertionError("indexed documents must not be reprocessed")

        monkeypatch.setattr(ocr_service, "extract_document_text", fail_extract)
        assert documents_api.requeue_interrupted_documents() == 0

    def test_recovery_backfills_embeddings_before_requeuing(self, monkeypatch):
        order: list[str] = []
        monkeypatch.setattr(
            "app.services.embedding_service.reindex_pending_documents",
            lambda: order.append("backfill") or 0,
        )
        monkeypatch.setattr(
            "app.api.documents.requeue_interrupted_documents",
            lambda: order.append("requeue") or 0,
        )
        import app.main as main

        main.run_startup_recovery()
        assert order == ["backfill", "requeue"]


class TestFailureIsVisible:
    def test_empty_extraction_is_a_failure_not_a_completion(
        self, tmp_path, monkeypatch, no_index_thread
    ):
        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(
                db, tmp_path, [None], DocumentStatus.PROCESSING.value
            )
            doc_id = document.id
        finally:
            db.close()

        def broken_extract(file_path, file_type, language="auto", **_kw):
            return DocumentExtractionResult(
                pages=[], combined_text="", overall_ocr_confidence=0.0, ocr_engine="Error"
            )

        monkeypatch.setattr(ocr_service, "extract_document_text", broken_extract)

        documents_api.process_document_task(
            document_id=doc_id,
            disk_path=pdf,
            content_type="application/pdf",
            norm_lang="auto",
            department=None,
            document_type=None,
            user_id=None,
        )

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document.status == "failed"
        finally:
            db.close()

    def test_processed_at_is_real_completion_time_not_transaction_start(
        self, tmp_path, monkeypatch, no_index_thread
    ):
        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(
                db, tmp_path, [None], DocumentStatus.PROCESSING.value
            )
            doc_id = document.id
        finally:
            db.close()

        def slow_extract(file_path, file_type, language="auto", on_page=None, **_kw):
            page = _page_result(1, "text after a delay")
            if on_page is not None:
                on_page(page)
            return _result([page])

        monkeypatch.setattr(ocr_service, "extract_document_text", slow_extract)
        documents_api.process_document_task(
            document_id=doc_id,
            disk_path=pdf,
            content_type="application/pdf",
            norm_lang="auto",
            department=None,
            document_type=None,
            user_id=None,
        )

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document.processed_at is not None
            # The upload row was created in an earlier transaction; a
            # transaction-timestamp would equal it exactly.
            assert document.processed_at >= document.uploaded_at
            assert (document.processed_at - document.uploaded_at).total_seconds() >= 0
        finally:
            db.close()


class TestIndexingIdempotency:
    def test_backfill_indexes_completed_but_unindexed_document(self, tmp_path, monkeypatch):
        """The document-409 scenario: OCR done, embeddings missing."""
        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(db, tmp_path, [None], "completed")
            doc_id = document.id
            page = DocumentPage(
                document_id=doc_id, page_number=1, page_path=str(pdf), width=595, height=842
            )
            db.add(page)
            db.flush()
            db.add(
                OCRResult(
                    document_id=doc_id,
                    page_id=page.id,
                    extracted_text="Invoice payment due total amount 500",
                    raw_text="Invoice payment due total amount 500",
                    confidence=93.0,
                    ocr_engine="PyMuPDF+PaddleOCR-English",
                )
            )
            db.commit()
        finally:
            db.close()

        assert reindex_pending_documents() == 1

        db = db_session.SessionLocal()
        try:
            assert (
                db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == doc_id).count()
                > 0
            )
            assert db.query(Document).get(doc_id).status == "indexed"
        finally:
            db.close()

        # Running recovery again must not add duplicate index rows.
        assert reindex_pending_documents() == 0
        db = db_session.SessionLocal()
        try:
            assert (
                db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == doc_id).count()
                >= 1
            )
            rows = (
                db.query(EmbeddingIndex)
                .filter(EmbeddingIndex.document_id == doc_id)
                .all()
            )
            assert len({(r.page_number, r.chunk_index) for r in rows}) == len(rows)
        finally:
            db.close()

    def test_indexing_is_single_flight_per_document(self, monkeypatch):
        """Two schedulings of the same document must not run concurrently.

        A second schedule while a job is in flight is queued as a follow-up
        so OCR corrections are not dropped after the first pass finishes.
        """
        import threading
        import time

        started = threading.Event()
        release = threading.Event()
        runs: list[int] = []

        def blocking_index(db, document_id):
            runs.append(document_id)
            if len(runs) == 1:
                started.set()
                assert release.wait(timeout=5), "test deadlock"
            return 1

        monkeypatch.setattr(
            "app.services.embedding_service.index_document_embeddings", blocking_index
        )

        schedule_document_indexing(777)
        assert started.wait(timeout=5)
        schedule_document_indexing(777)
        time.sleep(0.3)
        assert runs == [777], "follow-up must wait for the in-flight job"
        release.set()

        deadline = time.time() + 5
        while len(runs) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert runs == [777, 777]

    def test_failed_documents_are_not_backfilled(self, tmp_path, monkeypatch):
        db = db_session.SessionLocal()
        try:
            document, pdf = _make_document(db, tmp_path, [None], "failed")
            doc_id = document.id
            page = DocumentPage(
                document_id=doc_id, page_number=1, page_path=str(pdf), width=595, height=842
            )
            db.add(page)
            db.flush()
            db.add(
                OCRResult(
                    document_id=doc_id,
                    page_id=page.id,
                    extracted_text="partial page from a failed run",
                    confidence=10.0,
                    ocr_engine="PyMuPDF+Fake",
                )
            )
            db.commit()
        finally:
            db.close()

        reindex_pending_documents()

        db = db_session.SessionLocal()
        try:
            assert (
                db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == doc_id).count() == 0
            )
            assert db.query(Document).get(doc_id).status == "failed"
        finally:
            db.close()


class TestRuntimeOrdering:
    def test_paddle_init_goes_through_the_torch_order_guard(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            ml_runtime,
            "ensure_torch_loaded",
            lambda: calls.append("torch") or True,
        )
        monkeypatch.setitem(sys.modules, "paddle", None)
        ocr_service._ensure_paddle_cpu_stability()
        assert calls == ["torch"]


REAL_TESTS_ENABLED = bool(os.environ.get("DOCSENTINEL_REAL_ML_TESTS"))
_BACKEND = Path(__file__).resolve().parents[1]


def _mixed_pdf(path: Path, digital: str, scanned: list[str]) -> None:
    """Page 1 has a real text layer, page 2 only exists as a rasterised image.

    Used by the real-engine test so PaddleOCR has to recover the second page.
    """
    src = fitz.open()
    src_page = src.new_page(width=595, height=842)
    y = 90
    for line in scanned:
        src_page.insert_text((60, y), line, fontsize=18)
        y += 40
    image = src_page.get_pixmap(dpi=150).tobytes("png")
    src.close()

    out = fitz.open()
    first = out.new_page(width=595, height=842)
    first.insert_textbox(fitz.Rect(50, 60, 545, 780), digital, fontsize=13)
    second = out.new_page(width=595, height=842)
    second.insert_image(fitz.Rect(30, 30, 565, 812), stream=image)
    out.save(str(path))
    out.close()


def _run_python(code: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DB_HOST"] = "docsentinel-tests-isolated.invalid"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        # The child writes Devanagari and engine warnings; the Windows default
        # cp1252 decoder would raise and leave stdout/stderr as None.
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env=env,
        # Runs with the import path set instead of from the repo root, so the
        # probe image these scripts write lands in the test's temp directory.
        cwd=str(cwd),
    )


_BAD_ORDER = """
from PIL import Image
p = Image.new('RGB', (200, 80), 'white')
p.save('blank_probe.png')
import app.services.ml_runtime as ml_runtime
# Simulate the pre-fix code path: Paddle initialises with torch still absent.
ml_runtime.ensure_torch_loaded = lambda: True
from app.services.ocr_service import get_paddle_english_engine
eng = get_paddle_english_engine()
assert eng is not None, 'paddle engine unavailable'
eng.ocr('blank_probe.png', cls=False)
try:
    import sentence_transformers
    print('UNEXPECTED: torch imported after paddle')
except OSError as exc:
    print('REPRODUCED', type(exc).__name__)
"""

_GOOD_ORDER = """
from app.services.ml_runtime import ensure_torch_loaded
assert ensure_torch_loaded(), 'torch must import before paddle'
from PIL import Image
p = Image.new('RGB', (200, 80), 'white')
p.save('blank_probe.png')
from app.services.ocr_service import get_paddle_english_engine
eng = get_paddle_english_engine()
assert eng is not None, 'paddle engine unavailable'
eng.ocr('blank_probe.png', cls=False)
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', local_files_only=True)
vec = m.encode(['नागरी सुविधा payments'], normalize_embeddings=True)
eng.ocr('blank_probe.png', cls=False)
assert vec.shape[1] == 384, vec.shape
print('BOTH_OK')
"""


@pytest.mark.skipif(not REAL_TESTS_ENABLED, reason="set DOCSENTINEL_REAL_ML_TESTS=1")
def test_real_paddle_without_torch_guard_reproduces_the_indexing_break(tmp_path):
    """Ground truth for the root cause: Paddle first leaves torch unimportable."""
    result = _run_python(_BAD_ORDER, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out[-2000:]
    assert "REPRODUCED OSError" in out, out[-2000:]


@pytest.mark.skipif(not REAL_TESTS_ENABLED, reason="set DOCSENTINEL_REAL_ML_TESTS=1")
def test_real_paddle_and_sentence_transformers_coexist_with_the_guard(tmp_path):
    result = _run_python(_GOOD_ORDER, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out[-2000:]
    assert "BOTH_OK" in out, out[-2000:]


@pytest.mark.skipif(not REAL_TESTS_ENABLED, reason="set DOCSENTINEL_REAL_ML_TESTS=1")
def test_real_ocr_then_real_indexing_finishes_in_one_process(
    tmp_path, monkeypatch, isolated_uploads
):
    """End-to-end with real engines: the exact chain that silently broke for doc 409.

    PaddleOCR runs in the pipeline thread, then the indexing thread loads
    sentence-transformers in the same process. Before the torch-ordering guard
    that second step raised OSError and the document stayed unsearchable.
    Runs against the isolated SQLite database and a throwaway Chroma directory.
    """
    from app.core.config import settings
    from app.services.embedding_service import (
        reset_chroma_client,
        semantic_search,
        set_encode_override,
    )

    # Real model, still fully isolated: the suite's dummy encoder is removed and
    # Chroma is repointed at this test's tmp directory.
    set_encode_override(None)
    monkeypatch.setattr(settings, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma-real"))
    reset_chroma_client()

    name = "mixed-real.pdf"
    pdf = isolated_uploads / name
    _mixed_pdf(
        pdf,
        "Invoice for consulting services. Total amount due: 5000 rupees. Payment terms net 30 days.",
        [
            "Salary slip for employee Ramesh Kumar",
            "Gross salary 42000 rupees per month",
            "Net paid amount 38500 rupees",
        ],
    )

    db = db_session.SessionLocal()
    try:
        document = Document(
            original_filename=name,
            stored_filename=name,
            file_path=str(Path("uploads") / name),
            file_type="application/pdf",
            status=DocumentStatus.PROCESSING.value,
        )
        db.add(document)
        db.commit()
        doc_id = document.id
    finally:
        db.close()

    documents_api.process_document_task(
        document_id=doc_id,
        disk_path=pdf,
        content_type="application/pdf",
        norm_lang="auto",
        department=None,
        document_type=None,
        user_id=None,
    )

    db = db_session.SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == doc_id).first()
        assert document.status not in ("failed", "PROCESSING"), document.status
        rows = db.query(OCRResult).filter(OCRResult.document_id == doc_id).all()
        texts = [r.extracted_text or "" for r in rows]
        engines = {r.ocr_engine for r in rows}
    finally:
        db.close()

    assert len(rows) == 2, "both pages must persist separately"
    assert any("Invoice" in t for t in texts), texts
    # The scanned page only exists as pixels, so anything recovered proves OCR ran.
    scanned = max(texts, key=len)
    assert any("salar" in t.lower() for t in texts), f"scanned page lost: {scanned!r}"
    assert any("PaddleOCR" in (e or "") for e in engines), engines

    # Indexing is scheduled on its own thread; wait for it to land.
    deadline = time.time() + 240
    indexed = 0
    status = None
    while time.time() < deadline:
        db = db_session.SessionLocal()
        try:
            indexed = (
                db.query(EmbeddingIndex)
                .filter(EmbeddingIndex.document_id == doc_id)
                .count()
            )
            status = db.query(Document).get(doc_id).status
        finally:
            db.close()
        if indexed:
            break
        time.sleep(2)

    assert indexed > 0, f"indexing thread produced no vectors (status={status})"
    assert status == "indexed", status

    db = db_session.SessionLocal()
    try:
        hits = semantic_search(db, "salary paid to employee", top_k=5)
    finally:
        db.close()
    assert doc_id in {h.document_id for h in hits.items}, [
        (h.document_id, h.similarity) for h in hits.items
    ]
