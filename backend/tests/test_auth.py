from contextlib import contextmanager
import unittest

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.main import app
from app.models.user import User
from app.services.auth_service import init_default_admin, upsert_google_user


@contextmanager
def get_test_client():
    with SessionLocal() as db:
        init_default_admin(db)
    with TestClient(app) as c:
        yield c


class AuthTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not settings.GOOGLE_CLIENT_ID:
            settings.GOOGLE_CLIENT_ID = "mock-google-client-id"
        with SessionLocal() as db:
            init_default_admin(db)
        cls.client = TestClient(app)

    def test_google_auth_url(self):
        response = self.client.get("/api/auth/google")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("url", data)
        self.assertIn("accounts.google.com", data["url"])
        self.assertIn("client_id=", data["url"])

    def test_admin_login_success(self):
        response = self.client.post(
            "/api/auth/admin/login",
            json={"email": "admin@docsentinel.local", "password": "admin@098"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")
        self.assertEqual(data["user"]["role"], "admin")
        self.assertEqual(data["user"]["email"], "admin@docsentinel.local")
        self.assertNotIn("password_hash", data["user"])

    def test_admin_login_invalid_password(self):
        response = self.client.post(
            "/api/auth/admin/login",
            json={"email": "admin@docsentinel.local", "password": "wrongpassword123"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid email or password", response.json()["detail"])

    def test_admin_login_nonexistent_user(self):
        response = self.client.post(
            "/api/auth/admin/login",
            json={"email": "nobody@example.com", "password": "anypassword"},
        )
        self.assertEqual(response.status_code, 401)

    def test_get_current_user_profile(self):
        login_resp = self.client.post(
            "/api/auth/admin/login",
            json={"email": "admin@docsentinel.local", "password": "admin@098"},
        )
        token = login_resp.json()["access_token"]

        response = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        user = response.json()
        self.assertEqual(user["email"], "admin@docsentinel.local")
        self.assertEqual(user["role"], "admin")

    def test_get_current_user_unauthorized(self):
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)

        response_bad_token = self.client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid_token_here"},
        )
        self.assertEqual(response_bad_token.status_code, 401)

    def test_rbac_admin_endpoint_forbidden_for_regular_user(self):
        with SessionLocal() as db:
            regular_user = upsert_google_user(
                db,
                {
                    "sub": "google-test-user-12345",
                    "email": "student@example.com",
                    "name": "Regular Student",
                    "picture": "https://example.com/avatar.jpg",
                },
            )
            self.assertEqual(regular_user.role, "user")
            user_id = regular_user.id

        user_token = create_access_token({
            "sub": str(user_id),
            "email": "student@example.com",
            "role": "user",
        })

        response = self.client.get(
            "/api/auth/admin/users",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Admin privileges required", response.json()["detail"])

    def test_rbac_admin_endpoint_allowed_for_admin(self):
        login_resp = self.client.post(
            "/api/auth/admin/login",
            json={"email": "admin@docsentinel.local", "password": "admin@098"},
        )
        admin_token = login_resp.json()["access_token"]

        response = self.client.get(
            "/api/auth/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(response.status_code, 200)
        users = response.json()
        self.assertIsInstance(users, list)
        self.assertTrue(len(users) >= 1)
        self.assertTrue(any(u["email"] == "admin@docsentinel.local" for u in users))

    def test_google_user_upsert_idempotency(self):
        with SessionLocal() as db:
            google_payload = {
                "sub": "google-id-unique-999",
                "email": "googleuser@example.com",
                "name": "Google User",
                "picture": "https://example.com/pic.png",
            }
            user1 = upsert_google_user(db, google_payload)
            self.assertIsNotNone(user1.id)
            self.assertEqual(user1.role, "user")
            self.assertEqual(user1.email, "googleuser@example.com")
            initial_id = user1.id

            google_payload_updated = {
                "sub": "google-id-unique-999",
                "email": "googleuser@example.com",
                "name": "Google User Updated",
                "picture": "https://example.com/newpic.png",
            }
            user2 = upsert_google_user(db, google_payload_updated)
            self.assertEqual(user2.id, initial_id)
            self.assertEqual(user2.profile_picture, "https://example.com/newpic.png")


if __name__ == "__main__":
    unittest.main()
