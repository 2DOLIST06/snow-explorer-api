import json
import unittest
from unittest.mock import MagicMock, patch

from peewee import SqliteDatabase

from app import create_app
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.services.admin_auth import hash_password


MODELS = [AdminUser, AdminSession, AdminLoginAttempt]
VALID_URL = "https://www.snow-explorer.com/stations/val-thorens"


class AdminIndexNowTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.connect()
        self.database.create_tables(MODELS)
        self.app = create_app({
            "TESTING": True,
            "SKIP_DATABASE_INIT": True,
            "ADMIN_SESSION_SECRET": "s" * 64,
            "ADMIN_COOKIE_SECURE": False,
            "INDEXNOW_KEY": "test-indexnow-key",
        })
        self.client = self.app.test_client()
        AdminUser.create(email="admin@example.com", password_hash=hash_password("correct horse battery"))

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def login(self):
        response = self.client.post("/api/admin/auth/login", json={
            "email": "admin@example.com", "password": "correct horse battery",
        })
        return response.get_json()["csrf_token"]

    def post(self, urls, csrf=None):
        headers = {"X-CSRF-Token": csrf} if csrf else {}
        return self.client.post("/api/admin/indexnow", json={"urls": urls}, headers=headers)

    def test_unauthenticated_admin_is_rejected(self):
        response = self.post([VALID_URL])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "admin_authentication_required")

    @patch("app.routes.admin_indexnow.urlopen")
    def test_authenticated_admin_submits_multiple_urls(self, mocked_urlopen):
        upstream = MagicMock(status=202)
        mocked_urlopen.return_value.__enter__.return_value = upstream
        urls = [VALID_URL, "https://snow-explorer.com/stations/tignes"]
        response = self.post(urls, self.login())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "submitted": 2})
        sent = json.loads(mocked_urlopen.call_args.args[0].data)
        self.assertEqual(sent["urlList"], urls)
        self.assertEqual(sent["host"], "www.snow-explorer.com")
        self.assertEqual(sent["keyLocation"], "https://www.snow-explorer.com/test-indexnow-key.txt")

    @patch("app.routes.admin_indexnow.urlopen")
    def test_external_url_is_rejected_without_upstream_call(self, mocked_urlopen):
        response = self.post(["https://example.com/page"], self.login())
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])
        mocked_urlopen.assert_not_called()

    @patch("app.routes.admin_indexnow.urlopen", side_effect=__import__("urllib.error").error.HTTPError(
        "https://api.indexnow.org/indexnow", 403, "Forbidden", {}, None
    ))
    def test_indexnow_error_is_exposed_as_json(self, _mocked_urlopen):
        response = self.post([VALID_URL], self.login())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["indexnow_status"], 403)
        self.assertIn("clé IndexNow", response.get_json()["error"])

    def test_missing_csrf_is_rejected_by_existing_admin_authentication(self):
        self.login()
        self.assertEqual(self.post([VALID_URL]).status_code, 403)


if __name__ == "__main__":
    unittest.main()
