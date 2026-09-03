import os
import unittest
from unittest.mock import Mock, patch

from peewee import SqliteDatabase

from app import create_app
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.routes.admin_anmsm_cors import ANMSM_LOGO_ROUTES
from app.services.admin_auth import hash_password


PRODUCTION_ORIGINS = (
    "https://snow-explorer.com",
    "https://www.snow-explorer.com",
)
FORBIDDEN_ORIGIN = "https://example.com"
SYNC_ROUTE = "/api/admin/anmsm/logos/sync"
MODELS = [AdminUser, AdminSession, AdminLoginAttempt]


class AdminAnmsmCorsTests(unittest.TestCase):
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
        self.business_handler = Mock(return_value=("", 200))
        self.app.add_url_rule(
            SYNC_ROUTE,
            endpoint="test_anmsm_sync_business_route",
            view_func=self.business_handler,
            methods=["POST"],
            provide_automatic_options=False,
        )
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

    def _preflight(self, route, origin=PRODUCTION_ORIGINS[0]):
        requested_method = "GET" if route == ANMSM_LOGO_ROUTES[0] else "POST"
        return self.client.options(route, headers={
            "Origin": origin,
            "Access-Control-Request-Method": requested_method,
            "Access-Control-Request-Headers": "authorization,content-type",
        })

    def _login(self):
        response = self.client.post("/api/admin/auth/login", json={
            "email": "admin@example.com",
            "password": "correct horse battery",
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrf_token"]

    def test_every_anmsm_route_answers_preflight_without_authentication(self):
        for route in ANMSM_LOGO_ROUTES:
            with self.subTest(route=route):
                self.assertIn(self._preflight(route).status_code, {200, 204})

    def test_both_production_origins_receive_admin_cors_headers(self):
        for origin in PRODUCTION_ORIGINS:
            with self.subTest(origin=origin):
                response = self._preflight(SYNC_ROUTE, origin)
                self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), origin)
                self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")
                self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
                allowed_headers = response.headers["Access-Control-Allow-Headers"].lower()
                self.assertIn("authorization", allowed_headers)
                self.assertIn("content-type", allowed_headers)

    def test_forbidden_origin_receives_no_cors_access(self):
        response = self._preflight(SYNC_ROUTE, FORBIDDEN_ORIGIN)
        self.assertIn(response.status_code, {200, 204})
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        self.assertIsNone(response.headers.get("Access-Control-Allow-Credentials"))

    def test_sync_post_remains_protected_and_authenticated_handler_runs(self):
        unauthorized = self.client.post(SYNC_ROUTE)
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(
            unauthorized.get_json()["error"], "admin_authentication_required"
        )
        self.business_handler.assert_not_called()

        csrf_token = self._login()
        authenticated = self.client.post(
            SYNC_ROUTE, headers={"X-CSRF-Token": csrf_token}
        )
        self.assertEqual(authenticated.status_code, 200)
        self.business_handler.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
