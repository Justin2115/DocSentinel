from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import documents as documents_api
from app.core.dependencies import get_current_user, require_upload_maker
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.models.user import User
from app.services.ocr_quality import SEVERE_TEXT_WARNINGS, assess_ocr_text
from app.services.ocr_service import extract_document_text


FIXTURE = Path(__file__).parent / "fixtures" / "CIA 1 Advance_Security_Lab.docx"


def test_supplied_docx_is_parsed_natively_with_title_tools_and_table():
    result = extract_document_text(
        FIXTURE,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert result.processing_status == "completed"
    assert "Advance Security Laboratory" in result.combined_text
    assert "Nmap" in result.combined_text
    assert "OWASP Juice Shop" in result.combined_text
    assert "Assigned safe target" in result.combined_text
    assert "word/document.xml" not in result.combined_text
    assert "PK!" not in result.combined_text
    assert result.ocr_engine == "python-docx"


def test_invalid_docx_package_is_rejected_instead_of_decoded(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK\x03\x04\x00\xff word/document.xml binary")
    result = extract_document_text(path, "application/octet-stream")
    assert result.processing_status == "failed"
    assert result.combined_text == ""
    assert result.error_message


def test_text_file_invalid_utf8_is_rejected(tmp_path):
    path = tmp_path / "broken.txt"
    path.write_bytes(b"ordinary text \xff")
    result = extract_document_text(path, "text/plain")
    assert result.processing_status == "failed"
    assert result.error_message == "text-file-is-not-valid-UTF-8"


def test_xlsx_uses_sheet_and_cell_native_extraction(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "costs.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Estimated Costs"
    sheet.append(["Item", "Amount"])
    sheet.append(["Electrical Work", "₹25,000"])
    workbook.save(path)
    workbook.close()

    result = extract_document_text(path, "application/octet-stream")
    assert result.processing_status == "completed"
    assert "Worksheet: Estimated Costs" in result.combined_text
    assert "A2: Electrical Work" in result.combined_text
    assert "B2: ₹25,000" in result.combined_text
    assert result.pages[0].notes == "worksheet:Estimated Costs"


def test_digitally_generated_pdf_uses_native_text(tmp_path):
    fitz = pytest.importorskip("pymupdf")
    path = tmp_path / "native.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Digitally generated security laboratory document with searchable text.")
    document.save(path)
    document.close()

    result = extract_document_text(path, "application/pdf")
    assert result.processing_status == "completed"
    assert "Digitally generated security laboratory" in result.combined_text
    assert result.pages[0].ocr_engine == "PyMuPDF"
    assert result.is_ocr_applied is False


def _scan_pdf(path: Path) -> None:
    fitz = pytest.importorskip("pymupdf")
    image = Image.new("RGB", (900, 1100), "white")
    draw = ImageDraw.Draw(image)
    for y in range(100, 900, 40):
        draw.rectangle((90, y, 760, y + 12), fill="black")
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    document = fitz.open()
    page = document.new_page()
    page.insert_image(page.rect, stream=image_buffer.getvalue())
    document.save(path)
    document.close()


def test_mixed_pdf_processes_native_and_scanned_pages_separately(tmp_path, monkeypatch):
    fitz = pytest.importorskip("pymupdf")
    path = tmp_path / "mixed.pdf"
    image_pdf = tmp_path / "scan.pdf"
    _scan_pdf(image_pdf)
    scan = fitz.open(image_pdf)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "This digitally generated page has enough native text to be retained.")
    document.insert_pdf(scan)
    document.save(path)
    document.close()
    scan.close()

    calls = []

    def fake_ocr(_image, language="auto"):
        calls.append(language)
        return "Scanned page recognized by test provider.", 91.0, "mock-ocr", "en"

    monkeypatch.setattr("app.services.ocr_service._perform_image_ocr", fake_ocr)
    result = extract_document_text(path, "application/pdf", language="en")
    assert len(result.pages) == 2
    assert result.pages[0].ocr_engine == "PyMuPDF"
    assert result.pages[1].ocr_engine == "mock-ocr"
    assert "Scanned page recognized" in result.pages[1].text
    assert len(calls) == 1


@pytest.mark.parametrize("text", [
    "A readable English security report includes a useful title and details.",
    "यह चालान भुगतान की देय तारीख और कुल राशि बताता है।",
    "या मराठी दस्तऐवजात खर्चाची एकूण रक्कम आणि देय तारीख नमूद आहे.",
])
def test_valid_english_hindi_and_marathi_are_not_marked_as_binary(text):
    assessment = assess_ocr_text(text)
    assert not (set(assessment.warnings) & SEVERE_TEXT_WARNINGS)


def test_image_signature_is_decoded_and_existing_ocr_path_is_used(tmp_path, monkeypatch):
    path = tmp_path / "actual.png"
    Image.new("RGB", (300, 300), "white").save(path)
    calls = []

    def fake_ocr(image, language="auto"):
        calls.append(image.size)
        return "A real image contains readable test text.", 88.0, "mock-ocr", "en"

    monkeypatch.setattr("app.services.ocr_service._perform_image_ocr", fake_ocr)
    result = extract_document_text(path, "application/octet-stream", language="en")
    assert result.processing_status == "completed"
    assert "readable test text" in result.combined_text
    assert calls


@pytest.mark.parametrize("provider_response", [
    ("", 0, "mock", "en"),
    {},
    None,
])
def test_empty_or_malformed_image_provider_response_fails_cleanly(tmp_path, monkeypatch, provider_response):
    path = tmp_path / "empty.png"
    Image.new("RGB", (300, 300), "white").save(path)
    monkeypatch.setattr("app.services.ocr_service._perform_image_ocr", lambda *_a, **_k: provider_response)
    result = extract_document_text(path, "image/png")
    assert result.processing_status == "failed"
    assert result.combined_text == ""


def test_office_package_markers_and_control_characters_are_suspicious():
    assert "binary-package-markers" in assess_ocr_text("PK\x03\x04 word/document.xml").warnings
    assert "invalid-control-characters" in assess_ocr_text("Report\x00text").warnings


@pytest.fixture
def upload_test_environment(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'upload-test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db = test_session()
    user = User(id=1, email="ingestion-test@example.invalid", name="Ingestion Test", role="UPLOAD_MAKER", is_active=True)
    db.add(user)
    db.commit()
    db.close()

    def override_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[require_upload_maker] = lambda: user
    monkeypatch.setattr(documents_api, "schedule_document_indexing", lambda _document_id: None)
    client = TestClient(app)
    yield client, test_session
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(require_upload_maker, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_actual_docx_upload_persists_real_text_without_package_garbage(upload_test_environment):
    client, test_session = upload_test_environment
    response = client.post(
        "/api/documents/upload",
        files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]
    with test_session() as db:
        document = db.query(Document).filter(Document.id == document_id).one()
        result_text = "\n".join(row.extracted_text or "" for row in document.ocr_results)
        assert "OWASP Juice Shop" in result_text
        assert "Nmap" in result_text
        assert "word/document.xml" not in result_text
        assert document.status != "failed"
        stored_file = Path(documents_api.UPLOAD_DIR) / document.stored_filename
    stored_file.unlink(missing_ok=True)


def test_successful_reprocess_replaces_rows_without_duplicates(upload_test_environment):
    client, test_session = upload_test_environment
    response = client.post(
        "/api/documents/upload",
        files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]
    with test_session() as db:
        stored_name = db.query(Document).filter(Document.id == document_id).one().stored_filename
    stored_file = Path(documents_api.UPLOAD_DIR) / stored_name
    try:
        retry = client.post(f"/api/documents/{document_id}/reprocess")
        assert retry.status_code == 200, retry.text
        with test_session() as db:
            document = db.query(Document).filter(Document.id == document_id).one()
            assert db.query(DocumentPage).filter(DocumentPage.document_id == document_id).count() == 1
            assert db.query(OCRResult).filter(OCRResult.document_id == document_id).count() == 1
            assert "OWASP Juice Shop" in db.query(OCRResult).filter(OCRResult.document_id == document_id).one().extracted_text
            assert document.status != "failed"
    finally:
        stored_file.unlink(missing_ok=True)


def test_gibberish_upload_is_failed_and_never_persisted_or_indexed(upload_test_environment):
    client, test_session = upload_test_environment
    indexed = []
    documents_api.schedule_document_indexing = lambda document_id: indexed.append(document_id)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("bad.txt", b"PK\x03\x04 word/document.xml", "text/plain")},
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]
    with test_session() as db:
        document = db.query(Document).filter(Document.id == document_id).one()
        assert document.status == "failed"
        assert db.query(OCRResult).filter(OCRResult.document_id == document_id).count() == 0
    assert document_id not in indexed
    with test_session() as db:
        stored_file = Path(documents_api.UPLOAD_DIR) / db.query(Document).filter(Document.id == document_id).one().stored_filename
    stored_file.unlink(missing_ok=True)


def test_failed_reprocess_preserves_prior_valid_extraction(upload_test_environment):
    client, test_session = upload_test_environment
    stored_name = "retry-invalid-package.docx"
    stored_path = Path(documents_api.UPLOAD_DIR) / stored_name
    stored_path.write_bytes(b"PK\x03\x04 broken office package")
    with test_session() as db:
        document = Document(
            original_filename=stored_name,
            stored_filename=stored_name,
            file_path=f"uploads/{stored_name}",
            file_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            uploaded_by=1,
            status="indexed",
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        page = DocumentPage(document_id=document.id, page_number=1)
        db.add(page)
        db.flush()
        db.add(OCRResult(document_id=document.id, page_id=page.id, extracted_text="Previously valid extracted text."))
        db.commit()
        document_id = document.id
    try:
        response = client.post(f"/api/documents/{document_id}/reprocess")
        assert response.status_code == 422
        with test_session() as db:
            rows = db.query(OCRResult).filter(OCRResult.document_id == document_id).all()
            assert [row.extracted_text for row in rows] == ["Previously valid extracted text."]
            assert db.query(Document).filter(Document.id == document_id).one().status == "indexed"
    finally:
        stored_path.unlink(missing_ok=True)
