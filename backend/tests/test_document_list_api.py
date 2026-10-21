"""Contract for GET /api/documents - the page the "+" selector walks through.

The selector can only list every document the signed-in user may reach if the
endpoint keeps three properties: a 100-row page cap that the client must respect,
an order that is stable across pages, and role filtering applied before paging.
Rows are created in the throwaway database that conftest binds, so nothing here
touches production documents, tables or the production Chroma directory.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document
from app.models.user import User
from tests.helpers import get_test_auth_headers

# Mirrors Query(le=100) on the endpoint; asking for more is a 422, not a clamp.
PAGE_CAP = 100

FIXED_UPLOAD_TIME = datetime(2026, 1, 5, 9, 30)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(db, email, role, department=None):
    return get_test_auth_headers(db, email=email, role=role, department=department)


def _add_documents(
    count,
    department="LEGAL",
    uploaded_at=None,
    uploaded_by=None,
    assigned_checker=None,
):
    db = SessionLocal()
    ids = []
    try:
        for index in range(count):
            document = Document(
                original_filename=f"page_{index:03d}.pdf",
                stored_filename=f"page_{index:03d}.pdf",
                file_path=f"uploads/page_{index:03d}.pdf",
                file_type="application/pdf",
                file_size=1024,
                status="indexed",
                department=department,
                uploaded_by=uploaded_by,
                assigned_checker=assigned_checker,
                uploaded_at=uploaded_at
                or FIXED_UPLOAD_TIME + timedelta(minutes=index),
            )
            db.add(document)
            db.commit()
            db.refresh(document)
            ids.append(document.id)
        return ids
    finally:
        db.close()


def test_page_size_above_the_cap_is_rejected(client):
    """The selector used to ask for 200 rows, which this endpoint refuses."""
    db = SessionLocal()
    try:
        headers = _headers(db, "cap-admin@docsentinel.test", "ADMIN")
    finally:
        db.close()

    response = client.get("/api/documents", params={"skip": 0, "limit": 200}, headers=headers)

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"][-1] == "limit"
    assert error["ctx"] == {"le": PAGE_CAP}


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": -1}, {"limit": 101}, {"skip": -1}],
)
def test_out_of_range_pagination_is_rejected(client, params):
    db = SessionLocal()
    try:
        headers = _headers(db, "range-admin@docsentinel.test", "ADMIN")
    finally:
        db.close()

    assert client.get("/api/documents", params=params, headers=headers).status_code == 422


def test_a_cap_sized_page_is_accepted(client):
    _add_documents(PAGE_CAP + 5)
    db = SessionLocal()
    try:
        headers = _headers(db, "page-admin@docsentinel.test", "ADMIN")
    finally:
        db.close()

    response = client.get("/api/documents", params={"skip": 0, "limit": PAGE_CAP}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == PAGE_CAP + 5
    assert len(body["items"]) == PAGE_CAP
    assert body["limit"] == PAGE_CAP


def test_paging_covers_every_document_exactly_once(client):
    """Walking skip/limit must not drop or repeat a document."""
    created = _add_documents(PAGE_CAP + 7)
    db = SessionLocal()
    try:
        headers = _headers(db, "walk-admin@docsentinel.test", "ADMIN")
    finally:
        db.close()

    listed = []
    skip = 0
    while skip < PAGE_CAP + 7:
        response = client.get(
            "/api/documents", params={"skip": skip, "limit": PAGE_CAP}, headers=headers
        )
        assert response.status_code == 200
        items = response.json()["items"]
        if not items:
            break
        listed.extend(item["id"] for item in items)
        skip += PAGE_CAP

    assert sorted(listed) == sorted(created)
    assert len(listed) == len(set(listed))


def test_tied_upload_times_page_in_a_stable_order(client):
    """Documents that share a timestamp still get a total order to page by."""
    created = _add_documents(6, uploaded_at=FIXED_UPLOAD_TIME)
    db = SessionLocal()
    try:
        headers = _headers(db, "tie-admin@docsentinel.test", "ADMIN")
    finally:
        db.close()

    whole_library = client.get(
        "/api/documents", params={"skip": 0, "limit": PAGE_CAP}, headers=headers
    ).json()["items"]
    assert [item["id"] for item in whole_library] == sorted(created, reverse=True)

    paged = []
    for skip in range(0, 6, 2):
        page = client.get(
            "/api/documents", params={"skip": skip, "limit": 2}, headers=headers
        ).json()["items"]
        paged.extend(item["id"] for item in page)

    assert paged == [item["id"] for item in whole_library]


def test_anonymous_listing_is_rejected(client):
    """No token, no list: the endpoint must never become public."""
    response = client.get("/api/documents", params={"skip": 0, "limit": 100})

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_expired_or_invalid_token_is_rejected(client):
    response = client.get(
        "/api/documents",
        params={"skip": 0, "limit": 100},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired access token"


def test_deactivated_account_is_forbidden(client):
    db = SessionLocal()
    try:
        user = User(
            email="off@docsentinel.test",
            name="deactivated",
            role="ADMIN",
            department="LEGAL",
            is_active=False,
            password_hash="test_password_hash",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        headers = {
            "Authorization": f"Bearer {create_access_token({'sub': str(user.id), 'role': user.role})}"
        }
    finally:
        db.close()

    response = client.get("/api/documents", params={"skip": 0, "limit": 100}, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "User account is deactivated"


def test_an_empty_authorized_library_is_still_a_success(client):
    """A 200 with no rows must stay distinguishable from a failed request.

    The selector shows "nothing you can access is searchable" for this shape and
    an error note for the cases above; collapsing them would hide outages.
    """
    _add_documents(2, department="OPERATIONS")
    db = SessionLocal()
    try:
        headers = _headers(db, "empty-checker@docsentinel.test", "UPLOAD_CHECKER", "LEGAL")
    finally:
        db.close()

    response = client.get(
        "/api/documents", params={"skip": 0, "limit": PAGE_CAP}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_admin_lists_every_accessible_department(client):
    legal = _add_documents(3, department="LEGAL")
    operations = _add_documents(2, department="OPERATIONS")
    db = SessionLocal()
    try:
        headers = _headers(db, "rbac-admin@docsentinel.test", "ADMIN")
    finally:
        db.close()

    body = client.get(
        "/api/documents", params={"skip": 0, "limit": PAGE_CAP}, headers=headers
    ).json()

    assert body["total"] == 5
    assert sorted(item["id"] for item in body["items"]) == sorted(legal + operations)


def test_maker_only_lists_its_own_department(client):
    legal = _add_documents(2, department="LEGAL")
    _add_documents(2, department="OPERATIONS")
    db = SessionLocal()
    try:
        headers = _headers(db, "rbac-maker@docsentinel.test", "UPLOAD_MAKER", "LEGAL")
    finally:
        db.close()

    body = client.get(
        "/api/documents", params={"skip": 0, "limit": PAGE_CAP}, headers=headers
    ).json()

    assert body["total"] == len(legal)
    assert {item["id"] for item in body["items"]} == set(legal)


def test_checker_only_lists_assigned_documents(client):
    db = SessionLocal()
    try:
        checker_email = "rbac-checker@docsentinel.test"
        headers = _headers(db, checker_email, "UPLOAD_CHECKER", "LEGAL")
        checker = db.query(User).filter(User.email == checker_email).first()
        assigned = _add_documents(1, department="LEGAL", assigned_checker=checker.id)
        _add_documents(1, department="OPERATIONS", assigned_checker=checker.id)
        _add_documents(1, department="LEGAL")
    finally:
        db.close()

    body = client.get(
        "/api/documents", params={"skip": 0, "limit": PAGE_CAP}, headers=headers
    ).json()

    assert body["total"] == len(assigned)
    assert {item["id"] for item in body["items"]} == set(assigned)
