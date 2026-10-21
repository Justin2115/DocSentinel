"""Ground-truth contract for structured table extraction.

Every fixture here declares its grid before the reader runs, so the correct text,
row, column, span and header flag of every cell is known in advance and what the
reader returns can be compared against it cell for cell.

Digital fixtures are real PDFs, drawn rule by rule and read through PyMuPDF.
Scanned fixtures are real raster page images: the detector measures their ruling,
and the recogniser is replaced by lines of known text, position and score. That
isolation is deliberate - a test that loaded an OCR model would measure the model
rather than the geometry, and the suite must not need it. Real-engine accuracy is
measured separately and reported, never asserted here.

Nothing in this file touches the production database, uploads or indexes. The
throwaway SQLite file and temporary Chroma directory bound by conftest.py are what
the persistence tests use.
"""

from __future__ import annotations

import io
import unicodedata
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.db.session import SessionLocal
from app.models.document import Document, DocumentPage, ExtractedTable
from app.schemas.document import ExtractedTableResponse
from app.services import ocr_service
from app.services.embedding_service import chunk_text
from app.services.evidence_service import build_page_units, extract_values
from app.services.table_structure import TableCell, TableStructure
from app.services.table_structure import from_ruled_grid as real_from_ruled_grid

_PIL_SIZES: dict[int, object] = {}


def _pil_font(size: int) -> ImageFont.ImageFont:
    if size not in _PIL_SIZES:
        font = ImageFont.load_default()
        for name in ("arial.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        _PIL_SIZES[size] = font
    return _PIL_SIZES[size]


_DEVANAGARI_FONT_NAMES = (
    "Nirmala.ttc",
    "Mangal.ttf",
    "NotoSansDevanagari*.tt[fc]",
    "Lohit-Devanagari.ttf",
    "lohit-devanagari.ttf",
)


def _devanagari_font() -> str | None:
    """A font on this machine able to draw Devanagari, or None so the test skips."""
    for root in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts")):
        if not root.is_dir():
            continue
        for pattern in _DEVANAGARI_FONT_NAMES:
            for hit in sorted(root.glob(f"**/{pattern}")):
                return str(hit)
    return None


def _cell_box(row_bounds, col_bounds, row, col, colspan=1, rowspan=1):
    """The open rectangle inside the ruling for one cell's top-left slot."""
    return (
        col_bounds[col] + 8,
        row_bounds[row] + 8,
        col_bounds[min(col + colspan, len(col_bounds) - 1)],
        row_bounds[min(row + rowspan, len(row_bounds) - 1)],
    )


def _ruled(
    *,
    row_bounds: list[float],
    col_bounds: list[float],
    cells: dict[tuple[int, int], tuple[str, int, int]],
    omit_v: frozenset = frozenset(),
    omit_h: frozenset = frozenset(),
    scale: int = 1,
    score: float = 0.95,
    line: int = 2,
    font_size: int = 30,
    margins: tuple[int, int, int, int] = (140, 120, 140, 120),
) -> tuple[Image.Image, dict, list]:
    """Draw a ruled page exactly as declared and return (image, truth, ocr_lines).

    `cells` maps the top-left grid slot of every real cell to (text, colspan,
    rowspan); `omit_v`/`omit_h` name the interior dividers that are genuinely not
    drawn, which is what makes a span a span rather than a guess. The returned
    `ocr_lines` stand in for a recogniser that read each declared string inside its
    own cell box, so these tests measure the geometry rather than a model.
    """
    rows = [value * scale for value in row_bounds]
    cols = [value * scale for value in col_bounds]
    width = int(cols[-1] + margins[2] * scale)
    height = int(rows[-1] + margins[3] * scale)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    stroke = max(1, int(line * scale))

    h_gaps = [(cols[c], cols[c + 1]) for c in range(len(cols) - 1)]
    v_gaps = [(rows[r], rows[r + 1]) for r in range(len(rows) - 1)]
    for index, y in enumerate(rows):
        for col, span in enumerate(h_gaps):
            if (index, col) in omit_h:
                continue
            draw.line((span[0], y, span[1], y), fill="black", width=stroke)
    for index, x in enumerate(cols):
        for row, span in enumerate(v_gaps):
            if (row, index) in omit_v:
                continue
            draw.line((x, span[0], x, span[1]), fill="black", width=stroke)

    font = _pil_font(max(8, int(font_size * scale)))
    truth: dict[tuple[int, int], tuple[str, int, int]] = {}
    lines: list = []
    for (row, col), spec in cells.items():
        text, colspan, rowspan = spec
        truth[(row, col)] = spec
        if not text.strip():
            continue
        box = _cell_box(rows, cols, row, col, colspan, rowspan)
        pad = 10 * scale
        draw.text((box[0] + pad, box[1] + pad), text, font=font, fill="black")
        x1, y1, x2, y2 = (float(v) for v in draw.textbbox((box[0] + pad, box[1] + pad), text, font=font))
        lines.append(
            [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]], [text, score]]
        )
    return image, truth, lines


def _set_line_text(lines: list, text: str, replacement: str) -> None:
    """Rewrite what the stand-in recogniser reported for one printed string."""
    for line in lines:
        if line[1][0] == text:
            line[1][0] = replacement
            return
    raise AssertionError(f"fixture never printed {text!r}")


def _join_lines(lines: list, first: str, second: str) -> None:
    """Report two printed strings as one detection box covering both.

    A detector sometimes reads a row's description and the number beside it as a
    single line, and then its quad spans the divider between two cells. Nothing on
    the page changes; only what the recogniser says it saw does.
    """
    found = [
        index
        for index, line in enumerate(lines)
        if line[1][0] in (first, second)
    ]
    if len(found) != 2:
        raise AssertionError(f"fixture never printed both {first!r} and {second!r}")
    points = [
        [float(x), float(y)] for index in found for x, y in lines[index][0]
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    scores = [lines[index][1][1] for index in found]
    box = [
        [min(xs), min(ys)],
        [max(xs), min(ys)],
        [max(xs), max(ys)],
        [min(xs), max(ys)],
    ]
    kept = found[0]
    lines[kept] = [box, [f"{first} {second}", min(scores)]]
    lines.pop(found[1])




PLAIN4 = {
    "row_bounds": [150.0, 240.0, 330.0, 420.0, 510.0],
    "col_bounds": [120.0, 400.0, 700.0, 1000.0, 1220.0],
    "cells": {
        (0, 0): ("Item", 1, 1),
        (0, 1): ("Qty", 1, 1),
        (0, 2): ("Rate", 1, 1),
        (0, 3): ("Amount", 1, 1),
        (1, 0): ("Cement bags", 1, 1),
        (1, 1): ("40", 1, 1),
        (1, 2): ("380.00", 1, 1),
        (1, 3): ("15,200.00", 1, 1),
        (2, 0): ("Sand", 1, 1),
        (2, 1): ("12", 1, 1),
        (2, 2): ("1,100.00", 1, 1),
        (2, 3): ("13,200.00", 1, 1),
        (3, 0): ("Transport", 1, 1),
        (3, 1): ("2", 1, 1),
        (3, 2): ("900.00", 1, 1),
        (3, 3): ("1,800.00", 1, 1),
    },
}

COLSPAN = {
    "row_bounds": [150.0, 240.0, 330.0, 420.0, 510.0],
    "col_bounds": [120.0, 400.0, 700.0, 1000.0, 1220.0],
    "cells": {
        (0, 0): ("Item", 1, 1),
        (0, 1): ("Qty", 1, 1),
        (0, 2): ("Rate", 1, 1),
        (0, 3): ("Amount", 1, 1),
        (1, 0): ("Cement", 1, 1),
        (1, 1): ("40", 1, 1),
        (1, 2): ("380.00", 1, 1),
        (1, 3): ("15,200.00", 1, 1),
        (2, 0): ("Sand", 1, 1),
        (2, 1): ("12", 1, 1),
        (2, 2): ("1,100.00", 1, 1),
        (2, 3): ("13,200.00", 1, 1),
        (3, 0): ("Grand total", 3, 1),
        (3, 3): ("30,200.00", 1, 1),
    },
    "omit_v": frozenset({(3, 1), (3, 2)}),
}

ROWSPAN = {
    "row_bounds": [150.0, 240.0, 330.0, 420.0, 510.0],
    "col_bounds": [120.0, 400.0, 700.0, 1000.0, 1220.0],
    "cells": {
        (0, 0): ("Cement", 1, 2),
        (0, 1): ("40 kg bags", 1, 1),
        (0, 2): ("380.00", 1, 1),
        (0, 3): ("15,200.00", 1, 1),
        (1, 1): ("12", 1, 1),
        (1, 2): ("1,100.00", 1, 1),
        (1, 3): ("1,200.00", 1, 1),
        (2, 0): ("Sand", 1, 1),
        (2, 1): ("800.00", 1, 1),
        (3, 0): ("Freight", 1, 1),
        (3, 1): ("350.00", 1, 1),
    },
    # The rule between row 0 and row 1 is not drawn in column 0: that is what makes
    # "Cement" one cell two rows tall rather than two cells. Every other divider is
    # drawn, so the grid is the four columns the ruling shows.
    "omit_h": frozenset({(1, 0)}),
}

BLANK = {
    "row_bounds": [150.0, 240.0, 330.0, 420.0],
    "col_bounds": [120.0, 480.0, 840.0, 1120.0],
    "cells": {
        (0, 0): ("Description", 1, 1),
        (0, 1): ("Amount", 1, 1),
        (0, 2): ("Remarks", 1, 1),
        (1, 0): ("Labour", 1, 1),
        (1, 1): ("4,500.00", 1, 1),
        (1, 2): ("Approved", 1, 1),
        (2, 0): ("Material", 1, 1),
        # (2, 1) and (2, 2) print nothing at all: an empty cell, not an unreadable one.
    },
}

# One ruling, two tables: rows 0-3 sit in the top band and rows 5-7 below the gap,
# so row 4 - the 490px white space between the two - belongs to neither table.
TWO_TABLES = {
    "row_bounds": [150.0, 240.0, 330.0, 420.0, 510.0, 1000.0, 1090.0, 1180.0, 1270.0],
    "col_bounds": [120.0, 400.0, 700.0, 1000.0, 1220.0],
    "cells": {
        (0, 0): ("Header A", 1, 1),
        (0, 1): ("B", 1, 1),
        (0, 2): ("C", 1, 1),
        (0, 3): ("D", 1, 1),
        (1, 0): ("1", 1, 1),
        (1, 1): ("2", 1, 1),
        (1, 2): ("3", 1, 1),
        (1, 3): ("4", 1, 1),
        (2, 0): ("5", 1, 1),
        (2, 1): ("6", 1, 1),
        (2, 2): ("7", 1, 1),
        (2, 3): ("8", 1, 1),
        (3, 0): ("9", 1, 1),
        (3, 1): ("10", 1, 1),
        (3, 2): ("11", 1, 1),
        (3, 3): ("12", 1, 1),
        (5, 0): ("Footer A", 1, 1),
        (5, 1): ("B", 1, 1),
        (5, 2): ("C", 1, 1),
        (5, 3): ("D", 1, 1),
        (6, 0): ("Tax", 1, 1),
        (6, 1): ("2,500.00", 1, 1),
        (6, 2): ("GST", 1, 1),
        (6, 3): ("900.00", 1, 1),
        (7, 0): ("Net", 1, 1),
        (7, 1): ("27,500.00", 1, 1),
        (7, 2): ("Paid", 1, 1),
        (7, 3): ("10,000.00", 1, 1),
    },
}


def _grid_of(structure) -> dict:
    """(row, col) -> (text, colspan, rowspan) for every cell the reader returned."""
    return {
        (cell.row, cell.col): (cell.text, cell.colspan, cell.rowspan)
        for cell in structure.cells
    }


def _spans(region) -> dict:
    return {
        (cell["row"], cell["col"]): (cell["colspan"], cell["rowspan"])
        for cell in region["cells"]
    }


def _truth_spans(cells: dict) -> dict:
    return {key: (spec[1], spec[2]) for key, spec in cells.items()}


def _declared_spans(spans: dict, cells: dict) -> dict:
    """The spans of the slots the fixture declared.

    Slots the page prints nothing in still get a cell of their own - an empty cell
    is a fact about the page, not a gap to be flagged - so the comparison is made
    over the declared positions and the rest are checked to be empty separately.
    """
    missing = [key for key in cells if key not in spans]
    assert not missing, f"the reader lost cells {missing}"
    return {key: spans[key] for key in cells}


def _declared_cells(grid: dict, truth: dict) -> dict:
    """The cells the fixture printed, plus a check that nothing else has text."""
    missing = [key for key in truth if key not in grid]
    assert not missing, f"the reader lost cells {missing}"
    extra = {
        key: value
        for key, value in grid.items()
        if key not in truth and value[0].strip()
    }
    assert not extra, f"text appeared in cells the page never printed: {extra}"
    return {key: grid[key] for key in truth}


# --------------------------------------------------------------------------
# the measured detector
# --------------------------------------------------------------------------


class TestRuledGridDetector:
    def test_plain_grid_matches_the_drawn_truth(self):
        image, _truth, _lines = _ruled(**PLAIN4)
        regions = ocr_service._ruling_regions(image)
        assert len(regions) == 1
        region = regions[0]
        assert len(region["row_bounds"]) == 5
        assert len(region["col_bounds"]) == 5
        assert region["bbox"] == pytest.approx((120.0, 150.0, 1220.0, 510.0), abs=2.0)
        assert region["header_rows"] == [0]
        # Nothing is missing from this ruling, so no cell spans anything.
        assert len(region["cells"]) == 16
        assert all(
            cell["colspan"] == 1 and cell["rowspan"] == 1 for cell in region["cells"]
        )

    def test_missing_interior_divider_becomes_a_colspan(self):
        image, truth, _lines = _ruled(**COLSPAN)
        region = ocr_service._ruling_regions(image)[0]
        assert len(region["cells"]) == 14  # 16 slots, two swallowed by the total
        assert _spans(region)[(3, 0)] == (3, 1)
        assert _spans(region)[(3, 3)] == (1, 1)
        assert _declared_spans(_spans(region), truth) == _truth_spans(truth)

    def test_missing_horizontal_divider_becomes_a_rowspan(self):
        image, truth, _lines = _ruled(**ROWSPAN)
        region = ocr_service._ruling_regions(image)[0]
        spans = _spans(region)
        assert spans[(0, 0)] == (1, 2)
        assert (1, 0) not in spans  # the slot the tall cell swallows has no box
        assert _declared_spans(spans, truth) == _truth_spans(truth)

    def test_a_divider_that_spans_part_of_the_height_is_not_a_column(self):
        """A rule drawn in only some rows is a merge, not a grid line.

        Dropping the divider between columns 1 and 2 from the top two rows of a
        four-row table leaves a stroke that covers half the height: the page does
        not have a column boundary there, so the grid is three columns wide.
        """
        spec = {
            **ROWSPAN,
            "omit_v": frozenset({(0, 2), (1, 2)}),
            "cells": {(0, 0): ("Cement", 1, 1), (0, 1): ("40 kg bags", 2, 1)},
        }
        image, _truth, _lines = _ruled(**spec)
        region = ocr_service._ruling_regions(image)[0]
        assert len(region["col_bounds"]) == 4
        assert region["col_bounds"] == pytest.approx([120.0, 400.0, 1000.0, 1220.0], abs=2.0)

    @pytest.mark.parametrize("scale", [1, 2])
    def test_resolution_changes_the_numbers_not_the_grid(self, scale):
        image, truth, _lines = _ruled(**ROWSPAN, scale=scale)
        region = ocr_service._ruling_regions(image)[0]
        assert _declared_spans(_spans(region), truth) == _truth_spans(truth)
        assert len(region["row_bounds"]) == 5
        assert len(region["col_bounds"]) == 5

    def test_two_tables_on_one_page_stay_two_regions(self):
        image, _truth, _lines = _ruled(**TWO_TABLES)
        regions = ocr_service._ruling_regions(image)
        assert len(regions) == 2
        assert len(regions[0]["row_bounds"]) == 5  # first table: 4 rows of its own
        assert len(regions[1]["row_bounds"]) == 4  # second: 3 rows, no borrowed lines
        assert regions[1]["bbox"][1] > regions[0]["bbox"][3]

    def test_borderless_page_has_no_ruling(self):
        image = Image.new("RGB", (1200, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text(
            (140, 120), "Invoice 4471 - due on receipt", font=_pil_font(30), fill="black"
        )
        assert ocr_service._ruling_regions(image) == []


# --------------------------------------------------------------------------
# cells: text, position, confidence
# --------------------------------------------------------------------------


def _no_engine(monkeypatch):
    monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: None)


def _scan(image, lines, *, devanagari=False, page_number=1):
    return ocr_service._extract_scanned_tables(
        image,
        devanagari_ocr=devanagari,
        ocr_lines=lines,
        page_number=page_number,
    )[0]


class TestScannedCells:
    def test_text_stays_in_the_row_and_column_it_was_printed_in(self, monkeypatch):
        _no_engine(monkeypatch)
        image, truth, lines = _ruled(**PLAIN4)
        structures = _scan(image, lines)
        assert len(structures) == 1
        structure = structures[0]
        assert structure.engine == "Ruled-grid"
        assert structure.source == "scanned"
        assert (structure.row_count, structure.col_count) == (4, 4)
        assert _declared_cells(_grid_of(structure), truth) == truth

    def test_merged_cells_keep_their_text_and_their_row(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**COLSPAN)
        structure = _scan(image, lines)[0]
        assert structure.row_texts()[-1] == "Grand total |  |  | 30,200.00"
        total = next(cell for cell in structure.cells if cell.text == "Grand total")
        assert (total.row, total.col, total.colspan) == (3, 0, 3)

    def test_rowspan_text_appears_once_at_its_own_slot(self, monkeypatch):
        _no_engine(monkeypatch)
        image, truth, lines = _ruled(**ROWSPAN)
        structure = _scan(image, lines)[0]
        assert _declared_cells(_grid_of(structure), truth) == truth
        rows = structure.row_texts()
        assert rows[0] == "Cement | 40 kg bags | 380.00 | 15,200.00"
        assert rows[1] == " | 12 | 1,100.00 | 1,200.00"
        assert sum(cell.text == "Cement" for cell in structure.cells) == 1
        cement = next(cell for cell in structure.cells if cell.text == "Cement")
        assert (cement.row, cement.col, cement.rowspan) == (0, 0, 2)

    def test_cell_confidence_is_the_score_that_produced_it(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4, score=0.83)
        structure = _scan(image, lines)[0]
        scored = [cell for cell in structure.cells if cell.confidence is not None]
        assert len(scored) == 16
        assert all(cell.confidence == 83.0 for cell in scored)
        assert structure.confidence == 83.0
        assert structure.requires_review is False

    def test_a_low_score_is_neither_raised_nor_lost(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4)
        for line in lines:
            if line[1][0] == "Cement bags":
                line[1][1] = 0.31
        structure = _scan(image, lines)[0]
        unsure = next(cell for cell in structure.cells if cell.text == "Cement bags")
        assert unsure.confidence == 31.0
        assert structure.confidence < 95.0

    def test_top_band_is_the_header_row(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4)
        structure = _scan(image, lines)[0]
        assert structure.header_rows == [0]
        assert all(cell.is_header for cell in structure.cells if cell.row == 0)
        assert all(not cell.is_header for cell in structure.cells if cell.row > 0)
        # Uniform ruling cannot prove which band is a header, so it is declared.
        assert "inferred" in structure.note

    def test_every_cell_box_is_inside_the_page(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**ROWSPAN)
        structure = _scan(image, lines)[0]
        width, height = image.size
        assert structure.cells
        for cell in structure.cells:
            assert cell.bbox is not None
            x1, y1, x2, y2 = cell.bbox
            assert -1.0 <= x1 < x2 <= width + 1.0
            assert -1.0 <= y1 < y2 <= height + 1.0
        assert structure.bbox == pytest.approx((120.0, 150.0, 1220.0, 510.0), abs=2.0)

    def test_blank_cell_stays_blank_and_is_not_flagged(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**BLANK)
        structure = _scan(image, lines)[0]
        empty = {(cell.row, cell.col): cell for cell in structure.cells}
        assert empty[(2, 1)].text == ""
        assert empty[(2, 1)].uncertain is False
        assert empty[(2, 2)].text == ""
        assert empty[(2, 2)].uncertain is False
        assert structure.uncertain_cells == 0
        assert structure.requires_review is False
        assert structure.row_texts()[-1] == "Material |  | "

    def test_a_line_outside_the_ruling_is_not_guessed_into_the_nearest_cell(
        self, monkeypatch
    ):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4)
        lines.append(
            [
                [[20.0, 20.0], [110.0, 20.0], [110.0, 50.0], [20.0, 50.0]],
                ["PAID", 0.99],
            ]
        )
        structure = _scan(image, lines)[0]
        joined = " ".join(cell.text for cell in structure.cells)
        assert "PAID" not in joined
        assert structure.uncertain_cells == 0

    def test_two_tables_on_a_page_do_not_share_rows(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**TWO_TABLES)
        structures = _scan(image, lines, page_number=7)
        assert len(structures) == 2
        assert all(s.page_number == 7 for s in structures)
        first, second = structures
        assert first.row_texts()[0].startswith("Header A")
        assert "Tax" not in " ".join(first.row_texts())
        assert second.row_texts()[0].startswith("Footer A")
        assert "Tax" in " ".join(second.row_texts())
        assert "Header A" not in " ".join(second.row_texts())
        assert set(first.row_texts()).isdisjoint(second.row_texts())

    def test_a_grid_with_nothing_in_it_is_not_stored_as_a_table(self, monkeypatch):
        """No recognised line means no cell text, and an empty grid is not content."""
        _no_engine(monkeypatch)
        image, _truth, _lines = _ruled(**COLSPAN)
        structures, reported = ocr_service._extract_scanned_tables(
            image, devanagari_ocr=False, ocr_lines=None, page_number=2
        )
        assert structures == []
        assert reported == 0


# --------------------------------------------------------------------------
# a cell that is mostly digits is the one place a wrong reading is dangerous
# --------------------------------------------------------------------------


def _row_of(texts: list[str]) -> TableStructure:
    """A scanned one-row table whose cells say exactly what is listed."""
    structure = TableStructure(
        page_number=1, row_count=1, col_count=len(texts), source="scanned"
    )
    for col, text in enumerate(texts):
        structure.cells.append(TableCell(row=0, col=col, text=text))
    return structure


class TestAMisreadAmountIsFlagged:
    """A damaged digit run is marked for review, never repaired or blanked."""

    @pytest.mark.parametrize(
        "text",
        [
            "15,200.00",
            "1,800",
            "40",
            "₹1,250.00",
            "Rs. 2,500",
            "10 kg",
            "3/4",
            "(500.00)",
            "50%",
            "1-2",
            "१२३४",
        ],
    )
    def test_a_number_a_page_really_says_is_left_alone(self, text):
        structure = _row_of([text])
        assert structure.flag_misread_amounts() == 0
        assert structure.cells[0].uncertain is False
        assert structure.note is None

    @pytest.mark.parametrize(
        "text",
        [
            "15,2O0.00",  # O read for 0
            "2,500. .O०",  # a zero turned into two different glyphs
            "1,l00.00",  # lower-case L read for 1
            "1,8O0",
        ],
    )
    def test_a_digit_run_that_is_not_a_number_is_flagged(self, text):
        structure = _row_of([text])
        assert structure.flag_misread_amounts() == 1
        cell = structure.cells[0]
        # Nothing is corrected, nothing is invented: the reading survives as-is
        # and the reviewer is the one told it cannot be trusted.
        assert cell.text == text
        assert cell.uncertain is True
        assert structure.requires_review is True
        assert "could not resolve into a number" in structure.note

    def test_a_number_is_never_invented_when_the_cell_is_flagged(self):
        structure = _row_of(["15,2O0.00"])
        structure.flag_misread_amounts()
        assert "15,200.00" not in structure.to_html()
        assert structure.row_texts() == ["15,2O0.00"]

    def test_a_number_beside_prose_stays_prose(self):
        structure = _row_of(["Clause 3 of 5 boxes signed"])
        assert structure.flag_misread_amounts() == 0

    def test_a_digital_text_layer_is_never_queried_this_way(self):
        """The characters a PDF carries were not read from pixels, so nothing
        about them is a misread; only the scanned builders can flag."""
        structure = _row_of(["15,2O0.0०"])
        structure.source = "digital"
        assert structure.flag_misread_amounts() == 0
        assert structure.cells[0].uncertain is False

    def test_a_damaged_amount_is_flagged_on_a_real_scanned_grid(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4)
        _set_line_text(lines, "15,200.00", "15,2O0.0०")
        structure = _scan(image, lines)[0]
        damaged = next(cell for cell in structure.cells if cell.text == "15,2O0.0०")
        assert damaged.uncertain is True
        assert structure.requires_review is True
        assert "could not resolve into a number" in structure.note
        # the undamaged cells of the same grid are not dragged into review
        assert next(
            cell for cell in structure.cells if cell.text == "13,200.00"
        ).uncertain is False

    def test_the_flagged_amount_stays_searchable_in_its_own_row(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4)
        _set_line_text(lines, "15,200.00", "15,2O0.0०")
        structure = _scan(image, lines)[0]
        row = next(line for line in structure.row_texts() if "Cement bags" in line)
        assert row == "Cement bags | 40 | 380.00 | 15,2O0.0०"
        assert "15,200.00" not in " ".join(structure.row_texts())


# --------------------------------------------------------------------------
# one copy of each row: the page keeps its prose, the structure keeps the table
# --------------------------------------------------------------------------


def _scanned_pdf(path: Path, image: Image.Image) -> Path:
    """Wrap a raster page in a PDF at 0.36 points per pixel.

    The scanned path rasterizes a page at 200 dpi, which is 200/72 points per
    pixel, so the render lands back on this fixture's own pixel grid and the
    stand-in recogniser can report boxes that belong to it.
    """
    doc = fitz.open()
    page = doc.new_page(width=image.width * 0.36, height=image.height * 0.36)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(path))
    doc.close()
    return path


def _stub_recogniser(monkeypatch, frame, lines, *, detected="en", confidence=92.0):
    """Replace the recogniser with one that reports `lines` on the rendered page.

    `frame` is the size the fixture was drawn at; the boxes are scaled into the
    image the pipeline actually hands over, the same way a real engine's
    coordinates belong to the image it was given. The text it returns is the
    geometric reconstruction of every line, which is what a scanned page has
    always been indexed with.
    """

    def fake_ocr(image, language="auto"):
        factor = image.width / float(frame[0])
        scaled = [
            [
                [[float(x) * factor, float(y) * factor] for x, y in line[0]],
                [line[1][0], line[1][1]],
            ]
            for line in lines
        ]
        text, _rows, sorted_lines = ocr_service.reconstruct_reading_order_and_tables(
            scaled
        )
        return text, confidence, "FakeOCR", detected, sorted_lines

    monkeypatch.setattr(ocr_service, "_perform_image_ocr", fake_ocr)


def _process_scan(tmp_path, monkeypatch, image, lines, *, detected="en"):
    """Run the real page pipeline over a raster with a stand-in recogniser."""
    _no_engine(monkeypatch)
    for name in (
        "_rapid_ocr_engine",
        "_paddle_devanagari_engine",
        "_paddle_english_engine",
    ):
        monkeypatch.setattr(ocr_service, name, False)
    pdf = _scanned_pdf(tmp_path / "scan.pdf", image)
    _stub_recogniser(monkeypatch, image.size, lines, detected=detected)
    return ocr_service.process_pdf_document(pdf, language=detected).pages[0]


class TestOneCopyOfEachRowOnAScannedPage:
    def _page(self, tmp_path, monkeypatch, image, lines, *, detected="en"):
        return _process_scan(tmp_path, monkeypatch, image, lines, detected=detected)

    def test_a_measured_row_is_indexed_once(self, tmp_path, monkeypatch):
        image, _truth, lines = _ruled(**PLAIN4)
        page = self._page(tmp_path, monkeypatch, image, lines)
        assert page.page_type == "scanned"
        assert page.table_count == 1
        assert "[scanned-table p1]" in page.text
        assert "Cement bags | 40 | 380.00 | 15,200.00" in page.text
        for phrase in ("Cement bags", "15,200.00", "Item | Qty | Rate | Amount"):
            assert page.text.count(phrase) == 1

    def test_the_untouched_ocr_text_is_still_the_pages_own(
        self, tmp_path, monkeypatch
    ):
        image, truth, lines = _ruled(**PLAIN4)
        page = self._page(tmp_path, monkeypatch, image, lines)
        assert all(spec[0] in page.raw_text for spec in truth.values())
        assert page.raw_text != page.text

    def test_prose_outside_the_ruling_keeps_its_own_line(
        self, tmp_path, monkeypatch
    ):
        image, _truth, lines = _ruled(**PLAIN4)
        title = "Sanctioned estimate"
        ImageDraw.Draw(image).text((140, 40), title, font=_pil_font(30), fill="black")
        lines.append(
            [
                [[140.0, 40.0], [420.0, 40.0], [420.0, 70.0], [140.0, 70.0]],
                [title, 0.99],
            ]
        )
        page = self._page(tmp_path, monkeypatch, image, lines)
        assert page.text.count(title) == 1
        prose, tables = page.text.split("[scanned-table p1]", 1)
        assert title in prose
        assert "Cement bags" not in prose
        assert "Cement bags | 40 | 380.00 | 15,200.00" in tables
        assert title not in " ".join(
            cell.text for structure in page.tables for cell in structure.cells
        )

    def test_a_page_without_ruling_keeps_every_line_it_read(
        self, tmp_path, monkeypatch
    ):
        image = Image.new("RGB", (1200, 300), "white")
        draw = ImageDraw.Draw(image)
        texts = ["Invoice 4471", "due on receipt", "no table here"]
        lines = []
        for position, text in enumerate(texts):
            origin = (140, 60 + 70 * position)
            font = _pil_font(30)
            draw.text(origin, text, font=font, fill="black")
            box = draw.textbbox(origin, text, font=font)
            lines.append(
                [
                    [
                        [float(box[0]), float(box[1])],
                        [float(box[2]), float(box[1])],
                        [float(box[2]), float(box[3])],
                        [float(box[0]), float(box[3])],
                    ],
                    [text, 0.95],
                ]
            )
        page = self._page(tmp_path, monkeypatch, image, lines)
        assert page.table_count == 0
        assert page.tables == []
        for text in texts:
            assert page.text.count(text) == 1
            assert page.raw_text.count(text) == 1


# --------------------------------------------------------------------------
# one recognised line that spans two cells belongs to neither
# --------------------------------------------------------------------------


class TestALineThatCrossesADivider:
    def test_neither_cell_keeps_a_line_that_spans_both(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4)
        _join_lines(lines, "Cement bags", "40")
        structure = _scan(image, lines)[0]
        cells = {(cell.row, cell.col): cell for cell in structure.cells}
        assert cells[(1, 0)].text == ""
        assert cells[(1, 1)].text == ""
        assert cells[(1, 0)].uncertain is True
        assert cells[(1, 1)].uncertain is True
        assert "Cement bags 40" not in " ".join(cell.text for cell in structure.cells)
        assert structure.uncertain_cells == 2
        assert structure.requires_review is True
        assert "crosses a cell boundary" in structure.note
        # the cells the page could place are untouched by the one it could not
        assert structure.row_texts()[0] == "Item | Qty | Rate | Amount"
        assert structure.row_texts()[2] == "Sand | 12 | 1,100.00 | 13,200.00"
        assert structure.row_texts()[1] == " |  | 380.00 | 15,200.00"

    def test_a_loose_box_that_reaches_only_one_cell_still_counts(
        self, monkeypatch
    ):
        """A quad that spills past the margin into no second cell loses nothing."""
        _no_engine(monkeypatch)
        image, truth, lines = _ruled(**PLAIN4)
        for line in lines:
            if line[1][0] == "Cement bags":
                xs = [point[0] for point in line[0]]
                ys = [point[1] for point in line[0]]
                line[0] = [
                    [min(xs) - 30.0, min(ys) - 3.0],
                    [max(xs) + 3.0, min(ys) - 3.0],
                    [max(xs) + 3.0, max(ys) + 3.0],
                    [min(xs) - 30.0, max(ys) + 3.0],
                ]
        structure = _scan(image, lines)[0]
        assert _declared_cells(_grid_of(structure), truth) == truth
        assert structure.uncertain_cells == 0
        assert structure.requires_review is False

    def test_the_line_stays_in_the_text_of_the_page_that_read_it(
        self, tmp_path, monkeypatch
    ):
        image, _truth, lines = _ruled(**PLAIN4)
        _join_lines(lines, "Cement bags", "40")
        page = _process_scan(tmp_path, monkeypatch, image, lines)
        assert page.table_count == 1
        prose, table = page.text.split("[scanned-table p1]", 1)
        assert "Cement bags 40" in prose
        assert "| | 380.00 | 15,200.00" in table
        # the blank cells keep their column positions: one separator per boundary
        blank_row = next(row for row in table.splitlines() if "380.00" in row)
        assert blank_row.count("|") == 3
        assert page.text.count("Cement bags") == 1
        assert page.tables[0].requires_review is True


# --------------------------------------------------------------------------
# the table model, consulted only where it agrees with the measured grid
# --------------------------------------------------------------------------


class _StubEngine:
    """A PP-Structure-shaped stand-in whose HTML is what a model reported."""

    def __init__(self, tables):
        self.tables = tables
        self.calls = 0

    def __call__(self, _image):
        self.calls += 1
        return self.tables


def _model(html):
    return {"type": "table", "res": {"html": html}}


def _html(rows):
    out = ["<table>"]
    for row in rows:
        out.append("<tr>")
        for text, colspan, rowspan in row:
            out.append(f"<td colspan='{colspan}' rowspan='{rowspan}'>{text}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


class TestModelHints:
    def test_a_disagreeing_number_never_replaces_the_pages_own_amount(
        self, monkeypatch
    ):
        """Cross-reader consensus is not consensus when the two read differently.

        Here the page printed the amount as "15,2O0.00" and the table model's slot
        says "15,200.00". The model's grid can be wrong about which cell a number
        belongs to - measured on a Marathi scan it reported the row's amount for
        its rate cell - so overwriting the page with it would silently change an
        amount. The page keeps its own string and the cell asks for review.
        """
        image, _truth, lines = _ruled(**PLAIN4)
        _set_line_text(lines, "15,200.00", "15,2O0.00")
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("Item", 1, 1), ("Qty", 1, 1), ("Rate", 1, 1), ("Amount", 1, 1)],
                            [("Cement bags", 1, 1), ("40", 1, 1), ("380.00", 1, 1), ("15,200.00", 1, 1)],
                            [("Sand", 1, 1), ("12", 1, 1), ("1,100.00", 1, 1), ("13,200.00", 1, 1)],
                            [("Transport", 1, 1), ("2", 1, 1), ("900.00", 1, 1), ("1,800.00", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        assert engine.calls == 0
        structure = _scan(image, lines)[0]
        assert engine.calls == 1
        amount = next(c for c in structure.cells if (c.row, c.col) == (1, 3))
        assert amount.text == "15,2O0.00"
        assert amount.uncertain is True
        # The rest of the row, and the rest of the page, are unaffected.
        assert [
            cell.text for cell in structure.cells if cell.row == 1
        ] == ["Cement bags", "40", "380.00", "15,2O0.00"]
        assert structure.uncertain_cells == 1
        assert structure.requires_review is True
        assert "do not agree" in structure.note

    def test_the_pages_own_number_is_searchable_after_the_disagreement(
        self, monkeypatch
    ):
        """Flagging a cell must not remove it from the text of its page."""
        image, _truth, lines = _ruled(**PLAIN4)
        _set_line_text(lines, "15,200.00", "15,2O0.00")
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("Item", 1, 1), ("Qty", 1, 1), ("Rate", 1, 1), ("Amount", 1, 1)],
                            [("Cement bags", 1, 1), ("40", 1, 1), ("380.00", 1, 1), ("15,200.00", 1, 1)],
                            [("Sand", 1, 1), ("12", 1, 1), ("1,100.00", 1, 1), ("13,200.00", 1, 1)],
                            [("Transport", 1, 1), ("2", 1, 1), ("900.00", 1, 1), ("1,800.00", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        structure = _scan(image, lines)[0]
        row = structure.row_texts()[1]
        assert "15,2O0.00" in row
        assert "15,200.00" not in row

    def test_an_agreeing_grid_lends_the_model_its_cleaner_printing(
        self, monkeypatch
    ):
        """Digits both readers saw the same way are the same number, better typed."""
        image, truth, lines = _ruled(**PLAIN4)
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("Item", 1, 1), ("Qty", 1, 1), ("Rate", 1, 1), ("Amount", 1, 1)],
                            [("Cement bags", 1, 1), ("40", 1, 1), ("380.00", 1, 1), ("15,200.00", 1, 1)],
                            [("Sand", 1, 1), ("12", 1, 1), ("1,100.00", 1, 1), ("13,200.00", 1, 1)],
                            [("Transport", 1, 1), ("2", 1, 1), ("900.00", 1, 1), ("1,800.00", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        structure = _scan(image, lines)[0]
        assert _declared_cells(_grid_of(structure), truth) == truth
        assert structure.uncertain_cells == 0
        assert structure.requires_review is False

    def test_a_grid_of_another_shape_is_not_mapped_onto_the_cells(self, monkeypatch):
        image, truth, lines = _ruled(**PLAIN4)
        engine = _StubEngine([_model(_html([[("PHANTOM", 1, 1)]]))])
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        structure = _scan(image, lines)[0]
        assert "PHANTOM" not in " ".join(cell.text for cell in structure.cells)
        assert _declared_cells(_grid_of(structure), truth) == truth

    def test_latin_lookalikes_on_a_devanagari_page_are_stored_unreadable(
        self, monkeypatch
    ):
        cells = {
            (0, 0): ("उपभोक्ता", 1, 1),
            (0, 1): ("क्रमांक", 1, 1),
            (0, 2): ("", 1, 1),
            (1, 0): ("रस्त्यावर", 1, 1),
            (1, 1): ("12", 1, 1),
            (1, 2): ("", 1, 1),
        }
        image, _truth, lines = _ruled(
            row_bounds=PLAIN4["row_bounds"][:3],
            col_bounds=PLAIN4["col_bounds"][:4],
            cells=cells,
            font_size=24,
        )
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("M", 1, 1), ("B M I B", 1, 1), ("X Y", 1, 1)],
                            [("S T", 1, 1), ("12", 1, 1), ("450", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        structure = _scan(image, lines, devanagari=True)[0]
        got = {(cell.row, cell.col): cell for cell in structure.cells}
        # What the page itself read survives, in its own cell.
        assert got[(0, 0)].text == "उपभोक्ता"
        assert got[(0, 0)].confidence == 95.0
        assert got[(1, 1)].text == "12"
        # The model's Latin lookalike for a cell the page did not read is not text:
        # it is stored unreadable and flagged, rather than invented.
        assert got[(0, 2)].text == ""
        assert got[(0, 2)].uncertain is True
        assert "X Y" not in structure.to_html()
        # Nor is its number. Measured on a Marathi scan, the same model reports the
        # rate figure inside the quantity column, so a digit it offers for a cell
        # the page never read is a figure in the wrong place, not a free win.
        assert got[(1, 2)].text == ""
        assert got[(1, 2)].uncertain is True
        assert "450" not in structure.to_html()
        assert structure.uncertain_cells == 2
        assert structure.requires_review is True
        assert "ds-table-uncertain" in structure.to_html()

    def test_the_page_keeps_its_own_amount_when_the_model_points_it_elsewhere(
        self, monkeypatch
    ):
        """The regression measured on a real Marathi scan, kept as a test.

        There, the table model reported the row's amount for the cell whose own
        pixels read 250.00, and the old agreement rule wrote 750.00 into it with no
        confidence at all. A rate the page never read must not replace the rate it
        did read: the cell keeps 250.00, keeps the page's score, and is flagged.
        """
        cells = {
            (0, 0): ("काम वर्णन", 1, 1),
            (0, 1): ("दर", 1, 1),
            (0, 2): ("रकम", 1, 1),
            (1, 0): ("नली निर्मिती", 1, 1),
            (1, 1): ("250.00", 1, 1),
            (1, 2): ("750.00", 1, 1),
        }
        image, _truth, lines = _ruled(
            row_bounds=PLAIN4["row_bounds"][:3],
            col_bounds=PLAIN4["col_bounds"][:4],
            cells=cells,
            font_size=24,
        )
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("S T", 1, 1), ("X Y", 1, 1), ("Z", 1, 1)],
                            [("N L", 1, 1), ("750.00", 1, 1), ("750.00", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        structure = _scan(image, lines, devanagari=True)[0]
        got = {(cell.row, cell.col): cell for cell in structure.cells}
        assert got[(1, 1)].text == "250.00"
        assert got[(1, 1)].confidence == 95.0
        assert got[(1, 1)].uncertain is True
        # Where the two readers land on the same digits, nothing is disputed.
        assert got[(1, 2)].text == "750.00"
        assert got[(1, 2)].uncertain is False
        assert structure.uncertain_cells == 1
        assert structure.requires_review is True
        assert "do not agree" in structure.note

    def test_a_model_that_agrees_on_a_digit_keeps_its_own_reading(self, monkeypatch):
        image, _truth, lines = _ruled(**PLAIN4)
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("Item", 1, 1), ("Qty", 1, 1), ("Rate", 1, 1), ("Amount", 1, 1)],
                            [("Cement bags", 1, 1), ("40", 1, 1), ("380.00", 1, 1), ("15,200.00", 1, 1)],
                            [("Sand", 1, 1), ("12", 1, 1), ("1,100.00", 1, 1), ("13,200.00", 1, 1)],
                            [("Transport", 1, 1), ("2", 1, 1), ("900.00", 1, 1), ("1,800.00", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        structure = _scan(image, lines)[0]
        assert _declared_cells(_grid_of(structure), PLAIN4["cells"]) == PLAIN4["cells"]
        assert all(cell.confidence == 95.0 for cell in structure.cells)

    def test_no_ruling_falls_back_to_the_model_html_grid(self, monkeypatch):
        image = Image.new("RGB", (1200, 700), "white")
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("Subtotal", 1, 1), ("1,200.00", 1, 1)],
                            [("GST", 1, 1), ("180.00", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        structures = _scan(image, [])
        assert len(structures) == 1
        structure = structures[0]
        assert structure.engine == "PP-Structure"
        assert structure.row_texts() == ["Subtotal | 1,200.00", "GST | 180.00"]
        # The model gave no cell geometry, so the structure says so and the
        # reviewer is told these rows have no boxes behind them.
        assert "no cell geometry" in structure.note
        assert structure.requires_review is True
        assert all(cell.bbox is None for cell in structure.cells)

    def test_one_region_failing_does_not_cost_the_page_the_other(self, monkeypatch):
        image, _truth, lines = _ruled(**TWO_TABLES)
        _no_engine(monkeypatch)

        def explode_first(page_number, **kwargs):
            if len(kwargs["row_bounds"]) == 5:
                raise ValueError("too many values to unpack (expected 2)")
            return real_from_ruled_grid(page_number, **kwargs)

        monkeypatch.setattr(ocr_service, "from_ruled_grid", explode_first)
        structures, reported = ocr_service._extract_scanned_tables(
            image, devanagari_ocr=False, ocr_lines=lines, page_number=1
        )
        assert len(structures) == 1
        assert reported == 1
        assert structures[0].row_texts()[0].startswith("Footer A")

    def test_every_region_failing_leaves_the_page_untouched(self, monkeypatch):
        image, _truth, lines = _ruled(**PLAIN4)
        _no_engine(monkeypatch)

        def explode(*_args, **_kwargs):
            raise RuntimeError("simulated table-recognition crash")

        monkeypatch.setattr(ocr_service, "from_ruled_grid", explode)
        structures, reported = ocr_service._extract_scanned_tables(
            image, devanagari_ocr=False, ocr_lines=lines, page_number=1
        )
        assert structures == []
        assert reported == 0


# --------------------------------------------------------------------------
# digital PDFs, read through PyMuPDF
# --------------------------------------------------------------------------


def _draw_grid(page, row_bounds, col_bounds, omit_v, omit_h, line_width):
    for index, y in enumerate(row_bounds):
        for col in range(len(col_bounds) - 1):
            if (index, col) in omit_h:
                continue
            page.draw_line(
                fitz.Point(col_bounds[col], y),
                fitz.Point(col_bounds[col + 1], y),
                width=line_width,
            )
    for index, x in enumerate(col_bounds):
        for row in range(len(row_bounds) - 1):
            if (row, index) in omit_v:
                continue
            page.draw_line(
                fitz.Point(x, row_bounds[row]),
                fitz.Point(x, row_bounds[row + 1]),
                width=line_width,
            )


def _write_grid_pdf(path, grids):
    """Build a PDF where every grid is drawn and printed on its own page."""
    doc = fitz.open()
    for grid in grids:
        page = doc.new_page(width=grid["width"], height=grid["height"])
        _draw_grid(
            page,
            grid["row_bounds"],
            grid["col_bounds"],
            grid.get("omit_v", frozenset()),
            grid.get("omit_h", frozenset()),
            grid.get("line_width", 1.1),
        )
        for (row, col), (text, colspan, rowspan) in grid["cells"].items():
            if not text:
                continue
            box = _cell_box(
                grid["row_bounds"], grid["col_bounds"], row, col, colspan, rowspan
            )
            kwargs = {"fontsize": grid.get("font_size", 11)}
            if grid.get("fontfile"):
                kwargs.update(fontfile=grid["fontfile"], fontname="gtfont")
            page.insert_text(
                fitz.Point(box[0] + 2, box[1] + kwargs["fontsize"]), text, **kwargs
            )
    doc.save(str(path))
    doc.close()


def _digital_structures(path):
    doc = fitz.open(str(path))
    try:
        return [ocr_service._extract_page_tables(page)[0] for page in doc]
    finally:
        doc.close()


class TestDigitalGroundTruth:
    def test_digital_grid_matches_the_drawn_truth(self, tmp_path):
        pdf = tmp_path / "digital.pdf"
        _write_grid_pdf(
            pdf,
            [
                {
                    "width": 1360,
                    "height": 630,
                    "row_bounds": COLSPAN["row_bounds"],
                    "col_bounds": COLSPAN["col_bounds"],
                    "cells": COLSPAN["cells"],
                    "omit_v": COLSPAN["omit_v"],
                }
            ],
        )
        structures = _digital_structures(pdf)[0]
        assert len(structures) == 1
        structure = structures[0]
        assert structure.engine == "PyMuPDF"
        assert structure.source == "digital"
        assert (structure.row_count, structure.col_count) == (4, 4)
        assert _declared_cells(_grid_of(structure), COLSPAN["cells"]) == COLSPAN["cells"]
        assert structure.header_rows == [0]

    def test_digital_empty_cell_is_stored_empty_not_guessed(self, tmp_path):
        pdf = tmp_path / "blank.pdf"
        _write_grid_pdf(
            pdf,
            [
                {
                    "width": 1360,
                    "height": 460,
                    "row_bounds": BLANK["row_bounds"],
                    "col_bounds": BLANK["col_bounds"],
                    "cells": BLANK["cells"],
                }
            ],
        )
        structure = _digital_structures(pdf)[0][0]
        assert (structure.row_count, structure.col_count) == (3, 3)
        empty = [cell for cell in structure.cells if not cell.text.strip()]
        assert {(cell.row, cell.col) for cell in empty} == {(2, 1), (2, 2)}
        assert all(cell.uncertain is False for cell in empty)
        assert structure.uncertain_cells == 0
        # A digital text layer was never OCR'd: that is no measurement, not a zero.
        assert all(cell.confidence is None for cell in structure.cells)

    def test_tables_on_two_pages_are_not_mixed(self, tmp_path):
        pdf = tmp_path / "twopage.pdf"
        bounds = {"row_bounds": [150.0, 240.0, 330.0], "col_bounds": PLAIN4["col_bounds"]}
        _write_grid_pdf(
            pdf,
            [
                {
                    "width": 1360,
                    "height": 420,
                    **bounds,
                    "cells": {
                        (0, 0): ("Page one", 1, 1),
                        (0, 1): ("Qty", 1, 1),
                        (0, 2): ("Rate", 1, 1),
                        (0, 3): ("Amount", 1, 1),
                        (1, 0): ("Cement", 1, 1),
                        (1, 1): ("40", 1, 1),
                        (1, 2): ("380.00", 1, 1),
                        (1, 3): ("15,200.00", 1, 1),
                    },
                },
                {
                    "width": 1360,
                    "height": 420,
                    **bounds,
                    "cells": {
                        (0, 0): ("Page two", 1, 1),
                        (0, 1): ("Qty", 1, 1),
                        (0, 2): ("Rate", 1, 1),
                        (0, 3): ("Amount", 1, 1),
                        (1, 0): ("Sand", 1, 1),
                        (1, 1): ("12", 1, 1),
                        (1, 2): ("1,100.00", 1, 1),
                        (1, 3): ("13,200.00", 1, 1),
                    },
                },
            ],
        )
        per_page = _digital_structures(pdf)
        assert [len(items) for items in per_page] == [1, 1]
        first, second = per_page[0][0], per_page[1][0]
        assert (first.page_number, second.page_number) == (1, 2)
        assert first.row_texts()[0].startswith("Page one")
        assert "Page two" not in " ".join(first.row_texts())
        assert second.row_texts()[0].startswith("Page two")

    def test_devanagari_cells_keep_their_vowel_signs(self, tmp_path):
        fontfile = _devanagari_font()
        if fontfile is None:
            pytest.skip("no Devanagari font available to build the fixture")
        declared = {
            (0, 0): ("उपभोक्ता", 1, 1),
            (0, 1): ("क्रमांक", 1, 1),
            (1, 0): ("रस्त्यावरील", 1, 1),
            (1, 1): ("माल", 1, 1),
            (2, 0): ("एकूण रक्कम", 1, 1),
            (2, 1): ("१,२००.००", 1, 1),
        }
        pdf = tmp_path / "deva.pdf"
        try:
            _write_grid_pdf(
                pdf,
                [
                    {
                        "width": 1360,
                        "height": 520,
                        "row_bounds": [80.0, 200.0, 320.0, 440.0],
                        "col_bounds": [100.0, 620.0, 1080.0],
                        "cells": declared,
                        "fontfile": fontfile,
                        "font_size": 20,
                    }
                ],
            )
        except Exception as err:  # a font MuPDF cannot embed is a fixture problem
            pytest.skip(f"Devanagari font unusable for the fixture: {err}")
        structure = _digital_structures(pdf)[0][0]
        got = {(cell.row, cell.col): cell.text for cell in structure.cells}
        assert len(got) == 6
        for key, (text, _cs, _rs) in declared.items():
            value = unicodedata.normalize("NFC", got[key])
            assert value == unicodedata.normalize("NFC", text), f"cell {key}: {value!r}"
        # The regression this guards: table.extract() detached a matra from its
        # consonant and moved it ('रस्ता' came out as 'रसता लावणी्').
        assert "रस्त्यावरील" in unicodedata.normalize("NFC", got[(1, 0)])


# --------------------------------------------------------------------------
# the same cells, seen by search, the viewer and the chatbot
# --------------------------------------------------------------------------


class TestStructuredRowsReachEveryReader:
    @pytest.fixture
    def scanned(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**COLSPAN, score=0.83)
        return _scan(image, lines, page_number=3)[0]

    def test_rows_are_indexed_with_their_page(self, scanned):
        lines = ocr_service._table_text_lines([scanned], 3, marker="table")
        assert lines[0] == "[table p3]"
        assert "Grand total |  |  | 30,200.00" in lines
        chunks = chunk_text("\n".join(lines))
        assert any("Grand total" in chunk and "30,200.00" in chunk for chunk in chunks)

    def test_cells_are_evidence_with_document_and_page(self, scanned):
        page_text = "\n".join(
            ocr_service._table_text_lines([scanned], 3, marker="table")
        )
        units = build_page_units(7, "invoice.pdf", 3, page_text)
        rows = [unit for unit in units if unit.is_table_row]
        assert rows, "the table rows must become evidence units"
        total = next(unit for unit in rows if "Grand total" in unit.text)
        assert (total.document_id, total.document_name, total.page_number) == (
            7,
            "invoice.pdf",
            3,
        )
        assert total.header == ("Item", "Qty", "Rate", "Amount")
        assert "30,200.00" in [token.raw for token in total.values]
        assert extract_values(total.text)

    def test_html_is_a_real_table_with_the_real_spans(self, scanned):
        html = scanned.to_html()
        assert html.startswith('<table class="ds-table"')
        assert 'data-page="3"' in html
        assert '<td colspan="3">Grand total</td>' in html
        assert "15,200.00" in html

    def test_html_escaping_keeps_a_label_out_of_the_markup(self):
        structure = TableStructure(page_number=1, row_count=1, col_count=1)
        structure.cells.append(
            TableCell(row=0, col=0, text='<script>x</script> & "quoted"')
        )
        html = structure.to_html()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_an_all_empty_row_is_not_rendered_as_an_empty_header(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**BLANK)
        structure = _scan(image, lines)[0]
        html = structure.to_html()
        assert html.count("<tr>") == len(structure.row_texts())
        assert "<th></th>" not in html


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


# The columns `extracted_tables` carries, in the order the pipeline writes them.
TABLE_PAYLOAD_KEYS = (
    "bbox",
    "row_count",
    "col_count",
    "header_rows",
    "cells",
    "engine",
    "source",
    "confidence",
    "uncertain_cells",
    "requires_review",
    "note",
)


def _store_structure(structures: list) -> int:
    """Write structures the way the pipeline does and return the document id."""
    db = SessionLocal()
    try:
        document = Document(
            original_filename="invoice.pdf",
            stored_filename="invoice.pdf",
            file_path="uploads/invoice.pdf",
            file_type="application/pdf",
            file_size=1024,
            status="indexed",
            department="LEGAL",
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        for structure in structures:
            page = DocumentPage(
                document_id=document.id,
                page_number=structure.page_number,
                page_path=document.file_path,
                width=1200,
                height=700,
            )
            db.add(page)
            db.commit()
            db.refresh(page)
            payload = structure.to_payload()
            db.add(
                ExtractedTable(
                    document_id=document.id,
                    page_id=page.id,
                    page_number=structure.page_number,
                    **{key: payload[key] for key in TABLE_PAYLOAD_KEYS},
                )
            )
            db.commit()
        return document.id
    finally:
        db.close()


class TestStorage:
    def test_cells_boxes_and_confidence_round_trip(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**COLSPAN, score=0.83)
        structure = _scan(image, lines)[0]
        payload = structure.to_payload()

        db = SessionLocal()
        try:
            document = Document(
                original_filename="invoice.pdf",
                stored_filename="invoice.pdf",
                file_path="uploads/invoice.pdf",
                file_type="application/pdf",
                file_size=1024,
                status="indexed",
                department="LEGAL",
            )
            db.add(document)
            db.commit()
            db.refresh(document)
            document_id = document.id
            page = DocumentPage(
                document_id=document_id,
                page_number=1,
                page_path=document.file_path,
                width=image.size[0],
                height=image.size[1],
            )
            db.add(page)
            db.commit()
            db.refresh(page)
            db.add(
                ExtractedTable(
                    document_id=document.id,
                    page_id=page.id,
                    page_number=1,
                    **{key: payload[key] for key in TABLE_PAYLOAD_KEYS},
                )
            )
            db.commit()
            db.expunge_all()

            stored = (
                db.query(ExtractedTable)
                .filter(ExtractedTable.document_id == document_id)
                .one()
            )
            assert stored.engine == "Ruled-grid"
            assert stored.source == "scanned"
            assert (stored.row_count, stored.col_count) == (4, 4)
            assert stored.header_rows == [0]
            assert stored.bbox == pytest.approx((120.0, 150.0, 1220.0, 510.0), abs=2.0)
            assert float(stored.confidence) == pytest.approx(structure.confidence)
            assert stored.uncertain_cells == 0
            assert stored.requires_review is False

            cells = {(c["row"], c["col"]): c for c in stored.cells}
            assert cells[(3, 0)]["text"] == "Grand total"
            assert cells[(3, 0)]["colspan"] == 3
            assert cells[(3, 0)]["bbox"] == pytest.approx(
                [120.0, 420.0, 1000.0, 510.0], abs=2.0
            )
            assert cells[(1, 3)]["confidence"] == 83.0

            response = ExtractedTableResponse.model_validate(stored)
            assert response.page_number == 1
            assert len(response.cells) == len(structure.cells)
            assert response.cells[0].bbox is not None
        finally:
            db.close()


class TestTablesReachTheApi:
    """A stored grid is only useful if the reviewer's client receives a grid.

    This is the whole travel of one table: the reader's structure, the
    `extracted_tables` row the pipeline would write, and the body of
    `GET /api/documents/{id}`. Rows from two pages must arrive as two tables, each
    with its own page number, so nothing from page 2 can be read as page 1.
    """

    def _headers(self):
        from tests.helpers import get_test_auth_headers

        db = SessionLocal()
        try:
            return get_test_auth_headers(db, "tables-api@docsentinel.test", "ADMIN")
        finally:
            db.close()

    def _body(self, document_id):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        response = client.get(
            f"/api/documents/{document_id}", headers=self._headers()
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_api_returns_the_grid_with_its_cells_boxes_and_scores(
        self, monkeypatch
    ):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**COLSPAN, score=0.83)
        page_one = _scan(image, lines)[0]
        document_id = _store_structure([page_one])

        tables = self._body(document_id)["extracted_tables"]
        assert len(tables) == 1
        table = tables[0]
        assert table["page_number"] == 1
        assert (table["row_count"], table["col_count"]) == (4, 4)
        assert table["header_rows"] == [0]
        assert table["engine"] == "Ruled-grid"
        assert table["source"] == "scanned"
        assert float(table["confidence"]) == pytest.approx(page_one.confidence)
        assert table["uncertain_cells"] == 0
        assert table["requires_review"] is False

        cells = {(c["row"], c["col"]): c for c in table["cells"]}
        assert cells[(3, 0)]["text"] == "Grand total"
        assert cells[(3, 0)]["colspan"] == 3
        assert cells[(3, 0)]["bbox"] == pytest.approx(
            [120.0, 420.0, 1000.0, 510.0], abs=2.0
        )
        assert cells[(1, 3)]["confidence"] == 83.0
        assert cells[(0, 0)]["header"] is True

    def test_two_pages_stay_two_tables(self, monkeypatch):
        _no_engine(monkeypatch)
        image, _truth, lines = _ruled(**COLSPAN, score=0.83)
        first = _scan(image, lines, page_number=1)[0]
        image2, _truth2, lines2 = _ruled(**PLAIN4)
        second = _scan(image2, lines2, page_number=2)[0]
        document_id = _store_structure([first, second])

        tables = {
            table["page_number"]: table
            for table in self._body(document_id)["extracted_tables"]
        }
        assert set(tables) == {1, 2}
        page_one = {cell["text"] for cell in tables[1]["cells"]}
        page_two = {cell["text"] for cell in tables[2]["cells"]}
        assert "Grand total" in page_one and "30,200.00" in page_one
        assert "Cement bags" in page_two and "15,200.00" in page_two
        assert "Transport" in page_two and "1,800.00" in page_two
        # No row of one page was carried into the other's table.
        assert "Grand total" not in page_two
        assert "30,200.00" not in page_two
        assert "Transport" not in page_one
        assert tables[1]["page_id"] != tables[2]["page_id"]

    def test_an_unreadable_cell_reaches_the_api_flagged(self, monkeypatch):
        """The review flag is part of the payload, not something the viewer guesses."""
        image, _truth, lines = _ruled(**PLAIN4)
        engine = _StubEngine(
            [
                _model(
                    _html(
                        [
                            [("Item", 1, 1), ("Qty", 1, 1), ("Rate", 1, 1), ("Amount", 1, 1)],
                            [("Cement bags", 1, 1), ("40", 1, 1), ("380.00", 1, 1), ("15,200.00", 1, 1)],
                            [("Sand", 1, 1), ("12", 1, 1), ("1,100.00", 1, 1), ("13,200.00", 1, 1)],
                            [("Transport", 1, 1), ("2", 1, 1), ("900.00", 1, 1), ("1,800.00", 1, 1)],
                        ]
                    )
                )
            ]
        )
        monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: engine)
        _set_line_text(lines, "15,200.00", "15,2O0.00")
        structure = _scan(image, lines)[0]
        document_id = _store_structure([structure])

        table = self._body(document_id)["extracted_tables"][0]
        assert table["requires_review"] is True
        assert table["uncertain_cells"] == 1
        assert "do not agree" in table["note"]
        cell = {(c["row"], c["col"]): c for c in table["cells"]}[(1, 3)]
        assert cell["text"] == "15,2O0.00"
        assert cell["uncertain"] is True
