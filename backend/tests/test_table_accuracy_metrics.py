"""Measured rates over a fixture battery: cells, gate refusals and labelled fields.

The other table tests each hold one behaviour. This file counts: it runs a battery
of fixtures, compares what the pipeline returned against what the fixture declares,
and reports the rate for each measure. The measures are:

* exact cell accuracy - a slot counts only when its text matches the printed text
  character for character after Unicode normalisation; position is part of the answer
* numeric-cell accuracy - the same, over slots whose printed text is a number, which
  is where a wrong reading is dangerous
* table rejection rate - grids built on nothing that the gate refused
* false-rejection rate - real tables the gate refused, counted against the gate
* field accuracy - labelled values extracted from synthetic pages, over the fields
  each page declares

What these measure is the pipeline's own handling of grids, text placement and
labels. They are not OCR accuracy on real documents: the scanned batteries replace
the recogniser with lines of known text and position, so a rate here says nothing
about a model's character errors, and no real-document accuracy figure is implied.
Real-engine error rates are not measured by this file and are not claimed anywhere.

Nothing here touches the production database, uploads or indexes; conftest.py binds
a throwaway SQLite file and a temporary Chroma directory.
"""

from __future__ import annotations

import re
import unicodedata

import pytest

from app.services import ocr_service
from app.services import table_quality as tq
from app.services.extraction_service import (
    DocumentExtractionResult,
    ExtractedPage,
    extract_document_report,
)
from app.services.table_structure import TableCell, TableStructure
from tests.test_document_type_coverage import (
    AADHAAR,
    BANK_STATEMENT,
    CONTRACT,
    INVOICE,
    NEUTRAL_PAGE,
    POLICY,
    RESUME,
)
from tests.test_table_ground_truth import (
    BLANK,
    COLSPAN,
    PLAIN4,
    ROWSPAN,
    _ruled,
    _write_grid_pdf,
    _digital_structures,
)

# name -> fixture spec. Each is one grid on one page, so a returned structure can
# only be scored against its own declared grid.
GRID_FIXTURES = {
    "plain 4x4": PLAIN4,
    "colspan row": COLSPAN,
    "rowspan cell": ROWSPAN,
    "blank cells": BLANK,
}

DIGITAL_PAGE_SIZE = (1360, 630)

# Every number this file measures, filled in as the batteries run.
MEASURED: dict[str, dict] = {}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "").strip())


def _expected_slots(spec: dict) -> dict[tuple[int, int], str]:
    """Every slot of the drawn grid and what is printed in it.

    A span owns the slots it covers, so those positions are not cells of their own
    and are left out rather than counted as blanks. A position the fixture prints
    nothing in is an empty cell and stays in, because the reader is expected to
    report it as empty.
    """
    rows = len(spec["row_bounds"]) - 1
    cols = len(spec["col_bounds"]) - 1
    covered: dict[tuple[int, int], str] = {}
    swallowed: set[tuple[int, int]] = set()
    for (row, col), (text, colspan, rowspan) in spec["cells"].items():
        covered[(row, col)] = text
        for dr in range(rowspan):
            for dc in range(colspan):
                if (dr, dc) == (0, 0):
                    continue
                swallowed.add((row + dr, col + dc))
    expected = {
        (row, col): ""
        for row in range(rows)
        for col in range(cols)
        if (row, col) not in covered and (row, col) not in swallowed
    }
    expected.update(covered)
    return {key: _norm(value) for key, value in expected.items()}


_DIGIT_RUN = re.compile(r"[0-9\u0966-\u096F]")


def _is_numeric(text: str) -> bool:
    """Whether a cell's printed text is mostly a number.

    "15,200.00", "40", "50%" and "१,२००.००" are; "Clause 3 of 5 boxes" is not.
    """
    if not text:
        return False
    digits = _DIGIT_RUN.findall(text)
    body = re.sub(r"\s", "", text)
    return bool(digits) and len(digits) >= 0.6 * len(body)


def _score(structure: TableStructure, expected: dict) -> dict:
    """Compare one returned grid against the slots the fixture declared."""
    got = {(cell.row, cell.col): _norm(cell.text) for cell in structure.cells}
    matched = [key for key, text in expected.items() if got.get(key) == text]
    wrong = [
        (key, expected[key], got[key])
        for key, text in expected.items()
        if key in got and got[key] != text
    ]
    lost = [key for key in expected if key not in got]
    invented = [
        (key, text) for key, text in got.items() if key not in expected and text
    ]
    numeric_keys = [key for key, text in expected.items() if _is_numeric(text)]
    numeric_ok = [key for key in numeric_keys if got.get(key) == expected[key]]
    return {
        "slots": len(expected),
        "matched": len(matched),
        "wrong": wrong,
        "lost": lost,
        "invented": invented,
        "numeric_slots": len(numeric_keys),
        "numeric_matched": len(numeric_ok),
        "cells_returned": len(structure.cells),
    }


def _aggregate(name: str, scores: dict[str, dict]) -> dict:
    slots = sum(item["slots"] for item in scores.values())
    matched = sum(item["matched"] for item in scores.values())
    numeric = sum(item["numeric_slots"] for item in scores.values())
    numeric_ok = sum(item["numeric_matched"] for item in scores.values())
    summary = {
        "fixtures": len(scores),
        "slots": slots,
        "exact_cell_accuracy": matched / slots if slots else 0.0,
        "numeric_cell_accuracy": numeric_ok / numeric if numeric else 0.0,
        "wrong_slots": sum(len(item["wrong"]) for item in scores.values()),
        "lost_slots": sum(len(item["lost"]) for item in scores.values()),
        "invented_slots": sum(len(item["invented"]) for item in scores.values()),
        "per_fixture": scores,
    }
    MEASURED[name] = summary
    return summary


def _no_model(monkeypatch):
    monkeypatch.setattr(ocr_service, "get_paddle_table_engine", lambda: None)


class TestDigitalCellAccuracy:
    """Real PDFs, drawn rule by rule, read by PyMuPDF through the pipeline's own path."""

    @pytest.fixture(scope="class")
    def pdf(self, tmp_path_factory):
        path = tmp_path_factory.mktemp("accuracy") / "grids.pdf"
        _write_grid_pdf(
            path,
            [
                {
                    "width": DIGITAL_PAGE_SIZE[0],
                    "height": DIGITAL_PAGE_SIZE[1],
                    "row_bounds": spec["row_bounds"],
                    "col_bounds": spec["col_bounds"],
                    "cells": spec["cells"],
                    "omit_v": spec.get("omit_v", frozenset()),
                    "omit_h": spec.get("omit_h", frozenset()),
                }
                for spec in GRID_FIXTURES.values()
            ],
        )
        return path

    def test_every_digital_grid_is_kept_as_a_grid(self, pdf):
        kept = _digital_structures(pdf)
        # A fixture whose grid the gate refused comes back with no cells; the
        # accuracy below would then collapse, but the reason is stated here.
        refused = [index for index, item in enumerate(kept) if not item[0].cells]
        assert refused == []
        assert len(kept) == len(GRID_FIXTURES)

    def test_digital_cell_accuracy_is_measured(self, pdf):
        scores = {}
        for (name, spec), structures in zip(GRID_FIXTURES.items(), _digital_structures(pdf)):
            structure = structures[0]
            expected = _expected_slots(spec)
            score = _score(structure, expected)
            assert score["wrong"] == [], f"{name}: {score['wrong']}"
            assert score["lost"] == [], f"{name}: lost {score['lost']}"
            assert score["invented"] == [], f"{name}: invented {score['invented']}"
            scores[name] = score
        summary = _aggregate("digital", scores)
        assert summary["exact_cell_accuracy"] == 1.0
        assert summary["numeric_cell_accuracy"] == 1.0
        assert summary["slots"] >= 40

    def test_digital_cells_carry_no_invented_accuracy(self, pdf):
        """A text layer was not read from pixels, so nothing here is a measured score."""
        for structures in _digital_structures(pdf):
            structure = structures[0]
            assert all(cell.confidence is None for cell in structure.cells)
            assert structure.confidence is None


class TestScannedGeometryCellAccuracy:
    """Ruling measured from ink; the recogniser stands in with known lines.

    Isolating the recogniser is deliberate: these rates measure whether the geometry
    puts each string in the cell it was printed in, and say nothing about how well a
    model reads characters.
    """

    def _scan(self, monkeypatch, spec):
        _no_model(monkeypatch)
        image, _truth, lines = _ruled(**spec)
        structures = ocr_service._extract_scanned_tables(
            image, devanagari_ocr=False, ocr_lines=lines, page_number=1
        )[0]
        return structures[0]

    def test_scanned_cell_accuracy_is_measured(self, monkeypatch):
        scores = {}
        for name, spec in GRID_FIXTURES.items():
            structure = self._scan(monkeypatch, spec)
            expected = _expected_slots(spec)
            score = _score(structure, expected)
            assert score["wrong"] == [], f"{name}: {score['wrong']}"
            assert score["lost"] == [], f"{name}: lost {score['lost']}"
            assert score["invented"] == [], f"{name}: invented {score['invented']}"
            scores[name] = score
        summary = _aggregate("scanned", scores)
        assert summary["exact_cell_accuracy"] == 1.0
        assert summary["numeric_cell_accuracy"] == 1.0

    def test_a_number_damaged_in_the_reading_is_counted_not_repaired(self, monkeypatch):
        """The rate has to survive a bad read: the cell is flagged, never rewritten."""
        _no_model(monkeypatch)
        image, _truth, lines = _ruled(**PLAIN4)
        for line in lines:
            if line[1][0] == "15,200.00":
                line[1][0] = "15,2O0.00"
        structure = ocr_service._extract_scanned_tables(
            image, devanagari_ocr=False, ocr_lines=lines, page_number=1
        )[0][0]
        score = _score(structure, _expected_slots(PLAIN4))
        # One slot of sixteen reads wrong, and the pipeline knows it is wrong:
        # the accuracy drops and the cell asks for review instead of being fixed.
        assert score["wrong"] == [((1, 3), "15,200.00", "15,2O0.00")]
        assert score["matched"] == 15
        assert structure.cells, "kept as a table, not thrown away for one bad digit"
        assert [cell.text for cell in structure.cells if cell.uncertain] == ["15,2O0.00"]


def _grid(rows, cols, cells, *, engine="Ruled-grid", source="scanned"):
    structure = TableStructure(
        page_number=1, row_count=rows, col_count=cols, engine=engine, source=source
    )
    structure.cells.extend(cells)
    return structure.refresh_flags()


def _boxed(row, col, text, *, size=20, confidence=90.0, from_page=True, uncertain=False):
    x, y = col * size, row * size
    return TableCell(
        row,
        col,
        text,
        bbox=(x, y, x + size, y + size),
        confidence=confidence,
        from_page=from_page,
        uncertain=uncertain,
    )


def _complete(rows, cols, **kwargs):
    return [
        _boxed(r, c, f"{chr(97 + r)}{c}", **kwargs)
        for r in range(rows)
        for c in range(cols)
    ]


class TestGateRejectionRate:
    """Every grid built on nothing, counted as a refusal rate."""

    CASES = {
        "no cells at all": (_grid(3, 3, []), {}),
        "cell outside its own grid": (
            _grid(2, 2, [_boxed(0, 0, "a"), _boxed(5, 1, "b")]),
            {"page_size": (200, 200), "page_lines": 8},
        ),
        "two cells in one slot": (
            _grid(2, 2, [_boxed(0, 0, "a"), _boxed(0, 1, "b"), _boxed(1, 0, "c"),
                         _boxed(1, 0, "d")]),
            {"page_size": (200, 200), "page_lines": 8},
        ),
        "boxes off the page": (
            _grid(2, 2, [_boxed(0, 0, "a"), TableCell(1, 1, "b", bbox=(900, 900, 960, 960))]),
            {"page_size": (200, 200), "page_lines": 8},
        ),
        "cells covering the same ink": (
            _grid(2, 2, [_boxed(0, 0, "a", size=40),
                         TableCell(0, 1, "b", bbox=(2, 2, 42, 42), confidence=90.0)]),
            {"page_size": (200, 200), "page_lines": 8},
        ),
        "nothing readable": (
            _grid(3, 3, [_boxed(r, c, "", from_page=False, uncertain=True)
                         for r in range(3) for c in range(3)]),
            {"page_size": (200, 200), "page_lines": 4},
        ),
        "text the page never backed": (
            _grid(2, 2, [_boxed(r, c, "guess", from_page=False)
                         for r in range(2) for c in range(2)]),
            {"page_size": (200, 200), "page_lines": 6},
        ),
        "ruled grid mostly blank": (
            _grid(4, 4, [_boxed(r, c, "x" if (r, c) == (0, 0) else "")
                         for r in range(4) for c in range(4)]),
            {"page_size": (200, 200), "page_lines": 6},
        ),
        "grid losing the page's own text": (
            _grid(2, 2, _complete(2, 2)),
            {"page_size": (200, 200), "region_chars": 200, "page_lines": 20},
        ),
    }

    def test_the_gate_refuses_every_grid_built_on_nothing(self):
        refused, reasons = 0, []
        for name, (structure, kwargs) in self.CASES.items():
            decision = tq.assess(structure, **kwargs)
            assert decision.accept is False, f"{name} was accepted"
            assert decision.reason, f"{name} refused without a reason"
            refused += 1
            reasons.append(decision.reason)
        MEASURED["rejection"] = {
            "grids": len(self.CASES),
            "refused": refused,
            "rate": refused / len(self.CASES),
            "reasons": reasons,
        }
        assert len(set(reasons)) == len(reasons), "each defect must name itself"

    def test_a_refusal_leaves_a_record_and_no_cells(self):
        structure = _grid(3, 3, [])
        decision = tq.assess(structure, page_size=(200, 200), page_lines=8)
        record = tq.rejection_record(structure, decision)
        assert record.cells == []
        assert record.confidence is None
        assert record.requires_review is True


class TestGateFalseRejectionRate:
    """The other direction, counted: real tables the gate must keep."""

    @staticmethod
    def _cases():
        damaged = _grid(
            2, 3,
            _complete(2, 3)[:-1] + [_boxed(1, 2, "15,2O0.00", uncertain=True,
                                           confidence=41.0)],
        )
        ragged = _grid(
            2, 3,
            [_boxed(0, 0, "a"), _boxed(0, 1, "b"), _boxed(0, 2, "c"), _boxed(1, 0, "d")],
        )
        return {
            "complete ruled grid": (
                _grid(3, 3, _complete(3, 3)), {"page_lines": 9}),
            "digital grid with empty cells": (
                _grid(5, 2,
                      [_boxed(0, 0, "Subtotal", confidence=None)]
                      + [_boxed(r, c, "", confidence=None)
                         for r in range(1, 5) for c in range(2)],
                      engine="PyMuPDF", source="digital"),
                {"page_lines": 40}),
            "borderless model grid": (
                _grid(2, 2, [TableCell(0, 0, "Subtotal"), TableCell(0, 1, "1,200.00"),
                             TableCell(1, 0, "GST"), TableCell(1, 1, "180.00")],
                      engine="PP-Structure"),
                {"page_lines": 8}),
            "model grid the page backs": (
                _grid(2, 2, [_boxed(0, 0, "Subtotal"), _boxed(0, 1, "1,200.00"),
                             _boxed(1, 0, "GST"), _boxed(1, 1, "180.00")],
                      engine="PP-Structure"),
                {"page_lines": 4}),
            "one damaged amount": (damaged, {"page_lines": 6}),
            "small grid judged on content": (
                _grid(2, 4, [_boxed(0, 0, "Sl"), _boxed(0, 1, "Item"),
                             _boxed(1, 0, "1"), _boxed(1, 1, "Rod")]),
                {"page_lines": 6}),
            "merged heading row": (
                _grid(2, 3, [TableCell(0, 0, "heading", bbox=(0, 0, 60, 20),
                                       colspan=3, confidence=90.0),
                             _boxed(1, 0, "a"), _boxed(1, 1, "b"), _boxed(1, 2, "c")]),
                {"page_lines": 4}),
            "ragged rows": (ragged, {"page_lines": 4}),
        }

    def test_the_gate_keeps_every_grid_built_on_evidence(self):
        refused = []
        kept = 0
        for name, (structure, extra) in self._cases().items():
            # The text the reader produced inside the grid's own area is the text
            # its cells hold; a case that pretends otherwise is a defect, not a table.
            region = None if structure.source == "digital" else sum(
                len(cell.text) for cell in structure.cells
            )
            decision = tq.assess(
                structure, page_size=(200, 200), region_chars=region, **extra
            )
            if decision.accept:
                kept += 1
            else:
                refused.append((name, decision.reason))
        MEASURED["false-rejection"] = {
            "grids": kept + len(refused),
            "refused": len(refused),
            "rate": len(refused) / (kept + len(refused)),
            "refused_names": refused,
        }
        assert refused == []

    def test_a_damaged_or_ragged_grid_is_kept_and_flagged_not_silently_shown(self):
        """Through the real path: the grid survives, and the reviewer is told."""
        damaged = _grid(
            2, 3,
            _complete(2, 3)[:-1] + [_boxed(1, 2, "15,2O0.00", uncertain=True)],
        )
        ragged = _grid(
            2, 3,
            [_boxed(0, 0, "a"), _boxed(0, 1, "b"), _boxed(0, 2, "c"), _boxed(1, 0, "d")],
        )
        for structure in (damaged, ragged):
            result = tq.screen([structure], page_size=(200, 200), page_lines=6)
            assert result.accepted == [structure], result.decisions[0].reason
            assert structure.requires_review is True
            assert structure.cells, "a flag must not cost the page its table"
            assert structure.confidence is not None

    def test_an_unreadable_cell_reaches_the_reviewer_whichever_reader_made_it(self):
        """The gate, not each builder, is what promises an uncertain cell is reviewed."""
        structure = _grid(
            2, 2,
            [_boxed(0, 0, "Qty"), _boxed(0, 1, "12"),
             _boxed(1, 0, "Rate"), _boxed(1, 1, "1,5O0.00", uncertain=True)],
        )
        assert structure.requires_review is False, "the builder said nothing"
        tq.screen([structure], page_size=(200, 200), page_lines=4)
        assert structure.requires_review is True
        assert "could not be read or checked" in structure.note


class TestFieldAccuracyOnLabelledPages:
    """Fields the page states, counted against the fields the fixture declares.

    The denominator is the declared set only: a value the pipeline puts somewhere
    else, or a field the page never labels, is not scored here. That is what makes
    this a measurement of the rules it was built from rather than a claim about
    documents in general.
    """

    TRUTH = {
        "invoice": (
            INVOICE,
            {
                "invoice_number": "INV-2024-9981",
                "subtotal": "1,500.00",
                "tax": "270.00",
                "total_amount": "1,770.00",
                "due_date": "15/06/2024",
                "line_items_count": "2",
            },
        ),
        "aadhaar": (
            AADHAAR,
            {
                "aadhaar_number": "2345 6789 0123",
                "date_of_birth": "12/04/1990",
                "gender": "Male",
                "nationality": "Indian",
            },
        ),
        "contract": (
            CONTRACT,
            {
                "effective_date": "01/01/2024",
                "expiry_date": "31/12/2024",
                "governing_law": "State of New York",
            },
        ),
        "resume": (
            RESUME,
            {
                "full_name": "Priya Nair",
                "email": "priya.nair@example.com",
                "phone": "+91 98765 43210",
            },
        ),
        "bank statement": (
            BANK_STATEMENT,
            {
                "opening_balance": "42,500.00",
                "closing_balance": "112,500.00",
                "bank_identifier": "HDFC0000123",
                "statement_period": "01/04/2024 to 30/04/2024",
            },
        ),
        "policy": (
            POLICY,
            {
                "document_number": "PL-2024-778199",
                "sum_assured": "5,00,000.00",
                "premium": "38,400.00",
                "policyholder": "Suresh Iyer",
            },
        ),
    }

    def _report(self, text):
        page = ExtractedPage(page_number=1, text=text, confidence=95.0)
        return extract_document_report(
            DocumentExtractionResult(
                pages=[page],
                combined_text=text,
                overall_ocr_confidence=95.0,
                is_ocr_applied=True,
            )
        )

    def test_declared_field_values_are_measured(self):
        results = {}
        for name, (text, truth) in self.TRUTH.items():
            found = {f.field_name: f.field_value for f in self._report(text).fields}
            wrong = {
                key: (value, found.get(key))
                for key, value in truth.items()
                if found.get(key) != value
            }
            assert wrong == {}, f"{name}: {wrong}"
            results[name] = {"fields": len(truth), "matched": len(truth) - len(wrong)}
        total = sum(item["fields"] for item in results.values())
        ok = sum(item["matched"] for item in results.values())
        MEASURED["field"] = {
            "declared": total,
            "matched": ok,
            "accuracy": ok / total,
            "per_document": results,
        }
        assert MEASURED["field"]["accuracy"] == 1.0

    def test_a_page_labeling_nothing_yields_no_fields(self):
        report = self._report(NEUTRAL_PAGE)
        assert report.fields == []
        assert report.document_type == "General Document"
        assert report.requires_review is True

    def test_type_routing_is_measured(self):
        routes = {
            "invoice": ("Invoices", INVOICE),
            "aadhaar": ("ID Document", AADHAAR),
            "contract": ("Contracts", CONTRACT),
            "resume": ("Resumes", RESUME),
            "bank statement": ("Bank Statements", BANK_STATEMENT),
            "policy": ("Insurance Policy", POLICY),
            "notes": ("General Document", NEUTRAL_PAGE),
        }
        wrong = []
        for name, (expected, text) in routes.items():
            got = self._report(text).document_type
            if got != expected:
                wrong.append((name, expected, got))
        MEASURED["routing"] = {
            "pages": len(routes),
            "correct": len(routes) - len(wrong),
            "accuracy": (len(routes) - len(wrong)) / len(routes),
            "wrong": wrong,
        }
        assert wrong == []


class TestMeasuredRatesAreReported:
    """One place to read what the batteries above measured."""

    def test_report(self, capsys):
        for name in ("digital", "scanned", "rejection", "false-rejection", "field",
                     "routing"):
            assert name in MEASURED, f"{name} battery never ran"

        lines = ["", "measured on synthetic fixtures (not real-document accuracy):"]
        for name, data in MEASURED.items():
            if name in ("digital", "scanned"):
                lines.append(
                    f"  {name:<15} exact cell {data['exact_cell_accuracy']:.1%} "
                    f"of {data['slots']} slots, numeric "
                    f"{data['numeric_cell_accuracy']:.1%}, "
                    f"lost {data['lost_slots']}, invented {data['invented_slots']}"
                )
            elif name in ("rejection", "false-rejection"):
                lines.append(
                    f"  {name:<15} {data['refused']}/{data['grids']} grids = "
                    f"{data['rate']:.1%}"
                )
            else:
                denom = data.get("declared") or data.get("pages")
                num = data.get("matched") or data.get("correct")
                lines.append(f"  {name:<15} {num}/{denom} = {data['accuracy']:.1%}")
        report = "\n".join(lines)
        with capsys.disabled():
            print(report)
