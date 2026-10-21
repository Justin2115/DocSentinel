"""Regression tests for the OCR routing, preprocessing escalation, background
upload processing and indexing-status safeguards.

These tests use synthetic PDFs and mocked OCR engines only — no real PaddleOCR /
RapidOCR model is loaded and no production resource is touched (conftest binds
the suite to a throwaway SQLite database and a temp Chroma directory).
"""

import io
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from app.services import ocr_service
from app.services.ocr_service import DocumentExtractionResult, ExtractedPage
from tests.helpers import get_test_auth_headers


def _uploader_headers() -> dict[str, str]:
    """Bearer token for an UPLOAD_MAKER, which POST /api/documents/upload requires."""
    import app.db.session as db_session

    db = db_session.SessionLocal()
    try:
        return get_test_auth_headers(db, email="pipeline-maker@docsentinel.test", role="UPLOAD_MAKER")
    finally:
        db.close()


def _digital_pdf(path: Path, pages_text: list[str]) -> None:
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(fitz.Rect(40, 40, 555, 800), text, fontsize=11)
    doc.save(str(path))
    doc.close()


def _scanned_pdf(path: Path, n_pages: int = 1) -> None:
    """PDF whose pages are only page-sized raster images (no text layer)."""
    doc = fitz.open()
    imgdoc = fitz.open()
    page = imgdoc.new_page(width=595, height=842)
    page.insert_textbox(fitz.Rect(40, 40, 555, 200), "rendered scan content", fontsize=20)
    pix = page.get_pixmap(dpi=100)
    img_bytes = pix.tobytes("png")
    imgdoc.close()
    for _ in range(n_pages):
        target = doc.new_page(width=595, height=842)
        target.insert_image(target.rect, stream=img_bytes)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def no_real_ocr(monkeypatch):
    """Ensure no test can lazily initialize a real OCR engine."""
    monkeypatch.setattr(ocr_service, "_rapid_ocr_engine", False)
    monkeypatch.setattr(ocr_service, "_paddle_devanagari_engine", False)
    monkeypatch.setattr(ocr_service, "_paddle_english_engine", False)


class TestDigitalFastPath:
    def test_digital_pages_never_call_ocr(self, tmp_path, monkeypatch, no_real_ocr):
        pdf = tmp_path / "digital.pdf"
        _digital_pdf(pdf, ["Invoice number INV-1001 total amount $50.00\n" * 3])

        def fail_ocr(*args, **kwargs):
            raise AssertionError("OCR must not run on pages with usable embedded text")

        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fail_ocr)

        result = ocr_service.process_pdf_document(pdf, language="auto")
        assert len(result.pages) == 1
        assert "INV-1001" in result.pages[0].text
        assert not result.is_ocr_applied
        assert result.ocr_engine == "PyMuPDF"

    def test_mixed_pdf_ocrs_only_scanned_pages(self, tmp_path, monkeypatch, no_real_ocr):
        doc = fitz.open()
        p1 = doc.new_page(width=595, height=842)
        p1.insert_textbox(fitz.Rect(40, 40, 555, 300), "Digital page with enough embedded text content here", fontsize=11)
        imgdoc = fitz.open()
        ip = imgdoc.new_page(width=595, height=842)
        ip.insert_textbox(fitz.Rect(40, 40, 500, 120), "scan", fontsize=30)
        pix = ip.get_pixmap(dpi=100)
        imgdoc.close()
        p2 = doc.new_page(width=595, height=842)
        p2.insert_image(p2.rect, stream=pix.tobytes("png"))
        pdf = tmp_path / "mixed.pdf"
        doc.save(str(pdf))
        doc.close()

        calls: list[int] = []
        state = {"page": 0}

        def fake_ocr(image, language="auto"):
            calls.append(1)
            return "महाराष्ट्र state certificate", 88.0, "FakePaddle", "mr", []

        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)

        result = ocr_service.process_pdf_document(pdf, language="auto")
        assert len(result.pages) == 2
        assert len(calls) == 1, "OCR should run exactly once (only the scanned page)"
        # The digital page was never OCR'd, so it has no measured confidence to
        # report; the scanned page keeps the engine's own figure.
        assert result.pages[0].confidence is None
        assert result.pages[1].confidence == 88.0
        assert "महाराष्ट्र" in result.pages[1].text

    def test_corrupt_or_empty_pdf_is_handled(self, tmp_path, no_real_ocr):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"%PDF-1.4 broken not a real pdf")
        result = ocr_service.process_pdf_document(bad, language="auto")
        assert result.ocr_engine == "Error"
        assert result.pages == []


class TestStampAndPreprocessRouting:
    def test_page_sized_images_skipped_for_stamp_ocr(self, tmp_path, monkeypatch, no_real_ocr):
        pdf = tmp_path / "scan_with_text.pdf"
        _digital_pdf(pdf, ["Page with real digital text layer content for classification"] * 2)
        # Embed a page-sized image on the digital page
        doc = fitz.open(str(pdf))
        page = doc[0]
        imgdoc = fitz.open()
        ip = imgdoc.new_page(width=595, height=842)
        ip.insert_textbox(fitz.Rect(40, 40, 500, 120), "PAID", fontsize=40)
        pix = ip.get_pixmap(dpi=150)
        imgdoc.close()
        page.insert_image(page.rect, stream=pix.tobytes("png"))
        pdf2 = tmp_path / "scan_with_text2.pdf"
        doc.save(str(pdf2))
        doc.close()

        used: list[bytes] = []

        class FakeEngine:
            def __call__(self, img_bytes):
                used.append(img_bytes)
                return ([[[[0, 0], [1, 0], [1, 1], [0, 1]], "PAID", 0.99]], None)

        monkeypatch.setattr(ocr_service, "_rapid_ocr_engine", FakeEngine())

        result = ocr_service.process_pdf_document(pdf2, language="en")
        assert used == [], "Full-page raster images must not be re-OCRed as stamps"
        assert "PAID" not in (result.pages[0].status_stamps or [])

    def test_small_stamp_images_still_read(self, tmp_path, monkeypatch, no_real_ocr):
        pdf = tmp_path / "invoice.pdf"
        _digital_pdf(pdf, ["Invoice number INV-77 total amount $10.00 payment due soon content"])
        doc = fitz.open(str(pdf))
        page = doc[0]
        stamp = fitz.open()
        sp = stamp.new_page(width=120, height=60)
        sp.insert_textbox(fitz.Rect(5, 5, 115, 55), "PAID", fontsize=20)
        pix = sp.get_pixmap(dpi=150)
        stamp.close()
        page.insert_image(fitz.Rect(400, 700, 480, 760), stream=pix.tobytes("png"))
        pdf2 = tmp_path / "invoice2.pdf"
        doc.save(str(pdf2))
        doc.close()

        class FakeEngine:
            def __call__(self, img_bytes):
                return ([[[[0, 0], [1, 0], [1, 1], [0, 1]], "PAID", 0.99]], None)

        monkeypatch.setattr(ocr_service, "_rapid_ocr_engine", FakeEngine())

        result = ocr_service.process_pdf_document(pdf2, language="en")
        assert result.pages[0].status_stamps == ["PAID"]

    def test_preprocessing_escalation_when_first_pass_fails(self, tmp_path, monkeypatch, no_real_ocr):
        pdf = tmp_path / "poor_scan.pdf"
        _scanned_pdf(pdf, n_pages=1)

        attempts: list[str] = []

        def fake_ocr(image, language="auto"):
            attempts.append("preprocessed" if getattr(image, "_docsentinel_pre", False) else "raw")
            if attempts[-1] == "preprocessed":
                return "recovered text after preprocessing", 72.0, "FakePaddle", "en", []
            return "", 0.0, "FakePaddle", "en", []

        def fake_preprocess(image):
            from PIL import Image

            out = Image.new("RGB", (40, 40))
            out._docsentinel_pre = True
            return out

        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)
        monkeypatch.setattr(ocr_service, "preprocess_scan_image", fake_preprocess)

        result = ocr_service.process_pdf_document(pdf, language="auto")
        assert attempts == ["raw", "preprocessed"]
        assert "recovered text" in result.pages[0].text
        assert any("Preprocessed" in e for e in [result.ocr_engine])


class TestModelInitReuse:
    def test_failed_engine_init_is_cached(self, monkeypatch):
        monkeypatch.setattr(ocr_service, "_rapid_ocr_engine", False)
        assert ocr_service.get_rapid_ocr_engine() is None
        assert ocr_service._rapid_ocr_engine is False  # not retried per page

    def test_initialized_engine_reused(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(ocr_service, "_rapid_ocr_engine", sentinel)
        assert ocr_service.get_rapid_ocr_engine() is sentinel


class TestIndexedStatusGuard:
    def test_review_status_not_downgraded_to_indexed(self, no_real_ocr):
        from app.models.document import Document
        from app.services.embedding_service import _apply_indexed_status

        doc = Document(original_filename="a.pdf", stored_filename="a.pdf", status="PENDING_REVIEW")
        _apply_indexed_status(doc)
        assert doc.status == "PENDING_REVIEW"

        doc2 = Document(original_filename="b.pdf", stored_filename="b.pdf", status="failed")
        _apply_indexed_status(doc2)
        assert doc2.status == "failed"

        doc3 = Document(original_filename="c.pdf", stored_filename="c.pdf", status="completed")
        _apply_indexed_status(doc3)
        assert doc3.status == "indexed"


class TestBackgroundUpload:
    def test_upload_returns_processing_and_task_completes(self, tmp_path, monkeypatch):
        from PIL import Image

        from app.api import documents as documents_api
        from app.services import upload_service
        from app.models.document import Document
        import app.db.session as db_session

        # Redirect uploads to a temp dir so the real uploads folder is untouched
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        monkeypatch.setattr(upload_service, "UPLOAD_DIR", uploads_dir)
        monkeypatch.setattr(documents_api, "UPLOAD_DIR", uploads_dir)

        # Fake OCR pipeline output (no real model runs)
        fake_result = DocumentExtractionResult(
            pages=[ExtractedPage(page_number=1, text="Contract between parties with signature line", confidence=95.0)],
            combined_text="Contract between parties with signature line",
            overall_ocr_confidence=95.0,
            ocr_engine="Fake",
            is_ocr_applied=False,
        )
        ocr_calls: list[int] = []

        def fake_extract(file_path, file_type, language="auto", only_pages=None, on_page=None):
            ocr_calls.append(1)
            return fake_result

        monkeypatch.setattr(ocr_service, "extract_document_text", fake_extract)

        indexed: list[int] = []
        monkeypatch.setattr(
            documents_api,
            "schedule_document_indexing",
            lambda document_id: indexed.append(document_id),
        )

        client = TestClient(documents_api.app if hasattr(documents_api, "app") else _get_main_app())
        resp = client.post(
            "/api/documents/upload",
            files={"file": ("sample.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"language": "auto"},
            headers=_uploader_headers(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "PROCESSING"
        doc_id = body["id"]

        # TestClient executes background tasks before returning: the pipeline
        # must have run and moved the document out of PROCESSING.
        assert ocr_calls == [1]
        assert indexed == [doc_id]

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document is not None
            assert document.status in ("completed", "PENDING_REVIEW")
        finally:
            db.close()

    def test_failed_pipeline_marks_document_failed(self, tmp_path, monkeypatch):
        from app.api import documents as documents_api
        from app.services import upload_service
        from app.models.document import Document
        import app.db.session as db_session

        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        monkeypatch.setattr(upload_service, "UPLOAD_DIR", uploads_dir)
        monkeypatch.setattr(documents_api, "UPLOAD_DIR", uploads_dir)

        def boom(file_path, file_type, language="auto", only_pages=None, on_page=None):
            raise RuntimeError("OCR engine exploded")

        monkeypatch.setattr(ocr_service, "extract_document_text", boom)
        monkeypatch.setattr(documents_api, "schedule_document_indexing", lambda document_id: None)

        client = TestClient(_get_main_app())
        resp = client.post(
            "/api/documents/upload",
            files={"file": ("broken.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"language": "auto"},
            headers=_uploader_headers(),
        )
        assert resp.status_code == 201
        doc_id = resp.json()["id"]

        db = db_session.SessionLocal()
        try:
            document = db.query(Document).filter(Document.id == doc_id).first()
            assert document.status == "failed"
        finally:
            db.close()

    def test_duplicate_task_delivery_is_skipped(self, tmp_path, monkeypatch):
        """A document already moved out of PROCESSING must not be reprocessed."""
        from app.api import documents as documents_api
        from app.models.document import Document
        import app.db.session as db_session

        doc = Document(
            original_filename="done.pdf",
            stored_filename="done.pdf",
            file_path="uploads/done.pdf",
            file_type="application/pdf",
            file_size=1,
            status="completed",
        )
        db = db_session.SessionLocal()
        db.add(doc)
        db.commit()
        doc_id = doc.id
        db.close()

        calls: list[int] = []
        monkeypatch.setattr(ocr_service, "extract_document_text", lambda *a, **k: calls.append(1) or DocumentExtractionResult())

        documents_api.process_document_task(
            document_id=doc_id,
            disk_path=tmp_path / "missing.pdf",
            content_type="application/pdf",
            norm_lang="auto",
            department=None,
            document_type=None,
            user_id=None,
        )
        assert calls == []


def _get_main_app():
    from app.main import app

    return app
