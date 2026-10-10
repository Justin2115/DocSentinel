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
    department: str | None = None
    fields: list[ExtractedFieldData] = field(default_factory=list)
    overall_confidence: float = 0.0
    requires_review: bool = False
    review_reason: str | None = None


# Classification keywords mapping: (document_type, department, keywords)
DOCUMENT_TYPE_RULES: list[tuple[str, str, list[str]]] = [
    # Finance
    ("Invoices", "Finance", ["invoice", "tax invoice", "bill to", "subtotal", "amount due", "invoice number", "inv#", "gstin"]),
    ("Invoice", "Finance", ["invoice", "tax invoice", "bill to", "subtotal", "amount due", "invoice number", "inv#", "gstin"]),
    ("Budget Reports", "Finance", ["budget report", "budget allocation", "financial forecast", "expenditure report", "fiscal year", "budget variance", "financial plan"]),
    ("Tax Documents", "Finance", ["tax document", "form 16", "w-2", "1099", "tax return", "income tax", "tds certificate", "tax assessment", "pan card"]),
    ("Receipt", "Finance", ["receipt", "cash receipt", "payment receipt", "amount paid", "balance paid", "transaction id"]),

    # HR
    ("Employee Records", "HR", ["employee record", "employment details", "employee details", "employee name", "staff record", "curriculum vitae", "resume", "joining date", "personal details", "educational details"]),
    ("Application Form", "HR", ["application form", "applicant name", "reference #", "employment application", "candidate", "personal details", "educational details"]),
    ("Policies", "HR", ["policy", "guidelines", "code of conduct", "regulations", "terms of employment", "leave policy", "workplace policy", "hr policy"]),
    ("Appraisals", "HR", ["appraisal", "performance review", "annual appraisal", "kpi", "competency evaluation", "key performance", "performance evaluation"]),

    # Legal
    ("Contracts", "Legal", ["contract", "parties", "non-disclosure", "confidentiality agreement", "witnesseth", "hereby agree", "terms and conditions"]),
    ("Contract", "Legal", ["contract", "parties", "non-disclosure", "confidentiality agreement", "witnesseth", "hereby agree", "terms and conditions"]),
    ("Agreements", "Legal", ["agreement", "memorandum of understanding", "mou", "service agreement", "lease agreement", "partnership agreement"]),
    ("Compliance Documents", "Legal", ["compliance document", "audit report", "regulatory compliance", "iso certification", "gdpr", "statutory compliance", "compliance"]),

    # Operations
    ("SOPs", "Operations", ["standard operating procedure", "sop", "procedure manual", "operating procedure", "workflow guide", "standard procedure"]),
    ("Operational Reports", "Operations", ["operational report", "daily operations", "incident report", "production log", "efficiency report", "shift handover", "logistics report"]),

    # IT
    ("Technical Documentation", "IT", ["technical documentation", "api documentation", "architecture diagram", "system specification", "deployment guide", "database schema", "user manual"]),
    ("Change Requests", "IT", ["change request", "rfc", "cr-", "system change", "configuration change", "infrastructure change", "maintenance window"]),

    # Other / Medical / ID (fallback mappings)
    ("Medical Claim", "Operations", ["medical claim", "diagnosis", "health insurance", "hospital", "patient", "claim form", "doctor"]),
    ("ID Document", "HR", ["identity card", "passport", "driver license", "driving licence", "aadhaar", "ssn", "date of birth", "dob", "nationality", "id number"]),
]


# Currency symbol & code prefix pattern supporting multiple combinations (e.g. USD $37.50)
_CURRENCY_PREFIX = r"(?:USD|EUR|GBP|INR|CAD|AUD|Rs\.?|[$€£₹]|\s)*"

# Field extraction regex patterns & confidence weights
# Formats: (field_name, regex_pattern, base_confidence)
FIELD_EXTRACTION_RULES: list[dict[str, Any]] = [
    # Document Number / Identifier
    {
        "field_name": "document_number",
        "patterns": [
            r"(?i)(?:reference\s*#?|ref\s*no\.?|reference\s*no\.?)\s*[:\|\-\n]?\s*([A-Z0-9\-\/]{5,30})",
            r"(?i)(?:invoice\s*(?:number|no\.?|#)|inv\s*#?)\s*[:\|\-\n]?\s*([A-Z0-9\-\/]{3,30})",
            r"(?i)(?:application\s*(?:number|no\.?|#)|app\s*#?)\s*[:\|\-\n]?\s*([A-Z0-9\-\/]{4,30})",
            r"(?i)(?:id\s*(?:number|no\.?|#)|document\s*no\.?)\s*[:\|\-\n]?\s*([A-Z0-9\-\/]{4,30})",
            r"(?i)(?:policy\s*(?:number|no\.?|#)|claim\s*no\.?)\s*[:\|\-\n]?\s*([A-Z0-9\-\/]{4,30})",
        ],
        "base_confidence": 92.0,
    },
    # Dates
    {
        "field_name": "date",
        "patterns": [
            r"(?i)(?:दिनांक|तारीख|date\s*of\s*birth|dob)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
            r"(?i)(?:invoice\s*date|issue\s*date|billing\s*date|date)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
            r"(?i)(?:date)\s*[:\|\-\n]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
            r"(?i)(?:date)\s*[:\|\-\n]?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
            r"\b(\d{4}[-\/]\d{2}[-\/]\d{2})\b",
            r"\b(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{4})\b",
        ],
        "base_confidence": 88.0,
    },
    # Due Date
    {
        "field_name": "due_date",
        "patterns": [
            r"(?i)(?:due\s*date|payment\s*due)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
            r"(?i)(?:due\s*date|payment\s*due)\s*[:\|\-\n]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        ],
        "base_confidence": 90.0,
    },
    # Full Name / Person Name
    {
        "field_name": "full_name",
        "patterns": [
            r"(?i)(?:applicant\s*name|candidate\s*name|full\s*name|name\s*of\s*applicant|patient\s*name|employee\s*name)\s*[:\|\-\n]?\s*([A-Za-z\u0900-\u097F]+(?:\s+[A-Za-z\u0900-\u097F]+){1,3})",
            r"(?i)(?:नाम|नाव|name)\s*[:\|\-\n]?\s*([A-Za-z\u0900-\u097F]+(?:\s+[A-Za-z\u0900-\u097F]+){1,3})",
            r"(?i)(?:invoiced\s*to|bill\s*to|sold\s*to|customer\s*name)\s*[:\|\-\n]?\s*([A-Za-z\u0900-\u097F]+(?:\s+[A-Za-z\u0900-\u097F]+){1,3})",
        ],
        "base_confidence": 85.0,
    },
    # Subtotal
    {
        "field_name": "subtotal",
        "patterns": [
            rf"(?i)(?:sub\s*total|subtotal)\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
        ],
        "base_confidence": 92.0,
    },
    # Credit
    {
        "field_name": "credit",
        "patterns": [
            rf"(?i)\bcredit\b\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
        ],
        "base_confidence": 90.0,
    },
    # Discount
    {
        "field_name": "discount",
        "patterns": [
            rf"(?i)(?:discount|promotional\s*code)[^|\n]*[:\|\-]?\s*{_CURRENCY_PREFIX}([-]?[\d,]+\.\d{{2}})",
            rf"(?i)(?:discount|coupon)\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([-]?[\d,]+\.\d{{2}})",
        ],
        "base_confidence": 90.0,
    },
    # Tax
    {
        "field_name": "tax",
        "patterns": [
            rf"(?i)(?:tax|vat|gst|sales\s*tax)\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
        ],
        "base_confidence": 88.0,
    },
    # Total Amount (Guarded to prevent misattribution to Credit 0.00 or Subtotal)
    {
        "field_name": "total_amount",
        "patterns": [
            rf"(?i)(?:grand\s*total|total\s*amount|total\s*payable|net\s*amount|एकूण\s*रक्कम|कुल\s*राशि)\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            rf"(?i)(?<!sub\s)(?<!description\s)\btotal\b\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            rf"(?i)(?:amount\s*due)\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
        ],
        "base_confidence": 92.0,
    },
    # Outstanding Balance
    {
        "field_name": "outstanding_balance",
        "patterns": [
            rf"(?i)(?:outstanding\s*balance|balance\s*due|balance)\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
        ],
        "base_confidence": 91.0,
    },
    # Amount Paid
    {
        "field_name": "amount_paid",
        "patterns": [
            rf"(?i)(?:amount\s*paid|paid\s*amount)\s*[:\|\-\n]?\s*{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            rf"(?i)ch_[A-Za-z0-9]+\s+\d{{2}}/\d{{2}}/\d{{4}}\s+{_CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
        ],
        "base_confidence": 89.0,
    },
    # Currency
    {
        "field_name": "currency",
        "patterns": [
            r"\b(USD|EUR|GBP|INR|CAD|AUD)\b",
            r"([$€£₹])",
        ],
        "base_confidence": 95.0,
    },
    # Transaction ID
    {
        "field_name": "transaction_id",
        "patterns": [
            r"(?i)(?:transaction\s*id|txn\s*id)\s*[:\|\-\n]?\s*([A-Za-z0-9_]{10,40})",
            r"\b(ch_[A-Za-z0-9]{15,35})\b",
            r"\b(txn_[A-Za-z0-9]{10,35})\b",
        ],
        "base_confidence": 94.0,
    },
    # Transaction Date
    {
        "field_name": "transaction_date",
        "patterns": [
            r"(?i)(?:transaction\s*date|txn\s*date)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
            r"(?i)ch_[A-Za-z0-9]+\s+(\d{2}/\d{2}/\d{4})",
        ],
        "base_confidence": 90.0,
    },
    # Payment Status
    {
        "field_name": "payment_status",
        "patterns": [
            r"(?i)(?:payment\s*status|status)\s*[:\|\-\n]?\s*(PAID|UNPAID|REFUNDED|CANCELLED|CANCELED|OVERDUE|PENDING|VOID|DRAFT)\b",
        ],
        "base_confidence": 93.0,
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
            r"(?i)(?:phone|mobile|tel|contact|फोन|मोबाईल)\s*[:\|\-\n]?\s*(\+?\d{1,4}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{2,5}[-.\s]?\d{3,5})",
            r"\b(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        ],
        "base_confidence": 87.0,
    },
    # Organization / Company
    {
        "field_name": "organization",
        "patterns": [
            r"(?i)(?:company|organization|institution|college\/institute|employer|vendor)\s*[:\|\-\n]?\s*([A-Za-z0-9\s,\.&]{3,50})",
            r"(?i)(?:tata\s+consultancy\s+services|tcs|infosys|wipro|google|microsoft|amazon|accenture|cognizant)\b",
            r"^([A-Z][a-zA-Z0-9\s]+(?:LLC|Inc\.?|Corp\.?|Ltd\.?|Business\s+Name))",
        ],
        "base_confidence": 82.0,
    },
    # Address
    {
        "field_name": "address",
        "patterns": [
            r"(?i)(?:address|billing\s*address|shipping\s*address|permanent\s*address|पत्ता|पता)\s*[:\|\-\n]?\s*([A-Za-z0-9\u0900-\u097F\s,\.\-#\/]{5,80})",
            r"\b(\d+\s+[A-Za-z0-9\s,\.]+(?:St\.?|Street|Ave\.?|Avenue|Rd\.?|Road|Blvd\.?|Lane)[^,\n]*,[^,\n]+[A-Z]{2}\s+\d{5})\b",
        ],
        "base_confidence": 78.0,
    },
]


def classify_document_category(text: str) -> tuple[str, str, float]:
    """
    Classify both document type and department based on keyword frequency.
    Returns (department, document_type, confidence).
    """
    if not text:
        return "Operations", "General Document", 50.0

    lower_text = text.lower()
    scores: dict[tuple[str, str], int] = {}

    for doc_type, dept, keywords in DOCUMENT_TYPE_RULES:
        count = sum(1 for kw in keywords if kw in lower_text)
        if count > 0:
            scores[(doc_type, dept)] = count

    if not scores:
        return "Operations", "General Document", 60.0

    best_match = max(scores, key=scores.get)
    max_score = scores[best_match]
    confidence = min(95.0, 65.0 + (max_score * 7.5))

    return best_match[1], best_match[0], round(confidence, 2)


def classify_document_type(text: str) -> tuple[str, float]:
    """
    Classify the document type based on keyword frequency and relevance.
    Returns (document_type, confidence).
    """
    _, doc_type, conf = classify_document_category(text)
    return doc_type, conf



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
    department, doc_type, type_confidence = classify_document_category(combined_text)

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

    # Flag conflicting or ambiguous fields for human review (Safeguard 2)
    page_statuses = set()
    for p in ocr_result.pages:
        if getattr(p, "status_stamps", None):
            page_statuses.update(p.status_stamps)
    if len(page_statuses) > 1:
        requires_review = True
        review_reasons.append(
            f"Conflicting payment statuses across pages ({', '.join(sorted(page_statuses))})"
        )

    field_dict = {f.field_name: f.field_value for f in all_fields}
    if field_dict.get("total_amount") == "0.00" and field_dict.get("subtotal") and field_dict.get("subtotal") != "0.00":
        requires_review = True
        review_reasons.append("Potential total amount conflict (Total is 0.00 while subtotal is non-zero)")

    # If any extracted field has very low confidence (< 60.0%)
    low_conf_fields = [f.field_name for f in all_fields if f.confidence < 60.0]
    if low_conf_fields:
        requires_review = True
        review_reasons.append(f"Low confidence on fields: {', '.join(low_conf_fields)}")

    final_reason = "; ".join(review_reasons) if review_reasons else None

    return DocumentExtractionReport(
        document_type=doc_type,
        department=department,
        fields=all_fields,
        overall_confidence=overall_conf,
        requires_review=requires_review,
        review_reason=final_reason,
    )

