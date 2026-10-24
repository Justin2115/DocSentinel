"""One frame for the page, the cell boxes and the lines that filled them.

Text read from a rotated or deskewed copy of a page is measured in that copy's
coordinates. A quarter turn has an exact inverse, so its grid is mapped back onto
the page the viewer shows; a deskew angle the pipeline never recorded does not, so
those boxes stay where they were measured and the structure says so instead of
claiming page coordinates.
"""

import pytest

from app.services.ocr_service import _place_grid_on_page, _rotation_box_map
from app.services.table_structure import TableCell, TableStructure

PAGE = (600, 800)  # width, height as the viewer sees the page


def structure(page_number: int = 1, *, with_cells: bool = True) -> TableStructure:
    cells = (
        [
            TableCell(row=0, col=0, text="Item", is_header=True, bbox=(10, 20, 110, 40), from_page=True),
            TableCell(row=0, col=1, text="Rate", is_header=True, bbox=(110, 20, 210, 40), from_page=True),
            TableCell(row=1, col=0, text="Steel", bbox=(10, 40, 110, 60), from_page=True),
            TableCell(row=1, col=1, text="100.00", bbox=(110, 40, 210, 60), from_page=True),
        ]
        if with_cells
        else []
    )
    return TableStructure(
        page_number=page_number,
        row_count=2,
        col_count=2,
        cells=cells,
        bbox=(10, 20, 210, 60),
        engine="PP-Structure",
        source="scanned",
    )


class TestRotationInverse:
    """A quarter turn inverts by arithmetic, not by approximation."""

    @pytest.mark.parametrize(
        "rot,expected",
        [
            (0, (10.0, 20.0, 210.0, 60.0)),
            (90, (20.0, 590.0, 60.0, 790.0)),
            (180, (390.0, 740.0, 590.0, 780.0)),
            (270, (540.0, 10.0, 580.0, 210.0)),
        ],
    )
    def test_grid_bbox_lands_on_the_untouched_page(self, rot, expected):
        mapped = _rotation_box_map(rot, PAGE)((10.0, 20.0, 210.0, 60.0))
        assert mapped == expected

    @pytest.mark.parametrize(
        "rot,measured",
        [
            (0, (600, 800)),
            (90, (800, 600)),
            (180, (600, 800)),
            (270, (800, 600)),
        ],
    )
    def test_mapped_boxes_stay_inside_the_displayed_page(self, rot, measured):
        """Whatever the reader measured inside the turned copy lands on the page."""
        mapped = _rotation_box_map(rot, PAGE)
        mw, mh = float(measured[0]), float(measured[1])
        for box in (
            (10.0, 20.0, 156.0, 78.0),
            (0.0, 0.0, mw, mh),
            (mw - 5.0, mh - 5.0, mw, mh),
        ):
            x1, y1, x2, y2 = mapped(box)
            assert x1 <= x2 and y1 <= y2
            assert -0.01 <= x1 and x2 <= PAGE[0] + 0.01
            assert -0.01 <= y1 and y2 <= PAGE[1] + 0.01

    @pytest.mark.parametrize("rot", [90, 180, 270])
    def test_a_line_inside_a_cell_stays_inside_it(self, rot):
        """Containment survives because the same mapping moves both sides.

        This is the property the reviewer depends on: the OCR line that filled a
        cell still sits in that cell once both reach the page frame.
        """
        mapped = _rotation_box_map(rot, PAGE)
        cell_box = (10.0, 20.0, 110.0, 40.0)
        line_box = (20.0, 25.0, 95.0, 35.0)
        cx1, cy1, cx2, cy2 = mapped(cell_box)
        lx1, ly1, lx2, ly2 = mapped(line_box)
        assert cx1 <= lx1 and lx2 <= cx2
        assert cy1 <= ly1 and ly2 <= cy2

    @pytest.mark.parametrize("rot", [90, 180, 270])
    def test_turning_and_turning_back_returns_the_original(self, rot):
        box = (12.0, 34.0, 156.0, 78.0)
        forward = _rotation_box_map(rot, PAGE)
        # The frame a quarter turn produced is the page with its sides swapped; a
        # half turn keeps them.
        turned = (PAGE[1], PAGE[0]) if rot in (90, 270) else PAGE
        back = _rotation_box_map((360 - rot) % 360, turned)
        assert tuple(round(v, 6) for v in back(forward(box))) == box


class TestPlaceGridOnPage:
    def test_rotated_read_is_mapped_into_page_frame(self):
        item = structure()
        _place_grid_on_page([item], 90, False, PAGE)
        assert item.frame == "page"
        assert item.note is None
        assert item.bbox == pytest.approx((20.0, 590.0, 60.0, 790.0))
        assert item.cells[0].bbox == pytest.approx((20.0, 690.0, 40.0, 790.0))
        assert item.cells[3].bbox == pytest.approx((40.0, 590.0, 60.0, 690.0))

    def test_unrotated_read_is_left_exactly_where_it_was_measured(self):
        item = structure()
        _place_grid_on_page([item], None, False, PAGE)
        assert item.frame == "page"
        assert item.bbox == (10, 20, 210, 60)

    def test_deskewed_read_says_which_frame_it_is_in(self):
        item = structure()
        _place_grid_on_page([item], None, True, PAGE)
        assert item.frame == "corrected"
        assert "deskewed frame" in item.note
        # Nothing recorded the angle, so nothing invents an inverse transform:
        # the cells and the lines that filled them keep the frame they share.
        assert item.cells[0].bbox == (10, 20, 110, 40)

    def test_refused_record_still_names_the_area_it_was_measured_over(self):
        item = structure(with_cells=False)
        _place_grid_on_page([item], 90, False, PAGE)
        assert item.cells == []
        assert item.frame == "page"
        assert item.bbox == pytest.approx((20.0, 590.0, 60.0, 790.0))

    def test_nothing_measured_is_nothing_to_move(self):
        item = structure(with_cells=False)
        item.bbox = None
        _place_grid_on_page([item], 90, True, PAGE)
        assert item.bbox is None
        assert item.frame == "page"
        assert item.note is None

    def test_existing_note_survives_the_frame_note(self):
        item = structure()
        item.add_note("3 cell(s) hold digits the reader could not resolve")
        _place_grid_on_page([item], None, True, PAGE)
        assert item.note.startswith("3 cell(s) hold digits")
        assert "deskewed frame" in item.note

    def test_several_grids_on_one_page_all_move(self):
        first, second = structure(1), structure(1)
        _place_grid_on_page([first, second], 270, False, PAGE)
        assert first.bbox == second.bbox == pytest.approx((540.0, 10.0, 580.0, 210.0))
        assert all(item.frame == "page" for item in (first, second))


class TestStoredFrameReadBack:
    """The frame a stored grid was measured in comes back with it."""

    def test_deskew_note_is_read_as_a_corrected_frame(self):
        from app.api.documents import _merged_extraction_pages
        from app.db import session as db_session
        from app.models.document import Document, DocumentPage, ExtractedTable, OCRResult

        item = structure()
        _place_grid_on_page([item], None, True, PAGE)
        payload = item.to_payload()
        db = db_session.SessionLocal()
        try:
            document = Document(
                original_filename="scan.pdf",
                stored_filename="scan.pdf",
                file_path="uploads/scan.pdf",
                file_type="application/pdf",
            )
            db.add(document)
            db.flush()
            page = DocumentPage(
                document_id=document.id, page_number=1, width=600, height=800
            )
            db.add(page)
            db.flush()
            db.add(
                OCRResult(
                    document_id=document.id,
                    page_id=page.id,
                    extracted_text="Item | Rate",
                    confidence=88.0,
                    ocr_engine="PaddleOCR",
                )
            )
            db.add(
                ExtractedTable(
                    document_id=document.id,
                    page_id=page.id,
                    page_number=1,
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
            result = _merged_extraction_pages(db, document.id)
        finally:
            db.close()
        rebuilt = result.pages[0].tables[0]
        assert rebuilt.frame == "corrected"
        assert rebuilt.cells[0].bbox == (10.0, 20.0, 110.0, 40.0)
        assert "deskewed frame" in rebuilt.note
