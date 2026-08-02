import unittest

from flask import Flask, jsonify

from app.services.admin_auth import protect_admin_routes


class AdminAuthenticationTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config["ADMIN_API_TOKEN"] = "admin-test"
        protect_admin_routes(app)

        @app.get("/api/admin/stations")
        def admin_page():
            return jsonify({"ok": True})

        @app.get("/api/resorts")
        def public_page():
            return jsonify({"ok": True})

        self.client = app.test_client()

    def test_every_admin_route_requires_authentication(self):
        response = self.client.get("/api/admin/stations")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "admin_authentication_required"})

    def test_bearer_token_authenticates_admin_route(self):
        response = self.client.get(
            "/api/admin/stations",
            headers={"Authorization": "Bearer admin-test"},
        )
        self.assertEqual(response.status_code, 200)

    def test_legacy_admin_header_remains_supported(self):
        response = self.client.get(
            "/api/admin/stations",
            headers={"X-Admin-Token": "admin-test"},
        )
        self.assertEqual(response.status_code, 200)

    def test_public_routes_remain_public(self):
        response = self.client.get("/api/resorts")
        self.assertEqual(response.status_code, 200)

    def test_admin_routes_fail_closed_without_configured_token(self):
        self.client.application.config["ADMIN_API_TOKEN"] = None
        response = self.client.get("/api/admin/stations")
        self.assertEqual(response.status_code, 401)

    def test_cors_preflight_does_not_require_token(self):
        response = self.client.options("/api/admin/stations")
        self.assertNotEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
