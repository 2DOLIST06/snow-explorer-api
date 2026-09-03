import unittest
from unittest.mock import patch

from flask import Flask
from peewee import SqliteDatabase

from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.admin_user import AdminUser
from app.models.resort import Resort
from app.models.station_logo_candidate import StationLogoCandidate
from app.routes.admin_anmsm_mappings import bp_admin_anmsm_mappings
from app.services.anmsm_station_mappings import confirm_mappings, suggestions
from app.services.anmsm_logos import sync


MODELS = [Resort, AdminUser, AnmsmStationMapping, StationLogoCandidate]


class AnmsmStationMappingTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind(MODELS); self.database.connect(); self.database.create_tables(MODELS)
        self.alpha = Resort.create(id="alpha", name="Val d'Isère", slug="val-disere")
        self.beta = Resort.create(id="beta", name="Les Deux Alpes", slug="les-deux-alpes")
        self.app = Flask(__name__); self.app.register_blueprint(bp_admin_anmsm_mappings)
        self.client = self.app.test_client()
        self.feed = [
            {"external_station_id": "A1", "external_name": "VAL D ISERE",
             "logo": {"url": "https://logo", "title": "Logo", "credit": "ANMSM", "media_id": "m1"}},
            {"external_station_id": "A2", "external_name": "Deux Alpess",
             "logo": {"url": None, "title": None, "credit": None, "media_id": None}},
        ]

    def tearDown(self):
        self.database.drop_tables(MODELS); self.database.close()

    def test_exact_and_approximate_suggestions_are_never_persisted(self):
        exact = suggestions("Val-d’Isère", [self.alpha, self.beta])
        approximate = suggestions("Deux Alpess", [self.alpha, self.beta])
        self.assertEqual((exact[0]["station_id"], exact[0]["score"], exact[0]["match_type"]),
                         ("alpha", 100, "normalized_exact"))
        self.assertEqual(approximate[0]["station_id"], "beta")
        self.assertEqual(approximate[0]["match_type"], "similar")
        self.assertEqual(AnmsmStationMapping.select().count(), 0)

    def test_listing_unmatched_pagination_stats_and_contract(self):
        with patch("app.routes.admin_anmsm_mappings.fetch_stations", return_value=self.feed):
            response = self.client.get("/api/admin/anmsm/station-mappings?status=unmatched&per_page=1")
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["pagination"], {"page": 1, "per_page": 1, "total": 2, "pages": 2})
        self.assertEqual(body["stats"], {"received": 2, "matched": 0, "unmatched": 2, "without_logo": 1})
        self.assertIsNone(body["items"][0]["mapping"])
        self.assertEqual(set(body["items"][0]["logo"]), {"url", "title", "credit"})

    def test_simple_grouped_unknown_duplicate_and_correction(self):
        first = confirm_mappings([{"external_station_id": "A1", "station_id": "alpha"}], {"A1", "A2"})
        self.assertTrue(first[0]["ok"]); self.assertTrue(AnmsmStationMapping.get().verified)

        grouped = confirm_mappings([
            {"external_station_id": "A1", "station_id": "beta"},
            {"external_station_id": "A2", "station_id": "alpha"},
            {"external_station_id": "missing", "station_id": "beta"},
            {"external_station_id": "A2", "station_id": "missing"},
        ], {"A1", "A2"})
        self.assertTrue(grouped[0]["ok"])  # explicit correction of A1
        self.assertEqual(grouped[1]["error"], "station_already_mapped")
        self.assertEqual(grouped[2]["error"], "unknown_external_station")
        self.assertEqual(grouped[3]["error"], "unknown_station")
        self.assertEqual(AnmsmStationMapping.get().station_id, "beta")

    def test_confirm_route_group_and_delete_only_mapping(self):
        with patch("app.routes.admin_anmsm_mappings.fetch_stations", return_value=self.feed):
            response = self.client.post("/api/admin/anmsm/station-mappings/confirm", json={"mappings": [
                {"external_station_id": "A1", "station_id": "alpha"},
                {"external_station_id": "A2", "station_id": "beta"},
            ]})
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(AnmsmStationMapping.select().count(), 2)
        deleted = self.client.delete("/api/admin/anmsm/station-mappings/A1")
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNotNone(Resort.get_or_none(Resort.id == "alpha"))

    def test_sync_after_confirmation_has_stable_numeric_stats_and_pending_candidate(self):
        confirm_mappings([{"external_station_id": "A1", "station_id": "alpha"}], {"A1"})
        with self.app.app_context(), \
             patch("app.services.anmsm_logos.fetch_stations", return_value=[self.feed[0]]), \
             patch("app.services.anmsm_logos.download", return_value=b"source"), \
             patch("app.services.anmsm_logos.optimize", return_value=(b"webp", {
                 "source_format": "png", "source_width": 10, "source_height": 10,
                 "content_width": 10, "content_height": 10, "aspect_ratio": 1,
                 "visual_occupancy_width": .02, "visual_occupancy_height": .02,
                 "optimized_width": 512, "optimized_height": 512, "warnings": []})), \
             patch("app.services.anmsm_logos.s3.put_webp", return_value="https://s3/candidate.webp"):
            stats = sync()
        expected = {"stations_received", "stations_matched", "stations_unmatched", "logos_created",
                    "logos_updated", "logos_unchanged", "stations_without_logo",
                    "conversions_succeeded", "errors", "duration_seconds"}
        self.assertEqual(set(stats), expected)
        self.assertTrue(all(isinstance(stats[key], (int, float)) for key in expected))
        self.assertEqual((stats["stations_matched"], stats["logos_created"]), (1, 1))
        self.assertEqual(StationLogoCandidate.get().status, "pending")


if __name__ == "__main__": unittest.main()
