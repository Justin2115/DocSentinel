"""Line items come from real grid rows, and a missing score stays missing.

Three weaknesses these tests hold shut:

* a resumed run rebuilt pages from text only, so every table row degraded into an
  invented field at an invented 85%;
* a field with nothing measured behind it was averaged as if it were a read;
* a value the reader could not resolve was reported the same way as a value it read
  with doubt.

Only the last class touches the database, and the test suite rebinds it to a
throwaway SQLite file.
"""

from app.api.documents import _merged_extraction_pages
from app.db import session as db_session
from app.models.document import Document, DocumentPage, ExtractedTable, OCRResult
from app.services.extraction_service import (
    DocumentExtractionResult,
    ExtractedPage,
    extract_document_report,
    extract_table_line_items,
)
from app.services.table_structure import TableCell, TableStructure

HEADER_NOTE = "Not shown as a table: no cell in the grid could be read"


def cell(
    row: int,
    col: int,
    text: str,
    *,
    conf: float | None = None,
    header: bool = False,
    uncertain: bool = False,
    from_page: bool = True,
) -> TableCell:
    return TableCell(
        row=row,
        col=col,
        text=text,
        confidence=conf,
        is_header=header,
        uncertain=uncertain,
        from_page=from_page,
        bbox=(col * 40.0, row * 20.0, col * 40.0 + 40.0, row * 20.0 + 20.0),
    )


def grid(
    page_number: int,
    cells: list[TableCell],
    *,
    rows: int | None = None,
    cols: int | None = None,
    source: str = "digital",
    engine: str = "PyMuPDF",
) -> TableStructure:
    structure = TableStructure(
        page_number=page_number,
        row_count=(
            rows
            if rows is not None
            else max((c.row for c in cells), default=-1) + 1
        ),
        col_count=(
            cols
            if cols is not None
            else max((c.col for c in cells), default=-1) + 1
        ),
        cells=cells,
        engine=engine,
        source=source,
    )
    return structure.refresh_flags()


def refused(page_number: int, rows: int = 2, cols: int = 2) -> TableStructure:
    """The record the quality gate leaves in place of a rejected grid."""
    return TableStructure(
        page_number=page_number,
        row_count=rows,
        col_count=cols,
        cells=[],
        engine="PP-Structure",
        source="scanned",
        confidence=None,
        requires_review=True,
        note=HEADER_NOTE,
    )


def by_name(fields) -> dict[str, object]:
    return {f.field_name: f for f in fields}


class TestLineItemsFromRealRows:
    """One grid row is one labelled field, citing the page it was read from."""

    def page(self, confs: tuple[float | None, ...] | None = None) -> ExtractedPage:
        def conf(row: int, col: int) -> float | None:
            return None if confs is None else confs[row * 3 + col]

        cells = [
            cell(0, 0, "Description", header=True, conf=conf(0, 0)),
            cell(0, 1, "Qty", header=True, conf=conf(0, 1)),
            cell(0, 2, "Rate", header=True, conf=conf(0, 2)),
            cell(1, 0, "Steel Rods", conf=conf(1, 0)),
            cell(1, 1, "10", conf=conf(1, 1)),
            cell(1, 2, "100.00", conf=conf(1, 2)),
            cell(2, 0, "Fasteners", conf=conf(2, 0)),
            cell(2, 1, "50", conf=conf(2, 1)),
            cell(2, 2, "10.00", conf=conf(2, 2)),
        ]
        return ExtractedPage(
            page_number=3,
            text="TAX INVOICE",
            confidence=None,
            tables=[grid(3, cells)],
            page_type="digital",
        )

    def test_row_is_labelled_from_the_header_row(self):
        fields = by_name(extract_table_line_items([self.page()]))
        assert fields["line_item_1"].field_value == (
            "Description=Steel Rods; Qty=10; Rate=100.00"
        )
        assert fields["line_item_1"].page_number == 3

    def test_header_row_is_reported_once_and_not_counted_as_an_item(self):
        fields = by_name(extract_table_line_items([self.page()]))
        assert fields["table_1_headers"].field_value == "Description | Qty | Rate"
        assert fields["line_items_count"].field_value == "2"
        assert "line_item_3" not in fields

    def test_digital_row_claims_no_accuracy_and_raises_no_doubt(self):
        fields = by_name(extract_table_line_items([self.page()]))
        # The text layer was never scored: nothing is claimed about its accuracy,
        # and nothing doubts it either, because the page printed these characters.
        assert fields["line_item_1"].confidence is None
        assert fields["line_item_1"].uncertain is False
        assert fields["line_items_count"].confidence is None
        assert 85.0 not in [f.confidence for f in fields.values()]

    def test_scanned_row_confidence_is_its_own_cells_mean(self):
        fields = by_name(
            extract_table_line_items(
                [self.page((90.0, 80.0, 70.0, 60.0, 40.0, 80.0, 95.0, 85.0, 75.0))]
            )
        )
        assert fields["line_item_1"].confidence == 60.0
        assert fields["line_item_2"].confidence == 85.0
        assert fields["table_1_headers"].confidence == 75.0
        assert fields["line_items_count"].confidence == 72.5

    def test_unreadable_cell_flags_only_its_own_row(self):
        cells = [
            cell(0, 0, "Item", header=True, conf=90.0),
            cell(0, 1, "Qty", header=True, conf=90.0),
            cell(0, 2, "Amount", header=True, conf=90.0),
            cell(1, 0, "Rent", conf=92.0),
            cell(1, 1, "1", conf=90.0),
            cell(1, 2, "", uncertain=True),
            cell(2, 0, "Power", conf=91.0),
            cell(2, 1, "3", conf=91.0),
            cell(2, 2, "1,250.00", conf=93.0),
        ]
        page = ExtractedPage(
            page_number=1,
            text="",
            confidence=None,
            tables=[grid(1, cells, cols=3, source="scanned", engine="PP-Structure")],
            page_type="scanned",
        )
        fields = by_name(extract_table_line_items([page]))
        assert fields["line_item_1"].field_value == "Item=Rent; Qty=1"
        assert fields["line_item_1"].uncertain is True
        assert fields["line_item_2"].uncertain is False
        assert fields["line_items_count"].uncertain is True

    def test_refused_grid_yields_no_line_items_of_its_own(self):
        page = ExtractedPage(
            page_number=2,
            text=(
                "Description | Qty | Rate\n"
                "Steel Rods | 10 | 100.00\n"
                "Fasteners | 50 | 10.00"
            ),
            confidence=88.0,
            tables=[refused(2)],
            page_type="scanned",
        )
        # A refused grid stored no cells, so its columns are not evidence; the
        # page's own reading is, and it keeps the score the page was given.
        fields = by_name(extract_table_line_items([page]))
        assert fields["line_item_1"].field_value == (
            "Description=Steel Rods; Qty=10; Rate=100.00"
        )
        assert fields["line_item_1"].confidence == 88.0
        assert fields["line_items_count"].field_value == "2"

    def test_grid_with_cells_owns_the_page_so_rows_are_not_double_counted(self):
        page = ExtractedPage(
            page_number=1,
            text="Steel Rods | 10 | 100.00\nFasteners | 50 | 10.00",
            confidence=88.0,
            tables=[
                grid(
                    1,
                    [
                        cell(0, 0, "Item", header=True),
                        cell(0, 1, "Qty", header=True),
                        cell(1, 0, "Steel Rods"),
                        cell(1, 1, "10"),
                    ],
                    cols=2,
                )
            ],
        )
        fields = extract_table_line_items([page])
        assert [f.field_name for f in fields] == [
            "table_1_headers",
            "line_item_1",
            "line_items_count",
        ]

    def test_row_with_one_readable_cell_is_not_a_line_item(self):
        page = ExtractedPage(
            page_number=1,
            text="",
            confidence=90.0,
            tables=[
                grid(
                    1,
                    [
                        cell(0, 0, "Item", header=True),
                        cell(0, 1, "Qty", header=True),
                        cell(1, 0, "Steel Rods"),
                        cell(1, 1, ""),
                    ],
                    cols=2,
                )
            ],
        )
        assert [f.field_name for f in extract_table_line_items([page])] == [
            "table_1_headers"
        ]

    def test_grid_without_a_header_row_invents_no_header_field(self):
        page = ExtractedPage(
            page_number=1,
            text="",
            confidence=90.0,
            tables=[
                grid(
                    1,
                    [
                        cell(0, 0, "Steel Rods"),
                        cell(0, 1, "10"),
                        cell(1, 0, "Fasteners"),
                        cell(1, 1, "50"),
                    ],
                    cols=2,
                )
            ],
        )
        fields = by_name(extract_table_line_items([page]))
        assert "table_1_headers" not in fields
        assert fields["line_item_1"].field_value == "column_1=Steel Rods; column_2=10"


class TestProseFallback:
    """Only a run of lines agreeing on its column count counts as rows."""

    def test_two_agreeing_rows_are_rows(self):
        page = ExtractedPage(
            page_number=1,
            text="Item | Qty | Rate\nSteel | 10 | 100.00\nBolts | 50 | 10.00",
            confidence=88.0,
            page_type="digital",
        )
        fields = by_name(extract_table_line_items([page]))
        assert fields["line_item_1"].field_value == "Item=Steel; Qty=10; Rate=100.00"
        assert fields["line_items_count"].confidence == 88.0

    def test_a_sentence_containing_a_pipe_is_not_a_table(self):
        page = ExtractedPage(
            page_number=1,
            text="Terms | see the schedule overleaf\nThis line has no pipe at all.",
            confidence=88.0,
        )
        assert extract_table_line_items([page]) == []

    def test_rows_of_other_widths_are_not_padded_into_the_table(self):
        page = ExtractedPage(
            page_number=1,
            text=(
                "Item | Qty\nSteel | 10\nBolts | 50\n"
                "Carriage | 20 | 20.00 | extra\nNote | more | extra"
            ),
            confidence=88.0,
        )
        fields = by_name(extract_table_line_items([page]))
        assert fields["line_items_count"].field_value == "2"

    def test_unscored_scan_is_uncertain_but_unscored_text_layer_is_not(self):
        scan = ExtractedPage(
            page_number=1,
            text="Item | Qty\nSteel | 10\nBolts | 50",
            confidence=None,
            page_type="scanned",
        )
        fields = by_name(extract_table_line_items([scan]))
        assert fields["line_item_1"].confidence is None
        assert fields["line_item_1"].uncertain is True

        digital = ExtractedPage(
            page_number=1,
            text="Item | Qty\nSteel | 10\nBolts | 50",
            confidence=None,
            page_type="digital",
        )
        assert by_name(extract_table_line_items([digital]))["line_item_1"].uncertain is (
            False
        )

    def test_failed_page_stays_uncertain(self):
        page = ExtractedPage(
            page_number=1,
            text="Item | Qty\nSteel | 10\nBolts | 50",
            confidence=None,
            page_type="digital",
            ocr_failed=True,
        )
        fields = by_name(extract_table_line_items([page]))
        assert fields["line_item_1"].confidence is None
        assert fields["line_item_1"].uncertain is True


def report_of(page: ExtractedPage) -> object:
    return extract_document_report(
        DocumentExtractionResult(
            pages=[page],
            combined_text=page.text,
            overall_ocr_confidence=page.confidence or 0.0,
            is_ocr_applied=True,
        )
    )


class TestHonestDocumentConfidence:
    INVOICE_TEXT = (
        "TAX INVOICE\nInvoice No: INV-2024-9981\nDate: 15/05/2024\n"
        "GSTIN: 27ABCDE1234F1Z5\nPAN: ABCDE1234F\n"
        "Subtotal: $1,500.00\nTax: $270.00\nGrand Total: $1,770.00\n"
    )

    def test_unmeasured_fields_do_not_move_the_average(self):
        plain = report_of(
            ExtractedPage(page_number=1, text=self.INVOICE_TEXT, confidence=95.0)
        )
        with_grid = report_of(
            ExtractedPage(
                page_number=1,
                text=self.INVOICE_TEXT,
                confidence=95.0,
                tables=[
                    grid(
                        1,
                        [
                            cell(0, 0, "Description", header=True),
                            cell(0, 1, "Qty", header=True),
                            cell(1, 0, "Steel Rods"),
                            cell(1, 1, "10"),
                            cell(2, 0, "Fasteners"),
                            cell(2, 1, "50"),
                        ],
                        cols=2,
                    )
                ],
            )
        )
        assert [
            f.confidence for f in with_grid.fields if f.confidence is not None
        ] == [f.confidence for f in plain.fields]
        assert with_grid.overall_confidence == plain.overall_confidence
        assert len(with_grid.fields) > len(plain.fields)

    def test_null_confidence_is_not_reported_as_a_low_score(self):
        report = report_of(
            ExtractedPage(
                page_number=1,
                text=self.INVOICE_TEXT,
                confidence=95.0,
                tables=[
                    grid(
                        1,
                        [
                            cell(0, 0, "Description", header=True),
                            cell(0, 1, "Qty", header=True),
                            cell(1, 0, "Steel Rods"),
                            cell(1, 1, "10"),
                        ],
                        cols=2,
                    )
                ],
            )
        )
        assert "line_item_1" not in (report.review_reason or "")

    def test_low_measured_row_sends_the_document_to_review_by_name(self):
        report = report_of(
            ExtractedPage(
                page_number=1,
                text="TAX INVOICE\nSubtotal: $1,500.00\nTax: $270.00\nGrand Total: $1,770.00",
                confidence=95.0,
                tables=[
                    grid(
                        1,
                        [
                            cell(0, 0, "Description", header=True, conf=90.0),
                            cell(0, 1, "Qty", header=True, conf=80.0),
                            cell(1, 0, "Steel Rods", conf=31.0),
                            cell(1, 1, "10", conf=29.0),
                        ],
                        cols=2,
                        source="scanned",
                        engine="PP-Structure",
                    )
                ],
                page_type="scanned",
            )
        )
        assert report.requires_review is True
        assert "line_item_1" in report.review_reason

    def test_unreadable_cell_is_stated_as_unreadable(self):
        report = report_of(
            ExtractedPage(
                page_number=2,
                text="TAX INVOICE\nSubtotal: $1,500.00\nGrand Total: $1,500.00",
                confidence=92.0,
                tables=[
                    grid(
                        2,
                        [
                            cell(0, 0, "Item", header=True, conf=95.0),
                            cell(0, 1, "Qty", header=True, conf=95.0),
                            cell(0, 2, "Amount", header=True, conf=95.0),
                            cell(1, 0, "Steel Rods", conf=93.0),
                            cell(1, 1, "10", conf=94.0),
                            cell(1, 2, "", uncertain=True),
                        ],
                        cols=3,
                        source="scanned",
                        engine="PP-Structure",
                    )
                ],
                page_type="scanned",
            )
        )
        assert report.requires_review is True
        assert "could not be read or checked" in report.review_reason
        assert "no engine could read them" in report.review_reason
        assert "page 2" in report.review_reason


class TestResumedRunRehydration:
    """A resumed run must extract from the stored grid, not from its text."""

    def store(self, structure: TableStructure, *, page_number: int) -> int:
        db = db_session.SessionLocal()
        try:
            document = Document(
                original_filename="invoice.pdf",
                stored_filename="invoice.pdf",
                file_path="uploads/invoice.pdf",
                file_type="application/pdf",
            )
            db.add(document)
            db.flush()
            page = DocumentPage(
                document_id=document.id,
                page_number=page_number,
                width=595,
                height=842,
            )
            db.add(page)
            db.flush()
            db.add(
                OCRResult(
                    document_id=document.id,
                    page_id=page.id,
                    extracted_text="TAX INVOICE",
                    raw_text="TAX INVOICE",
                    confidence=None,
                    ocr_engine="PyMuPDF",
                )
            )
            payload = structure.to_payload()
            db.add(
                ExtractedTable(
                    document_id=document.id,
                    page_id=page.id,
                    page_number=page_number,
                    row_count=payload["row_count"],
                    col_count=payload["col_count"],
                    bbox=payload["bbox"],
                    header_rows=payload["header_rows"],
                    cells=payload["cells"],
                    engine=payload["engine"],
                    source=payload["source"],
                    confidence=payload["confidence"],
                    uncertain_cells=payload["uncertain_cells"],
                    requires_review=payload["requires_review"],
                    note=payload["note"],
                )
            )
            db.commit()
            return document.id
        finally:
            db.close()

    def rebuild(self, document_id: int):
        db = db_session.SessionLocal()
        try:
            return _merged_extraction_pages(db, document_id)
        finally:
            db.close()

    def test_stored_grid_returns_as_a_grid_not_as_text(self):
        document_id = self.store(
            grid(
                1,
                [
                    cell(0, 0, "Description", header=True),
                    cell(0, 1, "Qty", header=True),
                    cell(1, 0, "Steel Rods"),
                    cell(1, 1, "10"),
                ],
                cols=2,
            ),
            page_number=1,
        )
        result = self.rebuild(document_id)
        structure = result.pages[0].tables[0]
        assert (structure.row_count, structure.col_count) == (2, 2)
        assert structure.header_rows == [0]
        assert structure.page_backed_cells == 4
        assert structure.confidence is None
        # Never scored is not zero: a resumed run must not cap its fields at 0.
        assert result.pages[0].confidence is None
        assert result.overall_ocr_confidence == 0.0

    def test_resumed_line_items_keep_their_labels_page_and_score(self):
        document_id = self.store(
            grid(
                4,
                [
                    cell(0, 0, "Description", header=True, conf=90.0),
                    cell(0, 1, "Rate", header=True, conf=70.0),
                    cell(1, 0, "Steel Rods", conf=95.0),
                    cell(1, 1, "100.00", conf=85.0),
                ],
                cols=2,
                source="scanned",
                engine="PP-Structure",
            ),
            page_number=4,
        )
        fields = by_name(extract_table_line_items(self.rebuild(document_id).pages))
        assert fields["line_item_1"].field_value == "Description=Steel Rods; Rate=100.00"
        assert fields["line_item_1"].page_number == 4
        assert fields["line_item_1"].confidence == 90.0

    def test_resumed_refusal_stays_refused(self):
        document_id = self.store(refused(1), page_number=1)
        result = self.rebuild(document_id)
        structure = result.pages[0].tables[0]
        assert structure.cells == []
        assert structure.requires_review is True
        assert HEADER_NOTE in structure.note
        assert extract_table_line_items(result.pages) == []

    def test_resumed_uncertain_cell_stays_uncertain(self):
        document_id = self.store(
            grid(
                1,
                [
                    cell(0, 0, "Item", header=True, conf=95.0),
                    cell(0, 1, "Qty", header=True, conf=95.0),
                    cell(0, 2, "Amount", header=True, conf=95.0),
                    cell(1, 0, "Steel Rods", conf=93.0),
                    cell(1, 1, "10", conf=94.0),
                    cell(1, 2, "", uncertain=True),
                ],
                cols=3,
                source="scanned",
                engine="PP-Structure",
            ),
            page_number=1,
        )
        structure = self.rebuild(document_id).pages[0].tables[0]
        assert structure.uncertain_cells == 1
        fields = by_name(
            extract_table_line_items(
                [
                    ExtractedPage(
                        page_number=1,
                        text="",
                        confidence=94.0,
                        tables=[structure],
                        page_type="scanned",
                    )
                ]
            )
        )
        assert fields["line_item_1"].uncertain is True
        assert fields["line_item_1"].confidence == 93.5
