"""Honest processing outcomes: real Office parsing, and failures that stay visible.

These cover the three ways a run used to report success it had not earned:
a .docx/.xlsx read as raw bytes at an invented 95%, pages whose OCR raised, and
an extraction report whose quality flags the pipeline ignored. Everything runs on
temporary files in tmp_path against the throwaway database conftest binds.
"""

import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import ocr_service
from app.services.ocr_service import (
    DocumentExtractionResult,
    ExtractedPage,
    process_structured_document,
)


DOCX_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:r><w:t>Invoice 42</w:t></w:r></w:p>"
    "<w:tbl><w:tr>"
    "<w:tc><w:p><w:r><w:t>Total</w:t></w:r></w:p></w:tc>"
    "<w:tc><w:p><w:r><w:t>$120.00</w:t></w:r></w:p></w:tc>"
    "</w:tr></w:tbl>"
    "<w:p><w:r><w:t>Due on 01/02/2026</w:t></w:r></w:p>"
    "</w:body></w:document>"
)

XLSX_WORKBOOK_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Ledger" sheetId="1" r:id="rId1"/></sheets></workbook>'
)

XLSX_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
    '/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
)

XLSX_SHARED_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">'
    "<si><t>Client</t></si><si><t>महाराष्ट्र</t></si></sst>"
)

XLSX_SHEET_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<sheetData>'
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>120</v></c></row>'
    '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="s"><v>0</v></c></row>'
    "</sheetData></worksheet>"
)


def _write_docx(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", DOCX_DOCUMENT_XML)


def _write_xlsx(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", XLSX_WORKBOOK_XML)
        archive.writestr("xl/_rels/workbook.xml.rels", XLSX_RELS_XML)
        archive.writestr("xl/sharedStrings.xml", XLSX_SHARED_XML)
        archive.writestr("xl/worksheets/sheet1.xml", XLSX_SHEET_XML)


class TestOfficeFormats:
    def test_docx_paragraphs_and_table_rows_keep_reading_order(self, tmp_path):
        path = tmp_path / "invoice.docx"
        _write_docx(path)

        result = process_structured_document(path, ".docx")

        lines = result.combined_text.splitlines()
        assert lines == ["Invoice 42", "Total | $120.00", "Due on 01/02/2026"]
        assert result.pages[0].page_number == 1
        assert result.pages[0].table_count == 0

    def test_xlsx_rows_keep_columns_and_sheet_names(self, tmp_path):
        path = tmp_path / "ledger.xlsx"
        _write_xlsx(path)

        result = process_structured_document(path, ".xlsx")

        lines = result.combined_text.splitlines()
        assert lines[0] == "[sheet: Ledger]"
        assert lines[1] == "Client | 120"
        assert "महाराष्ट्र | Client" in lines[2]

    def test_office_text_is_never_given_an_invented_confidence(self, tmp_path):
        path = tmp_path / "invoice.docx"
        _write_docx(path)

        result = process_structured_document(path, ".docx")

        assert result.pages[0].confidence is None
        assert result.overall_ocr_confidence == 0.0
        assert not result.is_ocr_applied

    def test_legacy_binary_office_file_fails_with_an_actionable_message(self, tmp_path):
        # OLE compound-file header: no installed engine can read this, and
        # decoding it as text used to produce "success" at 95%.
        for suffix in (".doc", ".xls"):
            path = tmp_path / f"legacy{suffix}"
            path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 2048)

            with pytest.raises(ValueError) as raised:
                process_structured_document(path, suffix)

            assert "PDF" in str(raised.value)

    def test_unknown_binary_is_rejected_instead_of_garbled_text(self, tmp_path):
        path = tmp_path / "mystery.bin"
        path.write_bytes(bytes(range(256)) * 8)

        with pytest.raises(ValueError):
            process_structured_document(path, ".bin")


def _fake_extraction(pages: list[ExtractedPage], **kwargs) -> DocumentExtractionResult:
    combined = "\n\n".join(page.text for page in pages if page.text).strip()
    measured = [
        page.confidence for page in pages if page.confidence is not None
    ]
    return DocumentExtractionResult(
        pages=pages,
        combined_text=combined,
        overall_ocr_confidence=(
            round(sum(measured) / len(measured), 2) if measured else 0.0
        ),
        ocr_engine=kwargs.pop("ocr_engine", "Fake"),
        ocr_failed_pages=sum(1 for page in pages if page.ocr_failed),
        **kwargs,
    )


def _upload(client, filename: str) -> int:
    """Upload as an UPLOAD_MAKER; the endpoint requires an authenticated owner."""
    from app.db.session import SessionLocal
    from tests.helpers import get_test_auth_headers

    db = SessionLocal()
    try:
        headers = get_test_auth_headers(
            db, email="honesty-maker@docsentinel.test", role="UPLOAD_MAKER"
        )
    finally:
        db.close()

    response = client.post(
        "/api/documents/upload",
        files={"file": (filename, b"%PDF-1.4 fake", "application/pdf")},
        data={"language": "auto"},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def uploads_in_tmp(tmp_path, monkeypatch):
    from app.api import documents as documents_api
    from app.services import upload_service

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(upload_service, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(documents_api, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(documents_api, "schedule_document_indexing", lambda document_id: None)
    return uploads_dir


def _status_of(document_id: int):
    from app.db.session import SessionLocal
    from app.models.document import Document, ReviewQueue

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


class TestFailureIsNotSuccess:
    def test_every_page_failing_ocr_marks_the_document_failed(
        self, uploads_in_tmp, monkeypatch
    ):
        pages = [
            ExtractedPage(page_number=1, text="", confidence=None, page_type="scanned", ocr_failed=True),
            ExtractedPage(page_number=2, text="", confidence=None, page_type="scanned", ocr_failed=True),
        ]
        monkeypatch.setattr(
            ocr_service,
            "extract_document_text",
            lambda *args, **kwargs: _fake_extraction(
                pages, is_ocr_applied=True, ocr_engine="PyMuPDF+Failed"
            ),
        )

        doc_id = _upload(TestClient(app), "scan.pdf")

        status, _ = _status_of(doc_id)
        assert status == "failed"

    def test_partially_failed_pages_are_reviewable_with_the_reason(
        self, uploads_in_tmp, monkeypatch
    ):
        pages = [
            ExtractedPage(
                page_number=1,
                text="Invoice INV-7 total amount $40.00 due 02/03/2026 client name sample",
                confidence=91.0,
                page_type="scanned",
                engine="PyMuPDF+Fake",
            ),
            ExtractedPage(
                page_number=2,
                text="Payment terms net thirty days for invoice INV-7",
                confidence=88.0,
                page_type="scanned",
                engine="PyMuPDF+Fake",
            ),
            ExtractedPage(
                page_number=3,
                text="",
                confidence=None,
                page_type="scanned",
                ocr_failed=True,
                engine="PyMuPDF+Failed",
            ),
        ]
        monkeypatch.setattr(
            ocr_service,
            "extract_document_text",
            lambda *args, **kwargs: _fake_extraction(
                pages, is_ocr_applied=True, ocr_engine="PyMuPDF+Fake, PyMuPDF+Failed"
            ),
        )

        doc_id = _upload(TestClient(app), "mixed.pdf")

        status, reasons = _status_of(doc_id)
        assert status == "PENDING_REVIEW"
        assert any("failed OCR" in (reason or "") for reason in reasons)

    def test_a_clean_document_does_not_enter_the_review_queue(
        self, uploads_in_tmp, monkeypatch
    ):
        pages = [
            ExtractedPage(
                page_number=1,
                text=(
                    "Invoice INV-900 dated 04/05/2026 client Acme Supplies\n"
                    "Total amount $250.00 payment due 04/06/2026\n"
                ),
                confidence=96.0,
                page_type="scanned",
                engine="PyMuPDF+Fake",
            )
        ]
        monkeypatch.setattr(
            ocr_service,
            "extract_document_text",
            lambda *args, **kwargs: _fake_extraction(
                pages, is_ocr_applied=True, ocr_engine="PyMuPDF+Fake"
            ),
        )

        doc_id = _upload(TestClient(app), "clean.pdf")

        status, reasons = _status_of(doc_id)
        assert status == "completed"
        assert reasons == []

    def test_failure_reason_reaches_the_document_detail(
        self, uploads_in_tmp, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError("PaddleOCR could not start")

        monkeypatch.setattr(ocr_service, "extract_document_text", boom)

        client = TestClient(app)
        doc_id = _upload(client, "broken.pdf")

        from app.db.session import SessionLocal
        from tests.helpers import get_test_auth_headers

        with SessionLocal() as db:
            headers = get_test_auth_headers(db, "detail-admin@docsentinel.test", "ADMIN")

        detail = client.get(f"/api/documents/{doc_id}", headers=headers).json()
        assert detail["status"] == "failed"
        assert any(
            "PaddleOCR could not start" in (item["reason"] or "")
            for item in detail["review_items"]
        )

    def test_low_confidence_needs_review_even_when_the_type_is_known(
        self, uploads_in_tmp, monkeypatch
    ):
        pages = [
            ExtractedPage(
                page_number=1,
                text="receipt cash 12 store",
                confidence=22.0,
                page_type="scanned",
                engine="PyMuPDF+Fake",
            )
        ]
        monkeypatch.setattr(
            ocr_service,
            "extract_document_text",
            lambda *args, **kwargs: _fake_extraction(
                pages, is_ocr_applied=True, ocr_engine="PyMuPDF+Fake"
            ),
        )

        doc_id = _upload(TestClient(app), "blurry.pdf")

        status, reasons = _status_of(doc_id)
        assert status == "PENDING_REVIEW"
        assert any("onfidence" in (reason or "") for reason in reasons)
