import os
import unittest
from unittest.mock import patch

from peewee import SqliteDatabase

from app import create_app
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.services.admin_auth import hash_password


PRODUCTION_ORIGINS = (
    "https://snow-explorer.com",
    "https://www.snow-explorer.com",
)
FORBIDDEN_ORIGIN = "https://example.com"
LOGOS_ROUTE = "/api/admin/anmsm/logos"
SYNC_ROUTE = f"{LOGOS_ROUTE}/sync"
MODELS = [AdminUser, AdminSession, AdminLoginAttempt]


class AdminAnmsmRouteTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
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

    def _preflight(self, origin=PRODUCTION_ORIGINS[0]):
        return self.client.options(SYNC_ROUTE, headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })

    def _login(self):
        response = self.client.post("/api/admin/auth/login", json={
            "email": "admin@example.com",
            "password": "correct horse battery",
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrf_token"]

    def test_url_map_registers_get_post_and_automatic_options(self):
        rules = {
            rule.rule: rule.methods
            for rule in self.app.url_map.iter_rules()
            if "anmsm" in rule.rule
        }
        self.assertIn("GET", rules[LOGOS_ROUTE])
        self.assertIn("POST", rules[SYNC_ROUTE])
        self.assertIn("OPTIONS", rules[SYNC_ROUTE])

    def test_sync_preflight_is_automatic_and_public(self):
        response = self._preflight()
        self.assertIn(response.status_code, {200, 204})
        self.assertIn("POST", response.headers["Allow"])

    def test_both_production_origins_receive_admin_cors_headers(self):
        for origin in PRODUCTION_ORIGINS:
            with self.subTest(origin=origin):
                response = self._preflight(origin)
                self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), origin)
                self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")
                self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])

    def test_forbidden_origin_receives_no_cors_access(self):
        response = self._preflight(FORBIDDEN_ORIGIN)
        self.assertIn(response.status_code, {200, 204})
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_sync_post_is_protected_and_authenticated_request_runs_sync(self):
        unauthorized = self.client.post(SYNC_ROUTE)
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(
            unauthorized.get_json()["error"], "admin_authentication_required"
        )

        csrf_token = self._login()
        with patch("app.services.anmsm_logos.sync", return_value={"created": 2}) as sync:
            authenticated = self.client.post(
                SYNC_ROUTE, headers={"X-CSRF-Token": csrf_token}
            )
        self.assertEqual(authenticated.status_code, 200)
        self.assertEqual(
            authenticated.get_json(), {"ok": True, "stats": {"created": 2}}
        )
        sync.assert_called_once_with()

    def test_sync_controller_errors_are_json(self):
        csrf_token = self._login()
        with patch("app.services.anmsm_logos.sync", side_effect=RuntimeError("boom")):
            response = self.client.post(
                SYNC_ROUTE, headers={"X-CSRF-Token": csrf_token}
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json(), {"error": "anmsm_logo_sync_failed"})

    def test_logo_listing_remains_available(self):
        csrf_token = self._login()
        endpoint = "admin_station_logos.candidates"
        original = self.app.view_functions[endpoint]
        self.app.view_functions[endpoint] = lambda: ({"items": []}, 200)
        try:
            response = self.client.get(
                LOGOS_ROUTE, headers={"X-CSRF-Token": csrf_token}
            )
        finally:
            self.app.view_functions[endpoint] = original
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"items": []})


if __name__ == "__main__":
    unittest.main()
