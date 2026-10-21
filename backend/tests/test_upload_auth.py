"""Upload authentication and document ownership.

POST /api/documents/upload used to accept anonymous callers and store
uploaded_by=NULL, which left every anonymous document invisible to the maker
whose Library filters on Document.uploaded_by and orphaned for the review
rules that compare the caller against the uploader.

Everything here runs against the throwaway SQLite database and the temp Chroma
directory that conftest binds, with the upload directory redirected into
tmp_path. The processing task is stubbed out: these tests assert who may upload
and who ends up owning the row, not what OCR does with the file.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document
from app.models.user import User
from tests.helpers import get_test_auth_headers

UPLOAD_BODY = {"file": ("auth_probe.pdf", b"%PDF-1.4 fake", "application/pdf")}
UPLOAD_DATA = {"language": "auto"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def stubbed_upload(tmp_path, monkeypatch):
    """Keep the acknowledgement path but run no pipeline: temp storage, no OCR, no indexing."""
    from app.api import documents as documents_api
    from app.services import upload_service

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(upload_service, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(documents_api, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(
        documents_api, "process_document_task", lambda **kwargs: None
    )
    monkeypatch.setattr(
        documents_api, "schedule_document_indexing", lambda document_id: None
    )
    return uploads_dir


def _account(email: str, role: str, department: str | None = None, is_active: bool = True):
    """Return (user_id, bearer headers) for a user row in the test database."""
    db = SessionLocal()
    try:
        headers = get_test_auth_headers(db, email=email, role=role, department=department)
        user = db.query(User).filter(User.email == email).first()
        if user.is_active != is_active:
            user.is_active = is_active
            db.commit()
        return user.id, headers
    finally:
        db.close()


def _upload(client, headers=None, data=None):
    kwargs = {"files": UPLOAD_BODY, "data": data or UPLOAD_DATA}
    if headers is not None:
        kwargs["headers"] = headers
    return client.post("/api/documents/upload", **kwargs)


def _owner_of(document_id: int):
    db = SessionLocal()
    try:
        row = db.query(Document).filter(Document.id == document_id).first()
        return row.uploaded_by if row else None
    finally:
        db.close()


def _seed_document(department: str | None, uploaded_by: int | None, name: str) -> int:
    db = SessionLocal()
    try:
        document = Document(
            original_filename=name,
            stored_filename=name,
            file_path=f"uploads/{name}",
            file_type="application/pdf",
            file_size=1024,
            status="APPROVED",
            department=department,
            uploaded_by=uploaded_by,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document.id
    finally:
        db.close()


class TestUploadRequiresAuthentication:
    def test_anonymous_upload_is_rejected_and_nothing_is_stored(self, client, stubbed_upload):
        response = _upload(client)

        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"

        db = SessionLocal()
        try:
            assert db.query(Document).count() == 0
        finally:
            db.close()
        assert list(stubbed_upload.iterdir()) == []

    def test_garbage_token_is_rejected(self, client, stubbed_upload):
        response = _upload(client, headers={"Authorization": "Bearer not-a-real-jwt"})

        assert response.status_code == 401
        assert "access token" in response.json()["detail"].lower()

    def test_deactivated_account_cannot_upload(self, client, stubbed_upload):
        _, headers = _account(
            "disabled-maker@docsentinel.test", "UPLOAD_MAKER", is_active=False
        )

        response = _upload(client, headers=headers)

        assert response.status_code == 403
        assert "deactivated" in response.json()["detail"].lower()

    def test_upload_checker_is_not_an_allowed_uploader(self, client, stubbed_upload):
        """The upload route is guarded by canUpload=Admin|Upload Maker in the UI;
        require_upload_maker is the API-layer half of that same rule."""
        _, headers = _account("checker@docsentinel.test", "UPLOAD_CHECKER")

        response = _upload(client, headers=headers)

        assert response.status_code == 403
        assert "Upload Maker privileges required" in response.json()["detail"]


class TestUploadPersistsTheAuthenticatedOwner:
    def test_admin_upload_owns_the_document(self, client, stubbed_upload):
        admin_id, headers = _account("owner-admin@docsentinel.test", "ADMIN")

        response = _upload(client, headers=headers)

        assert response.status_code == 201
        body = response.json()
        assert body["uploaded_by"] == admin_id
        assert _owner_of(body["id"]) == admin_id

    def test_non_admin_maker_owns_the_document(self, client, stubbed_upload):
        maker_id, headers = _account("maker-owner@docsentinel.test", "UPLOAD_MAKER")

        response = _upload(client, headers=headers)

        assert response.status_code == 201
        body = response.json()
        assert body["uploaded_by"] == maker_id
        assert _owner_of(body["id"]) == maker_id

    def test_uploaded_document_appears_in_its_owners_library(self, client, stubbed_upload):
        """A maker without a department only ever sees rows they uploaded, so an
        anonymous upload used to be invisible to everyone but an admin."""
        _, maker_headers = _account("listed-maker@docsentinel.test", "UPLOAD_MAKER")
        _, other_headers = _account(
            "other-maker@docsentinel.test", "UPLOAD_MAKER", department="IT"
        )

        doc_id = _upload(client, headers=maker_headers).json()["id"]

        mine = client.get("/api/documents", headers=maker_headers).json()["items"]
        theirs = client.get("/api/documents", headers=other_headers).json()["items"]

        assert [item["id"] for item in mine] == [doc_id]
        assert theirs == []

    def test_re_upload_records_a_new_owner(self, client, stubbed_upload):
        """Two makers uploading the same filename each keep their own row."""
        first_id, first_headers = _account("first-maker@docsentinel.test", "UPLOAD_MAKER")
        second_id, second_headers = _account("second-maker@docsentinel.test", "UPLOAD_MAKER")

        first = _upload(client, headers=first_headers).json()
        second = _upload(client, headers=second_headers).json()

        assert first["id"] != second["id"]
        assert _owner_of(first["id"]) == first_id
        assert _owner_of(second["id"]) == second_id


class TestUnauthorizedDocumentAccess:
    def test_other_department_maker_cannot_read_or_delete(self, client):
        owner_id, owner_headers = _account(
            "finance-maker@docsentinel.test", "UPLOAD_MAKER", department="FINANCE"
        )
        stranger_id, stranger_headers = _account(
            "hr-maker@docsentinel.test", "UPLOAD_MAKER", department="HR"
        )
        doc_id = _seed_document("FINANCE", owner_id, "finance_owned.pdf")

        blocked = client.get(f"/api/documents/{doc_id}", headers=stranger_headers)
        assert blocked.status_code == 403
        assert "permission to view" in blocked.json()["detail"]

        assert client.get(f"/api/documents/{doc_id}", headers=owner_headers).status_code == 200
        assert client.get(f"/api/documents/{doc_id}", headers=stranger_headers).status_code == 403

        assert client.delete(f"/api/documents/{doc_id}", headers=stranger_headers).status_code == 403
        assert _owner_of(doc_id) == owner_id
        assert stranger_id != owner_id

    def test_admin_reads_any_department(self, client):
        owner_id, _ = _account(
            "ops-maker@docsentinel.test", "UPLOAD_MAKER", department="OPERATIONS"
        )
        _, admin_headers = _account("reading-admin@docsentinel.test", "ADMIN")
        doc_id = _seed_document("OPERATIONS", owner_id, "ops_owned.pdf")

        assert client.get(f"/api/documents/{doc_id}", headers=admin_headers).status_code == 200

    def test_legacy_ownerless_document_keeps_the_existing_department_rules(self, client):
        """Rows created before this fix still hold uploaded_by=NULL and are not
        reassigned by it. Their access rules are exactly the old ones: readable by
        a maker in the same department, invisible to other departments, and
        deletable only by an admin."""
        _, admin_headers = _account("legacy-admin@docsentinel.test", "ADMIN")
        _, same_dept_headers = _account(
            "legal-maker@docsentinel.test", "UPLOAD_MAKER", department="LEGAL"
        )
        _, other_dept_headers = _account(
            "ops-legal-maker@docsentinel.test", "UPLOAD_MAKER", department="OPERATIONS"
        )
        doc_id = _seed_document("LEGAL", None, "legacy_orphan.pdf")

        assert client.get(f"/api/documents/{doc_id}", headers=admin_headers).status_code == 200
        assert client.get(f"/api/documents/{doc_id}", headers=same_dept_headers).status_code == 200
        assert client.get(f"/api/documents/{doc_id}", headers=other_dept_headers).status_code == 403

        assert client.delete(f"/api/documents/{doc_id}", headers=same_dept_headers).status_code == 403
        assert _owner_of(doc_id) is None

    def test_anonymous_caller_cannot_read_any_document(self, client):
        owner_id, _ = _account("private-maker@docsentinel.test", "UPLOAD_MAKER")
        doc_id = _seed_document(None, owner_id, "private.pdf")

        assert client.get(f"/api/documents/{doc_id}").status_code == 401
