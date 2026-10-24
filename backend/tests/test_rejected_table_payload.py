"""What a refused grid looks like to the reviewer's client.

The gate keeps a page's real tables and turns a grid built on nothing into a record
with no cells, a reason and a promise that the page's own text stays. That record is
only useful if it arrives intact: an empty list of cells on its own is
indistinguishable from a table that happens to be unreadable, and the viewer would
draw a blank grid over the text the reviewer still has.

Nothing here touches the production database, uploads or indexes.
"""

from __future__ import annotations

import json
import os

from app.db.session import SessionLocal
from app.services import ocr_service
from app.services import table_quality as tq
from app.services.table_structure import TableCell, TableStructure
from tests.test_table_ground_truth import _store_structure


def grid(*, rows, cols, cells, engine="Ruled-grid", source="scanned", frame="page",
         note=None):
    structure = TableStructure(
        page_number=1,
        row_count=rows,
        col_count=cols,
        engine=engine,
        source=source,
        frame=frame,
        note=note,
    )
    structure.cells.extend(cells)
    return structure.refresh_flags()


def boxed(row, col, text, *, from_page=True, confidence=90.0):
    x, y = col * 20, row * 20
    return TableCell(
        row,
        col,
        text,
        bbox=(x, y, x + 20, y + 20),
        confidence=confidence,
        from_page=from_page,
    )


DESKEW_NOTE = "cell boxes are in the deskewed frame the text was read from"


def _headers():
    from tests.helpers import get_test_auth_headers

    db = SessionLocal()
    try:
        return get_test_auth_headers(db, "refused-tables@docsentinel.test", "ADMIN")
    finally:
        db.close()


def _tables(document_id):
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get(
        f"/api/documents/{document_id}", headers=_headers()
    )
    assert response.status_code == 200, response.text
    tables = response.json()["extracted_tables"]
    dump_to = os.environ.get("DOCSENTINEL_PAYLOAD_DUMP")
    if dump_to:
        # The viewer is checked against these exact bytes rather than a hand-written
        # copy of them: set the variable to a path to capture what the API sent.
        # One line per response, so a render check can pick the page that carries
        # both a grid and a refusal instead of whatever answered last.
        with open(dump_to, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"document_id": document_id, "tables": tables}) + "\n"
            )
    return tables


def _table_payload(document_id):
    tables = _tables(document_id)
    assert tables, "the document must carry its table records"
    return tables[0]


class TestRefusalRoundTrip:
    def test_the_refused_grid_arrives_as_a_record_not_an_empty_table(self):
        refused = grid(rows=4, cols=3, cells=[])
        screening = tq.screen([refused], page_size=(200, 200), page_lines=8)
        assert screening.accepted == []
        record = screening.kept[0]
        assert record.cells == []
        table = _table_payload(_store_structure([record]))
        assert table["cells"] == []
        assert table["requires_review"] is True
        assert table["confidence"] is None
        assert table["uncertain_cells"] == 0
        assert table["page_backed_cells"] == 0
        assert table["note"].startswith("Not shown as a table:")
        # The reason names the defect, so the reviewer knows what was measured.
        assert "cell(s)" in table["note"]

    def test_the_refusal_keeps_the_page_text_it_was_built_over(self):
        """A refused grid contributes no rows to the page, so nothing is taken back."""
        refused = grid(rows=4, cols=3, cells=[])
        record = tq.screen([refused], page_size=(200, 200), page_lines=8).kept[0]
        assert ocr_service._table_text_lines([record], 1, marker="table") == []

    def test_a_record_survives_alongside_the_grid_the_page_really_has(self):
        good = grid(
            rows=2,
            cols=2,
            cells=[boxed(0, 0, "Item"), boxed(0, 1, "Qty"), boxed(1, 0, "Rod"),
                   boxed(1, 1, "12")],
        )
        bad = grid(rows=3, cols=3, cells=[])
        screening = tq.screen([good, bad], page_size=(200, 200), page_lines=8)
        assert [structure.cells == [] for structure in screening.kept] == [False, True]
        stored = _tables(_store_structure(screening.kept))
        with_cells = [table for table in stored if table["cells"]]
        refused = [table for table in stored if not table["cells"]]
        assert len(with_cells) == 1 and len(refused) == 1
        assert refused[0]["requires_review"] is True
        assert refused[0]["note"].startswith("Not shown as a table:")
        assert with_cells[0]["requires_review"] is False
        assert with_cells[0]["page_backed_cells"] == 4


class TestCellsCarryTheirOwnEvidence:
    def test_from_page_and_the_count_travel_together(self):
        structure = grid(
            rows=2,
            cols=2,
            cells=[
                boxed(0, 0, "Subtotal", from_page=True),
                boxed(0, 1, "1,200.00", from_page=True),
                boxed(1, 0, "GST", from_page=False),
                boxed(1, 1, "", from_page=False),
            ],
        )
        assert structure.page_backed_cells == 2
        table = _table_payload(_store_structure([structure]))
        assert table["page_backed_cells"] == 2
        flags = {(c["row"], c["col"]): c["from_page"] for c in table["cells"]}
        assert flags == {
            (0, 0): True,
            (0, 1): True,
            (1, 0): False,
            (1, 1): False,
        }

    def test_a_digital_grid_reports_no_scores_and_no_claims_of_backing(self):
        """A text layer was never OCR'd: nothing here is a measured score."""
        structure = grid(
            rows=2,
            cols=2,
            engine="PyMuPDF",
            source="digital",
            cells=[
                boxed(0, 0, "Subtotal", confidence=None),
                boxed(0, 1, "1,200.00", confidence=None),
                boxed(1, 0, "GST", confidence=None),
                boxed(1, 1, "180.00", confidence=None),
            ],
        )
        table = _table_payload(_store_structure([structure]))
        assert table["confidence"] is None
        assert all(cell["confidence"] is None for cell in table["cells"])


class TestFrameTravelsWithTheGrid:
    def test_a_deskewed_grid_says_its_boxes_are_not_on_the_displayed_page(self):
        structure = grid(
            rows=2,
            cols=2,
            note=DESKEW_NOTE,
            frame="corrected",
            cells=[boxed(0, 0, "Item"), boxed(0, 1, "Qty"), boxed(1, 0, "Rod"),
                   boxed(1, 1, "12")],
        )
        table = _table_payload(_store_structure([structure]))
        assert table["frame"] == "corrected"

    def test_a_grid_read_from_the_page_as_displayed_says_so(self):
        structure = grid(
            rows=2,
            cols=2,
            cells=[boxed(0, 0, "Item"), boxed(0, 1, "Qty"), boxed(1, 0, "Rod"),
                   boxed(1, 1, "12")],
        )
        assert structure.frame == "page"
        assert _table_payload(_store_structure([structure]))["frame"] == "page"
