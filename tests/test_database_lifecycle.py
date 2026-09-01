import sys
import types
import unittest
from unittest.mock import patch


class _PasswordHasher:
    def __init__(self, **kwargs):
        pass


argon2 = types.ModuleType("argon2")
argon2.PasswordHasher = _PasswordHasher
argon2.Type = types.SimpleNamespace(ID="id")
argon2_exceptions = types.ModuleType("argon2.exceptions")
argon2_exceptions.InvalidHashError = ValueError
argon2_exceptions.VerificationError = ValueError
argon2_exceptions.VerifyMismatchError = ValueError
sys.modules.setdefault("argon2", argon2)
sys.modules.setdefault("argon2.exceptions", argon2_exceptions)

import app as app_module


class FakeDatabase:
    """Small stateful fake modelling the Peewee lifecycle used by Flask."""

    def __init__(self):
        self.closed = True
        self.connection_valid = False
        self.connect_calls = 0
        self.close_calls = 0

    def is_closed(self):
        return self.closed

    def connect(self):
        if not self.closed:
            raise AssertionError("connection opened twice")
        self.closed = False
        self.connection_valid = True
        self.connect_calls += 1

    def close(self):
        if self.closed:
            raise AssertionError("connection closed twice")
        self.closed = True
        self.connection_valid = False
        self.close_calls += 1

    def invalidate_connection(self):
        # A server-side disconnect can invalidate psycopg2 before Peewee's
        # thread-local state has been marked closed.
        self.connection_valid = False


class DatabaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase()
        self.database_patch = patch.object(app_module, "db", self.database)
        self.database_patch.start()
        self.app = app_module.create_app({
            "SKIP_DATABASE_INIT": True,
            "TESTING": True,
        })

        @self.app.get("/_test/database")
        def database_probe():
            if not self.database.connection_valid:
                raise RuntimeError("database connection is invalid")
            return {"connected": True}

        @self.app.get("/_test/database-error")
        def database_error():
            raise RuntimeError("view failed")

        @self.app.get("/_test/database-invalid")
        def database_invalid():
            self.database.invalidate_connection()
            raise RuntimeError("database connection was lost")

        self.client = self.app.test_client()

    def tearDown(self):
        self.database_patch.stop()

    def test_connection_is_opened_at_request_start(self):
        observed = []

        @self.app.get("/_test/database-state")
        def database_state():
            observed.append(self.database.is_closed())
            return {"connected": True}

        response = self.client.get("/_test/database-state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed, [False])
        self.assertEqual(self.database.connect_calls, 1)

    def test_connection_is_closed_after_normal_response(self):
        response = self.client.get("/_test/database")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.database.is_closed())
        self.assertEqual(self.database.close_calls, 1)

    def test_connection_is_closed_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "view failed"):
            self.client.get("/_test/database-error")

        self.assertTrue(self.database.is_closed())
        self.assertEqual(self.database.close_calls, 1)

    def test_successive_requests_have_independent_connection_cycles(self):
        first = self.client.get("/_test/database")
        second = self.client.get("/_test/database")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(self.database.is_closed())
        self.assertEqual(self.database.connect_calls, 2)
        self.assertEqual(self.database.close_calls, 2)

    def test_next_request_recovers_after_invalid_connection(self):
        with self.assertRaisesRegex(RuntimeError, "connection was lost"):
            self.client.get("/_test/database-invalid")

        self.assertTrue(self.database.is_closed())
        response = self.client.get("/_test/database")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.database.is_closed())
        self.assertEqual(self.database.connect_calls, 2)
        self.assertEqual(self.database.close_calls, 2)


if __name__ == "__main__":
    unittest.main()
