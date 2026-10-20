from PIL import Image, ImageDraw

from app.services.ocr_service import (
    DocumentExtractionResult,
    ExtractedPage,
    _tesseract_language_code,
    detect_blank_page,
)
from app.services.ocr_quality import assess_ocr_text, normalize_for_metric, transcription_metrics


def _make_text_page(text: str = "Sample text") -> Image.Image:
    img = Image.new("L", (1400, 1800), 255)
    draw = ImageDraw.Draw(img)
    draw.text((80, 120), text, fill=30, font=None)
    return img


def test_blank_page_detection_flags_white_pages():
    blank = Image.new("L", (1200, 1600), 255)
    is_blank, reason, _ = detect_blank_page(blank)
    assert is_blank is True
    assert reason in {"blank-or-nearly-blank", "mostly-white-page"}


def test_text_page_is_not_treated_as_blank():
    text_page = _make_text_page("नमस्कार, this is a test page")
    is_blank, _, _ = detect_blank_page(text_page)
    assert is_blank is False


def test_quality_assessment_flags_suspicious_output_without_rewriting_it():
    text = "\ufffd A"
    assessment = assess_ocr_text(text)
    assert "replacement-characters-present" in assessment.warnings
    assert "suspiciously-short-output" in assessment.warnings
    assert assessment.character_count == len(text)


def test_transcription_metrics_keep_digits_punctuation_and_devanagari_distinctions():
    report = transcription_metrics("दिनांक: १२/०३/२०२५", "दिनांक १२/०३/२०२६")
    assert report["cer"] > 0
    assert normalize_for_metric("A-12, ०१") == "A-12, ०१"


def test_transcription_metrics_identical_text_are_zero():
    report = transcription_metrics("नमस्कार\nDoc १२", "नमस्कार Doc १२")
    assert report["cer"] == 0
    assert report["wer"] == 0


def test_tesseract_does_not_silently_substitute_unavailable_requested_language(monkeypatch):
    monkeypatch.setattr("app.services.ocr_service.get_available_tesseract_languages", lambda: {"eng"})
    assert _tesseract_language_code("mr") == ""
    assert _tesseract_language_code("en+mr") == ""
    assert _tesseract_language_code("en") == "eng"


def test_tesseract_auto_uses_only_installed_supported_data(monkeypatch):
    monkeypatch.setattr("app.services.ocr_service.get_available_tesseract_languages", lambda: {"eng", "hin"})
    assert _tesseract_language_code("auto") == "eng+hin"


def test_regex_extraction_does_not_publish_uncalibrated_confidence():
    from app.services.extraction_service import extract_document_report

    result = DocumentExtractionResult(
        pages=[ExtractedPage(page_number=1, text="Invoice No: AB12345 Total: 500.00", confidence=None)],
        combined_text="Invoice No: AB12345 Total: 500.00",
        overall_ocr_confidence=None,
    )
    report = extract_document_report(result)
    assert report.overall_confidence is None
    assert report.requires_review is True
    assert report.fields
    assert all(field.confidence is None for field in report.fields)
