import json
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rbac import Department, UserRole
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models.audit import ActivityLog
from app.models.document import Document, ReviewQueue
from app.models.user import User
from app.services.admin_service import init_system_settings_and_permissions
from app.services.auth_service import init_default_admin


class DepartmentRBACAndAuditTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with SessionLocal() as db:
            init_default_admin(db)
            init_system_settings_and_permissions(db)

            # Ensure clean super admin
            super_admin = db.query(User).filter(User.email == "admin@docsentinel.local").first()
            super_admin.role = UserRole.SUPER_ADMIN.value
            super_admin.is_active = True
            db.commit()
            cls.super_admin_id = super_admin.id

            # Helper to create test user
            def _create_user(email: str, name: str, role: str, dept: str | None, pw: str = "pass123"):
                u = db.query(User).filter(User.email == email).first()
                if not u:
                    u = User(
                        email=email,
                        name=name,
                        role=role,
                        department=dept,
                        password_hash=hash_password(pw),
                        is_active=True,
                    )
                    db.add(u)
                    db.commit()
                    db.refresh(u)
                else:
                    u.role = role
                    u.department = dept
                    u.password_hash = hash_password(pw)
                    u.is_active = True
                    db.commit()
                return u

            cls.finance_admin = _create_user("fin_admin@test.local", "Finance Admin", UserRole.DEPARTMENT_ADMIN.value, "FINANCE")
            cls.finance_checker = _create_user("fin_checker@test.local", "Finance Checker", UserRole.DEPARTMENT_CHECKER.value, "FINANCE")
            cls.finance_maker = _create_user("fin_maker@test.local", "Finance Maker", UserRole.DEPARTMENT_MAKER.value, "FINANCE")

            cls.hr_admin = _create_user("hr_admin@test.local", "HR Admin", UserRole.DEPARTMENT_ADMIN.value, "HR")
            cls.hr_checker = _create_user("hr_checker@test.local", "HR Checker", UserRole.DEPARTMENT_CHECKER.value, "HR")
            cls.hr_maker = _create_user("hr_maker@test.local", "HR Maker", UserRole.DEPARTMENT_MAKER.value, "HR")

            cls.fin_admin_id = cls.finance_admin.id
            cls.fin_checker_id = cls.finance_checker.id
            cls.fin_maker_id = cls.finance_maker.id
            cls.hr_admin_id = cls.hr_admin.id
            cls.hr_checker_id = cls.hr_checker.id
            cls.hr_maker_id = cls.hr_maker.id

        cls.client = TestClient(app)

        # Generate tokens
        def _token(u_id: int, email: str, role: str, dept: str | None):
            t = create_access_token({"sub": str(u_id), "email": email, "role": role, "name": email.split("@")[0]})
            return {"Authorization": f"Bearer {t}"}

        cls.super_admin_headers = _token(cls.super_admin_id, "admin@docsentinel.local", UserRole.SUPER_ADMIN.value, None)
        cls.fin_admin_headers = _token(cls.fin_admin_id, "fin_admin@test.local", UserRole.DEPARTMENT_ADMIN.value, "FINANCE")
        cls.fin_checker_headers = _token(cls.fin_checker_id, "fin_checker@test.local", UserRole.DEPARTMENT_CHECKER.value, "FINANCE")
        cls.fin_maker_headers = _token(cls.fin_maker_id, "fin_maker@test.local", UserRole.DEPARTMENT_MAKER.value, "FINANCE")
        cls.hr_admin_headers = _token(cls.hr_admin_id, "hr_admin@test.local", UserRole.DEPARTMENT_ADMIN.value, "HR")
        cls.hr_checker_headers = _token(cls.hr_checker_id, "hr_checker@test.local", UserRole.DEPARTMENT_CHECKER.value, "HR")
        cls.hr_maker_headers = _token(cls.hr_maker_id, "hr_maker@test.local", UserRole.DEPARTMENT_MAKER.value, "HR")

    def setUp(self):
        """Create test documents inside setUp so clean_document_tables fixture does not wipe them."""
        with SessionLocal() as db:
            fin_doc = Document(
                original_filename="finance_q3.pdf",
                stored_filename="stored_finance_q3.pdf",
                file_path="uploads/finance_q3.pdf",
                file_type="application/pdf",
                file_size=1024,
                status="indexed",
                department="FINANCE",
                uploaded_by=self.fin_maker_id,
            )
            hr_doc = Document(
                original_filename="hr_benefits.pdf",
                stored_filename="stored_hr_benefits.pdf",
                file_path="uploads/hr_benefits.pdf",
                file_type="application/pdf",
                file_size=1024,
                status="indexed",
                department="HR",
                uploaded_by=self.hr_maker_id,
            )
            db.add_all([fin_doc, hr_doc])
            db.commit()
            db.refresh(fin_doc)
            db.refresh(hr_doc)
            self.fin_doc_id = fin_doc.id
            self.hr_doc_id = hr_doc.id

    # ========================================================
    # 1. ALL FOUR ROLES AND ALLOWED / FORBIDDEN OPERATIONS
    # ========================================================
    def test_01_super_admin_system_wide_access(self):
        resp = self.client.get("/api/admin/users", headers=self.super_admin_headers)
        self.assertEqual(resp.status_code, 200)
        users = resp.json()
        depts = {u.get("department") for u in users if u.get("department")}
        self.assertIn("FINANCE", depts)
        self.assertIn("HR", depts)

    def test_01_department_admin_restricted_to_own_department(self):
        resp = self.client.get("/api/admin/users", headers=self.fin_admin_headers)
        self.assertEqual(resp.status_code, 200)
        users = resp.json()
        for u in users:
            if u.get("department"):
                self.assertEqual(u["department"].upper(), "FINANCE")

    def test_01_maker_and_checker_forbidden_from_admin_user_management(self):
        resp_maker = self.client.get("/api/admin/users", headers=self.fin_maker_headers)
        self.assertEqual(resp_maker.status_code, 403)

        resp_checker = self.client.get("/api/admin/users", headers=self.fin_checker_headers)
        self.assertEqual(resp_checker.status_code, 403)

    # ========================================================
    # 2. FINANCE CHECKER ATTEMPTING CROSS-DEPARTMENT ACCESS
    # ========================================================
    def test_02_finance_checker_cannot_access_hr_documents(self):
        # Accessing HR document details
        resp = self.client.get(f"/api/documents/{self.hr_doc_id}", headers=self.fin_checker_headers)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("department", resp.json()["detail"].lower())

        # Accessing HR document processing status
        resp_status = self.client.get(f"/api/documents/{self.hr_doc_id}/processing", headers=self.fin_checker_headers)
        self.assertEqual(resp_status.status_code, 403)

        # Attempting to approve HR document
        resp_app = self.client.post(f"/api/documents/{self.hr_doc_id}/approve", headers=self.fin_checker_headers)
        self.assertEqual(resp_app.status_code, 403)

    def test_02_finance_checker_can_access_finance_document(self):
        resp = self.client.get(f"/api/documents/{self.fin_doc_id}", headers=self.fin_checker_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], self.fin_doc_id)

    # ========================================================
    # 3. MAKER ATTEMPTING REVIEW QUEUE AND VERIFICATION APIS
    # ========================================================
    def test_03_maker_forbidden_from_review_queue_and_actions(self):
        # Review queue list
        resp = self.client.get("/api/documents/review/queue", headers=self.fin_maker_headers)
        self.assertEqual(resp.status_code, 403)

        # Approve document
        resp_app = self.client.post(f"/api/documents/{self.fin_doc_id}/approve", headers=self.fin_maker_headers)
        self.assertEqual(resp_app.status_code, 403)

        # Reject document
        resp_rej = self.client.post(f"/api/documents/{self.fin_doc_id}/reject", headers=self.fin_maker_headers)
        self.assertEqual(resp_rej.status_code, 403)

        # Edit extracted field
        resp_patch = self.client.patch(
            f"/api/documents/{self.fin_doc_id}/fields/99999",
            json={"field_value": "New Value"},
            headers=self.fin_maker_headers,
        )
        self.assertEqual(resp_patch.status_code, 403)

    # ========================================================
    # 4. DEPARTMENT SCOPING AND DROPDOWN RULE
    # ========================================================
    def test_04_super_admin_can_filter_or_view_all_departments(self):
        resp_hr = self.client.get("/api/documents?department=HR", headers=self.super_admin_headers)
        self.assertEqual(resp_hr.status_code, 200)
        docs = resp_hr.json()["items"]
        self.assertTrue(len(docs) >= 1)
        for d in docs:
            self.assertEqual(d["department"], "HR")

        resp_fin = self.client.get("/api/documents?department=FINANCE", headers=self.super_admin_headers)
        self.assertEqual(resp_fin.status_code, 200)
        docs_fin = resp_fin.json()["items"]
        self.assertTrue(len(docs_fin) >= 1)
        for d in docs_fin:
            self.assertEqual(d["department"], "FINANCE")

    def test_04_finance_user_automatically_scoped_even_if_manipulated(self):
        # Frontend-supplied department=HR should be overridden by server-side FINANCE
        resp = self.client.get("/api/documents?department=HR", headers=self.fin_checker_headers)
        self.assertEqual(resp.status_code, 200)
        docs = resp.json()["items"]
        self.assertTrue(len(docs) >= 1)
        for d in docs:
            self.assertEqual(d["department"], "FINANCE")
            self.assertNotEqual(d["department"], "HR")

    # ========================================================
    # 5. SUCCESSFUL AND FAILED LOGIN AUDIT EVENTS
    # ========================================================
    def test_05_successful_login_is_logged(self):
        resp = self.client.post(
            "/api/auth/admin/login",
            json={"email": "admin@docsentinel.local", "password": "admin@098"},
        )
        self.assertEqual(resp.status_code, 200)

        with SessionLocal() as db:
            log = (
                db.query(ActivityLog)
                .filter(ActivityLog.username == "admin@docsentinel.local", ActivityLog.action == "LOGIN_SUCCESS")
                .order_by(ActivityLog.timestamp.desc())
                .first()
            )
            self.assertIsNotNone(log)
            self.assertEqual(log.status, "SUCCESS")
            self.assertIn("admin", log.target_resource.lower())

    def test_05_failed_login_is_logged_without_sensitive_secrets(self):
        secret_password_attempt = "VerySecretPassword123!"
        resp = self.client.post(
            "/api/auth/admin/login",
            json={"email": "admin@docsentinel.local", "password": secret_password_attempt},
        )
        self.assertEqual(resp.status_code, 401)

        with SessionLocal() as db:
            log = (
                db.query(ActivityLog)
                .filter(ActivityLog.username == "admin@docsentinel.local", ActivityLog.action == "LOGIN_FAILED")
                .order_by(ActivityLog.timestamp.desc())
                .first()
            )
            self.assertIsNotNone(log)
            self.assertEqual(log.status, "FAILURE")
            # Ensure the password attempt was NOT persisted
            if log.details:
                self.assertNotIn(secret_password_attempt, log.details)
            if log.failure_reason:
                self.assertNotIn(secret_password_attempt, log.failure_reason)

    # ========================================================
    # 6. DOCUMENT AND ADMINISTRATIVE ACTIVITY LOGGING
    # ========================================================
    def test_06_admin_user_role_change_logged(self):
        new_test_email = f"logtest-{uuid4().hex[:6]}@test.local"
        with SessionLocal() as db:
            user = User(
                email=new_test_email,
                name="Log Test User",
                role=UserRole.DEPARTMENT_MAKER.value,
                department="FINANCE",
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            target_user_id = user.id

        resp = self.client.patch(
            f"/api/admin/users/{target_user_id}/role",
            json={"role": UserRole.DEPARTMENT_CHECKER.value},
            headers=self.fin_admin_headers,
        )
        self.assertEqual(resp.status_code, 200)

        with SessionLocal() as db:
            log = (
                db.query(ActivityLog)
                .filter(ActivityLog.action == "USER_ROLE_CHANGED", ActivityLog.actor_id == self.fin_admin_id)
                .order_by(ActivityLog.timestamp.desc())
                .first()
            )
            self.assertIsNotNone(log)
            self.assertEqual(log.status, "SUCCESS")
            self.assertIn("DEPARTMENT_CHECKER", str(log.details))

    # ========================================================
    # 7. DEPT ADMIN ATTEMPTING CROSS-DEPARTMENT USERS OR LOGS
    # ========================================================
    def test_07_department_admin_cannot_access_other_dept_users_or_logs(self):
        # Cross-department user filter attempt
        resp_users = self.client.get("/api/admin/users?department=HR", headers=self.fin_admin_headers)
        self.assertEqual(resp_users.status_code, 403)

        # Cross-department activity log attempt
        resp_logs = self.client.get("/api/admin/activity-logs?department=HR", headers=self.fin_admin_headers)
        self.assertEqual(resp_logs.status_code, 403)

    def test_07_department_admin_can_access_own_dept_logs(self):
        resp_logs = self.client.get("/api/admin/activity-logs", headers=self.fin_admin_headers)
        self.assertEqual(resp_logs.status_code, 200)
        data = resp_logs.json()
        for item in data["items"]:
            if item.get("department"):
                self.assertEqual(item["department"].upper(), "FINANCE")

    # ========================================================
    # 8. USER DELETION, SESSION REVOCATION, AND PRESERVATION
    # ========================================================
    def test_08_permanent_user_deletion_and_doc_preservation(self):
        del_email = f"delete-me-{uuid4().hex[:6]}@test.local"
        with SessionLocal() as db:
            del_user = User(
                email=del_email,
                name="To Be Deleted",
                role=UserRole.DEPARTMENT_MAKER.value,
                department="FINANCE",
                password_hash=hash_password("pw123"),
                is_active=True,
            )
            db.add(del_user)
            db.commit()
            db.refresh(del_user)
            del_user_id = del_user.id

            # Create document belonging to this user
            doc = Document(
                original_filename="user_doc.pdf",
                stored_filename="user_doc_stored.pdf",
                file_path="uploads/user_doc.pdf",
                file_type="application/pdf",
                file_size=512,
                status="indexed",
                department="FINANCE",
                uploaded_by=del_user_id,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            doc_id = doc.id

        # Generate token for that user before deletion
        token = create_access_token({"sub": str(del_user_id), "email": del_email, "role": UserRole.DEPARTMENT_MAKER.value})
        user_headers = {"Authorization": f"Bearer {token}"}

        # Verify active profile before deletion
        resp_me = self.client.get("/api/auth/me", headers=user_headers)
        self.assertEqual(resp_me.status_code, 200)

        # Permanently delete user
        resp_del = self.client.delete(f"/api/admin/users/{del_user_id}?permanent=true", headers=self.super_admin_headers)
        self.assertEqual(resp_del.status_code, 200)

        # Authenticated call with deleted user token must now fail
        resp_me_after = self.client.get("/api/auth/me", headers=user_headers)
        self.assertEqual(resp_me_after.status_code, 401)

        # User row is gone from DB
        with SessionLocal() as db:
            deleted = db.query(User).filter(User.id == del_user_id).first()
            self.assertIsNone(deleted)

            # Document is PRESERVED, with uploaded_by set to NULL
            preserved_doc = db.query(Document).filter(Document.id == doc_id).first()
            self.assertIsNotNone(preserved_doc)
            self.assertIsNone(preserved_doc.uploaded_by)

    # ========================================================
    # 9. UNAUTHORIZED DIRECT API CALLS AND IDOR ATTEMPTS
    # ========================================================
    def test_09_idor_maker_cannot_delete_or_modify_other_dept_document(self):
        # Maker trying to delete document in another department
        resp = self.client.delete(f"/api/documents/{self.hr_doc_id}", headers=self.fin_maker_headers)
        self.assertEqual(resp.status_code, 403)

    def test_09_dept_admin_cannot_promote_to_super_admin(self):
        # Department Admin trying to escalate another user to SUPER_ADMIN
        resp = self.client.patch(
            f"/api/admin/users/{self.fin_maker_id}/role",
            json={"role": UserRole.SUPER_ADMIN.value},
            headers=self.fin_admin_headers,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(
            "administrative" in resp.json()["detail"].lower()
            or "super" in resp.json()["detail"].lower()
            or "escalat" in resp.json()["detail"].lower()
        )

    def test_09_dept_admin_cannot_modify_user_in_other_department(self):
        # Finance Admin trying to modify an HR user
        resp = self.client.patch(
            f"/api/admin/users/{self.hr_maker_id}/role",
            json={"role": UserRole.DEPARTMENT_CHECKER.value},
            headers=self.fin_admin_headers,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("assigned department", resp.json()["detail"].lower())


    # ========================================================
    # 10. OCR TEXT CORRECTION AND AUDIT LOGGING
    # ========================================================
    def test_10_ocr_text_correction_and_audit_logging(self):
        with SessionLocal() as db:
            from app.models.document import OCRResult
            ocr = db.query(OCRResult).filter(OCRResult.document_id == self.fin_doc_id).first()
            if not ocr:
                ocr = OCRResult(document_id=self.fin_doc_id, extracted_text="Original corrupted OCR text", confidence=75.0)
                db.add(ocr)
                db.commit()
                db.refresh(ocr)
            ocr_id = ocr.id

        # Finance Checker correcting Finance document OCR text -> 200 OK
        resp = self.client.patch(
            f"/api/documents/{self.fin_doc_id}/ocr/{ocr_id}",
            json={"extracted_text": "Corrected Invoice Total: $12,500.00"},
            headers=self.fin_checker_headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["extracted_text"], "Corrected Invoice Total: $12,500.00")
        self.assertEqual(data["raw_text"], "Original corrupted OCR text")

        # Verify activity log
        with SessionLocal() as db:
            log = db.query(ActivityLog).filter(
                ActivityLog.action == "OCR_TEXT_CORRECTED",
                ActivityLog.target_resource == f"document:{self.fin_doc_id}:ocr:{ocr_id}",
            ).first()
            self.assertIsNotNone(log)
            self.assertEqual(log.status, "SUCCESS")

        # HR Checker trying to correct Finance document OCR text -> 403 Forbidden
        resp_hr = self.client.patch(
            f"/api/documents/{self.fin_doc_id}/ocr/{ocr_id}",
            json={"extracted_text": "Hacked OCR Text"},
            headers=self.hr_checker_headers,
        )
        self.assertEqual(resp_hr.status_code, 403)

        # Maker trying to correct OCR text -> 403 Forbidden
        resp_maker = self.client.patch(
            f"/api/documents/{self.fin_doc_id}/ocr/{ocr_id}",
            json={"extracted_text": "Maker OCR Text"},
            headers=self.fin_maker_headers,
        )
        self.assertEqual(resp_maker.status_code, 403)

    # ========================================================
    # 11. RAG CHATBOT CROSS-DEPARTMENT RETRIEVAL ISOLATION
    # ========================================================
    def test_11_rag_cross_department_retrieval_isolation(self):
        # Finance Maker attempting to query HR document via RAG
        payload = {
            "question": "What is the employee salary?",
            "document_ids": [self.hr_doc_id],
        }
        resp = self.client.post("/api/query/ask", json=payload, headers=self.fin_maker_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Scope enforcement must reject cross-department access
        self.assertEqual(data["engine"], "unauthorized")
        self.assertEqual(len(data["sources"]), 0)

    # ========================================================
    # 12. DEPARTMENT-SCOPED WORKFLOW RULES
    # ========================================================
    def test_12_department_scoped_workflow_rules(self):
        # Finance Admin trying to add an HR workflow rule -> 403 Forbidden
        resp_bad = self.client.post(
            "/api/admin/workflow/rules",
            json={
                "title": "HR Appraisal Review",
                "route": "HR Senior Team",
                "type": "warning",
                "threshold": 85.0,
                "document_type": "Appraisals",  # Belongs to HR
            },
            headers=self.fin_admin_headers,
        )
        self.assertEqual(resp_bad.status_code, 403)

        # Finance Admin adding a Finance workflow rule -> 201 Created
        resp_ok = self.client.post(
            "/api/admin/workflow/rules",
            json={
                "title": "Finance High Value Invoice",
                "route": "Finance Director",
                "type": "warning",
                "threshold": 90.0,
                "document_type": "Invoices",  # Belongs to Finance
            },
            headers=self.fin_admin_headers,
        )
        self.assertEqual(resp_ok.status_code, 201)
        created_rule_id = resp_ok.json()["id"]

        # HR Admin cannot delete the Finance rule -> 403 Forbidden
        resp_del_bad = self.client.delete(
            f"/api/admin/workflow/rules/{created_rule_id}",
            headers=self.hr_admin_headers,
        )
        self.assertEqual(resp_del_bad.status_code, 403)

        # Finance Admin can delete own department rule -> 200 OK
        resp_del_ok = self.client.delete(
            f"/api/admin/workflow/rules/{created_rule_id}",
            headers=self.fin_admin_headers,
        )
        self.assertEqual(resp_del_ok.status_code, 200)

if __name__ == "__main__":
    unittest.main()
