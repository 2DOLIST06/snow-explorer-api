import logging
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask, jsonify
from peewee import SqliteDatabase

from app import create_app
from app.models.admin_login_link import AdminLoginLink
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.routes.admin_auth import bp_admin_auth
from app.models.region import Region
from app.datetime_utils import ensure_utc, utcnow
from app.services.admin_auth import hash_password, protect_admin_routes, revoke_all_sessions

MODELS = [AdminUser, AdminSession, AdminLoginAttempt, AdminLoginLink]


class AdminAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind(MODELS)
        self.database.connect()
        self.database.create_tables(MODELS)
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            ADMIN_SESSION_SECRET="s" * 64,
            ADMIN_SESSION_COOKIE_NAME="admin_session",
            ADMIN_SESSION_TTL_SECONDS=28800,
            ADMIN_SESSION_TOUCH_INTERVAL_SECONDS=300,
            ADMIN_COOKIE_SECURE=True,
            ADMIN_COOKIE_SAMESITE="None",
            ADMIN_LOGIN_RATE_LIMIT=5,
            ADMIN_LOGIN_RATE_WINDOW_SECONDS=900,
            TRUST_PROXY_HEADERS=False,
            ADMIN_ALLOWED_ORIGINS=["https://www.snow-explorer.com"],
            FRONTEND_URL="https://www.snow-explorer.com",
            BREVO_API_KEY="test-key", BREVO_SENDER_EMAIL="hello@snow-explorer.com", BREVO_SENDER_NAME="Snow Explorer",
        )
        protect_admin_routes(app)
        app.register_blueprint(bp_admin_auth)

        @app.get("/api/admin/probe")
        def admin_probe():
            return jsonify({"ok": True})

        @app.post("/api/admin/probe")
        def admin_write_probe():
            return jsonify({"ok": True})

        @app.get("/api/admin/stations/")
        def admin_station_list():
            return jsonify({"items": []})

        @app.post("/api/admin/stations/import/preview")
        def admin_station_import_preview():
            return jsonify({"valid": True})

        @app.get("/api/public/probe")
        def public_probe():
            return jsonify({"ok": True})

        self.app = app
        self.client = app.test_client()
        self.user = AdminUser.create(email="admin@example.com", password_hash=hash_password("correct horse battery"),
                                     role="admin", is_active=True)

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def login(self, password="correct horse battery", email="ADMIN@example.com"):
        return self.client.post("/api/admin/auth/login", json={"email": email, "password": password})

    def test_success_cookie_and_no_session_token_in_json(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["user"]["email"], "admin@example.com")
        self.assertNotIn("session", body)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=None", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn(AdminSession.get().token_hash, cookie)

    def test_wrong_and_unknown_credentials_are_identical(self):
        wrong = self.login(password="incorrect password value")
        unknown = self.login(email="unknown@example.com", password="incorrect password value")
        self.assertEqual((wrong.status_code, wrong.get_json()), (401, {"error": "invalid_credentials"}))
        self.assertEqual((unknown.status_code, unknown.get_json()), (wrong.status_code, wrong.get_json()))

    def test_disabled_account(self):
        self.user.is_active = False
        self.user.save()
        response = self.login()
        self.assertEqual((response.status_code, response.get_json()), (403, {"error": "admin_disabled"}))

    def test_session_valid_and_absent(self):
        self.assertEqual(self.client.get("/api/admin/auth/session").status_code, 401)
        csrf = self.login().get_json()["csrf_token"]
        response = self.client.get("/api/admin/auth/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["csrf_token"], csrf)

    def test_expired_and_revoked_sessions_are_refused(self):
        self.login()
        session = AdminSession.get()
        session.expires_at = utcnow() - timedelta(seconds=1)
        session.save()
        self.assertEqual(self.client.get("/api/admin/probe").status_code, 401)
        session.expires_at = utcnow() + timedelta(hours=1)
        session.revoked_at = utcnow()
        session.save()
        self.assertEqual(self.client.get("/api/admin/probe").status_code, 401)

    def test_aware_and_database_naive_expiration_are_normalized(self):
        self.login()
        session = AdminSession.get()
        self.assertEqual(ensure_utc(session.expires_at).utcoffset(), timedelta(0))

        # Simulate a legacy TIMESTAMP value returned without tzinfo.
        naive_expiration = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        AdminSession.update(expires_at=naive_expiration).where(AdminSession.id == session.id).execute()
        loaded = AdminSession.get_by_id(session.id)
        self.assertIsNotNone(loaded.expires_at.tzinfo)
        response = self.client.get("/api/admin/probe")
        self.assertEqual(response.status_code, 200)

    def test_station_routes_validate_session_without_500(self):
        csrf = self.login().get_json()["csrf_token"]
        listing = self.client.get("/api/admin/stations/")
        preview = self.client.post(
            "/api/admin/stations/import/preview",
            json={"stations": []},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((listing.status_code, preview.status_code), (200, 200))
        self.assertNotEqual(listing.status_code, 500)
        self.assertNotEqual(preview.status_code, 500)

    def test_logout_requires_csrf_and_revokes(self):
        csrf = self.login().get_json()["csrf_token"]
        self.assertEqual(self.client.post("/api/admin/auth/logout").status_code, 403)
        response = self.client.post("/api/admin/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(AdminSession.get().revoked_at)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("Expires=Thu, 01 Jan 1970", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=None", cookie)
        self.assertIn("Path=/", cookie)

    def test_logout_all_revokes_every_session(self):
        csrf = self.login().get_json()["csrf_token"]
        with self.app.test_request_context("/"):
            # Internal revocation primitive is shared with password-change workflows.
            pass
        second = self.app.test_client().post("/api/admin/auth/login", json={"email": self.user.email, "password": "correct horse battery"})
        self.assertEqual(second.status_code, 200)
        response = self.client.post("/api/admin/auth/logout-all", headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AdminSession.select().where(AdminSession.revoked_at.is_null()).count(), 0)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=None", cookie)
        self.assertIn("Path=/", cookie)

    def test_global_protection_csrf_options_and_public_route(self):
        self.assertEqual(self.client.get("/api/admin/probe").status_code, 401)
        self.assertEqual(self.client.get("/api/public/probe").status_code, 200)
        self.assertNotEqual(self.client.options("/api/admin/future-route").status_code, 401)
        csrf = self.login().get_json()["csrf_token"]
        self.assertEqual(self.client.get("/api/admin/probe").status_code, 200)
        self.assertEqual(self.client.post("/api/admin/probe").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/probe", headers={"X-CSRF-Token": "bad"}).status_code, 403)
        self.assertEqual(self.client.post("/api/admin/probe", headers={"X-CSRF-Token": csrf}).status_code, 200)
        # A path with no registered view is still intercepted before Flask's 404.
        self.client.delete_cookie("admin_session")
        self.assertEqual(self.client.get("/api/admin/new-route-added-later").status_code, 401)

    def test_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.login(password="incorrect password value").status_code, 401)
        self.assertEqual(self.login(password="incorrect password value").status_code, 429)

    def test_region_routes_use_existing_session_and_csrf_protection(self):
        self.app = create_app({
            **self.app.config, "SKIP_DATABASE_INIT": True,
            "PUBLIC_CACHE_ENABLED": False,
        })
        self.client = self.app.test_client()
        # Isolate PostgreSQL connection hooks; all models use the SQLite fixture.
        database_hooks = patch("app.db")
        database_hooks.start()
        self.addCleanup(database_hooks.stop)
        with self.database.bind_ctx([Region]):
            self.database.create_tables([Region])
            Region.create(id="alpes", name="Alpes", description_html="<p>Existant</p>")
            for url in ("/api/admin/regions", "/api/admin/regions/", "/api/admin/regions/alpes"):
                self.assertEqual(self.client.get(url).status_code, 401)
            self.assertEqual(self.client.patch("/api/admin/regions/alpes", json={}).status_code, 401)
            csrf = self.login().get_json()["csrf_token"]
            listing = self.client.get("/api/admin/regions")
            self.assertEqual(listing.status_code, 200)
            region = listing.get_json()[0]
            url = f'/api/admin/regions/{region["slug"]}'
            self.assertEqual(self.client.get(url).get_json()["region"], region)
            self.assertEqual(self.client.patch(url, json={"meta_title": "Refusé"}).status_code, 403)
            self.assertEqual(self.client.patch(url, json={"seo_text": "Refusé"}).status_code, 403)
            self.assertIsNone(Region.get_by_id("alpes").meta_title)
            saved = self.client.patch(url, json={"meta_title": "Titre"}, headers={"X-CSRF-Token": csrf})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.get_json()["region"]["description_html"], "<p>Existant</p>")
            self.assertEqual(self.client.get(url).get_json()["region"], saved.get_json()["region"])
            saved = self.client.patch(url, json={"seo_text": "<h2>Contenu</h2><p>Texte</p>"},
                                      headers={"X-CSRF-Token": csrf})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.get_json()["region"]["seo_text"], "<h2>Contenu</h2><p>Texte</p>")

    def request_link(self, email="admin@example.com"):
        return self.client.post("/api/admin/auth/login-link", json={"email": email}, headers={"Origin": "https://www.snow-explorer.com"})

    def consume_link(self, token):
        return self.client.post("/api/admin/auth/login-link/consume", json={"token": token}, headers={"Origin": "https://www.snow-explorer.com"})

    @patch("app.services.admin_recovery.requests.post")
    def test_recovery_single_use_expiration_and_cookie(self, sender):
        self.assertEqual(self.request_link(" ADMIN@example.com ").status_code, 200)
        token = sender.call_args.kwargs["json"]["textContent"].split("#", 1)[1].split()[0]
        self.assertEqual(len(token), 64)
        self.assertNotEqual(AdminLoginLink.get().token_hash, token)
        self.assertEqual(self.request_link().status_code, 429)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(self.client.get("/api/admin/auth/login-link/consume").status_code, 401)
        AdminLoginLink.update(expires_at=utcnow() - timedelta(seconds=1)).execute()
        self.assertEqual(self.consume_link(token).status_code, 401)
        AdminLoginLink.update(expires_at=utcnow() + timedelta(minutes=1)).execute()
        consumed = self.consume_link(token)
        self.assertEqual(consumed.status_code, 200)
        self.assertIn("HttpOnly", consumed.headers["Set-Cookie"])
        self.assertIn("Secure", consumed.headers["Set-Cookie"])
        self.assertNotIn("token", consumed.get_json())
        self.assertEqual(self.client.get("/api/admin/probe").status_code, 200)
        self.assertEqual(self.consume_link(token).status_code, 401)

    @patch("app.services.admin_recovery.requests.post")
    def test_recovery_unknown_disabled_origin_and_delivery_errors(self, sender):
        import requests
        self.assertEqual(self.request_link("missing@example.com").get_json()["error"], "account_not_found")
        self.assertEqual(sender.call_count, 0)
        self.assertEqual(self.client.post("/api/admin/auth/login-link", json={"email": self.user.email}, headers={"Origin": "https://evil.example"}).status_code, 403)
        sender.side_effect = requests.RequestException("provider failure")
        self.assertEqual(self.request_link().status_code, 503)
        self.assertIsNone(AdminLoginLink.get().token_hash)
        self.assertIsNone(AdminLoginLink.get().requested_at)
        self.app.config["FRONTEND_URL"] = None
        self.assertEqual(self.request_link().status_code, 503)
        self.assertEqual(sender.call_count, 1)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.request_link().status_code, 403)
        self.assertEqual(AdminSession.select().count(), 0)

    @patch("app.services.admin_recovery.requests.post")
    def test_password_rotation_revokes_sessions_and_links(self, sender):
        csrf = self.login().get_json()["csrf_token"]
        other = self.app.test_client()
        other.post("/api/admin/auth/login", json={"email": self.user.email, "password": "correct horse battery"})
        self.request_link()
        token = sender.call_args.kwargs["json"]["textContent"].split("#", 1)[1].split()[0]
        endpoint = "/api/admin/auth/password"
        self.assertEqual(self.client.post(endpoint, json={"password": "a new secure password"}).status_code, 403)
        self.assertEqual(self.client.post(endpoint, json={"password": "short"}, headers={"X-CSRF-Token": csrf}).status_code, 400)
        changed = self.client.post(endpoint, json={"password": "a new secure password"}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed.get_json()["csrf_token"], csrf)
        self.assertEqual(other.get("/api/admin/probe").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/probe").status_code, 200)
        self.assertEqual(AdminLoginLink.select().count(), 0)
        self.assertEqual(self.consume_link(token).status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(password="a new secure password").status_code, 200)

    def test_recovery_rate_limit_and_password_authentication(self):
        self.assertEqual(self.client.post("/api/admin/auth/password", json={"password": "a new secure password"}).status_code, 401)
        for _ in range(5):
            self.assertEqual(self.request_link("missing@example.com").status_code, 404)
        self.assertEqual(self.request_link("missing@example.com").status_code, 429)

    def test_password_is_not_logged(self):
        password = "never log this password"
        with self.assertLogs("security.admin", level=logging.WARNING) as logs:
            self.login(password=password)
        self.assertNotIn(password, "\n".join(logs.output))

    def test_samesite_environment_and_admin_cors(self):
        origin = "https://www.snow-explorer.com"
        with patch.dict(os.environ, {"ADMIN_COOKIE_SAMESITE": "None"}):
            app = create_app({
                "TESTING": True,
                "SKIP_DATABASE_INIT": True,
                "ADMIN_SESSION_SECRET": "s" * 64,
                "ADMIN_ALLOWED_ORIGINS": [origin],
            })

        self.assertEqual(app.config["ADMIN_COOKIE_SAMESITE"], "None")
        # CORS does not need PostgreSQL; keep this request isolated too.
        with patch("app.db"):
            response = app.test_client().options(
                "/api/admin/auth/login",
                headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
            )
        self.assertEqual(response.headers["Access-Control-Allow-Credentials"], "true")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)


if __name__ == "__main__":
    unittest.main()
