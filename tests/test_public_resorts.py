import sys
import types
import unittest
from unittest.mock import patch
from datetime import date, datetime, timezone

from flask import Flask
from peewee import SqliteDatabase

sys.modules.setdefault("boto3", types.SimpleNamespace())

from app.models.resort import Resort  # noqa: E402
from app.models.region import Region  # noqa: E402
from app.models.piste import Piste  # noqa: E402
from app.models.lift import Lift  # noqa: E402
from app.models.station_widgets import StationWidgets  # noqa: E402
from app.models.ski_pass import (  # noqa: E402
    SkiPassPeriod,
    SkiPassPrice,
    SkiPassProduct,
    SkiPassSeason,
)
from app.routes.public_resorts import bp_public, bp_public_stations  # noqa: E402


class PublicResortsTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.models = [
            Region, Resort, Piste, Lift, StationWidgets, SkiPassSeason,
            SkiPassPeriod, SkiPassProduct, SkiPassPrice,
        ]
        self.database.bind(self.models)
        self.database.connect()
        self.database.create_tables(self.models)

        app = Flask(__name__)
        app.register_blueprint(bp_public)
        app.register_blueprint(bp_public_stations)
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
        self.assertIsNotNone(datetime.fromisoformat(resort["updated_at"]).tzinfo)

    def test_widget_change_updates_public_station_timestamp(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        resort = self.create_resort("1", "Auron", "auron", updated_at=old)

        StationWidgets.create(station_slug="auron", config="{}")
        resort = Resort.get_by_id(resort.id)

        self.assertGreater(resort.updated_at, old)
        response = self.client.get("/api/resorts/auron")
        self.assertEqual(
            datetime.fromisoformat(response.get_json()["updated_at"]),
            resort.updated_at,
        )

    def test_search_matches_name_case_insensitively_and_combines_with_active(self):
        self.create_resort("1", "Aurón", "auron", True)
        self.create_resort("2", "Auron inactive", "auron-inactive", False)
        self.create_resort("3", "Elsewhere", "elsewhere", True)

        response = self.client.get("/api/resorts/?q=AUR&active=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.get_json()], ["1"])

    def test_missing_or_empty_search_returns_every_active_resort(self):
        for identifier, name, slug in (
            ("1", "Auron", "auron"),
            ("2", "Isola 2000", "isola-2000"),
            ("3", "La Clusaz", "la-clusaz"),
            ("4", "Val Thorens", "val-thorens"),
        ):
            self.create_resort(identifier, name, slug)
        self.create_resort("5", "Hidden", "hidden", is_active=False)

        for path in ("/api/resorts/", "/api/resorts/?q="):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item["slug"] for item in response.get_json()],
                    ["auron", "isola-2000", "la-clusaz", "val-thorens"],
                )

    def test_query_failure_returns_explicit_json_500(self):
        with patch(
            "app.routes.public_resorts._base_query",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = self.client.get("/api/resorts/")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(
            response.get_json(), {"error": "Unable to retrieve stations"}
        )

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

    def test_corrected_paca_id_resolves_region_name_from_database(self):
        Region.create(
            id="provence-alpes-cote-d-azur",
            name="Provence Alpes Côte d'azur",
        )
        self.create_resort(
            "1", "Isola 2000", "isola-2000",
            region_id="provence-alpes-cote-d-azur", region_name=None,
        )

        response = self.client.get("/api/resorts/isola-2000")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["region"], {
            "id": "provence-alpes-cote-d-azur",
            "name": "Provence Alpes Côte d'azur",
        })

    def test_station_alias_uses_the_same_canonical_paca_contract(self):
        Region.create(
            id="provence-alpes-cote-d-azur",
            name="Provence-Alpes-Côte d'Azur",
        )
        for identifier, name, slug in (
            ("1", "Isola 2000", "isola-2000"),
            ("2", "Auron", "auron"),
        ):
            self.create_resort(
                identifier,
                name,
                slug,
                region_id="provence-alpes-cote-d-azur",
            )

        for slug in ("isola-2000", "auron"):
            with self.subTest(slug=slug):
                response = self.client.get(f"/api/stations/{slug}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.get_json()["region"]["id"],
                    "provence-alpes-cote-d-azur",
                )

    def test_station_alias_exposes_active_normalized_ski_pass(self):
        resort = self.create_resort(
            "1", "Chamonix", "chamonix", latitude=45.9237,
            longitude=6.8694, altitude_base_m=1035, altitude_top_m=3842,
        )
        inactive = SkiPassSeason.create(
            resort=resort, season="2024-2025", currency="EUR", is_active=False,
        )
        SkiPassPeriod.create(
            season=inactive, external_id="old", name="Ancienne saison",
            start_date=date(2024, 12, 1), end_date=date(2025, 4, 1),
        )
        season = SkiPassSeason.create(
            resort=resort, season="2025-2026", currency="EUR", is_active=True,
        )
        period = SkiPassPeriod.create(
            season=season, external_id="early-season",
            name="Du 29/11/2025 au 19/12/2025",
            start_date=date(2025, 11, 29), end_date=date(2025, 12, 19),
        )
        product = SkiPassProduct.create(
            season=season, external_id="1-day", name="1 jour",
            duration_days=1, duration_label="1 jour",
        )
        SkiPassPrice.create(
            product=product, period=period, category="adult",
            category_label="Adultes (15 à 64 ans)", price_type="dynamic",
            price_min="53.20", price_max="74.00",
            dynamic_label="Tarif dynamique",
        )

        response = self.client.get("/api/stations/chamonix")
        ski_pass = response.get_json()["ski_pass"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["slug"], "chamonix")
        self.assertEqual(response.get_json()["latitude"], 45.9237)
        self.assertEqual(response.get_json()["altitude_base_m"], 1035)
        self.assertEqual(ski_pass["season"], "2025-2026")
        self.assertIs(ski_pass["is_active"], True)
        self.assertEqual(ski_pass["periods"][0]["id"], period.id)
        self.assertEqual(ski_pass["periods"][0]["external_id"], "early-season")
        self.assertEqual(ski_pass["passes"][0]["id"], product.id)
        self.assertEqual(ski_pass["passes"][0]["external_id"], "1-day")
        self.assertEqual(ski_pass["passes"][0]["prices"][0]["price_min"], 53.2)
        self.assertEqual(ski_pass["passes"][0]["prices"][0]["period_id"], period.id)

    def test_station_alias_returns_null_without_active_normalized_season(self):
        resort = self.create_resort("1", "Alpe d'Huez", "alpe-d-huez")
        SkiPassSeason.create(
            resort=resort, season="2025-2026", currency="EUR", is_active=False,
        )

        response = self.client.get("/api/stations/alpe-d-huez")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["name"], "Alpe d'Huez")
        self.assertIsNone(response.get_json()["ski_pass"])

    def test_station_alias_returns_complete_legacy_station_without_json_grid(self):
        self.create_resort(
            "1", "Legacy", "legacy", description_md="Description historique",
            website_url="https://legacy.example.test", pistes_count=12,
        )

        response = self.client.get("/api/stations/legacy")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["description_md"], "Description historique")
        self.assertEqual(response.get_json()["pistes_count"], 12)
        self.assertIsNone(response.get_json()["ski_pass"])

    def test_missing_station_alias_is_json_404(self):
        response = self.client.get("/api/stations/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json()["error"], "station_not_found")

    def test_station_database_failure_is_not_transformed_into_404(self):
        with patch.object(Resort, "get_or_none", side_effect=RuntimeError("database unavailable")):
            response = self.client.get("/api/stations/chamonix")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json()["error"], "Unable to retrieve station")

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
