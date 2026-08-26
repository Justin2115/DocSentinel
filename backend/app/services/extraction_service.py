import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.ocr_service import DocumentExtractionResult, ExtractedPage

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFieldData:
    field_name: str
    field_value: str
    confidence: float
    page_number: int = 1
    original_value: str | None = None
    corrected_value: str | None = None
    is_verified: bool = False


@dataclass
class DocumentExtractionReport:
    document_type: str
    fields: list[ExtractedFieldData] = field(default_factory=list)
    overall_confidence: float = 0.0
    requires_review: bool = False
    review_reason: str | None = None


# Classification keywords
DOCUMENT_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("Invoice", ["invoice", "bill to", "tax invoice", "subtotal", "amount due", "invoice number", "inv#", "gstin"]),
    ("Receipt", ["receipt", "cash receipt", "payment receipt", "amount paid", "balance paid", "transaction id"]),
    ("Application Form", ["application form", "applicant name", "reference #", "employment application", "candidate", "personal details", "educational details"]),
    ("Medical Claim", ["medical claim", "diagnosis", "health insurance", "hospital", "patient", "claim form", "doctor"]),
    ("Contract", ["agreement", "terms and conditions", "parties", "non-disclosure", "confidentiality", "contract", "witnesseth", "hereby agree"]),
    ("ID Document", ["identity card", "passport", "driver license", "driving licence", "aadhaar", "ssn", "date of birth", "dob", "nationality", "id number"]),
]

# Field extraction regex patterns & confidence weights
# Formats: (field_name, regex_pattern, base_confidence)
FIELD_EXTRACTION_RULES: list[dict[str, Any]] = [
    # Document Number / Identifier
    {
        "field_name": "document_number",
        "patterns": [
            r"(?i)(?:reference\s*#?|ref\s*no\.?|reference\s*no\.?)\s*[:\-\n]?\s*([A-Z0-9\-\/]{5,30})",
            r"(?i)(?:invoice\s*(?:number|no\.?|#)|inv\s*#?)\s*[:\-\n]?\s*([A-Z0-9\-\/]{3,30})",
            r"(?i)(?:application\s*(?:number|no\.?|#)|app\s*#?)\s*[:\-\n]?\s*([A-Z0-9\-\/]{4,30})",
            r"(?i)(?:id\s*(?:number|no\.?|#)|document\s*no\.?)\s*[:\-\n]?\s*([A-Z0-9\-\/]{4,30})",
            r"(?i)(?:policy\s*(?:number|no\.?|#)|claim\s*no\.?)\s*[:\-\n]?\s*([A-Z0-9\-\/]{4,30})",
        ],
        "base_confidence": 92.0,
    },
    # Dates
    {
        "field_name": "date",
        "patterns": [
            r"(?i)(?:date\s*of\s*birth|dob)\s*[:\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
            r"(?i)(?:invoice\s*date|issue\s*date|billing\s*date|date)\s*[:\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
            r"(?i)(?:date)\s*[:\-\n]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
            r"(?i)(?:date)\s*[:\-\n]?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
            r"\b(\d{4}[-\/]\d{2}[-\/]\d{2})\b",
            r"\b(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{4})\b",
        ],
        "base_confidence": 88.0,
    },
    # Full Name / Person Name
    {
        "field_name": "full_name",
        "patterns": [
            r"(?i)(?:applicant\s*name|candidate\s*name|full\s*name|name\s*of\s*applicant|patient\s*name|employee\s*name)\s*[:\-\n]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
            r"(?i)(?:name)\s*[:\-\n]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
            r"(?i)(?:bill\s*to|sold\s*to|customer\s*name)\s*[:\-\n]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
        ],
        "base_confidence": 85.0,
    },
    # Total Amount
    {
        "field_name": "total_amount",
        "patterns": [
            r"(?i)(?:grand\s*total|total\s*amount|amount\s*due|total\s*payable|net\s*amount|total)\s*[:\-\n]?\s*(?:[$€£₹]|USD|EUR|GBP|INR|Rs\.?)?\s*([\d,]+\.\d{2})",
            r"(?i)(?:[$€£₹]|USD|EUR|GBP|INR|Rs\.?)\s*([\d,]+\.\d{2})\b",
            r"(?i)(?:total)\s*[:\-\n]?\s*([\d,]+\.?\d*)",
        ],
        "base_confidence": 90.0,
    },
    # Email Address
    {
        "field_name": "email",
        "patterns": [
            r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b",
        ],
        "base_confidence": 96.0,
    },
    # Phone Number
    {
        "field_name": "phone",
        "patterns": [
            r"(?i)(?:phone|mobile|tel|contact)\s*[:\-\n]?\s*(\+?\d{1,4}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{2,5}[-.\s]?\d{3,5})",
            r"\b(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        ],
        "base_confidence": 87.0,
    },
    # Organization / Company
    {
        "field_name": "organization",
        "patterns": [
            r"(?i)(?:company|organization|institution|college\/institute|employer|vendor)\s*[:\-\n]?\s*([A-Za-z0-9\s,\.&]{3,50})",
            r"(?i)(?:tata\s+consultancy\s+services|tcs|infosys|wipro|google|microsoft|amazon|accenture|cognizant)\b",
        ],
        "base_confidence": 82.0,
    },
    # Address
    {
        "field_name": "address",
        "patterns": [
            r"(?i)(?:address|billing\s*address|shipping\s*address|permanent\s*address)\s*[:\-\n]?\s*([A-Za-z0-9\s,\.\-#\/]{10,80})",
        ],
        "base_confidence": 78.0,
    },
]


def classify_document_type(text: str) -> tuple[str, float]:
    """
    Classify the document type based on keyword frequency and relevance.
    Returns (document_type, confidence).
    """
    if not text:
        return "General Document", 50.0

    lower_text = text.lower()
    scores: dict[str, int] = {}

    for doc_type, keywords in DOCUMENT_TYPE_RULES:
        count = sum(1 for kw in keywords if kw in lower_text)
        if count > 0:
            scores[doc_type] = count

    if not scores:
        return "General Document", 60.0

    best_type = max(scores, key=scores.get)
    max_score = scores[best_type]
    confidence = min(95.0, 65.0 + (max_score * 7.5))

    return best_type, round(confidence, 2)


def extract_fields_from_text(
    text: str, page_number: int = 1
) -> list[ExtractedFieldData]:
    """
    Extract structured key-value pairs from a text string.
    """
    extracted: list[ExtractedFieldData] = []
    seen_field_names: set[str] = set()

    for rule in FIELD_EXTRACTION_RULES:
        field_name = rule["field_name"]
        if field_name in seen_field_names:
            continue

        base_conf = rule["base_confidence"]
        for pattern in rule["patterns"]:
            match = re.search(pattern, text)
            if match:
                matched_val = None
                if match.groups():
                    for g in match.groups():
                        if g is not None:
                            matched_val = g.strip()
                            break
                if not matched_val:
                    matched_val = match.group(0).strip()
                value = matched_val
                # For single-line fields, keep only the first line
                if field_name in ("full_name", "document_number", "email", "phone", "date", "total_amount"):
                    value = value.split("\n")[0].strip()

                # Clean extraneous characters
                value = value.strip(":,.- \t\r\n")


                if len(value) >= 2 and not value.isspace():
                    # Calculate explainable confidence
                    conf = base_conf
                    # Length sanity adjustment
                    if len(value) < 3 and field_name not in ("id", "state"):
                        conf -= 10.0


                    extracted.append(
                        ExtractedFieldData(
                            field_name=field_name,
                            field_value=value,
                            confidence=round(conf, 2),
                            page_number=page_number,
                            original_value=value,
                            corrected_value=value,
                            is_verified=False,
                        )
                    )
                    seen_field_names.add(field_name)
                    break

    return extracted


def extract_document_report(
    ocr_result: DocumentExtractionResult,
) -> DocumentExtractionReport:
    """
    Main extraction pipeline:
    1. Classifies document type.
    2. Extracts fields across all pages.
    3. Calculates overall document confidence.
    4. Evaluates if the document needs human review in the review queue.
    """
    combined_text = ocr_result.combined_text
    doc_type, type_confidence = classify_document_type(combined_text)

    all_fields: list[ExtractedFieldData] = []
    seen_field_keys: set[str] = set()

    # Extract fields from each page
    for page in ocr_result.pages:
        page_fields = extract_fields_from_text(page.text, page_number=page.page_number)
        for f in page_fields:
            if f.field_name not in seen_field_keys:
                all_fields.append(f)
                seen_field_keys.add(f.field_name)

    # If some fields not found on individual pages, try combined text
    combined_fields = extract_fields_from_text(combined_text, page_number=1)
    for f in combined_fields:
        if f.field_name not in seen_field_keys:
            all_fields.append(f)
            seen_field_keys.add(f.field_name)

    # Compute overall document confidence
    # Weighted combination: 35% OCR confidence, 45% fields confidence, 20% document classification confidence
    field_confidences = [f.confidence for f in all_fields]
    avg_field_conf = (
        sum(field_confidences) / len(field_confidences)
        if field_confidences
        else (ocr_result.overall_ocr_confidence or 50.0)
    )

    overall_conf = (
        0.35 * (ocr_result.overall_ocr_confidence or 50.0)
        + 0.45 * avg_field_conf
        + 0.20 * type_confidence
    )
    overall_conf = round(min(100.0, max(10.0, overall_conf)), 2)

    # Review queue determination (Threshold = 75.0%)
    requires_review = False
    review_reasons = []

    if overall_conf < 75.0:
        requires_review = True
        review_reasons.append(f"Low overall confidence score ({overall_conf}%)")

    if ocr_result.overall_ocr_confidence and ocr_result.overall_ocr_confidence < 65.0:
        requires_review = True
        review_reasons.append(f"Low OCR quality ({ocr_result.overall_ocr_confidence}%)")

    if not all_fields and len(combined_text) > 50:
        requires_review = True
        review_reasons.append("No structured fields could be confidently extracted")

    # If any extracted field has very low confidence (< 60.0%)
    low_conf_fields = [f.field_name for f in all_fields if f.confidence < 60.0]
    if low_conf_fields:
        requires_review = True
        review_reasons.append(f"Low confidence on fields: {', '.join(low_conf_fields)}")

    final_reason = "; ".join(review_reasons) if review_reasons else None

    return DocumentExtractionReport(
        document_type=doc_type,
        fields=all_fields,
        overall_confidence=overall_conf,
        requires_review=requires_review,
        review_reason=final_reason,
    )
