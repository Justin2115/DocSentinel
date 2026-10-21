"""Structured table representation shared by the digital and scanned readers.

A table is kept as cells with grid coordinates, the bounding box the engine
actually measured for each cell, header flags, per-cell confidence where the
engine can produce one, and an `uncertain` flag where it cannot read the cell.
Rows are never merged across pages: every structure belongs to exactly one page.

Text renderers are derived from the structure (never the other way round) so the
`' | '` line format that chunking, keyword search and the evidence builder
already depend on cannot drift from the stored geometry.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html import escape
from typing import Any

CELL_SEP = " | "

# Digits and the punctuation that goes with them: the one thing an English table
# model reliably transcribes even on a page written in another script.
NUMERIC_CELL_RE = re.compile(r"[\d.,\s%\-/(₹$)]+")

# Letters an OCR engine substitutes for a digit. Only these count as a broken
# number, so a genuine "Rs. 2,500" or "10 kg" cell is not flagged.
DIGIT_CONFUSABLES = set("OoDQlLiI")


def _is_digit(char: str) -> bool:
    return unicodedata.category(char) == "Nd"


def _looks_like_a_misread_amount(text: str) -> bool:
    """Whether a cell that is mostly digits cannot be read as a number.

    Two generic tells, neither of which depends on the document: digits drawn
    from more than one numeral system in the same cell (the reader mixed ASCII
    '0' with Devanagari '०'), or one of the letters an engine confuses with a
    digit sitting against a digit. The text itself is never rewritten here.
    """
    stripped = text.strip()
    digits = [char for char in stripped if _is_digit(char)]
    if len(digits) < 2:
        return False
    if len(digits) / len(stripped) < 0.4:
        # A cell that merely mentions a number inside prose is prose.
        return False
    if len({ord(char) // 256 for char in digits}) > 1:
        return True
    previous = ""
    for char in stripped:
        if (_is_digit(char) and previous in DIGIT_CONFUSABLES) or (
            char in DIGIT_CONFUSABLES and _is_digit(previous)
        ):
            return True
        previous = char
    return False


def _round_box(box: tuple[float, float, float, float] | None) -> tuple[float, ...] | None:
    if box is None:
        return None
    return tuple(round(float(value), 2) for value in box)


def _distinct_starts(rects: list[Any], index: int) -> list[float]:
    starts: list[float] = []
    for rect in rects:
        if rect is None:
            continue
        value = float(rect[index])
        if not starts or all(abs(value - seen) > 1.0 for seen in starts):
            starts.append(value)
    return sorted(starts)


def _span_size(rect: Any, starts: list[float], low: int, high: int) -> int:
    """How many grid tracks one cell rectangle covers.

    Counting the track starts the rectangle spans is geometry, not a guess: the
    reader draws a merged cell as one wide rect and `None` for the tracks it
    swallows, so the run of `None`s alone would under-count a span that starts
    mid-row.
    """
    lo = float(rect[low])
    hi = float(rect[high])
    covered = [start for start in starts if lo - 1.0 <= start < hi - 1.0]
    return max(1, len(covered))


def _track_index(rect: Any, starts: list[float], edge: int) -> int:
    value = float(rect[edge])
    best, best_distance = 0, None
    for position, start in enumerate(starts):
        distance = abs(value - start)
        if best_distance is None or distance < best_distance:
            best, best_distance = position, distance
    return best


@dataclass
class TableCell:
    row: int
    col: int
    text: str = ""
    bbox: tuple[float, float, float, float] | None = None
    colspan: int = 1
    rowspan: int = 1
    is_header: bool = False
    # None means the engine has no measurable confidence for this cell (a digital
    # text layer was never OCR'd). It is not a score of 0.
    confidence: float | None = None
    uncertain: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "text": self.text,
            "bbox": _round_box(self.bbox),
            "colspan": self.colspan,
            "rowspan": self.rowspan,
            "header": self.is_header,
            "confidence": self.confidence,
            "uncertain": self.uncertain,
        }


@dataclass
class TableStructure:
    page_number: int
    row_count: int
    col_count: int
    cells: list[TableCell] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None
    engine: str = ""
    source: str = "digital"  # digital | scanned
    header_rows: list[int] = field(default_factory=list)
    confidence: float | None = None
    uncertain_cells: int = 0
    requires_review: bool = False
    note: str | None = None

    # ---- derivation -----------------------------------------------------
    def add_note(self, text: str) -> "TableStructure":
        """Append a caveat without losing the one already recorded."""
        self.note = "; ".join(filter(None, [self.note, text]))
        return self

    def flag_misread_amounts(self) -> int:
        """Mark cells made mostly of digits that cannot be read as a number.

        A wrong amount is the costliest thing a table can carry into review, so a
        cell showing the tells of a bad digit read is flagged. The text stays
        exactly as the engine read it and stays searchable: nothing is corrected,
        blanked or guessed here.
        """
        flagged = 0
        if self.source != "scanned":
            # A digital text layer is the document's own characters; nothing about
            # it was read from pixels, so there is no misread to flag.
            return 0
        for cell in self.cells:
            if cell.uncertain:
                continue
            if _looks_like_a_misread_amount(cell.text):
                cell.uncertain = True
                flagged += 1
        if flagged:
            self.refresh_flags()
            self.requires_review = True
            self.add_note(
                f"{flagged} cell(s) hold digits the reader could not resolve into "
                "a number; the text shown is what was seen, not a correction"
            )
        return flagged

    def refresh_flags(self) -> "TableStructure":
        """Recompute the counts that are properties of the cells, not inputs."""
        self.uncertain_cells = sum(1 for cell in self.cells if cell.uncertain)
        scored = [
            cell.confidence for cell in self.cells if cell.confidence is not None
        ]
        self.confidence = round(sum(scored) / len(scored), 2) if scored else None
        self.header_rows = sorted(
            {cell.row for cell in self.cells if cell.is_header},
        )
        if self.cells:
            boxes = [cell.bbox for cell in self.cells if cell.bbox is not None]
            if boxes:
                self.bbox = (
                    min(float(b[0]) for b in boxes),
                    min(float(b[1]) for b in boxes),
                    max(float(b[2]) for b in boxes),
                    max(float(b[3]) for b in boxes),
                )
        return self

    def grid(self) -> list[list[TableCell | None]]:
        rows: list[list[TableCell | None]] = [
            [None] * max(0, self.col_count) for _ in range(max(0, self.row_count))
        ]
        for cell in self.cells:
            if 0 <= cell.row < len(rows) and 0 <= cell.col < len(rows[cell.row]):
                rows[cell.row][cell.col] = cell
        return rows

    def row_texts(self) -> list[str]:
        """One `' | '` line per row, in engine order, spans padded with blanks."""
        lines: list[str] = []
        for row in self.grid():
            cells = [cell.text.strip() if cell is not None else "" for cell in row]
            line = CELL_SEP.join(cells)
            if line.strip(" |"):
                lines.append(line)
        return lines

    def has_content(self) -> bool:
        return any(cell.text.strip() for cell in self.cells)

    # ---- rendering ------------------------------------------------------
    def to_html(self) -> str:
        """Real markup with the structure the engine reported.

        Uncertain cells are marked so a reviewer sees the gap; nothing is filled
        in for them. A row with no readable cell anywhere is skipped, the same way
        `row_texts` skips it, so the viewer and the indexed text agree.
        """
        parts: list[str] = ['<table class="ds-table" data-page="%s">' % self.page_number]
        for row in self.grid():
            cells = [cell for cell in row if cell is not None]
            if not cells or all(
                not cell.text.strip() and not cell.uncertain for cell in cells
            ):
                continue
            parts.append("<tr>")
            for cell in cells:
                tag = "th" if cell.is_header else "td"
                attributes = ""
                if cell.colspan > 1:
                    attributes += f' colspan="{cell.colspan}"'
                if cell.rowspan > 1:
                    attributes += f' rowspan="{cell.rowspan}"'
                if cell.uncertain:
                    attributes += ' class="ds-table-uncertain" title="not readable"'
                parts.append(f"<{tag}{attributes}>{escape(cell.text)}</{tag}>")
            parts.append("</tr>")
        parts.append("</table>")
        return "".join(parts)

    def to_payload(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "row_count": self.row_count,
            "col_count": self.col_count,
            "bbox": _round_box(self.bbox),
            "header_rows": self.header_rows,
            "engine": self.engine,
            "source": self.source,
            "confidence": self.confidence,
            "uncertain_cells": self.uncertain_cells,
            "requires_review": self.requires_review,
            "note": self.note,
            "cells": [cell.to_payload() for cell in self.cells],
        }


def _words_for_rect(page_words: list[Any], rect: tuple[float, float, float, float]) -> str:
    """Join the page's own words whose centre falls inside one cell rectangle.

    The word list is read in the PDF's own block/line/word order, so a Devanagari
    cell keeps its vowel signs attached to the consonant they belong to.
    """
    x1, y1, x2, y2 = rect
    inside = []
    for word in page_words:
        centre_x = (float(word[0]) + float(word[2])) / 2.0
        centre_y = (float(word[1]) + float(word[3])) / 2.0
        if x1 <= centre_x <= x2 and y1 <= centre_y <= y2:
            inside.append(word)
    inside.sort(key=lambda w: (w[5], w[6], w[7]))
    return " ".join(str(w[4]) for w in inside).strip()


def from_pymupdf_table(
    pdf_page: Any,
    table: Any,
    rows: list[list[Any]],
    page_words: list[Any] | None = None,
) -> TableStructure:
    """Build a structure from PyMuPDF's own grid.

    `rows` is `table.extract()`. PyMuPDF returns `None` for the grid slots a
    merged cell swallows and one wide rectangle for the cell itself, so spans
    come from geometry rather than from the text.

    Cell text comes from the page's word list whenever it is supplied:
    `table.extract()` re-reads each cell on its own and detaches Devanagari vowel
    signs from their consonants ('रस्ता' comes out as 'रसता'), which would corrupt
    the very cells the reviewer needs to trust. The extract text is still used for
    a cell the word list leaves empty, so nothing is lost when a glyph sits
    outside the detected rectangle.
    """
    bbox = tuple(float(value) for value in table.bbox) if table.bbox else None
    header_box = getattr(getattr(table, "header", None), "bbox", None)
    header_rect = None
    if header_box is not None and len(header_box) == 4:
        try:
            header_rect = tuple(float(value) for value in header_box)
        except (TypeError, ValueError):
            header_rect = None

    row_rects = [row.cells for row in table.rows]
    flat = [rect for row in row_rects for rect in row if rect is not None]
    col_starts = _distinct_starts(flat, 0)
    row_starts = _distinct_starts(flat, 1)

    structure = TableStructure(
        page_number=int(getattr(pdf_page, "number", 0)) + 1,
        row_count=len(table.rows),
        col_count=max(table.col_count, max((len(row) for row in row_rects), default=0)),
        bbox=bbox,
        engine="PyMuPDF",
        source="digital",
    )

    for row_index, cells_in_row in enumerate(row_rects):
        text_row = rows[row_index] if row_index < len(rows) else []
        for col_slot, rect in enumerate(cells_in_row):
            if rect is None:
                continue
            text = text_row[col_slot] if col_slot < len(text_row) else None
            cell_box = tuple(float(value) for value in rect)
            extracted = ("" if text is None else str(text)).strip().replace("\n", " ")
            word_text = _words_for_rect(page_words, cell_box) if page_words else ""
            col = _track_index(cell_box, col_starts, 0) if col_starts else col_slot
            is_header = False
            if header_rect is not None:
                centre_y = (cell_box[1] + cell_box[3]) / 2.0
                is_header = header_rect[1] - 1.0 <= centre_y <= header_rect[3] + 1.0
            structure.cells.append(
                TableCell(
                    row=row_index,
                    col=col,
                    # An empty digital cell is genuinely empty: the text layer is
                    # authoritative, so it is stored as "" and not flagged.
                    text=word_text or extracted,
                    bbox=cell_box,
                    colspan=_span_size(cell_box, col_starts, 0, 2) if col_starts else 1,
                    rowspan=_span_size(cell_box, row_starts, 1, 3) if row_starts else 1,
                    is_header=is_header,
                )
            )

    return structure.refresh_flags()


def _resolve_cell_text(
    reading: tuple[str, float | None] | None,
    model_text: str,
    *,
    script_page: bool,
) -> tuple[str, float | None, bool]:
    """Decide one cell's (text, confidence, uncertain) from what was actually seen.

    A cell the page read keeps that reading and its recogniser score. A cell with
    no page reading is empty if the engine offered nothing, keeps the engine's
    reading on a Latin page, and on a Devanagari page is stored unreadable and
    flagged: measured on a Marathi scan, that table model puts the rate figure in
    the quantity column and its cell boxes overshoot the page, so not even its
    digits can be trusted to belong to the cell they would be stored in. An amount
    in the wrong column is worse than a blank that says "review me".
    """
    if reading is not None:
        return reading[0], reading[1], False
    if not model_text:
        return "", None, False
    if script_page:
        return "", None, True
    return model_text, None, False


def from_paddle_html_rows(
    page_number: int,
    rows: list[list[tuple[str, int, int, bool]]],
    rows_of_rects: list[list[tuple[float, float, float, float]]] | None,
    readings: dict[tuple[int, int], tuple[str, float | None]],
    *,
    script_page: bool,
    engine: str,
    note: str | None = None,
) -> TableStructure:
    """Build a structure from PP-Structure's HTML grid plus the page's own readings.

    `rows` carries `(text, colspan, rowspan, is_header)` per model cell and
    `rows_of_rects` one box per expanded grid slot, so a merged cell takes the
    union of the boxes it covers. `readings` maps a slot to the text (and
    recogniser score) the page OCR produced inside that box. A cell keeps the
    model's reading only where the page supports it: on a Devanagari page the
    English table model transcribes the script as Latin lookalikes, and those are
    stored as unreadable rather than as text. Purely numeric cells are the model's
    own strength, so their reading survives unless the page supplies one too.
    """
    col_count = max(
        (sum(max(1, colspan) for _, colspan, _, _ in row) for row in rows), default=0
    )
    structure = TableStructure(
        page_number=page_number,
        row_count=len(rows),
        col_count=col_count,
        engine=engine,
        source="scanned",
        note=note,
    )

    for row_index, html_row in enumerate(rows):
        slot = 0
        for text, colspan, rowspan, is_header in html_row:
            width = max(1, colspan)
            rect: tuple[float, float, float, float] | None = None
            if rows_of_rects is not None and row_index < len(rows_of_rects):
                rects = rows_of_rects[row_index]
                covered = [rects[slot + offset] for offset in range(width) if slot + offset < len(rects)]
                if covered:
                    rect = (
                        min(float(box[0]) for box in covered),
                        min(float(box[1]) for box in covered),
                        max(float(box[2]) for box in covered),
                        max(float(box[3]) for box in covered),
                    )
            reading = readings.get((row_index, slot))
            text, confidence, uncertain = _resolve_cell_text(
                reading, _clean(text), script_page=script_page
            )
            cell = TableCell(
                row=row_index,
                col=slot,
                bbox=rect,
                colspan=width,
                rowspan=max(1, rowspan),
                is_header=bool(is_header),
                text=text,
                confidence=confidence,
                uncertain=uncertain,
            )
            structure.cells.append(cell)
            slot += width

    structure.flag_misread_amounts()
    structure.refresh_flags()
    structure.requires_review = rows_of_rects is None or structure.uncertain_cells > 0
    return structure


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def from_ruled_grid(
    page_number: int,
    *,
    row_bounds: list[float],
    col_bounds: list[float],
    cells: list[dict],
    readings: dict[tuple[int, int], tuple[str, float | None]],
    script_page: bool,
    engine: str,
    header_rows: list[int] | None = None,
    hints: dict[tuple[int, int], str] | None = None,
    note: str | None = None,
) -> TableStructure:
    """Build a structure from a grid measured out of the page's own ruling.

    `row_bounds` and `col_bounds` are the ruled line positions, and `cells` one
    entry per cell: `(row, col, colspan, rowspan, rect)` where the spans come from
    the dividers that are genuinely absent inside the grid. `readings` holds the
    text (and recogniser score) the page OCR produced inside each rect, so a cell
    keeps the row/column position its own pixels put it in.
    """
    structure = TableStructure(
        page_number=page_number,
        row_count=max(0, len(row_bounds) - 1),
        col_count=max(0, len(col_bounds) - 1),
        engine=engine,
        source="scanned",
        note=note,
    )
    header_set = set(header_rows or [])
    for cell in cells:
        row, col = int(cell["row"]), int(cell["col"])
        text, confidence, uncertain = _resolve_cell_text(
            readings.get((row, col)),
            _clean((hints or {}).get((row, col), "")),
            script_page=script_page,
        )
        structure.cells.append(
            TableCell(
                row=row,
                col=col,
                text=text,
                bbox=cell.get("rect"),
                colspan=max(1, int(cell.get("colspan", 1))),
                rowspan=max(1, int(cell.get("rowspan", 1))),
                is_header=row in header_set,
                confidence=confidence,
                uncertain=uncertain,
            )
        )
    structure.flag_misread_amounts()
    structure.refresh_flags()
    # refresh_flags derives header_rows from the cells, which would lose a header
    # row whose cells are all empty; restore the grid's own answer.
    if header_rows:
        structure.header_rows = sorted(set(header_rows))
    structure.requires_review = structure.uncertain_cells > 0
    return structure
