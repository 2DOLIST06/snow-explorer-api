import os
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError
from peewee import SqliteDatabase

from app import create_app
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.services.admin_auth import hash_password


ORIGIN = "https://www.snow-explorer.com"
MODELS = [AdminUser, AdminSession, AdminLoginAttempt]
S3_ENV = {
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AWS_REGION": "eu-west-3",
    "AWS_S3_BUCKET": "test-bucket",
}


class S3PresignTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind(MODELS)
        self.database.connect()
        self.database.create_tables(MODELS)
        self.app = create_app({
            "TESTING": True,
            "SKIP_DATABASE_INIT": True,
            "ADMIN_SESSION_SECRET": "s" * 64,
            "ADMIN_COOKIE_SECURE": False,
            "ADMIN_COOKIE_SAMESITE": "Lax",
            "S3_ALLOWED_ORIGINS": [ORIGIN],
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

    def login(self):
        response = self.client.post("/api/admin/auth/login", json={
            "email": "admin@example.com",
            "password": "correct horse battery",
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrf_token"]

    def post(self, payload=None, csrf=None):
        headers = {"Origin": ORIGIN}
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        return self.client.post("/api/s3/presign", json=payload, headers=headers)

    def assert_cors(self, response):
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")

    def test_options_is_public_and_allows_required_headers(self):
        response = self.client.options("/api/s3/presign", headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        })
        self.assertEqual(response.status_code, 204)
        self.assert_cors(response)
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        allowed = response.headers["Access-Control-Allow-Headers"].lower()
        self.assertIn("content-type", allowed)
        self.assertIn("x-csrf-token", allowed)

    def test_post_without_session_is_401_with_cors(self):
        response = self.post({"filename": "logo.webp", "content_type": "image/webp"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "admin_authentication_required")
        self.assert_cors(response)

    def test_post_without_or_with_invalid_csrf_is_403_with_cors(self):
        self.login()
        missing = self.post({"filename": "logo.webp", "content_type": "image/webp"})
        invalid = self.post({"filename": "logo.webp", "content_type": "image/webp"}, "invalid")
        self.assertEqual((missing.status_code, invalid.status_code), (403, 403))
        self.assert_cors(missing)
        self.assert_cors(invalid)

    @patch.dict(os.environ, S3_ENV, clear=False)
    @patch("app.routes.uploads._s3")
    def test_valid_post_has_signed_content_type_and_camel_case_response(self, s3_factory):
        csrf = self.login()
        s3_factory.return_value.generate_presigned_url.return_value = "https://signed.example/upload"
        response = self.post({"filename": "logo.webp", "content_type": "image/webp"}, csrf)
        self.assertEqual(response.status_code, 200)
        self.assert_cors(response)
        body = response.get_json()
        self.assertEqual(set(body), {"uploadUrl", "publicUrl", "contentType"})
        self.assertEqual(body["uploadUrl"], "https://signed.example/upload")
        self.assertEqual(body["contentType"], "image/webp")
        self.assertTrue(body["publicUrl"].startswith(
            "https://test-bucket.s3.eu-west-3.amazonaws.com/uploads/"
        ))
        params = s3_factory.return_value.generate_presigned_url.call_args.kwargs["Params"]
        self.assertEqual(params["ContentType"], "image/webp")

    def test_invalid_payload_is_400_with_cors(self):
        csrf = self.login()
        response = self.post({"filename": "logo.webp"}, csrf)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "presign_failed")
        self.assert_cors(response)

    @patch.dict(os.environ, S3_ENV, clear=False)
    @patch("app.routes.uploads._s3")
    def test_boto3_exception_is_500_json_with_cors(self, s3_factory):
        csrf = self.login()
        error = {"Error": {"Code": "InternalError", "Message": "test"}}
        s3_factory.return_value.generate_presigned_url.side_effect = ClientError(error, "PutObject")
        response = self.post({"filename": "logo.webp", "content_type": "image/webp"}, csrf)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "presign_failed")
        self.assert_cors(response)


if __name__ == "__main__":
    unittest.main()
