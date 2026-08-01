import sys
import types
import unittest
from datetime import date

from flask import Flask
from peewee import SqliteDatabase

sys.modules.setdefault("boto3", types.SimpleNamespace())

from app.models.resort import Resort  # noqa: E402
from app.models.region import Region  # noqa: E402
from app.models.piste import Piste  # noqa: E402
from app.models.lift import Lift  # noqa: E402
from app.models.station_widgets import StationWidgets  # noqa: E402
from app.routes.public_resorts import bp_public  # noqa: E402


class PublicResortsTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.models = [Region, Resort, Piste, Lift, StationWidgets]
        self.database.bind(self.models)
        self.database.connect()
        self.database.create_tables(self.models)

        app = Flask(__name__)
        app.register_blueprint(bp_public)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(self.models)
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

    def test_public_detail_contract_counts_region_dates_urls_and_cfg(self):
        Region.create(id="paca", name="Provence-Alpes-Côte d’Azur")
        resort = self.create_resort(
            "1", "Auron", "auron", region_id="paca", region_name=None,
            pistes_count=None, lifts_count=None,
            season_open_date=date(2025, 12, 6), season_close_date=date(2026, 4, 12),
            cover_image_url="  ", logo_url="", website_url=" https://auron.com ",
            pistes_small_map_url="", pistes_large_map_url=" ", snowpark_map_url="",
        )
        Piste.create(id="p1", resort=resort, name="Verte", difficulty="green")
        Piste.create(id="p2", resort=resort, name="Bleue", difficulty="blue")
        Lift.create(id="l1", resort=resort, name="Télésiège", type="chair")
        StationWidgets.create(
            station_slug="auron",
            config=StationWidgets.to_json({
                "widgets": {"widgets": {"pistes": {"enabled": True}}},
                "adminToken": "secret",
            }),
        )

        response = self.client.get("/api/resorts/auron")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=300, s-maxage=3600")
        self.assertEqual(data["region"], {"id": "paca", "name": "Provence-Alpes-Côte d’Azur"})
        self.assertEqual(data["pistes_count"], 2)
        self.assertEqual(data["lifts_count"], 1)
        self.assertEqual(data["season_open_date"], "2025-12-06")
        self.assertEqual(data["season_close_date"], "2026-04-12")
        for field in ("cover_image_url", "logo_url", "pistes_small_map_url",
                      "pistes_large_map_url", "snowpark_map_url"):
            self.assertIsNone(data[field])
        self.assertEqual(data["website_url"], "https://auron.com")
        self.assertNotIn("widgets", data["cfg"])
        self.assertNotIn("adminToken", data)
        self.assertNotIn("adminToken", data["cfg"])
        self.assertEqual(data["cfg"]["pistes"], {"enabled": True})

    def test_stored_non_negative_counts_are_authoritative(self):
        resort = self.create_resort("1", "Stored", "stored", pistes_count=7, lifts_count=4)
        Piste.create(id="p1", resort=resort, name="One", difficulty="green")
        response = self.client.get("/api/resorts/stored")
        self.assertEqual(response.get_json()["pistes_count"], 7)
        self.assertEqual(response.get_json()["lifts_count"], 4)

    def test_missing_and_inactive_detail_are_clean_json_404(self):
        self.create_resort("1", "Inactive", "inactive", is_active=False)
        for slug in ("missing", "inactive"):
            with self.subTest(slug=slug):
                response = self.client.get(f"/api/resorts/{slug}")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.content_type, "application/json")
                self.assertEqual(response.get_json()["error"], "resort_not_found")

    def test_empty_database_detail_is_404_not_500(self):
        response = self.client.get("/api/resorts/anything")
        self.assertEqual(response.status_code, 404)
        self.assertIsInstance(response.get_json(), dict)


if __name__ == "__main__":
    unittest.main()
