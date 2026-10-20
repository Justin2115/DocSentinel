"""Transparent OCR quality signals and reproducible transcription metrics.

Signals in this module are flags, not calibrated probabilities. Text normalization
is deliberately limited to Unicode NFC and whitespace normalization so dates,
digits, punctuation, and Devanagari distinctions remain measurable.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OCRQualityAssessment:
    warnings: list[str] = field(default_factory=list)
    replacement_character_count: int = 0
    character_count: int = 0


def assess_ocr_text(text: str, *, expected_content: bool = True) -> OCRQualityAssessment:
    warnings: list[str] = []
    replacement_count = text.count("\ufffd")
    if replacement_count:
        warnings.append("replacement-characters-present")
        if replacement_count >= 3 or replacement_count / max(len(text), 1) > 0.01:
            warnings.append("excessive-replacement-characters")
    if contains_package_path(text):
        warnings.append("binary-package-markers")
    control_count = sum(
        1 for char in text
        if unicodedata.category(char) == "Cc" and char not in "\n\r\t"
    )
    if control_count:
        warnings.append("invalid-control-characters")
    if expected_content and len("".join(text.split())) < 8:
        warnings.append("suspiciously-short-output")
    visible = [char for char in text if not char.isspace()]
    if len(visible) >= 40:
        wordish = sum(char.isalnum() for char in visible)
        if wordish / len(visible) < 0.12:
            warnings.append("symbol-dominated-output")
    return OCRQualityAssessment(warnings, replacement_count, len(text))


SEVERE_TEXT_WARNINGS = frozenset({
    "excessive-replacement-characters",
    "binary-package-markers",
    "invalid-control-characters",
})


def contains_package_path(text: str) -> bool:
    """Detect ZIP/Office package content accidentally emitted as document text."""
    return any(
        marker in (text or "").casefold()
        for marker in (
            "word/document.xml",
            "word/styles.xml",
            "xl/workbook.xml",
            "xl/worksheets/",
            "[content_types].xml",
            "pk" + chr(3) + chr(4),
        )
    )


def normalize_for_metric(text: str) -> str:
    """Normalize only canonical Unicode composition and whitespace."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def _edit_distance(left: list[str], right: list[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_item in enumerate(left, start=1):
        current = [i]
        for j, right_item in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left_item != right_item),
            ))
        previous = current
    return previous[-1]


def transcription_metrics(reference: str, hypothesis: str) -> dict[str, float | int]:
    """Return CER and WER as ratios in [0, inf), plus edit denominators."""
    ref = normalize_for_metric(reference)
    hyp = normalize_for_metric(hypothesis)
    ref_chars, hyp_chars = list(ref), list(hyp)
    ref_words, hyp_words = ref.split(), hyp.split()
    char_edits = _edit_distance(ref_chars, hyp_chars)
    word_edits = _edit_distance(ref_words, hyp_words)
    return {
        "cer": char_edits / max(len(ref_chars), 1),
        "wer": word_edits / max(len(ref_words), 1),
        "char_edits": char_edits,
        "word_edits": word_edits,
        "reference_characters": len(ref_chars),
        "reference_words": len(ref_words),
    }
