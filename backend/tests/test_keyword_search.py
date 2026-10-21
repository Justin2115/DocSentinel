import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models.document import Document, ExtractedField, OCRResult
from app.services.search_service import (
    _fuzzy_query_tokens,
    keyword_search,
    make_snippet,
)


def test_make_snippet_centers_on_match():
    text = "aaa " + ("x" * 80) + " invoice total 1200 " + ("y" * 80)
    snippet = make_snippet(text, "invoice")
    assert "invoice" in snippet.lower()
    assert snippet.startswith("…") or "aaa" not in snippet


def test_make_snippet_uses_match_lines_not_document_tail():
    body = (
        "Header notes.\n"
        "Payment is overdue for this invoice totaling 1200 rupees.\n"
        "Please settle promptly.\n"
        + ("Closing remarks.\n" * 40)
    )
    snippet = make_snippet(body, "invoice")
    assert "invoice totaling 1200" in snippet
    assert "Closing remarks" not in snippet


def test_make_snippet_centers_inside_a_long_line():
    text = ("head " * 80) + "significant finding reported " + ("tail " * 80)
    snippet = make_snippet(text, "significant")
    assert "significant finding" in snippet
    assert "head" in snippet
    assert snippet.startswith("…")
    assert not snippet.endswith("tail " * 10)


def test_keyword_search_finds_filename_ocr_and_fields():
    db = SessionLocal()
    document = Document(
        original_filename="july_invoice_report.pdf",
        stored_filename="july_invoice_report.pdf",
        file_path="uploads/july_invoice_report.pdf",
        file_type="application/pdf",
        file_size=100,
        status="completed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)

        db.add(
            OCRResult(
                document_id=document.id,
                extracted_text="Payment is overdue for this invoice totaling 1200 rupees.",
            )
        )
        db.add(
            ExtractedField(
                document_id=document.id,
                field_name="vendor",
                field_value="Acme Supplies",
            )
        )
        db.commit()

        invoice_hits = keyword_search(db, "invoice", skip=0, limit=20)
        assert invoice_hits.total >= 2
        assert any(hit.document_id == document.id for hit in invoice_hits.items)
        assert any(hit.match_field == "filename" for hit in invoice_hits.items)
        assert any(
            hit.match_field == "extracted_text" and "1200" in hit.snippet
            for hit in invoice_hits.items
        )

        vendor_hits = keyword_search(db, "Acme", skip=0, limit=20)
        assert any(
            hit.document_id == document.id and hit.match_field == "field_value"
            for hit in vendor_hits.items
        )
    finally:
        db.query(ExtractedField).filter(
            ExtractedField.document_id == document.id
        ).delete()
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()


def test_fuzzy_query_tokens_include_identifiers():
    """Regression: identifier-style tokens (e.g. INV-3337) must be extracted for OCR typo recovery.

    Previously _fuzzy_query_tokens required isalpha(), so any token containing
    digits was discarded and identifier OCR errors could never be recovered.
    """
    tokens = _fuzzy_query_tokens("Show invoice INV-3337 details")
    assert "inv-3337" in tokens
    # Ordinary short words and stop words are still excluded.
    assert "show" not in tokens


def test_fuzzy_ocr_recovers_misread_identifier():
    """Letter-level OCR damage inside an identifier is recoverable via fuzzy matching."""
    db = SessionLocal()
    document = Document(
        original_filename="ocr_invoice.pdf",
        stored_filename="ocr_invoice.pdf",
        file_path="uploads/ocr_invoice.pdf",
        file_type="application/pdf",
        file_size=100,
        status="completed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        # OCR read the capital I as a lowercase l: INV-3337 -> lNV-3337
        db.add(
            OCRResult(
                document_id=document.id,
                extracted_text="Invoice lNV-3337 due on January 31.",
            )
        )
        db.commit()

        hits = keyword_search(db, "INV-3337", skip=0, limit=20)
        assert any(
            hit.document_id == document.id and hit.match_field == "fuzzy_ocr"
            for hit in hits.items
        ), "Expected a fuzzy_ocr hit recovering the misread identifier"
    finally:
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()


def test_fuzzy_ocr_does_not_merge_different_reference_numbers():
    """One differing digit makes it a different reference, so no merge is allowed.

    This is the stricter side of identifier matching: a recogniser that flips a
    digit has produced another document's number, and presenting it as the same
    one would be a fabrication.
    """
    db = SessionLocal()
    document = Document(
        original_filename="ocr_other_reference.pdf",
        stored_filename="ocr_other_reference.pdf",
        file_path="uploads/ocr_other_reference.pdf",
        file_type="application/pdf",
        file_size=100,
        status="completed",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        db.add(
            OCRResult(
                document_id=document.id,
                extracted_text="Invoice INV-3331 due on January 31.",
            )
        )
        db.commit()

        hits = keyword_search(db, "INV-3337", skip=0, limit=20)
        assert not any(
            hit.document_id == document.id for hit in hits.items
        ), "A reference number differing by one digit must not be returned"
    finally:
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()
