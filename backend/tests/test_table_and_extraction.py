import pytest
from app.services.ocr_service import (
    normalize_devanagari_text,
    reconstruct_reading_order_and_tables,
    ExtractedPage,
    DocumentExtractionResult,
)
from app.services.extraction_service import (
    _is_plausible_date,
    validate_totals_arithmetic,
    validate_identifier_format,
    extract_fields_from_text,
    extract_generic_key_value_pairs,
    extract_table_line_items,
    extract_document_report,
)


def _scanned_tables(image, *, devanagari_ocr, ocr_lines, page_number=1):
    """(row lines, structures, table count) from the scanned reader."""
    from app.services import ocr_service

    structures, count = ocr_service._extract_scanned_tables(
        image,
        devanagari_ocr=devanagari_ocr,
        ocr_lines=ocr_lines,
        page_number=page_number,
    )
    return [line for s in structures for line in s.row_texts()], structures, count


class TestDevanagariNormalization:
    """Verifies that Devanagari matras, viramas and Marathi characters are properly normalized."""

    def test_recombines_split_vowel_matras(self):
        # Spurious space before aa-matra and ee-matra
        raw = "क ा म   क र त ा   आ ह े"
        normalized = normalize_devanagari_text(raw)
        assert "काम" in normalized
        assert "करता" in normalized
        assert "आहे" in normalized

    def test_recombines_virama_halant_conjuncts(self):
        # Virama split from consonant
        raw = "क ् ष े त ् र"
        normalized = normalize_devanagari_text(raw)
        assert "क्षेत्र" in normalized

    def test_preserves_marathi_specific_characters(self):
        # Marathi 'ळ' (U+0933) and Marathi 'ॲ'
        text = "पु ळ े   आणि   ब ँ क"
        normalized = normalize_devanagari_text(text)
        assert "पुळे" in normalized
        assert "बँक" in normalized

    def test_rejoins_hyphenated_line_breaks(self):
        raw = "महा-\nराष्ट्र"
        normalized = normalize_devanagari_text(raw)
        assert normalized == "महाराष्ट्र"

    def test_keeps_the_space_between_two_whole_devanagari_words(self):
        """A blank table cell must not cost every other row its word spaces.

        The row reads "रस्ता |  | दर" because its middle cell is empty, and that
        double space used to make the normaliser weld "काम वर्णन" into "कामवर्णन"
        on the same page - so a search for the phrase the document actually prints
        could not find it.
        """
        raw = "काम वर्णन | प्रमाण\nरस्ता |  | दर"
        normalized = normalize_devanagari_text(raw)
        assert "काम वर्णन" in normalized
        assert "रस्ता" in normalized

    def test_glues_a_detached_glyph_beside_a_whole_word(self):
        """Only a single character on one side of the space means a dropped glyph."""
        raw = "रकम 3   द र 500.00"
        normalized = normalize_devanagari_text(raw)
        assert "दर 500.00" in normalized
        assert "रकम" in normalized


class TestGeometricReadingOrderAndTables:
    """Verifies geometric reading order and table reconstruction from 2D bounding boxes."""

    def test_table_row_and_column_association(self):
        # Two rows of a 3-column table:
        # Col 1: Item at x=50..150
        # Col 2: Qty at x=200..250
        # Col 3: Amount at x=300..380
        boxes = [
            # Row 1 (y ~ 100)
            {"text": "Consulting Services", "box": [50, 100, 150, 120]},
            {"text": "2", "box": [200, 102, 230, 122]},
            {"text": "1,500.00", "box": [300, 101, 380, 121]},
            # Row 2 (y ~ 140)
            {"text": "Implementation", "box": [50, 140, 160, 160]},
            {"text": "1", "box": [200, 141, 220, 161]},
            {"text": "3,000.00", "box": [300, 139, 380, 159]},
        ]
        text, tables, _ = reconstruct_reading_order_and_tables(boxes)
        lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
        assert len(lines) == 2
        # Verify columns are separated with '|'
        assert "Consulting Services | 2 | 1,500.00" in lines[0]
        assert "Implementation | 1 | 3,000.00" in lines[1]
        assert len(tables) >= 1
        assert len(tables[0]["headers"]) == 3
        assert len(tables[0]["rows"]) == 1

    def test_multi_column_order_respects_geometry(self):
        # Scrambled bounding boxes provided out-of-reading-order:
        # Expected: Row 1 (Item A, $100), Row 2 (Item B, $200)
        scrambled_boxes = [
            {"text": "$200.00", "box": [250, 200, 320, 220]},
            {"text": "Item A", "box": [40, 100, 100, 120]},
            {"text": "$100.00", "box": [250, 100, 320, 120]},
            {"text": "Item B", "box": [40, 200, 100, 220]},
        ]
        text, tables, _ = reconstruct_reading_order_and_tables(scrambled_boxes)
        lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
        assert "Item A | $100.00" in lines[0]
        assert "Item B | $200.00" in lines[1]


class TestRealCalendarValidation:
    """Verifies date checking against real calendar rules including leap years."""

    def test_valid_calendar_dates_accepted(self):
        assert _is_plausible_date("29/02/2024") is True  # 2024 is a leap year
        assert _is_plausible_date("31/01/2024") is True  # January has 31 days
        assert _is_plausible_date("30/04/2024") is True  # April has 30 days
        assert _is_plausible_date("15/08/1947") is True  # Valid historic date

    def test_invalid_calendar_dates_rejected(self):
        assert _is_plausible_date("29/02/2023") is False  # 2023 is NOT a leap year
        assert _is_plausible_date("31/04/2024") is False  # April has only 30 days
        assert _is_plausible_date("31/06/2024") is False  # June has only 30 days
        assert _is_plausible_date("31/11/2024") is False  # November has only 30 days
        assert _is_plausible_date("32/01/2024") is False  # Day > 31
        assert _is_plausible_date("15/13/2024") is False  # Month > 12


class TestArithmeticValidationOfTotals:
    """Verifies subtotal + tax - discount - credit arithmetic consistency."""

    def test_arithmetic_matches(self):
        fields = {
            "subtotal": "1,000.00",
            "tax": "180.00",
            "discount": "50.00",
            "total_amount": "1,130.00",
        }
        valid, msg = validate_totals_arithmetic(fields)
        assert valid is True
        assert msg is None

    def test_arithmetic_discrepancy_flagged(self):
        fields = {
            "subtotal": "1,000.00",
            "tax": "180.00",
            "discount": "50.00",
            "total_amount": "2,500.00",  # Incorrect total
        }
        valid, msg = validate_totals_arithmetic(fields)
        assert valid is False
        assert "Arithmetic discrepancy in totals" in msg
        assert "1130.00" in msg
        assert "2500.00" in msg


class TestIdentifierFormatValidation:
    """Verifies PAN and GSTIN format rules."""

    def test_valid_pan_accepted(self):
        valid, msg = validate_identifier_format("pan_number", "ABCDE1234F")
        assert valid is True

    def test_invalid_pan_flagged(self):
        valid, msg = validate_identifier_format("pan_number", "ABCD12345F")  # 4 letters, 5 digits
        assert valid is False
        assert "PAN format discrepancy" in msg

    def test_valid_gstin_accepted(self):
        valid, msg = validate_identifier_format("tax_id", "27ABCDE1234F1Z5")
        assert valid is True

    def test_invalid_gstin_flagged(self):
        valid, msg = validate_identifier_format("tax_id", "99ABCDE1234F1X5")  # Missing Z
        assert valid is False
        assert "GSTIN format discrepancy" in msg


class TestMultiDocumentExtraction:
    """Verifies extraction across diverse document types and generic fallback."""

    def test_invoice_extraction(self):
        text = """
        TAX INVOICE
        Vendor: Acme Industrial Supplies Ltd
        Bill To: Global Tech Solutions
        Invoice No: INV-2024-9981
        Date: 15/05/2024
        Due Date: 15/06/2024
        GSTIN: 27ABCDE1234F1Z5
        PAN: ABCDE1234F

        Description | Qty | Rate | Amount
        Steel Rods | 10 | 100.00 | 1,000.00
        Fasteners | 50 | 10.00 | 500.00

        Subtotal: $1,500.00
        Tax: $270.00
        Grand Total: $1,770.00
        """
        page = ExtractedPage(page_number=1, text=text, confidence=95.0)
        report = extract_document_report(
            DocumentExtractionResult(
                pages=[page],
                combined_text=text,
                overall_ocr_confidence=95.0,
                is_ocr_applied=True,
            )
        )
        assert report.document_type in ("Invoice", "Invoices")
        field_map = {f.field_name: f.field_value for f in report.fields}
        assert field_map.get("invoice_number") == "INV-2024-9981"
        assert field_map.get("subtotal") == "1,500.00"
        assert field_map.get("tax") == "270.00"
        assert field_map.get("total_amount") == "1,770.00"
        assert field_map.get("pan_number") == "ABCDE1234F"
        assert "line_item_1" in field_map
        assert "Steel Rods" in field_map["line_item_1"]
        assert report.requires_review is False

    def test_contract_extraction(self):
        text = """
        SERVICES AGREEMENT
        This Agreement is made by and between Alpha Corp, hereinafter Client,
        and Beta Services LLC, hereinafter Contractor.
        Effective Date: 01/01/2024
        Expiry Date: 31/12/2024
        Governing Law: State of New York
        Renewal Terms: Automatic renewal for 12 months unless terminated in writing.
        Termination: Either party may terminate with 30 days written notice.
        Limitation of Liability: Aggregate liability shall not exceed $100,000.
        Confidentiality: Recipient shall protect proprietary information for 3 years.
        """
        page = ExtractedPage(page_number=1, text=text, confidence=92.0)
        report = extract_document_report(
            DocumentExtractionResult(
                pages=[page],
                combined_text=text,
                overall_ocr_confidence=92.0,
                is_ocr_applied=True,
            )
        )
        field_map = {f.field_name: f.field_value for f in report.fields}
        assert "party_one" in field_map
        assert "party_two" in field_map
        assert field_map.get("effective_date") == "01/01/2024"
        assert field_map.get("expiry_date") == "31/12/2024"
        assert "New York" in field_map.get("governing_law", "")
        assert "termination_clause" in field_map
        assert "liability_clause" in field_map
        assert "confidentiality_clause" in field_map

    def test_identity_document_extraction(self):
        text = """
        REPUBLIC OF INDIA
        NATIONAL IDENTITY
        Aadhaar No: 2345 6789 0123
        Date of Birth: 12/04/1990
        Nationality: Indian
        """
        page = ExtractedPage(page_number=1, text=text, confidence=94.0)
        report = extract_document_report(
            DocumentExtractionResult(
                pages=[page],
                combined_text=text,
                overall_ocr_confidence=94.0,
                is_ocr_applied=True,
            )
        )
        field_map = {f.field_name: f.field_value for f in report.fields}
        assert field_map.get("id_number") == "2345 6789 0123"
        assert field_map.get("date_of_birth") == "12/04/1990"
        assert field_map.get("nationality") == "Indian"

    def test_generic_form_fallback_extraction(self):
        text = """
        MEMBERSHIP APPLICATION FORM
        Applicant Name: Rajesh Sharma
        Application Number: APP-9082
        Blood Group: O+
        Father Name: Suresh Sharma
        Eligible: [x]
        Subscribed: [ ]
        Emergency Contact: +919876543210
        Submission Date: 20/03/2024
        """
        page = ExtractedPage(page_number=1, text=text, confidence=90.0)
        report = extract_document_report(
            DocumentExtractionResult(
                pages=[page],
                combined_text=text,
                overall_ocr_confidence=90.0,
                is_ocr_applied=True,
            )
        )
        field_map = {f.field_name: f.field_value for f in report.fields}
        assert field_map.get("applicant_name") == "Rajesh Sharma"
        assert field_map.get("application_number") == "APP-9082"
        # Generic fallback should catch Blood Group, Father Name, and checkbox fields
        assert "blood_group" in field_map or "blood" in field_map
        assert "father_name" in field_map
        assert field_map.get("eligible") == "Yes (Checked)"
        assert field_map.get("subscribed") == "No (Unchecked)"


class TestScannedTableUnpackingRegression:
    """Regression tests for lines_src shape mismatch in _extract_scanned_tables."""

    def test_three_tuple_lines_src_does_not_raise_unpacking_error(self, monkeypatch):
        from PIL import Image

        # Mock paddle table engine returning a dummy table result
        dummy_table_res = [{
            "type": "table",
            "res": {
                "html": "<table><tr><td>Item</td><td>Total</td></tr><tr><td>Apples</td><td>$10</td></tr></table>",
                "cell_bbox": [[10, 10, 50, 30], [60, 10, 100, 30], [10, 35, 50, 55], [60, 35, 100, 55]],
            }
        }]

        class MockTableEngine:
            def __call__(self, img_np):
                return dummy_table_res

        monkeypatch.setattr("app.services.ocr_service.get_paddle_table_engine", lambda: MockTableEngine())

        # Test with 3-tuple lines: (box, text, score) as produced by RapidOCR/PaddleOCR sorted_lines
        three_tuple_lines = [
            ([[10, 10], [50, 10], [50, 30], [10, 30]], "Item", 0.95),
            ([[60, 10], [100, 10], [100, 30], [60, 30]], "Total", 0.92),
            ([[10, 35], [50, 35], [50, 55], [10, 55]], "Apples", 0.88),
            ([[60, 35], [100, 35], [100, 55], [60, 55]], "$10", 0.90),
        ]

        img = Image.new("RGB", (200, 200), color="white")
        lines, structures, count = _scanned_tables(
            img, devanagari_ocr=True, ocr_lines=three_tuple_lines
        )
        assert count == 1
        assert len(lines) >= 1
        assert "Item | Total" in lines[0] or "Apples | $10" in lines[0]
        # The scores the engine reported are carried onto the cells that came from
        # them, so a table's confidence is a measurement rather than a placeholder.
        cell = structures[0].cells[0]
        assert cell.confidence == 95.0
        assert structures[0].confidence is not None

    def test_mixed_and_dict_lines_src_supported(self, monkeypatch):
        from PIL import Image

        dummy_table_res = [{
            "type": "table",
            "res": {
                "html": "<table><tr><th>Header 1</th><th>Header 2</th></tr></table>",
                "cell_bbox": [[10, 10, 50, 30], [60, 10, 100, 30]],
            }
        }]

        class MockTableEngine:
            def __call__(self, img_np):
                return dummy_table_res

        monkeypatch.setattr("app.services.ocr_service.get_paddle_table_engine", lambda: MockTableEngine())

        # Test with dict entries
        dict_lines = [
            {"box": [[10, 10], [50, 10], [50, 30], [10, 30]], "text": "Header 1"},
            {"box": [[60, 10], [100, 10], [100, 30], [60, 30]], "text": "Header 2"},
        ]

        img = Image.new("RGB", (200, 200), color="white")
        lines, structures, count = _scanned_tables(
            img, devanagari_ocr=True, ocr_lines=dict_lines
        )
        assert count == 1
        assert len(lines) >= 1
        assert "Header 1 | Header 2" in lines[0]
        # <th> is the model's own header statement, so it becomes a header row
        assert structures[0].header_rows == [0]
        assert all(cell.is_header for cell in structures[0].cells)


class TestTableFailurePreservesOCRText:
    """Verifies that an error in the table reconstruction stage never discards OCR text."""

    def test_table_error_keeps_ocr_text(self, monkeypatch, tmp_path):
        import fitz
        from app.services.ocr_service import process_pdf_document

        # Create a single-page PDF
        pdf_path = tmp_path / "test_doc.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Important Invoice Content\nTotal Due: $500.00")
        doc.save(str(pdf_path))
        doc.close()

        # Monkeypatch table extraction to raise an unexpected error
        def broken_table_extractor(*args, **kwargs):
            raise ValueError("Simulated table geometry unpacking failure")

        monkeypatch.setattr("app.services.ocr_service._extract_scanned_tables", broken_table_extractor)
        # Force scanned table extraction path
        monkeypatch.setattr("app.services.ocr_service._looks_like_ruled_table", lambda img: True)
        from app.core.config import settings
        monkeypatch.setattr(settings, "SCANNED_TABLE_EXTRACTION", True)

        result = process_pdf_document(pdf_path)
        assert len(result.pages) == 1
        page_res = result.pages[0]
        # Text must be preserved!
        assert "Important Invoice Content" in page_res.text
        assert "500.00" in page_res.text
        assert page_res.ocr_failed is False
        assert "Failed" not in (page_res.engine or "")


class TestTableContinuationsAndMultiLineHeaders:
    """Verifies that description continuations and multi-line headers are reconstructed without breaking rows."""

    def test_description_continuation_merged_into_row(self):
        # Row 1: Item 1 | Web Development | $1000
        # Continuation: (Frontend React & TypeScript)
        boxes = [
            # Row 1 (y ~ 100)
            {"text": "1", "box": [50, 100, 70, 120]},
            {"text": "Web Development", "box": [100, 100, 220, 120]},
            {"text": "1,000.00", "box": [300, 100, 360, 120]},
            # Continuation line for Web Development (y ~ 130, single item)
            {"text": "(Frontend React & TypeScript)", "box": [100, 130, 260, 150]},
            # Row 2 (y ~ 170)
            {"text": "2", "box": [50, 170, 70, 190]},
            {"text": "Cloud Hosting", "box": [100, 170, 200, 190]},
            {"text": "200.00", "box": [300, 170, 350, 190]},
        ]
        text, tables, _ = reconstruct_reading_order_and_tables(boxes)
        assert "Web Development (Frontend React & TypeScript)" in text
        assert len(tables) >= 1

    def test_multi_line_headers_merged(self):
        boxes = [
            # Header line 1
            {"text": "Item", "box": [50, 50, 100, 70]},
            {"text": "Unit", "box": [150, 50, 200, 70]},
            {"text": "Total", "box": [250, 50, 300, 70]},
            # Header line 2
            {"text": "Description", "box": [50, 75, 120, 95]},
            {"text": "Price", "box": [150, 75, 190, 95]},
            {"text": "Amount", "box": [250, 75, 290, 95]},
            # Data row
            {"text": "Widget", "box": [50, 110, 100, 130]},
            {"text": "50.00", "box": [150, 110, 190, 130]},
            {"text": "250.00", "box": [250, 110, 300, 130]},
        ]
        text, tables, _ = reconstruct_reading_order_and_tables(boxes)
        assert "Item Description | Unit Price | Total Amount" in text
        assert len(tables) >= 1
        assert tables[0]["headers"] == ["Item Description", "Unit Price", "Total Amount"]


class TestCellBoxRowPairing:
    """Regression tests for mapping the table model's cell boxes onto its own HTML rows."""

    def test_pairing_uses_expanded_colspan_counts(self):
        from app.services.ocr_service import _group_rects_by_html_rows

        rects = [(10, 10, 50, 30), (60, 10, 100, 30), (10, 35, 50, 55), (60, 35, 100, 55)]
        rows = [[("a", 2)], [("b", 1), ("c", 1)]]
        assert _group_rects_by_html_rows(rects, rows) == [
            [rects[0], rects[1]],
            [rects[2], rects[3]],
        ]

    def test_pairing_refuses_when_counts_disagree(self):
        from app.services.ocr_service import _group_rects_by_html_rows

        # Fewer boxes than the HTML row/column model claims: no honest mapping exists.
        assert _group_rects_by_html_rows(
            [(10, 10, 50, 30), (60, 10, 100, 30)],
            [[("a", 1), ("b", 1), ("c", 1)]],
        ) is None

    def test_pairing_refuses_boxes_that_ascend_up_the_page(self):
        from app.services.ocr_service import _group_rects_by_html_rows

        # Counts add up but the implied rows run backwards, so the reading order
        # would be invented rather than observed.
        assert _group_rects_by_html_rows(
            [(10, 70, 50, 90), (60, 70, 100, 90), (10, 10, 50, 30), (60, 10, 100, 30)],
            [[("a", 1), ("b", 1)], [("c", 1), ("d", 1)]],
        ) is None

    def test_box_cloud_inside_page_is_left_untouched(self):
        from PIL import Image
        from app.services.ocr_service import _fit_rects_into_page

        rects = [(10.0, 10.0, 50.0, 30.0), (60.0, 10.0, 100.0, 30.0)]
        assert _fit_rects_into_page(rects, [0, 0, 200, 200], Image.new("RGB", (200, 200))) == rects

    def test_overshooting_box_cloud_is_scaled_into_the_table_region(self):
        from PIL import Image
        from app.services.ocr_service import _fit_rects_into_page

        # The model returns boxes in its own resized frame; here x runs to 400 on a
        # 200px page, which would match no OCR line on the page at all.
        rects = [(0.0, 0.0, 200.0, 100.0), (200.0, 0.0, 400.0, 100.0)]
        fitted = _fit_rects_into_page(rects, [20, 40, 120, 90], Image.new("RGB", (200, 200)))
        assert all(0 <= x1 < x2 <= 200 and 0 <= y1 < y2 <= 200 for x1, y1, x2, y2 in fitted)
        # Relative order and column widths survive the mapping.
        assert fitted[0][0] < fitted[1][0]
        assert abs((fitted[1][2] - fitted[1][0]) - (fitted[0][2] - fitted[0][0])) < 1e-6

    def test_missing_table_bbox_falls_back_to_the_page_box(self):
        from PIL import Image
        from app.services.ocr_service import _fit_rects_into_page

        fitted = _fit_rects_into_page(
            [(0.0, 0.0, 300.0, 300.0)], None, Image.new("RGB", (150, 100))
        )
        assert fitted == [(0.0, 0.0, 150.0, 100.0)]

    def test_row_text_is_recovered_when_gap_banding_makes_extra_rows(self, monkeypatch):
        """A row whose cells sit at different heights used to lose all its text.

        Vertical gap splitting sees three bands while the model's HTML has two rows;
        the disagreement discarded every cell assignment and the persisted table was
        the model's Latin lookalikes of the script.
        """
        from PIL import Image

        table = [{
            "type": "table",
            "res": {
                "html": (
                    "<table><tr><td colspan='2'>महिनो</td></tr>"
                    "<tr><td>नाम</td><td>रकम</td></tr></table>"
                ),
                # Row 2's second cell sits lower than its sibling: 3 gap bands, 2 HTML rows.
                "cell_bbox": [
                    [10, 10, 50, 30], [60, 10, 100, 30],
                    [10, 35, 50, 55], [60, 55, 100, 75],
                ],
            },
        }]

        class MockTableEngine:
            def __call__(self, img_np):
                return table

        def box(x1, y1, x2, y2):
            return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

        lines_src = [
            (box(12, 12, 48, 28), "महिनो", 0.9),
            (box(12, 37, 48, 53), "नाम", 0.9),
            (box(62, 57, 98, 73), "रकम", 0.9),
        ]

        monkeypatch.setattr(
            "app.services.ocr_service.get_paddle_table_engine", lambda: MockTableEngine()
        )
        lines, structures, count = _scanned_tables(
            Image.new("RGB", (200, 200), color="white"),
            devanagari_ocr=True,
            ocr_lines=lines_src,
        )

        assert count == 1
        assert any("नाम" in line and "रकम" in line and " | " in line for line in lines)
        assert any("महिनो" in line for line in lines)
        # The colspan cell covers both boxes of its row, so its own rectangle is
        # the union rather than the first slot's half-width box.
        merged = structures[0].cells[0]
        assert merged.colspan == 2
        assert merged.bbox == (10.0, 10.0, 100.0, 30.0)


class TestTableEvidenceGuards:
    """The table model must not contribute text the page itself does not support."""

    TABLE_HTML_LATIN = (
        "<table><tr><td>M</td><td>B M I B</td></tr>"
        "<tr><td>9fda</td><td>1,250.00</td></tr></table>"
    )
    BOXES = [
        [10, 10, 50, 30], [60, 10, 100, 30],
        [10, 35, 50, 55], [60, 35, 100, 55],
    ]

    def _run(self, monkeypatch, devanagari_ocr, ocr_lines, html=None):
        from PIL import Image

        table = [{
            "type": "table",
            "res": {"html": html or self.TABLE_HTML_LATIN, "cell_bbox": list(self.BOXES)},
        }]

        class MockTableEngine:
            def __call__(self, img_np):
                return table

        monkeypatch.setattr(
            "app.services.ocr_service.get_paddle_table_engine", lambda: MockTableEngine()
        )
        lines, structures, count = _scanned_tables(
            Image.new("RGB", (200, 200), color="white"),
            devanagari_ocr=devanagari_ocr,
            ocr_lines=ocr_lines,
        )
        self.last_structures = structures
        return lines, count

    @staticmethod
    def _box(x1, y1, x2, y2):
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    def test_entities_are_unescaped_in_cell_text(self, monkeypatch):
        lines, count = self._run(
            monkeypatch,
            devanagari_ocr=False,
            ocr_lines=None,
            html="<table><tr><td>M&amp;C &lt;50%</td><td>Rate &gt; 2</td></tr></table>",
        )
        assert count == 1
        assert any("M&C <50%" in line and "Rate > 2" in line for line in lines)
        assert not any("&amp;" in line or "&lt;" in line for line in lines)

    def test_script_page_drops_rows_with_no_page_support(self, monkeypatch):
        """Latin lookalikes of Devanagari glyphs are misreads, not transcriptions.

        A grid in which not one cell was read is refused by the quality gate rather
        than stored as an empty table: the page keeps its own recognised text, the
        record says what was found and why it is not shown, and the model's Latin
        noise is never indexed as the page's content.
        """
        ocr_lines = [(self._box(120, 120, 190, 140), "असलेली माहिती", 0.9)]
        from app.services import ocr_service, table_quality

        lines, count = self._run(monkeypatch, devanagari_ocr=True, ocr_lines=ocr_lines)
        assert count == 1
        record = self.last_structures[0]
        # The refusal keeps the measurement and drops the invented cells.
        assert record.cells == []
        assert (record.row_count, record.col_count) == (2, 2)
        assert record.confidence is None
        assert record.requires_review is True
        assert "Not shown as a table" in record.note
        assert "no cell in the grid could be read" in record.note
        assert lines == []
        assert "1,250.00" not in record.to_html()
        # A refused grid consumes nothing: with no accepted structure, every line
        # the page read stays in the page's own prose.
        refused = table_quality.screen([record], page_size=(200, 200), page_lines=1)
        assert refused.accepted == []
        kept_lines = ocr_service._lines_outside_structures(ocr_lines, refused.accepted)
        assert [item[1] for item in kept_lines] == ["असलेली माहिती"]

    def test_numeric_cells_survive_on_a_script_page(self, monkeypatch):
        """Digits survive on such a page when the page itself read them."""
        ocr_lines = [
            (self._box(12, 12, 48, 28), "नामांकन", 0.9),
            (self._box(62, 37, 98, 53), "1,250.00", 0.9),
        ]
        lines, count = self._run(monkeypatch, devanagari_ocr=True, ocr_lines=ocr_lines)
        assert count == 1
        assert len(lines) == 2
        assert any("नामांकन" in line for line in lines)
        assert any("1,250.00" in line for line in lines)
        # Cells the page gives no reading for stay empty instead of keeping the
        # model's Latin lookalikes.
        assert not any("9fda" in line or "B M I B" in line for line in lines)
        # Where the page read the same digits the model did, its score is the
        # cell's confidence; the script cell carries the score that produced it.
        amount = next(c for c in self.last_structures[0].cells if c.text == "1,250.00")
        assert amount.confidence == 90.0
        name = next(c for c in self.last_structures[0].cells if c.text == "नामांकन")
        assert name.confidence == 90.0

    def test_latin_page_keeps_the_table_model_reading(self, monkeypatch):
        """Without script evidence on the page, nothing is withheld from the row."""
        ocr_lines = [
            (self._box(12, 12, 48, 28), "M", 0.9),
            (self._box(62, 12, 98, 28), "B M I B", 0.9),
        ]
        lines, count = self._run(monkeypatch, devanagari_ocr=True, ocr_lines=ocr_lines)
        assert count == 1
        assert lines == ["M | B M I B", "9fda | 1,250.00"]
