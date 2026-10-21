"""Phase regression tests: blank detection, orientation retry, digital table
extraction, phone-vs-reference hardening, duplicate-page dedup and stage metrics.

All OCR engines are mocked — no real model runs here. Real-model quality metrics
are produced by the separate evaluation script.
"""

import io
import logging

import pymupdf as fitz
import pytest

from app.services import ocr_service
from app.services.extraction_service import extract_fields_from_text


@pytest.fixture
def no_real_ocr(monkeypatch):
    monkeypatch.setattr(ocr_service, "_rapid_ocr_engine", False)
    monkeypatch.setattr(ocr_service, "_paddle_devanagari_engine", False)
    monkeypatch.setattr(ocr_service, "_paddle_english_engine", False)


def _white_scan_pdf(path, n_pages=1):
    doc = fitz.open()
    imgdoc = fitz.open()
    ip = imgdoc.new_page(width=595, height=842)  # pure white render
    pix = ip.get_pixmap(dpi=100)
    img_bytes = pix.tobytes("png")
    imgdoc.close()
    for _ in range(n_pages):
        t = doc.new_page(width=595, height=842)
        t.insert_image(t.rect, stream=img_bytes)
    doc.save(str(path))
    doc.close()


class TestBlankAndOrientation:
    def test_blank_pages_skip_ocr(self, tmp_path, monkeypatch, no_real_ocr):
        pdf = tmp_path / "blank.pdf"
        _white_scan_pdf(pdf, n_pages=2)

        calls: list[int] = []
        monkeypatch.setattr(
            ocr_service,
            "_perform_image_ocr",
            lambda *a, **k: calls.append(1) or ("", 0.0, "Fake", "en", []),
        )

        result = ocr_service.process_pdf_document(pdf, language="auto")
        assert calls == []
        assert all(p.page_type == "blank" for p in result.pages)

    def test_orientation_retry_used_when_page_rotated(self, tmp_path, monkeypatch, no_real_ocr):
        # Landscape page holding a tall portrait scan: OCR only succeeds after
        # a 90-degree rotation.
        doc = fitz.open()
        page = doc.new_page(width=842, height=595)
        imgdoc = fitz.open()
        ip = imgdoc.new_page(width=595, height=842)
        ip.insert_textbox(fitz.Rect(30, 30, 565, 400), "rotated scan content", fontsize=24)
        pix = ip.get_pixmap(dpi=120)
        imgdoc.close()
        page.insert_image(page.rect, stream=pix.tobytes("png"))
        pdf = tmp_path / "rotated.pdf"
        doc.save(str(pdf))
        doc.close()

        def fake_ocr(image, language="auto"):
            # Recognizes text only in portrait orientation
            if image.size[1] > image.size[0]:
                return "recovered rotated page text content", 80.0, "FakePaddle", "en", []
            return "", 0.0, "FakePaddle", "en", []

        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)
        monkeypatch.setattr(ocr_service, "preprocess_scan_image", lambda img: img)

        result = ocr_service.process_pdf_document(pdf, language="auto")
        page = result.pages[0]
        assert page.ocr_retried
        assert "Rot90" in page.text or "Rot90" in result.ocr_engine or page.page_type == "scanned"
        assert "recovered rotated page text" in page.text

    def test_stage_metrics_emitted(self, tmp_path, monkeypatch, caplog, no_real_ocr):
        pdf = tmp_path / "digital.pdf"
        doc = fitz.open()
        p = doc.new_page(width=595, height=842)
        p.insert_textbox(fitz.Rect(40, 40, 555, 300), "Digital text page for metrics assertion content", fontsize=11)
        doc.save(str(pdf))
        doc.close()

        with caplog.at_level(logging.INFO, logger="docsentinel.metrics"):
            ocr_service.process_pdf_document(pdf, language="auto")

        joined = "\n".join(r.message for r in caplog.records)
        assert "stage=pdf.open" in joined
        assert "stage=page.text_extract" in joined
        assert "duration_s=" in joined
        # No document contents in metrics lines
        assert "metrics assertion content" not in joined


class TestDigitalTables:
    def test_table_rows_keep_cell_association(self, tmp_path, no_real_ocr):
        pdf = tmp_path / "table.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)

        rows = [
            ["Cr.No", "Work", "Amount"],
            ["1", "Road laying", "150000"],
            ["2", "Drainage", "250000"],
        ]
        x0, y0, cw, rh = 60, 120, 150, 30
        for i in range(len(rows) + 1):
            y = y0 + i * rh
            page.draw_line(fitz.Point(x0, y), fitz.Point(x0 + 3 * cw, y))
        for j in range(4):
            x = x0 + j * cw
            page.draw_line(fitz.Point(x, y0), fitz.Point(x, y0 + len(rows) * rh))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                page.insert_text(
                    fitz.Point(x0 + c * cw + 5, y0 + r * rh + 20),
                    cell,
                    fontsize=9,
                )
        doc.save(str(pdf))
        doc.close()

        result = ocr_service.process_pdf_document(pdf, language="auto")
        text = result.pages[0].text
        assert "[table p1]" in text
        assert "1 | Road laying | 150000" in text
        assert "2 | Drainage | 250000" in text
        assert result.pages[0].table_count >= 1
        # Table cell text must not also appear flattened outside the table rows
        assert text.count("150000") == 1


class TestFieldExtractionHardening:
    def test_reference_number_not_extracted_as_phone(self):
        fields = extract_fields_from_text(
            "Application No: 4521-A\nReference: 123-45-6789\nPolicy 98765 43210"
        )
        names = {f.field_name for f in fields}
        assert "phone" not in names

    def test_labelled_and_international_phones_still_extracted(self):
        fields = extract_fields_from_text("Mobile: 9822334455\nOffice +91 20 26123456")
        phones = [f for f in fields if f.field_name == "phone"]
        assert phones
        assert sum(c.isdigit() for c in phones[0].field_value) >= 8

    def test_stripe_receipt_coupling_removed(self):
        # amount_paid must not be tied to a specific payment-provider layout
        fields = extract_fields_from_text("ch_3PqrXY 05/12/2025 $250.00")
        assert not any(f.field_name == "amount_paid" for f in fields)


class TestScannedTables:
    """PP-Structure scanned-table path: engine mocked, geometry realistic."""

    @staticmethod
    def _box(x1, y1, x2, y2):
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    @staticmethod
    def _fake_table_result():
        # Matches PaddleOCR 2.x PPStructure output shape: cell_bbox + inline html.
        cells = [
            [0, 0, 250, 60],
            [250, 0, 500, 60],
            [0, 60, 250, 120],
            [250, 60, 500, 120],
        ]
        html = (
            "<table><tr><td>Cr.No</td><td>Work</td></tr>"
            "<tr><td>1</td><td>Road</td></tr></table>"
        )
        return [{"type": "table", "res": {"cell_bbox": cells, "html": html}}]

    def _scan_pdf(self, path):
        doc = fitz.open()
        imgdoc = fitz.open()
        ip = imgdoc.new_page(width=595, height=842)
        ip.insert_textbox(
            fitz.Rect(30, 30, 565, 200), " ruled scan page body text here ", fontsize=14
        )
        pix = ip.get_pixmap(dpi=100)
        imgdoc.close()
        t = doc.new_page(width=595, height=842)
        t.insert_image(t.rect, stream=pix.tobytes("png"))
        doc.save(str(path))
        doc.close()

    def test_table_cells_replaced_by_page_devanagari_lines(
        self, tmp_path, monkeypatch, no_real_ocr
    ):
        pdf = tmp_path / "scanned_table.pdf"
        self._scan_pdf(pdf)

        ocr_lines = [
            (self._box(10, 10, 120, 40), "क्रमांक"),  # center in cell (0,0)
            (self._box(260, 10, 480, 40), "काम वर्णन"),  # center in cell (0,1)
            (self._box(260, 70, 480, 100), "रस्ता लावणी"),  # center in cell (1,1)
        ]

        def fake_ocr(image, language="auto"):
            return (
                "पूर्ण मजकूर असलेले स्कॅन पान body text",
                80.0,
                "FakePaddle",
                "en+hi",
                list(ocr_lines),
            )

        result_payload = self._fake_table_result()

        class FakeEngine:
            def __call__(self, img_np):
                return result_payload

        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)
        monkeypatch.setattr(ocr_service, "_looks_like_ruled_table", lambda img: True)
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: FakeEngine())

        result = ocr_service.process_pdf_document(pdf, language="auto")
        page = result.pages[0]
        assert "[scanned-table p1]" in page.text
        assert "क्रमांक | काम वर्णन" in page.text
        # The model's digit "1" is not something this page read, so the cell is
        # stored unreadable and flagged instead of keeping a number no one saw.
        assert "| रस्ता लावणी" in page.text
        assert "1 | रस्ता लावणी" not in page.text
        assert page.tables[0].uncertain_cells == 1
        assert page.tables[0].requires_review is True
        assert page.table_count == 1
        assert "Tables1" in result.ocr_engine
        # Raw OCR stays pure page text — the structured block is additive only
        assert "[scanned-table" not in page.raw_text

    def test_colspan_header_row_maps_to_full_width_cell(
        self, tmp_path, monkeypatch, no_real_ocr
    ):
        pdf = tmp_path / "scanned_table_colspan.pdf"
        self._scan_pdf(pdf)

        def fake_ocr(image, language="auto"):
            return ("plain english scanned page content", 80.0, "FakePaddle", "en", [])

        result_payload = [{
            "type": "table",
            "res": {
                "cell_bbox": [[0, 0, 500, 60], [0, 60, 250, 120], [250, 60, 500, 120]],
                "html": (
                    '<table><tr><td colspan="3">Estimated Expenses</td></tr>'
                    "<tr><td>1</td><td>Road laying</td></tr></table>"
                ),
            },
        }]

        class FakeEngine:
            def __call__(self, img_np):
                return result_payload

        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)
        monkeypatch.setattr(ocr_service, "_looks_like_ruled_table", lambda img: True)
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: FakeEngine())

        result = ocr_service.process_pdf_document(pdf, language="auto")
        text = result.pages[0].text
        assert "Estimated Expenses" in text
        assert "1 | Road laying" in text

    def test_no_table_block_when_engine_unavailable(
        self, tmp_path, monkeypatch, no_real_ocr
    ):
        pdf = tmp_path / "scanned_table2.pdf"
        self._scan_pdf(pdf)

        def fake_ocr(image, language="auto"):
            return ("normal scanned page text content here", 80.0, "FakePaddle", "en", [])

        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)
        monkeypatch.setattr(ocr_service, "_looks_like_ruled_table", lambda img: True)
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: None)

        result = ocr_service.process_pdf_document(pdf, language="auto")
        page = result.pages[0]
        assert "[scanned-table" not in page.text
        assert page.table_count == 0

    def test_table_extraction_skipped_for_non_ruled_pages(
        self, tmp_path, monkeypatch, no_real_ocr
    ):
        pdf = tmp_path / "scanned_plain.pdf"
        self._scan_pdf(pdf)

        def fake_ocr(image, language="auto"):
            return ("plain letter page text without tables", 80.0, "FakePaddle", "en", [])

        calls: list[int] = []
        monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)
        monkeypatch.setattr(ocr_service, "_looks_like_ruled_table", lambda img: False)
        monkeypatch.setattr(
            ocr_service, "get_paddle_table_engine", lambda: calls.append(1)
        )

        result = ocr_service.process_pdf_document(pdf, language="auto")
        assert calls == []
        assert "[scanned-table" not in result.pages[0].text


class TestDuplicateDedup:
    def test_duplicate_page_text_excluded_from_combined(self, tmp_path, no_real_ocr):
        pdf = tmp_path / "dup.pdf"
        doc = fitz.open()
        for _ in range(2):
            p = doc.new_page(width=595, height=842)
            p.insert_textbox(
                fitz.Rect(40, 40, 555, 300),
                "Identical repeated page content with enough characters to hash",
                fontsize=11,
            )
        doc.save(str(pdf))
        doc.close()

        result = ocr_service.process_pdf_document(pdf, language="auto")
        assert len(result.pages) == 2
        assert result.pages[1].is_duplicate
        assert result.combined_text.count("Identical repeated page content") == 1
        # Both pages remain individually queryable (provenance preserved)
        assert all(p.text for p in result.pages)
