import os
import unittest
from unittest.mock import patch

from peewee import SqliteDatabase

from app import create_app
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.services.admin_auth import hash_password


ORIGIN = "https://www.snow-explorer.com"
PREVIEW_ROUTE = "/api/admin/stations/chamonix/forfaits/preview"
IMPORT_ROUTE = "/api/admin/stations/chamonix/forfaits/import"
MODELS = [AdminUser, AdminSession, AdminLoginAttempt]


class SkiPassCorsTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind(MODELS)
        self.database.connect()
        self.database.create_tables(MODELS)
        environment = dict(os.environ)
        environment.pop("ADMIN_ALLOWED_ORIGINS", None)
        with patch.dict(os.environ, environment, clear=True):
            self.app = create_app({
                "TESTING": True,
                "SKIP_DATABASE_INIT": True,
                "ADMIN_SESSION_SECRET": "s" * 64,
                "ADMIN_COOKIE_SECURE": False,
                "ADMIN_COOKIE_SAMESITE": "Lax",
            })
        self.client = self.app.test_client()
        AdminUser.create(
            email="admin@example.com",
            password_hash=hash_password("correct horse battery"),
            role="admin",
            is_active=True,
        )

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def assert_cors(self, response):
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")

    def login(self):
        response = self.client.post("/api/admin/auth/login", json={
            "email": "admin@example.com",
            "password": "correct horse battery",
        }, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrf_token"]

    def test_preview_preflight_allows_json_csrf_authorization_and_post(self):
        response = self.client.options(PREVIEW_ROUTE, headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token,authorization",
        })

        self.assertEqual(response.status_code, 200)
        self.assert_cors(response)
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        allowed = response.headers["Access-Control-Allow-Headers"].lower()
        self.assertIn("content-type", allowed)
        self.assertIn("x-csrf-token", allowed)
        self.assertIn("authorization", allowed)

    def test_preview_auth_and_validation_errors_keep_cors_headers(self):
        unauthorized = self.client.post(
            PREVIEW_ROUTE, json={}, headers={"Origin": ORIGIN}
        )
        self.assertEqual(unauthorized.status_code, 401)
        self.assert_cors(unauthorized)

        csrf = self.login()
        forbidden = self.client.post(
            PREVIEW_ROUTE, json={}, headers={"Origin": ORIGIN}
        )
        with patch("app.routes.ski_passes.preview", return_value={"valid": False}):
            invalid = self.client.post(PREVIEW_ROUTE, json={}, headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
            })
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(invalid.status_code, 422)
        self.assert_cors(forbidden)
        self.assert_cors(invalid)

    def test_station_preview_injects_slug_from_frontend_url(self):
        csrf = self.login()
        with patch("app.routes.ski_passes.preview", return_value={"valid": True}) as validate:
            response = self.client.post(PREVIEW_ROUTE, json={"season": "2026-2027"}, headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(validate.call_args.args[0]["station_slug"], "chamonix")
        self.assert_cors(response)

    def test_import_preflight_and_post_use_exact_frontend_url(self):
        preflight = self.client.options(IMPORT_ROUTE, headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        })
        self.assertEqual(preflight.status_code, 200)
        self.assert_cors(preflight)
        self.assertIn("POST", preflight.headers["Access-Control-Allow-Methods"])

        csrf = self.login()
        season = object()
        with (
            patch("app.routes.ski_passes.replace_grid", return_value=(season, [])) as replace,
            patch("app.routes.ski_passes.serialize_season", return_value={"season": "2026-2027"}),
        ):
            response = self.client.post(IMPORT_ROUTE, json={"season": "2026-2027"}, headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(replace.call_args.args[0]["station_slug"], "chamonix")
        self.assert_cors(response)

    def test_unhandled_preview_error_keeps_cors_headers(self):
        csrf = self.login()
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        with patch("app.routes.ski_passes.preview", side_effect=RuntimeError("boom")):
            response = self.client.post(PREVIEW_ROUTE, json={}, headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
            })
        self.assertEqual(response.status_code, 500)
        self.assert_cors(response)


if __name__ == "__main__":
    unittest.main()
