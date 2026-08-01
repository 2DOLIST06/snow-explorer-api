import sys
import types
import unittest

from flask import Flask
from peewee import SqliteDatabase

sys.modules.setdefault("boto3", types.SimpleNamespace())

from app.models.resort import Resort  # noqa: E402
from app.routes.public_resorts import bp_public  # noqa: E402


class PublicResortsTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.database.bind([Resort])
        self.database.connect()
        self.database.create_tables([Resort])

        app = Flask(__name__)
        app.register_blueprint(bp_public)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables([Resort])
        self.database.close()

    def create_resort(self, identifier, name, slug, is_active=True, **fields):
        return Resort.create(
            id=identifier,
            name=name,
            slug=slug,
            is_active=is_active,
            **fields,
        )

    def test_active_filter_excludes_inactive_and_invalid_public_slugs(self):
        self.create_resort("1", "Active", "active", True)
        self.create_resort("2", "Inactive", "inactive", False)
        self.create_resort("3", "Blank slug", "   ", True)

        response = self.client.get("/api/resorts/?active=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["slug"] for item in response.get_json()], ["active"])

    def test_public_list_needs_no_authentication_and_has_required_fields(self):
        self.create_resort(
            "1",
            "Auron",
            "auron",
            region_name="Provence-Alpes-Côte d’Azur",
            cover_image_url="https://cdn.example.test/auron.jpg",
        )

        response = self.client.get("/api/resorts/?active=true")
        resort = response.get_json()[0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Cache-Control"],
            "public, max-age=300, s-maxage=3600",
        )
        self.assertEqual(resort["id"], "1")
        self.assertEqual(resort["name"], "Auron")
        self.assertEqual(resort["slug"], "auron")
        self.assertIs(resort["is_active"], True)
        self.assertEqual(resort["region"]["name"], "Provence-Alpes-Côte d’Azur")
        self.assertEqual(
            resort["cover_image_url"], "https://cdn.example.test/auron.jpg"
        )

    def test_search_matches_name_case_insensitively_and_combines_with_active(self):
        self.create_resort("1", "Aurón", "auron", True)
        self.create_resort("2", "Auron inactive", "auron-inactive", False)
        self.create_resort("3", "Elsewhere", "elsewhere", True)

        response = self.client.get("/api/resorts/?q=AUR&active=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.get_json()], ["1"])

    def test_limit_and_stable_name_then_id_order(self):
        self.create_resort("2", "Beta", "beta-2")
        self.create_resort("1", "Beta", "beta-1")
        self.create_resort("3", "Alpha", "alpha")

        response = self.client.get("/api/resorts/?active=true&limit=2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.get_json()], ["3", "1"])

    def test_invalid_limits_return_clear_400(self):
        for value in ("0", "-1", "1.5", "abc", "201", "01"):
            with self.subTest(value=value):
                response = self.client.get(f"/api/resorts/?limit={value}")
                self.assertEqual(response.status_code, 400)
                self.assertIn("positive integer", response.get_json()["error"])

    def test_active_false_is_rejected_instead_of_exposing_admin_data(self):
        response = self.client.get("/api/resorts/?active=false")

        self.assertEqual(response.status_code, 400)
        self.assertIn("must be true", response.get_json()["error"])

    def test_empty_database_returns_empty_list(self):
        response = self.client.get("/api/resorts/?active=true&limit=6")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])


if __name__ == "__main__":
    unittest.main()
