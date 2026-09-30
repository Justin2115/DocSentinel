import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.dependencies import verify_document_review_access
from app.core.rbac import UserRole
from app.models.permission import FolderPermission


class FolderPermissionEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        FolderPermission.__table__.create(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_checker_review_is_blocked_by_folder_matrix(self):
        checker = SimpleNamespace(id=12, role=UserRole.UPLOAD_CHECKER.value, department=None)
        document = SimpleNamespace(
            uploaded_by=8,
            assigned_checker=12,
            department=None,
            document_type="Invoices",
        )
        with Session(self.engine) as db:
            db.add(FolderPermission(
                folder_name="Invoices",
                role=UserRole.UPLOAD_CHECKER.value,
                can_view=True,
                can_upload=False,
                can_review=False,
            ))
            db.commit()

            with self.assertRaises(HTTPException) as raised:
                verify_document_review_access(document, checker, db=db)

        self.assertEqual(raised.exception.status_code, 403)

    def test_admin_remains_unrestricted_by_folder_matrix(self):
        admin = SimpleNamespace(id=12, role=UserRole.ADMIN.value, department=None)
        document = SimpleNamespace(
            uploaded_by=8,
            assigned_checker=None,
            department=None,
            document_type="Invoices",
        )
        with Session(self.engine) as db:
            db.add(FolderPermission(
                folder_name="Invoices",
                role=UserRole.UPLOAD_CHECKER.value,
                can_view=False,
                can_upload=False,
                can_review=False,
            ))
            db.commit()
            verify_document_review_access(document, admin, db=db)

    def test_new_assignments_accept_only_supported_roles(self):
        self.assertEqual(UserRole.parse_assignment("Admin"), UserRole.ADMIN.value)
        self.assertEqual(UserRole.parse_assignment("Upload Maker"), UserRole.UPLOAD_MAKER.value)
        self.assertEqual(UserRole.parse_assignment("Upload Checker"), UserRole.UPLOAD_CHECKER.value)
        self.assertIsNone(UserRole.parse_assignment("Editor"))
        self.assertIsNone(UserRole.parse_assignment("Superuser"))


if __name__ == "__main__":
    unittest.main()
