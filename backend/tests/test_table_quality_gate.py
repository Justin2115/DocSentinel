"""The table quality gate: what it refuses, what it must keep, and what it leaves behind.

A refusal costs a page its table, so both directions are tested here: a grid built
on nothing must be refused, and a grid built on real evidence must not be. The
fixtures are synthetic grids made of cells, not documents, so nothing here encodes
an expected answer for any particular file.
"""

import pytest

from app.services import table_quality as tq
from app.services.table_structure import TableCell, TableStructure


def grid(
    *,
    rows=2,
    cols=2,
    cells=None,
    engine="Ruled-grid",
    source="scanned",
    page_number=1,
):
    structure = TableStructure(
        page_number=page_number,
        row_count=rows,
        col_count=cols,
        engine=engine,
        source=source,
    )
    for cell in cells or []:
        structure.cells.append(cell)
    return structure.refresh_flags()


def cell(
    row,
    col,
    text,
    *,
    box=None,
    confidence=None,
    from_page=True,
    uncertain=False,
    colspan=1,
    rowspan=1,
):
    return TableCell(
        row=row,
        col=col,
        text=text,
        bbox=box,
        colspan=colspan,
        rowspan=rowspan,
        confidence=confidence,
        from_page=from_page,
        uncertain=uncertain,
    )


def filled(rows, cols, *, box_size=20, text="x"):
    """A complete grid of page-read cells, each in its own box."""
    cells = []
    for r in range(rows):
        for c in range(cols):
            x = c * box_size
            y = r * box_size
            cells.append(
                cell(
                    r,
                    c,
                    f"{text}{r}{c}",
                    box=(x, y, x + box_size, y + box_size),
                    confidence=90.0,
                )
            )
    return cells


class TestRefusals:
    """Each measurable defect has its own reason, so a reviewer can act on it."""

    def test_grid_with_no_cells_is_refused(self):
        decision = tq.assess(grid(cells=[]))
        assert decision.accept is False
        assert "0 cell(s)" in decision.reason

    def test_cell_outside_the_reported_grid_is_refused(self):
        structure = grid(rows=2, cols=2, cells=[cell(0, 0, "a", box=(0, 0, 10, 10)),
                                                 cell(5, 1, "b", box=(0, 0, 10, 10))])
        decision = tq.assess(structure)
        assert decision.accept is False
        assert "outside the 2x2 grid" in decision.reason

    def test_two_cells_claiming_one_slot_is_refused(self):
        # Two cells report the same grid position, so one of them is invented.
        structure = grid(
            rows=2,
            cols=2,
            cells=[
                cell(0, 0, "a", box=(0, 0, 20, 20)),
                cell(0, 1, "b", box=(20, 0, 40, 20)),
                cell(1, 0, "c", box=(0, 20, 20, 40)),
                cell(1, 0, "d", box=(0, 20, 20, 40)),
            ],
        )
        decision = tq.assess(structure, page_size=(200, 200), page_lines=8)
        assert decision.accept is False
        assert decision.signals.duplicate_slots == 1
        assert "already holds" in decision.reason

    def test_boxes_off_the_page_are_refused(self):
        structure = grid(
            rows=2,
            cols=2,
            cells=[cell(0, 0, "a", box=(0, 0, 10, 10)),
                   cell(1, 1, "b", box=(900, 900, 960, 960))],
        )
        decision = tq.assess(structure, page_size=(200, 200), page_lines=8)
        assert decision.accept is False
        assert "outside the page" in decision.reason

    def test_overlapping_cell_boxes_are_refused(self):
        structure = grid(
            rows=2,
            cols=2,
            cells=[cell(0, 0, "a", box=(0, 0, 40, 40)),
                   cell(0, 1, "b", box=(2, 2, 42, 42))],
        )
        decision = tq.assess(structure, page_size=(200, 200), page_lines=8)
        assert decision.accept is False
        assert "cover the same part of the page" in decision.reason

    def test_grid_with_nothing_readable_is_refused(self):
        structure = grid(
            rows=3,
            cols=3,
            cells=[cell(r, c, "", box=(c * 20, r * 20, c * 20 + 20, r * 20 + 20),
                        from_page=False, uncertain=True)
                   for r in range(3)
                   for c in range(3)],
        )
        decision = tq.assess(structure, page_size=(200, 200), page_lines=4)
        assert decision.accept is False
        assert decision.reason == "no cell in the grid could be read"

    def test_cells_the_page_never_backed_are_refused_when_it_could_have(self):
        """Boxes plus page lines, yet no page text inside a single cell."""
        structure = grid(
            rows=2,
            cols=2,
            cells=[cell(r, c, "guess", box=(c * 20, r * 20, c * 20 + 20, r * 20 + 20),
                        from_page=False)
                   for r in range(2)
                   for c in range(2)],
        )
        decision = tq.assess(structure, page_size=(200, 200), page_lines=6)
        assert decision.accept is False
        assert "none of the" in decision.reason
        assert "could have been placed in 4 cell box(es)" in decision.reason

    def test_mostly_blank_ruled_grid_is_refused(self):
        cells = [
            cell(r, c, "header" if (r, c) == (0, 0) else "",
                 box=(c * 20, r * 20, c * 20 + 20, r * 20 + 20))
            for r in range(4)
            for c in range(4)
        ]
        structure = grid(rows=4, cols=4, cells=cells)
        decision = tq.assess(structure, page_size=(200, 200), page_lines=6)
        assert decision.accept is False
        assert "only 1 of 16 cells could be read" in decision.reason

    def test_grid_losing_the_pages_text_is_refused(self):
        structure = grid(rows=2, cols=2, cells=filled(2, 2))
        # The reader produced 200 characters inside this grid's own area; the grid
        # accounts for a handful of them.
        decision = tq.assess(
            structure, page_size=(200, 200), region_chars=200, page_lines=20
        )
        assert decision.accept is False
        assert "of the text the reader produced inside its own area" in decision.reason


class TestAcceptances:
    """Over-rejection is the other failure: a real table must survive the gate."""

    def test_complete_ruled_grid_is_accepted(self):
        structure = grid(rows=3, cols=3, cells=filled(3, 3))
        decision = tq.assess(
            structure,
            page_size=(200, 200),
            region_chars=sum(len(c.text) for c in structure.cells),
            page_lines=9,
        )
        assert decision.accept is True, decision.reason
        assert decision.signals.corroborated is True

    def test_digital_grid_with_empty_cells_is_accepted(self):
        """An empty cell in a text layer is empty in the document, not a failure."""
        cells = [cell(0, 0, "Subtotal", box=(0, 0, 50, 20), confidence=None)]
        cells += [
            cell(r, c, "", box=(c * 50, r * 20, c * 50 + 50, r * 20 + 20), confidence=None)
            for r in range(1, 5)
            for c in range(2)
        ]
        structure = grid(rows=5, cols=2, cells=cells, engine="PyMuPDF", source="digital")
        decision = tq.assess(structure, page_size=(200, 200), page_lines=40)
        assert decision.accept is True, decision.reason
        # Nothing was OCR'd, so no cell was measured and no confidence is reported.
        assert structure.confidence is None
        assert decision.signals.cell_fill < 0.5

    def test_model_grid_without_cell_boxes_is_accepted_but_unverifiable(self):
        """A borderless table is still a table: corroboration is impossible, not failed."""
        cells = [
            cell(0, 0, "Subtotal", box=None, from_page=False),
            cell(0, 1, "1,200.00", box=None, from_page=False),
            cell(1, 0, "GST", box=None, from_page=False),
            cell(1, 1, "180.00", box=None, from_page=False),
        ]
        structure = grid(rows=2, cols=2, cells=cells, engine="PP-Structure")
        decision = tq.assess(structure, page_size=(200, 200), page_lines=8)
        assert decision.accept is True, decision.reason
        assert decision.signals.corroborated is None
        assert decision.signals.corroboration_checkable is False

    def test_model_grid_with_page_corroborated_cells_is_accepted(self):
        cells = [
            cell(0, 0, "Subtotal", box=(0, 0, 40, 20), confidence=93.0),
            cell(0, 1, "1,200.00", box=(40, 0, 80, 20), confidence=95.0),
            cell(1, 0, "GST", box=(0, 20, 40, 40), confidence=91.0),
            cell(1, 1, "180.00", box=(40, 20, 80, 40), confidence=94.0),
        ]
        structure = grid(rows=2, cols=2, cells=cells, engine="PP-Structure")
        decision = tq.assess(
            structure, page_size=(200, 200), region_chars=40, page_lines=4
        )
        assert decision.accept is True, decision.reason
        assert structure.confidence == pytest.approx(93.25)

    def test_small_grid_is_judged_on_content_not_ratios(self):
        """Four cells cannot be measured against a fill ratio; only emptiness applies."""
        structure = grid(
            rows=2,
            cols=4,
            cells=[
                cell(0, 0, "Sl", box=(0, 0, 20, 20)),
                cell(0, 1, "Item", box=(20, 0, 40, 20)),
                cell(1, 0, "1", box=(0, 20, 20, 40)),
                cell(1, 1, "Rod", box=(20, 20, 40, 40)),
            ],
        )
        decision = tq.assess(structure, page_size=(200, 200), page_lines=6)
        assert decision.signals.cells < tq.MIN_GRID_FOR_RATIOS
        assert decision.accept is True, decision.reason


class TestScreening:
    def test_accepted_and_kept_differ_only_by_refusals(self):
        good = grid(rows=2, cols=2, cells=filled(2, 2))
        bad = grid(rows=2, cols=2, cells=[])
        result = tq.screen([good, bad], page_size=(200, 200), page_lines=8)
        assert result.accepted == [good]
        assert result.kept[0] is good
        assert result.kept[1].cells == []
        assert result.kept[1].requires_review is True
        assert len(result.rejected) == 1

    def test_rejection_record_keeps_the_measurement_and_names_the_fallback(self):
        bad = grid(rows=4, cols=3, cells=[])
        decision = tq.assess(bad, page_size=(200, 200), page_lines=8)
        record = tq.rejection_record(bad, decision)
        assert record.cells == []
        assert (record.row_count, record.col_count) == (4, 3)
        assert record.confidence is None
        assert record.requires_review is True
        assert "kept below instead" in record.note

    def test_boxes_without_any_page_geometry_are_kept_and_flagged(self):
        structure = grid(
            rows=2,
            cols=2,
            cells=[cell(r, c, "text", box=(c * 20, r * 20, c * 20 + 20, r * 20 + 20))
                   for r in range(2)
                   for c in range(2)],
        )
        result = tq.screen([structure], page_size=(200, 200), page_lines=0)
        assert len(result.accepted) == 1
        assert structure.requires_review is True
        assert "no recognised line geometry" in structure.note

    def test_page_lines_unknown_does_not_refuse_a_grid(self):
        structure = grid(
            rows=2,
            cols=2,
            cells=[cell(r, c, "text", box=(c * 20, r * 20, c * 20 + 20, r * 20 + 20),
                        from_page=False)
                   for r in range(2)
                   for c in range(2)],
        )
        decision = tq.assess(structure, page_size=(200, 200))
        assert decision.accept is True, decision.reason
        assert decision.signals.corroborated is None


class TestSignals:
    def test_spans_fill_every_slot_they_cover(self):
        structure = grid(
            rows=2,
            cols=3,
            cells=[
                cell(0, 0, "heading", box=(0, 0, 60, 20), colspan=3),
                cell(1, 0, "a", box=(0, 20, 20, 40)),
                cell(1, 1, "b", box=(20, 20, 40, 40)),
                cell(1, 2, "c", box=(40, 20, 60, 40)),
            ],
        )
        signals = tq.Signals.measure(structure, page_size=(200, 200), page_lines=4)
        assert signals.duplicate_slots == 0
        assert signals.row_fill == 1.0  # the colspan row is populated, not 1 of 3 blank
        # Four cells, but every one of the six grid positions is claimed by a span.
        assert signals.cells == 4
        assert signals.cell_fill == 1.0
        assert signals.unclaimed_slots == 0

    def test_ragged_rows_leave_grid_slots_unclaimed(self):
        """A 3-column grid whose second row has one cell is flagged, not hidden."""
        structure = grid(
            rows=2,
            cols=3,
            cells=[
                cell(0, 0, "a", box=(0, 0, 20, 20)),
                cell(0, 1, "b", box=(20, 0, 40, 20)),
                cell(0, 2, "c", box=(40, 0, 60, 20)),
                cell(1, 0, "d", box=(0, 20, 20, 40)),
            ],
        )
        result = tq.screen([structure], page_size=(200, 200), page_lines=4)
        signals = result.decisions[0].signals
        assert signals.unclaimed_slots == 2
        assert len(result.accepted) == 1
        assert structure.requires_review is True
        assert "hold no cell" in structure.note

    def test_ratios_and_agreement_are_reported_as_measured(self):
        structure = grid(rows=2, cols=2, cells=filled(2, 2))
        signals = tq.Signals.measure(
            structure, page_size=(200, 200), region_chars=12, page_lines=4
        )
        payload = signals.as_dict()
        assert payload["cell_fill"] == 1.0
        assert payload["line_agreement"] == 1.0
        assert payload["corroborated"] is True
        assert payload["page_lines"] == 4

    def test_agreement_is_none_without_region_text(self):
        structure = grid(rows=2, cols=2, cells=filled(2, 2))
        signals = tq.Signals.measure(structure)
        assert signals.line_agreement is None
        assert signals.as_dict()["line_agreement"] is None
