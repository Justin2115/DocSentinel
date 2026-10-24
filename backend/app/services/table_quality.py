"""Whether a reconstructed grid is fit to store, show and index.

A reader can produce a grid for a page that has no usable table in it: the ruling
measured noise, the table model printed more rows than the page carries, or a
deskew left the cells pointing at coordinates the stored page does not show. Such a
grid is worse than no grid, because a huge table of blank cells hides the recognised
text that a reviewer, a keyword search and the chatbot can still use.

This module measures a structure and decides. It never edits one: no cell is
filled in, no amount is corrected, and a rejected grid keeps the page's own text as
the page's text.

The bar is different for each way a grid gets built, because the grids are not built
on the same evidence:

`digital`
    PyMuPDF measured the PDF's own ruling and read the PDF's own text layer. An
    empty cell there is a cell that is empty in the document, so no ratio applies -
    only a grid that contradicts itself, or that holds nothing at all, is refused.
`ruled`
    The grid is measured out of the ink on the page, so its geometry is real, but
    every blank is a place where the reader could not put text. A grid that is
    mostly blank is a report about the reader, not about the page.
`model`
    PP-Structure guessed the grid from the image with no ruling to check it against,
    so wherever the check is possible the grid must be corroborated by the page's own
    text before it is shown as fact. `page_lines` says whether that check is possible:
    a grid with no cell boxes, or a page that returned no recognised line geometry at
    all, cannot be corroborated by construction rather than by failure, so it is kept
    and flagged as unverified instead of refused - a borderless table is still a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.table_structure import TableStructure

__all__ = ["Decision", "Signals", "assess", "rejection_record", "screen"]

# A grid this small is judged on its content: a ratio over four cells says nothing
# about whether those four cells are right.
MIN_GRID_FOR_RATIOS = 12

# Share of reported cells that must carry text, and share of rows that must hold
# any at all. `0.0` means the test is not applied to that layout.
CELL_FILL_RATIO = {"digital": 0.0, "ruled": 1 / 3, "model": 1 / 2}
ROW_FILL_RATIO = {"digital": 0.0, "ruled": 1 / 3, "model": 1 / 2}

# Text kept inside the grid against the text the reader produced inside the same
# area of the page. Below this the grid was not drawn where those words are.
LINE_AGREEMENT_RATIO = 0.5
MIN_REGION_CHARS = 40

# Two cells in different grid positions whose boxes overlap this much of the
# smaller one are claiming the same ink; sharing an edge is not an overlap.
OVERLAP_RATIO = 0.5

# A box may hang off the page by this fraction of the page's own width/height before
# it is called outside the page - rounding and a border line are not a contradiction.
PAGE_OVERFLOW_TOLERANCE = 0.01


def _layout_of(structure: TableStructure) -> str:
    if structure.source == "digital":
        return "digital"
    if structure.engine == "Ruled-grid":
        return "ruled"
    return "model"


def _area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, float(rect[2]) - float(rect[0])) * max(
        0.0, float(rect[3]) - float(rect[1])
    )


def _intersection(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    width = min(float(a[2]), float(b[2])) - max(float(a[0]), float(b[0]))
    height = min(float(a[3]), float(b[3])) - max(float(a[1]), float(b[1]))
    if width <= 0.0 or height <= 0.0:
        return 0.0
    return width * height


@dataclass(frozen=True)
class Signals:
    """Everything measured off the structure, before any judgement."""

    layout: str
    frame: str
    cells: int
    rows: int
    cols: int
    readable: int
    page_backed: int
    uncertain: int
    boxed: int
    populated_rows: int
    out_of_range: int
    duplicate_slots: int
    overlapping_boxes: int
    outside_page: int
    cell_chars: int
    region_chars: int | None = None
    # Grid positions no cell claims: a grid reporting 4x3 with cells in four
    # positions is not the grid it says it is.
    unclaimed_slots: int = 0
    # How many recognised lines with boxes the page itself produced. None means the
    # caller cannot say, which makes corroboration unmeasurable rather than failed.
    page_lines: int | None = None

    @property
    def cell_fill(self) -> float:
        return self.readable / self.cells if self.cells else 0.0

    @property
    def row_fill(self) -> float:
        return self.populated_rows / self.rows if self.rows else 0.0

    @property
    def line_agreement(self) -> float | None:
        if not self.region_chars:
            return None
        return self.cell_chars / self.region_chars

    @property
    def corroboration_checkable(self) -> bool:
        """Whether the page could have contradicted this grid's cells.

        A page line can only back a cell if the cell has a box to fall inside and
        the page produced line geometry to place. Without either, "no cell was
        corroborated" carries no information about the grid.
        """
        return self.boxed > 0 and bool(self.page_lines)

    @property
    def corroborated(self) -> bool | None:
        if not self.corroboration_checkable:
            return None
        return self.page_backed > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "frame": self.frame,
            "cells": self.cells,
            "rows": self.rows,
            "cols": self.cols,
            "readable": self.readable,
            "page_backed": self.page_backed,
            "uncertain": self.uncertain,
            "boxed": self.boxed,
            "populated_rows": self.populated_rows,
            "out_of_range": self.out_of_range,
            "duplicate_slots": self.duplicate_slots,
            "overlapping_boxes": self.overlapping_boxes,
            "outside_page": self.outside_page,
            "cell_chars": self.cell_chars,
            "region_chars": self.region_chars,
            "unclaimed_slots": self.unclaimed_slots,
            "page_lines": self.page_lines,
            "corroborated": self.corroborated,
            "cell_fill": round(self.cell_fill, 4),
            "row_fill": round(self.row_fill, 4),
            "line_agreement": (
                round(self.line_agreement, 4)
                if self.line_agreement is not None
                else None
            ),
        }

    @classmethod
    def measure(
        cls,
        structure: TableStructure,
        *,
        page_size: tuple[float, float] | None = None,
        region_chars: int | None = None,
        page_lines: int | None = None,
    ) -> "Signals":
        rows = max(0, structure.row_count)
        cols = max(0, structure.col_count)
        occupied: set[tuple[int, int]] = set()
        duplicates = 0
        out_of_range = 0
        readable = page_backed = uncertain = boxed = 0
        chars = 0
        # A readable cell that spans rows or columns fills every position it covers,
        # so a merged heading cell is not counted as one cell of five blank ones.
        filled_rows: set[int] = set()
        for cell in structure.cells:
            width = max(1, cell.colspan)
            height = max(1, cell.rowspan)
            if not (0 <= cell.row < rows and 0 <= cell.col < cols):
                out_of_range += 1
            elif any(
                (cell.row + r, cell.col + c) in occupied
                for r in range(height)
                for c in range(width)
            ):
                duplicates += 1
            for r in range(height):
                for c in range(width):
                    occupied.add((cell.row + r, cell.col + c))
            text = cell.text.strip()
            if text:
                readable += 1
                chars += len(text)
                filled_rows.update(range(cell.row, cell.row + height))
            if cell.from_page and text:
                page_backed += 1
            if cell.uncertain:
                uncertain += 1
            if cell.bbox is not None:
                boxed += 1
        boxes = [cell.bbox for cell in structure.cells if cell.bbox is not None]
        outside_page = 0
        # A corrected frame is a different image from the stored page (a deskew that
        # nothing inverted), so the page's own size says nothing about its boxes.
        if page_size is not None and structure.frame == "page":
            allow_x = float(page_size[0]) * PAGE_OVERFLOW_TOLERANCE
            allow_y = float(page_size[1]) * PAGE_OVERFLOW_TOLERANCE
            for box in boxes:
                if (
                    float(box[0]) < -allow_x
                    or float(box[1]) < -allow_y
                    or float(box[2]) > float(page_size[0]) + allow_x
                    or float(box[3]) > float(page_size[1]) + allow_y
                ):
                    outside_page += 1
        overlaps = 0
        for index, first in enumerate(boxes):
            for second in boxes[index + 1 :]:
                smaller = min(_area(first), _area(second))
                if smaller <= 0.0:
                    continue
                if _intersection(first, second) / smaller > OVERLAP_RATIO:
                    overlaps += 1
        return cls(
            layout=_layout_of(structure),
            frame=structure.frame,
            cells=len(structure.cells),
            rows=rows,
            cols=cols,
            unclaimed_slots=rows * cols
            - len(
                {
                    slot
                    for slot in occupied
                    if 0 <= slot[0] < rows and 0 <= slot[1] < cols
                }
            ),
            readable=readable,
            page_backed=page_backed,
            uncertain=uncertain,
            boxed=boxed,
            populated_rows=len({row for row in filled_rows if 0 <= row < rows}),
            out_of_range=out_of_range,
            duplicate_slots=duplicates,
            overlapping_boxes=overlaps,
            outside_page=outside_page,
            cell_chars=chars,
            region_chars=region_chars,
            page_lines=page_lines,
        )


@dataclass(frozen=True)
class Decision:
    accept: bool
    reason: str | None
    signals: Signals

    def __bool__(self) -> bool:
        return self.accept


def assess(
    structure: TableStructure,
    *,
    page_size: tuple[float, float] | None = None,
    region_chars: int | None = None,
    page_lines: int | None = None,
) -> Decision:
    """Measure one grid and decide whether it may be shown as a table.

    `page_size` (width, height in the same units as the cell boxes) makes the
    off-the-page check possible; without it that check is skipped rather than
    guessed. `region_chars` is the number of characters the reader produced inside
    the grid's own area of the page, for the agreement check. `page_lines` is the
    number of recognised lines with boxes the page produced, which decides whether
    the page could have corroborated the grid's cells at all.
    """
    signals = Signals.measure(
        structure,
        page_size=page_size,
        region_chars=region_chars,
        page_lines=page_lines,
    )
    def reject(reason: str) -> Decision:
        return Decision(False, reason, signals)

    if not structure.cells or structure.row_count < 1 or structure.col_count < 1:
        return reject(
            f"the grid has {signals.cells} cell(s) in a "
            f"{structure.row_count}x{structure.col_count} grid"
        )
    if signals.out_of_range:
        return reject(
            f"{signals.out_of_range} cell(s) sit outside the "
            f"{structure.row_count}x{structure.col_count} grid they report"
        )
    if signals.duplicate_slots:
        return reject(
            f"{signals.duplicate_slots} cell(s) claim a grid position another cell "
            "already holds"
        )
    if signals.outside_page and page_size is not None:
        return reject(
            f"{signals.outside_page} cell box(es) fall outside the page itself"
        )
    if signals.overlapping_boxes:
        return reject(
            f"{signals.overlapping_boxes} pair(s) of cells cover the same part of "
            "the page"
        )
    if signals.readable == 0:
        return reject("no cell in the grid could be read")
    # Corroboration is only a refusal when the page was in a position to give one:
    # a grid with no cell boxes, or a page that returned no line geometry, cannot
    # have a cell backed by the page's own text, so its emptiness says nothing
    # about the grid. Where both exist and none of the readable cells holds text
    # the page read, the model arranged cells the page does not support.
    if signals.corroborated is False:
        return reject(
            f"none of the {signals.readable} readable cell(s) holds text the page "
            "itself produced, although the page's own lines could have been placed "
            f"in {signals.boxed} cell box(es)"
        )
    if signals.cells >= MIN_GRID_FOR_RATIOS:
        if signals.cell_fill < CELL_FILL_RATIO[signals.layout]:
            return reject(
                f"only {signals.readable} of {signals.cells} cells could be read"
            )
        if signals.row_fill < ROW_FILL_RATIO[signals.layout]:
            return reject(
                f"only {signals.populated_rows} of {structure.row_count} rows hold "
                "any text"
            )
    agreement = signals.line_agreement
    if (
        signals.layout != "digital"
        and agreement is not None
        and signals.region_chars >= MIN_REGION_CHARS
        and agreement < LINE_AGREEMENT_RATIO
    ):
        return reject(
            f"the grid keeps {round(agreement * 100)}% of the text the reader "
            "produced inside its own area of the page"
        )
    return Decision(True, None, signals)


def rejection_record(
    structure: TableStructure, decision: Decision
) -> TableStructure:
    """What a rejected grid leaves behind: the measurement, and no invented cells.

    The reason and the counts stay, so the page can say what was found and why it is
    not shown, but the grid itself is not rendered - the reader's text for the page is
    the fallback, and it was never overwritten.
    """
    return TableStructure(
        page_number=structure.page_number,
        row_count=structure.row_count,
        col_count=structure.col_count,
        cells=[],
        bbox=structure.bbox,
        engine=structure.engine,
        source=structure.source,
        header_rows=[],
        confidence=None,
        uncertain_cells=structure.uncertain_cells,
        requires_review=True,
        note=(
            f"Not shown as a table: {decision.reason}. "
            "The page's own recognised text is kept below instead."
        ),
        page_backed_cells=structure.page_backed_cells,
        frame=structure.frame,
    )


@dataclass(frozen=True)
class Screening:
    accepted: list[TableStructure]
    kept: list[TableStructure]
    decisions: list[Decision]

    @property
    def rejected(self) -> list[Decision]:
        return [d for d in self.decisions if not d.accept]


def screen(
    structures: list[TableStructure],
    *,
    page_size: tuple[float, float] | None = None,
    region_chars_for: Callable[[TableStructure], int | None] | None = None,
    page_lines: int | None = None,
) -> Screening:
    """Split a page's grids into the ones to show and the ones to report.

    `kept` is what a page carries: every accepted grid unchanged, and each rejected
    grid in place of itself as a record with no cells. `accepted` is what consumes
    the page's lines: a rejected grid never takes text out of the page's prose, so
    the fallback is the page's own reading, complete.
    """
    accepted: list[TableStructure] = []
    kept: list[TableStructure] = []
    decisions: list[Decision] = []
    for structure in structures:
        region = (
            region_chars_for(structure) if region_chars_for is not None else None
        )
        decision = assess(
            structure,
            page_size=page_size,
            region_chars=region,
            page_lines=page_lines,
        )
        decisions.append(decision)
        if decision.accept:
            signals = decision.signals
            # A cell that says "this could not be read" is a fact about the grid the
            # reviewer has to see, whatever built it. Every reader that marks a cell
            # uncertain also asks for review today; the gate is where that stops
            # depending on each builder remembering.
            if signals.uncertain and not structure.requires_review:
                structure.requires_review = True
                structure.add_note(
                    f"{signals.uncertain} cell(s) in this grid could not be read or "
                    "checked against the page"
                )
            if signals.layout != "digital" and signals.unclaimed_slots:
                # Ragged rows are worth showing but not trusting: the grid's own
                # shape says more positions exist than any cell claims.
                structure.requires_review = True
                structure.add_note(
                    f"{signals.unclaimed_slots} grid position(s) hold no cell, so "
                    "the rows are shorter than the grid the engine reports"
                )
            # Boxes to check against but nothing to check them with: the grid is
            # kept, yet no cell here was confirmed by the page's own reading.
            if (
                signals.corroborated is None
                and signals.boxed > 0
                and signals.page_lines == 0
                and signals.layout != "digital"
            ):
                structure.requires_review = True
                structure.add_note(
                    "the page gave no recognised line geometry, so none of these "
                    "cells could be checked against the page's own text"
                )
            accepted.append(structure)
            kept.append(structure)
        else:
            kept.append(rejection_record(structure, decision))
    return Screening(accepted=accepted, kept=kept, decisions=decisions)
