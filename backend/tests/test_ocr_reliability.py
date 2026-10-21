"""Regressions for the defects found by the end-to-end run on a mixed-quality
document (clean digital table, aged low-contrast scan, Devanagari scan, a page
scanned through 90 degrees).

Every OCR engine is mocked here; the measured engine behaviour is reported by
the isolated end-to-end run, not by these tests.
"""

import numpy as np
import pytest
from PIL import Image

from app.services import ocr_service
from app.services.evidence_service import (
    build_query_plan,
    classify_value,
    extract_values,
)
from app.services.extraction_service import (
    _is_plausible_date,
    extract_document_report,
    extract_fields_from_text,
)


def _page_image(pixels, *, size=(300, 400)):
    return Image.fromarray(np.asarray(pixels, dtype=np.uint8), mode="L").resize(size)


class TestBlankDetectionOnAgedPages:
    def test_a_tinted_low_contrast_page_is_not_blank(self):
        """Text drawn in dark brown on a brown ground has no pixel below the old
        fixed cut, and used to be reported as an empty page."""
        arr = np.full((400, 300), 226, dtype=np.uint8)
        arr[120:150, 40:260] = 186  # the only "dark" band on the page
        assert ocr_service._estimate_ink_ratio(_page_image(arr)) >= 0.0002

    def test_a_uniform_page_is_still_blank(self):
        arr = np.full((400, 300), 190, dtype=np.uint8)
        assert ocr_service._estimate_ink_ratio(_page_image(arr)) < 0.0002

    def test_a_white_page_is_still_blank(self):
        assert (
            ocr_service._estimate_ink_ratio(Image.new("L", (300, 400), 255)) < 0.0002
        )


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class TestRotatedScanDetection:
    def test_wide_word_boxes_are_not_read_as_rotation(self):
        lines = [(_box(40, 60 * i, 300, 60 * i + 22), f"word {i}") for i in range(8)]
        assert ocr_service._boxes_imply_vertical_text(lines) is False

    def test_tall_narrow_boxes_signal_a_sideways_scan(self):
        lines = [(_box(40 * i, 30, 40 * i + 22, 300), f"word {i}") for i in range(8)]
        assert ocr_service._boxes_imply_vertical_text(lines) is True

    def test_a_handful_of_boxes_is_not_enough_to_claim_rotation(self):
        lines = [(_box(40, 30, 62, 300), "one")]
        assert ocr_service._boxes_imply_vertical_text(lines) is False

    def test_missing_geometry_never_claims_rotation(self):
        assert ocr_service._boxes_imply_vertical_text(None) is False
        assert ocr_service._boxes_imply_vertical_text([]) is False


class TestAmountClassification:
    """The recogniser writes most totals without a currency glyph or a comma."""

    @pytest.mark.parametrize(
        "raw", ["24555.80", "20810.00", "10260.00", "3745.80", "85.50", "3,000.45", "1250.00"]
    )
    def test_bare_decimal_amounts_are_amounts(self, raw):
        token = classify_value(raw, "amount")
        assert token is not None
        assert token.kind == "amount"
        assert token.state == "verified"

    @pytest.mark.parametrize("raw", ["2118.", "5..900.00", "12.3.4", "0.0.0"])
    def test_damaged_number_forms_are_still_not_amounts(self, raw):
        token = classify_value(raw, "amount")
        assert token.kind == "number"
        assert token.state == "unknown"


class TestValueKindIntent:
    def test_a_when_question_wants_a_date_even_though_it_names_payment(self):
        assert build_query_plan("When must the invoice be paid?").value_kind == "date"

    def test_a_total_due_question_still_wants_an_amount(self):
        plan = build_query_plan("What is the total amount due on the invoice?")
        assert plan.value_kind == "amount"

    def test_due_date_phrasing_still_wants_a_date(self):
        assert build_query_plan("What is the due date of this bill?").value_kind == "date"

    def test_hindi_and_marathi_when_questions_want_a_date(self):
        assert build_query_plan("बिल केव्हा परत करायचे आहे?").value_kind == "date"
        assert build_query_plan("यह बिल कब तक भुगतान करना है?").value_kind == "date"


class TestDatePlausibility:
    @pytest.mark.parametrize(
        "value", ["09/09/2024", "14-08-2024", "2024-08-14", "1.12.2024", "14/08/24"]
    )
    def test_calendar_dates_pass(self, value):
        assert _is_plausible_date(value) is True

    @pytest.mark.parametrize("value", ["1707-50-50", "14/13/2024", "00/05/2024", "99/99/2024"])
    def test_recogniser_noise_shaped_like_a_date_is_rejected(self, value):
        assert _is_plausible_date(value) is False

    def test_a_damaged_page_cannot_publish_an_impossible_date_field(self):
        fields = extract_fields_from_text("date 1707-50-50\nref 44", page_number=4)
        assert [f.field_name for f in fields if f.field_name == "date"] == []

    def test_a_clean_labeled_date_still_becomes_a_field(self):
        fields = extract_fields_from_text("Date: 14/08/2024\nref 44", page_number=1)
        assert [f.field_value for f in fields if f.field_name == "date"] == ["14/08/2024"]

    def test_text_dates_are_left_to_their_own_pattern(self):
        assert _is_plausible_date("14 August 2024") is True


class TestEmptyPageIsDisclosed:
    def test_a_page_that_yielded_no_text_is_a_review_reason(self):
        from app.services.ocr_service import DocumentExtractionResult, ExtractedPage

        read = ExtractedPage(page_number=1, text="Total Due 24555.80", confidence=91.0)
        empty = ExtractedPage(
            page_number=2, text="", confidence=None, page_type="blank",
            engine="PyMuPDF+SkippedBlank",
        )
        report = extract_document_report(
            DocumentExtractionResult(
                pages=[read, empty],
                combined_text=read.text,
                overall_ocr_confidence=91.0,
                is_ocr_applied=True,
            )
        )
        assert report.requires_review is True
        assert "no readable text" in (report.review_reason or "")
        assert "page 2" in (report.review_reason or "")

class TestSplitAmountIsReadAsOneNumber:
    def test_a_dangling_thousands_group_joins_the_decimal_below_it(self):
        """A recogniser that breaks "25,145.00" over two lines must not publish 145.00."""
        window = chr(10).join(["एकूण रक्कम:", "Rs.", "25,", "145.00"])
        values = extract_values(window)
        amounts = [v for v in values if v.kind == "amount"]
        assert [v.raw for v in amounts] == ["25,145.00"]
        assert amounts[0].state == "verified"
        assert not [v for v in values if v.raw == "145.00"]

    def test_two_complete_amounts_on_consecutive_lines_stay_two_amounts(self):
        window = chr(10).join(["Sub Total 20810.00", "GST at 18 percent 3745.80"])
        values = extract_values(window)
        assert {"20810.00", "3745.80"} <= {v.raw for v in values if v.kind == "amount"}
