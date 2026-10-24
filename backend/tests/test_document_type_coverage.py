"""One pipeline, many documents: type routing and field sets without per-file rules.

Each case is a representative page, not a project document. The point being held is
narrower than "everything is extracted": the fields a page labels are found by their
labels, the fields a page does not state are absent rather than invented, and a
number that cannot be a valid identity number is reported as read and flagged.
"""

import pytest

from app.services.extraction_service import (
    DocumentExtractionResult,
    ExtractedPage,
    extract_document_report,
    validate_identifier_format,
)

INVOICE = """
TAX INVOICE
Vendor: Acme Industrial Supplies Ltd
Bill To: Global Tech Solutions
Invoice No: INV-2024-9981
Date: 15/05/2024
Due Date: 15/06/2024
GSTIN: 27ABCDE1234F1Z5
Description | Qty | Rate | Amount
Steel Rods | 10 | 100.00 | 1,000.00
Fasteners | 50 | 10.00 | 500.00
Subtotal: 1,500.00
Tax: 270.00
Grand Total: 1,770.00
"""

AADHAAR = """
REPUBLIC OF INDIA
NATIONAL IDENTITY
Aadhaar No: 2345 6789 0123
Name: Ramesh Patil
Date of Birth: 12/04/1990
Gender: Male
Nationality: Indian
"""

CONTRACT = """
SERVICES AGREEMENT
This Agreement is made by and between Alpha Corp, hereinafter Client,
and Beta Services LLC, hereinafter Contractor.
Effective Date: 01/01/2024
Expiry Date: 31/12/2024
Governing Law: State of New York
Renewal Terms: Automatic renewal for 12 months unless terminated in writing.
Termination: Either party may terminate with 30 days written notice.
Limitation of Liability: Aggregate liability shall not exceed 100,000.00.
"""

RESUME = """
CURRICULUM VITAE
Full Name: Priya Nair
Email: priya.nair@example.com
Phone: +91 98765 43210
Professional Summary: Backend engineer with distributed systems experience.
Total Experience: 6 years in Python and Go services
Education: B.E. Computer Science, Pune University
Technical Skills: Python, PostgreSQL, Docker
"""

BANK_STATEMENT = """
STATEMENT OF ACCOUNT
HDFC Bank - Branch: Andheri West
Account No: 50100 23456 7890
IFSC Code: HDFC0000123
Statement Period: 01/04/2024 to 30/04/2024
Opening Balance: 42,500.00
Date | Description | Debit | Credit | Balance
05/04/2024 | Rent | 15,000.00 | | 27,500.00
18/04/2024 | Salary | | 85,000.00 | 112,500.00
Closing Balance: 112,500.00
"""

POLICY = """
INSURANCE POLICY SCHEDULE
Policy Number: PL-2024-778199
Policyholder: Suresh Iyer
Sum Assured: 5,00,000.00
Annual Premium: 38,400.00
Coverage Period: 01/04/2024 to 31/03/2025
Effective Date: 01/04/2024
Expiry Date: 31/03/2025
"""

# A page that states no type of its own and labels nothing a rule can read.
NEUTRAL_PAGE = """
Site visit notes
Weather overcast. Walked the storage yard and counted fourteen pallets.
Signed by the duty officer on duty.
"""


def report_of(text: str, *, confidence: float = 95.0):
    page = ExtractedPage(page_number=1, text=text, confidence=confidence)
    return extract_document_report(
        DocumentExtractionResult(
            pages=[page],
            combined_text=text,
            overall_ocr_confidence=confidence,
            is_ocr_applied=True,
        )
    )


def field_map(report) -> dict[str, str]:
    return {f.field_name: f.field_value for f in report.fields}


class TestTypeRouting:
    @pytest.mark.parametrize(
        "text,expected",
        [
            (INVOICE, "Invoices"),
            (CONTRACT, "Contracts"),
            (RESUME, "Resumes"),
            (BANK_STATEMENT, "Bank Statements"),
            (POLICY, "Insurance Policy"),
            (AADHAAR, "ID Document"),
        ],
    )
    def test_each_representative_page_routes_to_its_own_type(self, text, expected):
        assert report_of(text).document_type == expected

    def test_an_unrecognised_page_is_not_forced_into_a_type(self):
        report = report_of(NEUTRAL_PAGE)
        assert report.document_type == "General Document"
        # Saying "we found nothing in this page" is the honest outcome, and it is
        # shown as one rather than quietly stored as an empty document.
        assert report.requires_review is True
        assert "No structured fields" in report.review_reason

    def test_a_policy_is_not_mistaken_for_a_workplace_policy(self):
        # Both contain the bare word "policy"; the specific labels decide it.
        assert report_of(POLICY).document_type == "Insurance Policy"


class TestInvoiceFields:
    def test_amounts_and_identifiers(self):
        fields = field_map(report_of(INVOICE))
        assert fields["invoice_number"] == "INV-2024-9981"
        assert fields["subtotal"] == "1,500.00"
        assert fields["tax"] == "270.00"
        assert fields["total_amount"] == "1,770.00"
        assert fields["due_date"] == "15/06/2024"
        assert fields["supplier_name"].startswith("Acme Industrial")

    def test_line_items_are_labelled_rows_not_flattened_text(self):
        report = report_of(INVOICE)
        fields = field_map(report)
        assert fields["line_items_count"] == "2"
        assert "Description=Steel Rods" in fields["line_item_1"]
        assert "Amount=1,000.00" in fields["line_item_1"]

    def test_a_field_the_page_does_not_state_stays_absent(self):
        no_tax = INVOICE.replace("Tax: 270.00\n", "")
        fields = field_map(report_of(no_tax))
        # Nothing is guessed to make the arithmetic close.
        assert "tax" not in fields
        assert fields["total_amount"] == "1,770.00"


class TestIdentityDocument:
    def test_labelled_aadhaar_gets_its_own_field(self):
        fields = field_map(report_of(AADHAAR))
        assert fields["aadhaar_number"] == "2345 6789 0123"
        assert fields["date_of_birth"] == "12/04/1990"
        assert fields["gender"] == "Male"

    def test_aadhaar_format_rules(self):
        assert validate_identifier_format("aadhaar_number", "2345 6789 0123")[0] is True
        valid, message = validate_identifier_format("aadhaar_number", "1345 6789 0123")
        assert valid is False
        assert "Aadhaar format discrepancy" in message
        valid, _ = validate_identifier_format("aadhaar_no", "2345 678 0123")
        assert valid is False

    def test_a_misread_identity_number_is_flagged_and_left_as_read(self):
        damaged = AADHAAR.replace("2345 6789 0123", "2345 678 0123")
        report = report_of(damaged, confidence=71.0)
        fields = field_map(report)
        assert report.requires_review is True
        assert "Aadhaar format discrepancy" in report.review_reason
        # The number shown is the number the reader saw. Nothing is padded out.
        assert fields["aadhaar_no"] == "2345 678 0123"
        assert "aadhaar_number" not in fields


class TestBankStatement:
    def test_balances_keep_their_own_labels(self):
        fields = field_map(report_of(BANK_STATEMENT))
        assert fields["opening_balance"] == "42,500.00"
        assert fields["closing_balance"] == "112,500.00"
        # An opening balance is not money owed, so it must not surface as one.
        assert fields.get("outstanding_balance") != "42,500.00"

    def test_account_identifiers(self):
        fields = field_map(report_of(BANK_STATEMENT))
        assert fields["account_number"].replace(" ", "").endswith("234567890")
        assert fields["bank_identifier"] == "HDFC0000123"

    def test_transactions_are_rows_when_a_grid_holds_them(self):
        from app.services.table_structure import TableCell, TableStructure

        cells = [
            TableCell(0, 0, "Date", is_header=True, from_page=True),
            TableCell(0, 1, "Description", is_header=True, from_page=True),
            TableCell(0, 2, "Debit", is_header=True, from_page=True),
            TableCell(1, 0, "05/04/2024", from_page=True),
            TableCell(1, 1, "Rent", from_page=True),
            TableCell(1, 2, "15,000.00", from_page=True),
            TableCell(2, 0, "18/04/2024", from_page=True),
            TableCell(2, 1, "Salary", from_page=True),
            TableCell(2, 2, "", from_page=True),
        ]
        structure = TableStructure(
            page_number=1, row_count=3, col_count=3, cells=cells, engine="PyMuPDF"
        ).refresh_flags()
        page = ExtractedPage(
            page_number=1, text=BANK_STATEMENT, confidence=95.0, tables=[structure]
        )
        report = extract_document_report(
            DocumentExtractionResult(
                pages=[page],
                combined_text=BANK_STATEMENT,
                overall_ocr_confidence=95.0,
                is_ocr_applied=True,
            )
        )
        fields = field_map(report)
        assert fields["line_item_1"] == "Date=05/04/2024; Description=Rent; Debit=15,000.00"
        # A credit row simply has no debit cell: the column keeps its place and
        # the missing amount is absent rather than shown as zero.
        assert fields["line_item_2"] == "Date=18/04/2024; Description=Salary"
        assert fields["line_items_count"] == "2"


class TestContractsAndPolicies:
    def test_contract_parties_dates_and_clauses(self):
        fields = field_map(report_of(CONTRACT))
        assert "Alpha Corp" in fields["party_one"]
        assert "Beta Services" in fields["party_two"]
        assert fields["effective_date"] == "01/01/2024"
        assert fields["expiry_date"] == "31/12/2024"
        assert "termination_clause" in fields
        assert "renewal_terms" in fields

    def test_policy_figures_are_the_ones_a_policy_states(self):
        fields = field_map(report_of(POLICY))
        assert fields["document_number"] == "PL-2024-778199"
        assert fields["sum_assured"] == "5,00,000.00"
        assert fields["premium"] == "38,400.00"
        assert fields["policyholder"] == "Suresh Iyer"


class TestResume:
    def test_contact_and_career_labels(self):
        fields = field_map(report_of(RESUME))
        assert fields["full_name"] == "Priya Nair"
        assert fields["email"] == "priya.nair@example.com"
        assert fields["phone"] == "+91 98765 43210"
        assert fields["experience"].startswith("6 years")
        assert "Computer Science" in fields["education"]

    def test_labels_the_pipeline_has_no_rule_for_are_still_captured(self):
        # The generic label/value reader is what supports "other document types"
        # without a new rule per type.
        fields = field_map(report_of(RESUME))
        assert "Python, PostgreSQL, Docker" in fields["technical_skills"]


class TestNoFabricatedValues:
    def test_a_blank_label_stays_absent(self):
        text = INVOICE + "\nReference No:\nShipping Terms:\n"
        fields = field_map(report_of(text))
        # A label the page leaves empty yields no field row at all: not an empty
        # string that reads like a value and not a guess carried over from elsewhere.
        assert "shipping_terms" not in fields
        assert "reference_no" not in fields

    def test_a_heading_is_not_swallowed_as_the_value_below_it(self):
        # A label is one line. Reading across the break would name a field after
        # the heading above it and store the heading as part of its value.
        fields = field_map(report_of(AADHAAR))
        assert "national_identity_aadhaar_no" not in fields
        assert fields["aadhaar_number"] == "2345 6789 0123"

    def test_a_totals_mismatch_is_flagged_not_repaired(self):
        misread = INVOICE.replace("Grand Total: 1,770.00", "Grand Total: 1,710.00")
        report = report_of(misread)
        fields = field_map(report)
        assert report.requires_review is True
        assert "Arithmetic discrepancy" in report.review_reason
        # The page says 1,710.00, so 1,710.00 is what is shown.
        assert fields["total_amount"] == "1,710.00"
        assert fields["subtotal"] == "1,500.00"
