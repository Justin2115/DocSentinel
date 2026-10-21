"""The uploader's queue is read back from the database.

The upload screen used to rebuild its status cards from sessionStorage, so a
second tab or a cleared session lost every card. These tests cover the endpoint
that serves the queue from persisted rows instead: whose documents appear, in
what order, and what each card is told about its state.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.models.user import User
from tests.helpers import get_test_auth_headers


def _db_now() -> datetime:
    from app.api.documents import _db_now as doc_db_now

    db = SessionLocal()
    try:
        return doc_db_now(db)
    finally:
        db.close()


def _user_id(email: str) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None, "test user was not created"
        return user.id
    finally:
        db.close()


def _headers(email: str, role: str = "UPLOAD_MAKER") -> dict[str, str]:
    db = SessionLocal()
    try:
        return get_test_auth_headers(db, email, role)
    finally:
        db.close()


def _account(email: str) -> tuple[dict[str, str], int]:
    """Headers for a test user that is guaranteed to exist, plus its id."""
    headers = _headers(email)
    return headers, _user_id(email)


def _make_upload(
    *,
    uploaded_by: int | None,
    status: str = "PROCESSING",
    filename: str = "queue-sample.pdf",
    department: str | None = "Finance",
    document_type: str = "Invoice",
    pages: list[tuple[int, str]] | None = None,
) -> int:
    db = SessionLocal()
    try:
        document = Document(
            original_filename=filename,
            stored_filename=filename,
            file_path=f"uploads/{filename}",
            file_type="application/pdf",
            file_size=1024,
            status=status,
            department=department,
            document_type=document_type,
            uploaded_by=uploaded_by,
            uploaded_at=_db_now(),
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        for page_number, text in pages or []:
            page = DocumentPage(
                document_id=document.id,
                page_number=page_number,
                page_path=document.file_path,
            )
            db.add(page)
            db.flush()
            db.add(
                OCRResult(
                    document_id=document.id,
                    page_id=page.id,
                    extracted_text=text,
                    confidence=90.0,
                    ocr_engine="PyMuPDF+Fake",
                )
            )
        db.commit()
        return document.id
    finally:
        db.close()


QUEUE_URL = "/api/documents/uploads/recent"


def _index_chunks(document_id: int) -> None:
    """Record that vectors exist for a document without touching a real store."""
    from app.models.embedding import EmbeddingIndex

    db = SessionLocal()
    try:
        db.add(
            EmbeddingIndex(
                document_id=document_id,
                page_number=1,
                chunk_index=0,
                vector_db_ref="test/chunk-0",
            )
        )
        db.commit()
    finally:
        db.close()


class TestUploadQueueScope:
    def test_only_the_caller_own_uploads_are_listed(self):
        mine_headers, mine_id = _account("queue-owner@docsentinel.test")
        theirs_headers, theirs_id = _account("queue-other@docsentinel.test")
        mine = _make_upload(uploaded_by=mine_id)
        theirs = _make_upload(uploaded_by=theirs_id, filename="other-person.pdf")

        body = TestClient(app).get(QUEUE_URL, headers=mine_headers).json()
        ids = [item["document_id"] for item in body["items"]]

        assert mine in ids
        assert theirs not in ids
        # The other account sees its own document and not the first one.
        other_body = TestClient(app).get(QUEUE_URL, headers=theirs_headers).json()
        other_ids = [item["document_id"] for item in other_body["items"]]
        assert theirs in other_ids
        assert mine not in other_ids

    def test_unowned_legacy_uploads_are_not_in_anyone_s_queue(self):
        """Rows stored before uploads required an account belong to nobody."""
        orphan = _make_upload(uploaded_by=None, filename="legacy.pdf")

        for email in ("queue-owner@docsentinel.test", "queue-other@docsentinel.test"):
            body = TestClient(app).get(QUEUE_URL, headers=_headers(email)).json()
            assert orphan not in [item["document_id"] for item in body["items"]]

    def test_queue_requires_authentication(self):
        response = TestClient(app).get(QUEUE_URL)
        assert response.status_code in (401, 403)


class TestUploadQueuePayload:
    def test_newest_upload_comes_first_with_its_own_identity(self):
        email = "queue-order@docsentinel.test"
        headers, owner = _account(email)
        older = _make_upload(uploaded_by=owner, filename="older.pdf")
        newer = _make_upload(uploaded_by=owner, filename="newer.pdf")

        body = TestClient(app).get(QUEUE_URL, headers=headers).json()
        items = {item["document_id"]: item for item in body["items"]}

        assert [item["document_id"] for item in body["items"]].index(newer) < (
            [item["document_id"] for item in body["items"]].index(older)
        )
        assert items[newer]["filename"] == "newer.pdf"
        assert items[newer]["department"] == "Finance"
        assert items[newer]["document_type"] == "Invoice"
        assert items[newer]["uploaded_at"] is not None

    def test_limit_caps_the_queue(self):
        email = "queue-limit@docsentinel.test"
        headers, owner = _account(email)
        for index in range(3):
            _make_upload(uploaded_by=owner, filename=f"bulk-{index}.pdf")

        body = TestClient(app).get(f"{QUEUE_URL}?limit=2", headers=headers).json()
        assert len(body["items"]) == 2

    def test_in_progress_document_reports_a_simple_status(self):
        """Cards are re-read every couple of seconds; the line must stay readable."""
        from app.api.documents import claim_document_processing, release_document_processing

        email = "queue-running@docsentinel.test"
        headers, owner = _account(email)
        doc_id = _make_upload(
            uploaded_by=owner,
            pages=[(1, "first page text"), (2, "second page text")],
        )
        claim_document_processing(doc_id)
        try:
            item = next(
                entry
                for entry in TestClient(app)
                .get(QUEUE_URL, headers=headers)
                .json()["items"]
                if entry["document_id"] == doc_id
            )
        finally:
            release_document_processing(doc_id)
        message = item["processing"]["message"].lower()

        assert item["processing"]["is_running"] is True
        assert item["processing"]["stage"] == "reading_text"
        assert "page" not in message
        # The counts are still available to anyone who needs them, just not in the
        # sentence the card shows.
        assert item["processing"]["pages_completed"] == 2

    def test_failed_document_stays_in_the_queue_with_its_reason(self):
        email = "queue-failed@docsentinel.test"
        headers, owner = _account(email)
        doc_id = _make_upload(
            uploaded_by=owner,
            status="FAILED",
            filename="broken.pdf",
        )

        item = next(
            entry
            for entry in TestClient(app)
            .get(QUEUE_URL, headers=headers)
            .json()["items"]
            if entry["document_id"] == doc_id
        )
        assert item["processing"]["stage"] == "failed"
        assert item["filename"] == "broken.pdf"

    def test_settled_document_is_listed_after_a_reload(self):
        """Completion must not erase the card: it is the record of the run."""
        email = "queue-done@docsentinel.test"
        headers, owner = _account(email)
        doc_id = _make_upload(
            uploaded_by=owner,
            status="completed",
            pages=[(1, "digital text layer")],
        )
        _index_chunks(doc_id)

        client = TestClient(app)
        first = client.get(QUEUE_URL, headers=headers).json()
        second = client.get(QUEUE_URL, headers=headers).json()

        assert [i["document_id"] for i in first["items"]] == [
            i["document_id"] for i in second["items"]
        ]
        item = next(i for i in second["items"] if i["document_id"] == doc_id)
        assert item["processing"]["stage"] == "done"

    def test_completed_without_chunks_is_not_claimed_as_searchable(self):
        """A row that says completed but has no vectors must not read as indexed."""
        email = "queue-nochunks@docsentinel.test"
        headers, owner = _account(email)
        doc_id = _make_upload(
            uploaded_by=owner,
            status="completed",
            filename="not-indexed.pdf",
            pages=[(1, "digital text layer")],
        )

        item = next(
            entry
            for entry in TestClient(app)
            .get(QUEUE_URL, headers=headers)
            .json()["items"]
            if entry["document_id"] == doc_id
        )
        assert item["processing"]["searchable"] is False
        assert item["processing"]["stage"] == "making_searchable"
        assert "searchable" not in item["processing"]["message"]
