import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
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
    # Phone Number — only accept labelled numbers, international (+) format or
    # (xxx) area-code format; bare digit sequences are treated as references,
    # not phones (prevents reference numbers being misclassified as phone).
    {
        "field_name": "phone",
        "patterns": [
            r"(?i)(?:phone|mobile|telephone|tel|contact|फोन|मोबाईल|दूरभाष)\s*[:\|\-\n]?\s*(\+?\d[\d\s\-().]{7,18})",
            (r"(\+\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4})", 78.0),
            (r"(\(\d{3}\)\s?\d{3}[-.\s]?\d{4})", 75.0),
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
    # Tax ID / GSTIN
    {
        "field_name": "tax_id",
        "patterns": [
            r"(?i)(?:gstin|gst\s*no\.?|tax\s*id|vat\s*no\.?)\s*[:\|\-\n]?\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]|[A-Z0-9]{8,20})",
        ],
        "base_confidence": 94.0,
    },
    # PAN Number
    {
        "field_name": "pan_number",
        "patterns": [
            r"(?i)(?:pan\s*no\.?|pan\s*number|pan)\s*[:\|\-\n]?\s*([A-Z]{5}[0-9]{4}[A-Z])\b",
        ],
        "base_confidence": 95.0,
    },
    # Supplier Name
    {
        "field_name": "supplier_name",
        "patterns": [
            r"(?i)(?:vendor|supplier|from|seller|biller|merchant|issued\s*by)\s*[:\|\-\n]?\s*([A-Za-z0-9\u0900-\u097F\s,\.&]{3,50})",
        ],
        "base_confidence": 85.0,
    },
    # Customer Name
    {
        "field_name": "customer_name",
        "patterns": [
            r"(?i)(?:bill\s*to|sold\s*to|customer\s*name|client\s*name|invoiced\s*to|buyer)\s*[:\|\-\n]?\s*([A-Za-z0-9\u0900-\u097F\s,\.&]{3,50})",
        ],
        "base_confidence": 86.0,
    },
    # Invoice Number
    {
        "field_name": "invoice_number",
        "patterns": [
            r"(?i)(?:invoice\s*(?:no\.?|number|#)|inv\s*#|bill\s*(?:no\.?|number|#))\s*[:\|\-\n]?\s*([A-Za-z0-9\-\/]{3,30})",
            r"(?i)\binv(?:oice)?\s*(?:no\.?|number|#)\s*[:\|\-\n]?\s*([A-Za-z0-9\-\/]{3,30})",
        ],
        "base_confidence": 94.0,
    },
    # Payment Terms
    {
        "field_name": "payment_terms",
        "patterns": [
            r"(?i)(?:payment\s*terms|terms\s*of\s*payment|terms)\s*[:\|\-\n]?\s*(net\s*\d+|due\s*upon\s*receipt|immediate|cash\s*on\s*delivery|\d+\s*days)",
        ],
        "base_confidence": 88.0,
    },
    # Contract Parties
    {
        "field_name": "party_one",
        "patterns": [
            r"(?i)(?:between|by\s*and\s*between|first\s*party|disclosing\s*party|lessor|licensor|employer|client)\s*[:\|\-\n]?\s*([A-Za-z0-9\u0900-\u097F\s,\.&]{3,60}?)(?:,|\sand\b|\shereinafter|\s\()",
        ],
        "base_confidence": 84.0,
    },
    {
        "field_name": "party_two",
        "patterns": [
            r"(?i)(?:and\s*(?:the\s*)?second\s*party|receiving\s*party|lessee|licensee|employee|contractor)\s*[:\|\-\n]?\s*([A-Za-z0-9\u0900-\u097F\s,\.&]{3,60}?)(?:,|\shereinafter|\s\()",
            r"(?i)(?:and\s+)([A-Za-z0-9\u0900-\u097F\s,\.&]{3,60}?)(?:,\s*hereinafter)",
        ],
        "base_confidence": 84.0,
    },
    # Effective Date
    {
        "field_name": "effective_date",
        "patterns": [
            r"(?i)(?:effective\s*date|commencement\s*date|start\s*date|dated\s*as\s*of)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        ],
        "base_confidence": 90.0,
    },
    # Expiry Date
    {
        "field_name": "expiry_date",
        "patterns": [
            r"(?i)(?:expiration\s*date|expiry\s*date|termination\s*date|end\s*date)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        ],
        "base_confidence": 90.0,
    },
    # Governing Law
    {
        "field_name": "governing_law",
        "patterns": [
            r"(?i)(?:governed\s*by\s*(?:the\s*laws\s*of)?|governing\s*law|jurisdiction)\s*[:\|\-\n]?\s*([A-Za-z\s]{3,40})",
        ],
        "base_confidence": 86.0,
    },
    # Renewal Terms
    {
        "field_name": "renewal_terms",
        "patterns": [
            r"(?i)(?:renewal|term\s*of\s*agreement|renewal\s*terms)\s*[:\|\-\n]?\s*([A-Za-z0-9\s,\.]{5,80})",
        ],
        "base_confidence": 82.0,
    },
    # Contract Clauses: Termination, Liability, Confidentiality
    {
        "field_name": "termination_clause",
        "patterns": [
            r"(?i)(?:termination|cancellation|right\s*to\s*terminate)\s*[:\|\-\n]?\s*([^\n\r]{10,120})",
        ],
        "base_confidence": 82.0,
    },
    {
        "field_name": "liability_clause",
        "patterns": [
            r"(?i)(?:limitation\s*of\s*liability|liability\s*shall\s*not\s*exceed|liability\s*cap)\s*[:\|\-\n]?\s*([^\n\r]{5,90})",
        ],
        "base_confidence": 82.0,
    },
    {
        "field_name": "confidentiality_clause",
        "patterns": [
            r"(?i)(?:confidentiality|non-disclosure|confidential\s*information)\s*[:\|\-\n]?\s*([^\n\r]{10,120})",
        ],
        "base_confidence": 82.0,
    },
    # Forms: Applicant Name & Application Number
    {
        "field_name": "applicant_name",
        "patterns": [
            r"(?i)(?:applicant\s*name|candidate\s*name|name\s*of\s*applicant)\s*[:\|\-\n]?\s*([A-Za-z\u0900-\u097F\s\.]{3,50})",
        ],
        "base_confidence": 88.0,
    },
    {
        "field_name": "application_number",
        "patterns": [
            r"(?i)(?:application\s*(?:no\.?|number|#)|enrollment\s*(?:no\.?|number|#)|\bapp\s*#)\s*[:\|\-\n]?\s*([A-Za-z0-9\-\/]{4,30})",
        ],
        "base_confidence": 92.0,
    },
    {
        "field_name": "submission_date",
        "patterns": [
            r"(?i)(?:submission\s*date|submitted\s*on|application\s*date)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
        ],
        "base_confidence": 89.0,
    },
    # Identity Documents: ID Type, ID Number, DOB, Nationality
    {
        "field_name": "id_type",
        "patterns": [
            r"(?i)\b(passport|driving\s*licen[sc]e|driver\s*licen[sc]e|aadhaar(?:\s*card)?|pan(?:\s*card)?|voter\s*id|national\s*id|ssn)\b",
        ],
        "base_confidence": 92.0,
    },
    {
        "field_name": "id_number",
        "patterns": [
            r"(?i)(?:id\s*(?:no\.?|number|#)|licen[sc]e\s*no\.?|passport\s*no\.?|aadhaar\s*no\.?)\s*[:\|\-\n]?\s*([A-Za-z0-9\s\-]{6,25})",
            r"\b([A-Z][0-9]{7,8})\b",
            r"\b([2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4})\b",
        ],
        "base_confidence": 91.0,
    },
    {
        "field_name": "date_of_birth",
        "patterns": [
            r"(?i)(?:date\s*of\s*birth|dob|जन्म\s*तारीख|जन्मदिनांक)\s*[:\|\-\n]?\s*(\d{1,2}[-\/\.]\d{1,2}[-\/\.]\d{2,4})",
        ],
        "base_confidence": 93.0,
    },
    {
        "field_name": "nationality",
        "patterns": [
            r"(?i)(?:nationality|citizenship|country\s*of\s*issue)\s*[:\|\-\n]?\s*([A-Za-z]{3,30})",
        ],
        "base_confidence": 88.0,
    },
    # Certificates & Reports
    {
        "field_name": "recipient_name",
        "patterns": [
            r"(?i)(?:awarded\s*to|presented\s*to|certifies\s*that|this\s*is\s*to\s*certify\s*that)\s*[:\|\-\n]?\s*([A-Za-z\u0900-\u097F\s\.]{3,50})",
        ],
        "base_confidence": 86.0,
    },
    {
        "field_name": "certificate_title",
        "patterns": [
            r"(?i)(?:certificate\s*of\s*[A-Za-z\s]{3,40}|completion\s*certificate|achievement\s*certificate|report\s*title)\b",
        ],
        "base_confidence": 87.0,
    },
    {
        "field_name": "issuing_organization",
        "patterns": [
            r"(?i)(?:issued\s*by|certified\s*by|issuing\s*authority)\s*[:\|\-\n]?\s*([A-Za-z0-9\u0900-\u097F\s,\.&]{3,50})",
        ],
        "base_confidence": 85.0,
    },
    {
        "field_name": "report_period",
        "patterns": [
            r"(?i)(?:period\s*ending|for\s*the\s*period|report\s*period|quarter\s*ended)\s*[:\|\-\n]?\s*([A-Za-z0-9\s,\.\-\/]{4,40})",
        ],
        "base_confidence": 86.0,
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



_DATE_FIELD_NAMES = (
    "date",
    "due_date",
    "transaction_date",
    "effective_date",
    "expiry_date",
    "submission_date",
    "date_of_birth",
)


def _is_plausible_date(value: str) -> bool:
    """Validate date against real calendar rules (leap years, month days, century bounds).

    A damaged recogniser turns "09/09/2024" into "1707-50-50" or "31/04/2024".
    Real calendar rules ensure month lengths (e.g. April 30 days) and leap years
    (e.g. Feb 29 2024 valid, Feb 29 2023 invalid) are respected.
    """
    val = value.strip()
    parts = [p for p in re.split(r"[^0-9]+", val) if p]
    if len(parts) < 3:
        # Check if text date with month name e.g. "14 August 2024", "April 30, 2024"
        for fmt in (
            "%d %B %Y",
            "%d %b %Y",
            "%B %d %Y",
            "%b %d %Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ):
            try:
                datetime.strptime(val, fmt)
                return True
            except ValueError:
                pass
        return True

    numbers = [int(p) for p in parts[:3]]
    long_parts = [n for n in numbers if n >= 100]
    if len(long_parts) == 1:
        year = long_parts[0]
        smalls = [n for n in numbers if n < 100]
    elif not long_parts:
        year = 2000 + numbers[-1] if numbers[-1] <= 30 else 1900 + numbers[-1]
        smalls = numbers[:2]
    else:
        return False

    if year < 1900 or year > date.today().year + 5:
        return False
    if len(smalls) < 2:
        return True

    # Real calendar validation for day/month/year
    p1, p2 = smalls[0], smalls[1]
    for m, d in [(p1, p2), (p2, p1)]:
        if 1 <= m <= 12 and 1 <= d <= 31:
            try:
                date(year, m, d)
                return True
            except (ValueError, OverflowError):
                continue
    return False


def _parse_amount(value: str | None) -> float | None:
    """Parse numeric amount from currency strings."""
    if not value:
        return None
    cleaned = re.sub(r"[^\d.,\-+]", "", value.strip())
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." not in cleaned:
        if re.search(r",\d{2}$", cleaned):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def validate_totals_arithmetic(fields: dict[str, str]) -> tuple[bool, str | None]:
    """
    Validate arithmetic consistency on totals:
    subtotal + tax - discount - credit == total_amount (within 0.05 tolerance).
    Does not modify or repair recognized values; reports discrepancy clearly.
    """
    subtotal = _parse_amount(fields.get("subtotal"))
    total = _parse_amount(fields.get("total_amount"))
    tax = _parse_amount(fields.get("tax"))
    discount = _parse_amount(fields.get("discount"))
    credit = _parse_amount(fields.get("credit"))

    if total is not None and subtotal is not None:
        computed = subtotal
        if tax is not None:
            computed += tax
        if discount is not None:
            computed -= discount
        if credit is not None:
            computed -= credit

        diff = round(abs(computed - total), 2)
        if diff > 0.05:
            components = [f"subtotal ({subtotal:.2f})"]
            if tax is not None:
                components.append(f"+ tax ({tax:.2f})")
            if discount is not None:
                components.append(f"- discount ({discount:.2f})")
            if credit is not None:
                components.append(f"- credit ({credit:.2f})")
            calc_str = " ".join(components)
            return False, (
                f"Arithmetic discrepancy in totals: {calc_str} = {computed:.2f}, "
                f"but extracted total is {total:.2f} (diff {diff:.2f})"
            )

    return True, None


def validate_identifier_format(field_name: str, value: str) -> tuple[bool, str | None]:
    """Validate identifier against established format rules."""
    val = value.strip().upper()
    if field_name == "pan_number":
        if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", val):
            return False, f"PAN format discrepancy: '{value}' does not match standard 10-character PAN format"
    elif field_name == "tax_id" and len(val) == 15 and re.match(r"^[0-9]{2}", val):
        if not re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", val):
            return False, f"GSTIN format discrepancy: '{value}' does not match standard 15-character GSTIN structure"
    return True, None


_RESERVED_FIELD_NAMES = {
    "page", "http", "https", "www", "com", "org", "net", "tel", "fax", "email",
    "note", "notes", "warning", "caution", "notice", "total", "subtotal", "tax", "date",
    "table", "signature", "signed", "copy", "status",
}


def extract_generic_key_value_pairs(
    text: str,
    page_number: int = 1,
    existing_field_names: set[str] | None = None,
) -> list[ExtractedFieldData]:
    """
    Generic fallback extractor for arbitrary key-value pairs (e.g. from forms,
    certificates, administrative reports, or unconventional layouts).
    """
    if not text:
        return []

    existing = set(existing_field_names or ())
    results: list[ExtractedFieldData] = []

    kv_pattern = re.compile(
        r"^[ \t]*([A-Za-z\u0900-\u097F][A-Za-z0-9\u0900-\u097F\s/_\-]{1,35}?)\s*[:=]\s*([^\n\r]{2,100})$",
        re.MULTILINE,
    )

    for match in kv_pattern.finditer(text):
        raw_key = match.group(1).strip()
        raw_val = match.group(2).strip()

        norm_key = re.sub(r"[^\w]+", "_", raw_key.lower()).strip("_")
        if not norm_key or len(norm_key) < 3:
            continue
        if norm_key in _RESERVED_FIELD_NAMES or norm_key in existing:
            continue
        if raw_val.isspace() or len(raw_val) < 2:
            continue
        if re.match(r"^https?://", raw_val):
            continue

        val = raw_val.strip(":,.- \t")
        # Support checkbox indicators where present e.g. [x], [X], [✓], [ ]
        val = re.sub(r"\[[xX✓]\]", "Yes (Checked)", val)
        val = re.sub(r"\[\s*\]", "No (Unchecked)", val)
        conf = 78.0
        if len(val) < 3:
            conf -= 8.0

        results.append(
            ExtractedFieldData(
                field_name=norm_key,
                field_value=val,
                confidence=round(conf, 2),
                page_number=page_number,
                original_value=val,
                corrected_value=val,
                is_verified=False,
            )
        )
        existing.add(norm_key)

    return results


def extract_table_line_items(
    pages: list[ExtractedPage],
) -> list[ExtractedFieldData]:
    """
    Extract structured table summary and line item metrics across pages.
    Preserves row/column relationships, headers, and individual line item details.
    """
    table_fields: list[ExtractedFieldData] = []
    total_line_items = 0
    extracted_items_count = 0

    for page in pages:
        tables = getattr(page, "tables", None) or []
        for t_idx, tbl in enumerate(tables):
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            if headers and rows:
                total_line_items += len(rows)
                header_str = " | ".join(headers)
                table_fields.append(
                    ExtractedFieldData(
                        field_name=f"table_{t_idx + 1}_headers",
                        field_value=header_str,
                        confidence=88.0,
                        page_number=page.page_number,
                        original_value=header_str,
                        corrected_value=header_str,
                        is_verified=False,
                    )
                )
                for r_idx, r in enumerate(rows[:10]):
                    row_str = " | ".join(r)
                    if len(r) >= 2 and any(r):
                        extracted_items_count += 1
                        table_fields.append(
                            ExtractedFieldData(
                                field_name=f"line_item_{extracted_items_count}",
                                field_value=row_str,
                                confidence=85.0,
                                page_number=page.page_number,
                                original_value=row_str,
                                corrected_value=row_str,
                                is_verified=False,
                            )
                        )

        if not tables and page.text:
            lines = [l.strip() for l in page.text.split("\n") if " | " in l]
            if len(lines) >= 2:
                total_line_items += len(lines) - 1
                for r_idx, line in enumerate(lines[1:11]):
                    extracted_items_count += 1
                    table_fields.append(
                        ExtractedFieldData(
                            field_name=f"line_item_{extracted_items_count}",
                            field_value=line,
                            confidence=85.0,
                            page_number=page.page_number,
                            original_value=line,
                            corrected_value=line,
                            is_verified=False,
                        )
                    )

    if total_line_items > 0:
        table_fields.append(
            ExtractedFieldData(
                field_name="line_items_count",
                field_value=str(total_line_items),
                confidence=90.0,
                page_number=1,
                original_value=str(total_line_items),
                corrected_value=str(total_line_items),
                is_verified=False,
            )
        )
    return table_fields


def extract_fields_from_text(
    text: str, page_number: int = 1, page_ocr_conf: float | None = None
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
        for entry in rule["patterns"]:
            pattern, pattern_conf = entry if isinstance(entry, tuple) else (entry, base_conf)
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
                if field_name not in (
                    "address",
                    "party_one",
                    "party_two",
                    "renewal_terms",
                    "table_1_headers",
                    "table_2_headers",
                    "table_3_headers",
                ):
                    value = value.split("\n")[0].strip()

                # Clean extraneous characters
                value = value.strip(":,.- \t\r\n")

                if len(value) >= 2 and not value.isspace():
                    # Phone values must contain at least 8 digits to be credible
                    if field_name == "phone" and sum(c.isdigit() for c in value) < 8:
                        continue

                    # A date-shaped value that no calendar allows is recogniser noise
                    if field_name in _DATE_FIELD_NAMES and not _is_plausible_date(value):
                        continue

                    # Calculate explainable confidence
                    conf = pattern_conf
                    # Length sanity adjustment
                    if len(value) < 3 and field_name not in ("id", "state"):
                        conf -= 10.0

                    # Factor in page OCR confidence if available
                    if page_ocr_conf is not None and page_ocr_conf < 80.0:
                        conf = min(
                            conf,
                            round(conf * (page_ocr_conf / 100.0) + (100.0 - page_ocr_conf) * 0.35, 2),
                        )

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
    2. Extracts fields across all pages (rule-based + generic fallback + table items).
    3. Validates arithmetic consistency and identifier formats.
    4. Calculates honest overall document confidence.
    5. Evaluates if the document needs human review in the review queue.
    """
    combined_text = ocr_result.combined_text
    department, doc_type, type_confidence = classify_document_category(combined_text)

    all_fields: list[ExtractedFieldData] = []
    seen_field_keys: set[str] = set()

    # Extract fields from each page with OCR confidence context
    for page in ocr_result.pages:
        page_fields = extract_fields_from_text(
            page.text,
            page_number=page.page_number,
            page_ocr_conf=page.confidence,
        )
        for f in page_fields:
            if f.field_name not in seen_field_keys:
                all_fields.append(f)
                seen_field_keys.add(f.field_name)

    # If some fields not found on individual pages, try combined text
    combined_fields = extract_fields_from_text(
        combined_text,
        page_number=1,
        page_ocr_conf=ocr_result.overall_ocr_confidence,
    )
    for f in combined_fields:
        if f.field_name not in seen_field_keys:
            all_fields.append(f)
            seen_field_keys.add(f.field_name)

    # Generic key-value fallback extraction for forms, reports, certificates, and unclassified docs
    for page in ocr_result.pages:
        kv_fields = extract_generic_key_value_pairs(
            page.text,
            page_number=page.page_number,
            existing_field_names=seen_field_keys,
        )
        for f in kv_fields:
            if f.field_name not in seen_field_keys:
                all_fields.append(f)
                seen_field_keys.add(f.field_name)

    # Table line items and header extraction
    table_fields = extract_table_line_items(ocr_result.pages)
    for tf in table_fields:
        if tf.field_name not in seen_field_keys:
            all_fields.append(tf)
            seen_field_keys.add(tf.field_name)

    # Compute overall document confidence
    # Weighted combination of OCR quality, field quality and classification
    field_confidences = [f.confidence for f in all_fields]
    signals: list[tuple[float, float]] = []
    if ocr_result.overall_ocr_confidence:
        signals.append((0.35, ocr_result.overall_ocr_confidence))
    if field_confidences:
        signals.append((0.45, sum(field_confidences) / len(field_confidences)))
    signals.append((0.20, type_confidence))

    overall_conf = round(
        min(
            100.0,
            sum(weight * value for weight, value in signals)
            / sum(weight for weight, _ in signals),
        ),
        2,
    )

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

    empty_pages = [p.page_number for p in ocr_result.pages if not (p.text or "").strip()]
    if empty_pages:
        requires_review = True
        listed = ", ".join(str(n) for n in empty_pages[:8])
        review_reasons.append(
            f"{len(empty_pages)} page(s) produced no readable text (page{'s' if len(empty_pages) > 1 else ''} "
            f"{listed}); the quality score only covers the pages that were read"
        )

    page_statuses = set()
    for p in ocr_result.pages:
        if getattr(p, "status_stamps", None):
            page_statuses.update(p.status_stamps)
    if len(page_statuses) > 1:
        requires_review = True
        review_reasons.append(
            f"Conflicting payment statuses across pages ({', '.join(sorted(page_statuses))})"
        )

    # A table cell the engines could not agree on is stored empty and marked
    # uncertain. That is missing data a reviewer has to see, not a value to guess.
    unreadable = []
    for p in ocr_result.pages:
        count = sum(
            int(getattr(table, "uncertain_cells", 0) or 0)
            for table in (getattr(p, "tables", None) or [])
        )
        if count:
            unreadable.append((p.page_number, count))
    if unreadable:
        requires_review = True
        listed = ", ".join(f"page {n} ({c})" for n, c in unreadable[:6])
        review_reasons.append(
            f"Table cells left empty because no engine could read them: {listed}"
        )

    field_dict = {f.field_name: f.field_value for f in all_fields}

    # Arithmetic checks on totals (preserves recognized values without silent repairs)
    arithmetic_valid, arithmetic_msg = validate_totals_arithmetic(field_dict)
    if not arithmetic_valid and arithmetic_msg:
        requires_review = True
        review_reasons.append(arithmetic_msg)

    # Identifier format checks
    for f in all_fields:
        id_valid, id_msg = validate_identifier_format(f.field_name, f.field_value)
        if not id_valid and id_msg:
            requires_review = True
            review_reasons.append(id_msg)

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
