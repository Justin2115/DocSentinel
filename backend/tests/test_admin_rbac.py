import unittest
from uuid import uuid4
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rbac import UserRole
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.main import app
from app.models.user import User
from app.services.admin_service import init_system_settings_and_permissions
from app.services.auth_service import init_default_admin, upsert_google_user


class AdminRBACTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with SessionLocal() as db:
            init_default_admin(db)
            init_system_settings_and_permissions(db)
        cls.client = TestClient(app)

        # Login as Admin
        admin_login = cls.client.post(
            "/api/auth/admin/login",
            json={"email": "admin@docsentinel.local", "password": "admin@098"},
        )
        assert admin_login.status_code == 200, f"Admin login failed: {admin_login.text}"
        cls.admin_token = admin_login.json()["access_token"]
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}

        # Create Maker and Checker users for testing
        with SessionLocal() as db:
            maker = upsert_google_user(
                db,
                {
                    "sub": "maker-test-sub-101",
                    "email": "maker.test@example.com",
                    "name": "Maker Test",
                    "picture": None,
                },
            )
            maker.role = UserRole.UPLOAD_MAKER.value
            db.commit()
            cls.maker_id = maker.id

            checker = upsert_google_user(
                db,
                {
                    "sub": "checker-test-sub-102",
                    "email": "checker.test@example.com",
                    "name": "Checker Test",
                    "picture": None,
                },
            )
            checker.role = UserRole.UPLOAD_CHECKER.value
            db.commit()
            cls.checker_id = checker.id

        cls.maker_token = create_access_token({
            "sub": str(cls.maker_id),
            "email": "maker.test@example.com",
            "role": UserRole.UPLOAD_MAKER.value,
        })
        cls.maker_headers = {"Authorization": f"Bearer {cls.maker_token}"}

        cls.checker_token = create_access_token({
            "sub": str(cls.checker_id),
            "email": "checker.test@example.com",
            "role": UserRole.UPLOAD_CHECKER.value,
        })
        cls.checker_headers = {"Authorization": f"Bearer {cls.checker_token}"}

    # ========================================================
    # 1. USER LISTING
    # ========================================================
    def test_admin_can_list_users(self):
        resp = self.client.get("/api/admin/users", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        users = resp.json()
        self.assertTrue(len(users) >= 2)
        emails = [u["email"] for u in users]
        self.assertIn("admin@docsentinel.local", emails)

    def test_non_admin_cannot_list_users(self):
        resp_maker = self.client.get("/api/admin/users", headers=self.maker_headers)
        self.assertEqual(resp_maker.status_code, 403)

        resp_checker = self.client.get("/api/admin/users", headers=self.checker_headers)
        self.assertEqual(resp_checker.status_code, 403)

    # ========================================================
    # 2. INVITE USER
    # ========================================================
    def test_admin_can_invite_member(self):
        new_email = f"invite-{uuid4().hex}@docsentinel.local"
        payload = {
            "name": "Invited Checker",
            "email": new_email,
            "role": "Upload Checker",
            "department": "Finance",
        }
        resp = self.client.post(
            "/api/admin/users/invite",
            json=payload,
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["email"], new_email)
        self.assertEqual(data["name"], "Invited Checker")
        self.assertEqual(data["role"], "UPLOAD_CHECKER")
        self.assertEqual(data["department"], "FINANCE")
        with SessionLocal() as db:
            invited = db.query(User).filter(User.email == new_email).first()
            self.assertIsNone(invited.password_hash)

    def test_invite_duplicate_email_fails(self):
        payload = {
            "name": "Duplicate Admin",
            "email": "admin@docsentinel.local",
            "role": "Admin",
        }
        resp = self.client.post(
            "/api/admin/users/invite",
            json=payload,
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already exists", resp.json()["detail"])

    def test_non_admin_cannot_invite(self):
        payload = {
            "name": "Hacker",
            "email": "hacker@example.com",
            "role": "Admin",
        }
        resp = self.client.post(
            "/api/admin/users/invite",
            json=payload,
            headers=self.maker_headers,
        )
        self.assertEqual(resp.status_code, 403)

    # ========================================================
    # 3. CHANGE USER ROLE
    # ========================================================
    def test_admin_can_change_user_role(self):
        # Change maker to Upload Checker
        resp = self.client.patch(
            f"/api/admin/users/{self.maker_id}/role",
            json={"role": "Upload Checker"},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["role"], "UPLOAD_CHECKER")

        # Change back to Upload Maker
        resp2 = self.client.patch(
            f"/api/admin/users/{self.maker_id}/role",
            json={"role": "Upload Maker"},
            headers=self.admin_headers,
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["role"], "UPLOAD_MAKER")

    def test_admin_cannot_demote_themselves(self):
        with SessionLocal() as db:
            admin_user = db.query(User).filter(User.email == "admin@docsentinel.local").first()
            admin_id = admin_user.id

        resp = self.client.patch(
            f"/api/admin/users/{admin_id}/role",
            json={"role": "Upload Maker"},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("demote", resp.json()["detail"])

    def test_non_admin_cannot_change_role(self):
        resp = self.client.patch(
            f"/api/admin/users/{self.maker_id}/role",
            json={"role": "Admin"},
            headers=self.maker_headers,
        )
        self.assertEqual(resp.status_code, 403)

    # ========================================================
    # 4. DEACTIVATE / REMOVE MEMBER
    # ========================================================
    def test_admin_can_deactivate_member(self):
        # Create temp user to deactivate
        with SessionLocal() as db:
            temp_email = f"temp-{uuid4().hex}@example.com"
            temp = User(
                name="Temp User",
                email=temp_email,
                role="UPLOAD_MAKER",
                is_active=True,
            )
            db.add(temp)
            db.commit()
            db.refresh(temp)
            temp_id = temp.id

        resp = self.client.delete(
            f"/api/admin/users/{temp_id}",
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("deactivated", resp.json()["message"])

        # Verify target is now deactivated
        with SessionLocal() as db:
            u = db.query(User).filter(User.id == temp_id).first()
            self.assertFalse(u.is_active)

    def test_admin_cannot_deactivate_self(self):
        with SessionLocal() as db:
            admin_user = db.query(User).filter(User.email == "admin@docsentinel.local").first()
            admin_id = admin_user.id

        resp = self.client.delete(
            f"/api/admin/users/{admin_id}",
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot remove your own", resp.json()["detail"])

    # ========================================================
    # 5. PERMISSIONS MATRIX
    # ========================================================
    def test_admin_can_get_and_update_permissions(self):
        # Get permissions
        resp = self.client.get("/api/admin/permissions", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        matrix = resp.json()
        self.assertTrue(len(matrix) >= 1)
        folders = [item["folder"] for item in matrix]
        self.assertIn("Invoices", folders)

        # Update permission for Invoices
        updated_payload = [
            {
                "folder": "Invoices",
                "admin": True,
                "upload_maker": False,  # Maker disabled
                "upload_checker": True,
            }
        ]
        resp_update = self.client.put(
            "/api/admin/permissions",
            json=updated_payload,
            headers=self.admin_headers,
        )
        self.assertEqual(resp_update.status_code, 200)
        updated_matrix = resp_update.json()
        inv_item = next((i for i in updated_matrix if i["folder"] == "Invoices"), None)
        self.assertIsNotNone(inv_item)
        self.assertFalse(inv_item["upload_maker"])

        # Restore maker permission for Invoices
        restore_payload = [
            {
                "folder": "Invoices",
                "admin": True,
                "upload_maker": True,
                "upload_checker": True,
            }
        ]
        self.client.put("/api/admin/permissions", json=restore_payload, headers=self.admin_headers)

    def test_non_admin_cannot_modify_permissions(self):
        payload = [{"folder": "Invoices", "admin": True, "upload_maker": True, "upload_checker": True}]
        resp = self.client.put("/api/admin/permissions", json=payload, headers=self.maker_headers)
        self.assertEqual(resp.status_code, 403)

    # ========================================================
    # 6. WORKFLOW RULES
    # ========================================================
    def test_admin_can_get_and_update_workflow(self):
        resp = self.client.get("/api/admin/workflow", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("threshold", data)
        self.assertIn("rules", data)

        # Update threshold
        update_resp = self.client.put(
            "/api/admin/workflow",
            json={"threshold": 82.5},
            headers=self.admin_headers,
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["threshold"], 82.5)

    def test_admin_can_add_and_delete_rule(self):
        add_resp = self.client.post(
            "/api/admin/workflow/rules",
            json={
                "title": "Custom Test Rule",
                "route": "Test Unit",
                "type": "warning",
                "threshold": 78.0,
                "document_type": "Contracts",
            },
            headers=self.admin_headers,
        )
        self.assertEqual(add_resp.status_code, 201)
        created_rule = add_resp.json()
        rule_id = created_rule["id"]
        self.assertEqual(created_rule["title"], "Custom Test Rule")

        # Delete rule
        del_resp = self.client.delete(
            f"/api/admin/workflow/rules/{rule_id}",
            headers=self.admin_headers,
        )
        self.assertEqual(del_resp.status_code, 200)

    def test_non_admin_cannot_access_workflow(self):
        resp = self.client.get("/api/admin/workflow", headers=self.maker_headers)
        self.assertEqual(resp.status_code, 403)

        resp2 = self.client.put("/api/admin/workflow", json={"threshold": 90.0}, headers=self.checker_headers)
        self.assertEqual(resp2.status_code, 403)


if __name__ == "__main__":
    unittest.main()
