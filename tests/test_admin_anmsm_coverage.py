import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from flask import Flask
from peewee import SqliteDatabase

from app.models.admin_user import AdminUser
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.anmsm_station_snapshot import AnmsmStationSnapshot
from app.models.resort import Resort
from app.models.station_logo_candidate import StationLogoCandidate
from app.models.station_piste_map_candidate import StationPisteMapCandidate
from app.routes.admin_anmsm_coverage import bp_admin_anmsm_coverage

MODELS = [Resort, AdminUser, AnmsmStationMapping, AnmsmStationSnapshot,
          StationLogoCandidate, StationPisteMapCandidate]


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.db = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.db.bind(MODELS); self.db.connect(); self.db.create_tables(MODELS)
        self.now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        self.app = Flask(__name__); self.app.register_blueprint(bp_admin_anmsm_coverage)
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.drop_tables(MODELS); self.db.close()

    def resort(self, key, **values):
        return Resort.create(id=key, name=values.pop("name", key.title()), slug=key,
                             is_active=values.pop("is_active", True), **values)

    def snapshot(self, external_id, **values):
        if values.get("piste_map_available") is not None:
            values.setdefault("piste_map_observation_complete", True)
        return AnmsmStationSnapshot.create(external_station_id=external_id,
            station_name=values.pop("station_name", external_id), last_seen_at=self.now, **values)

    def mapping(self, resort, external_id):
        return AnmsmStationMapping.create(station=resort, external_station_id=external_id,
                                          source="anmsm", verified=True)

    def logo(self, resort, status="pending", **values):
        defaults=dict(external_station_id="A", source_url="https://source/logo.png",
            source_checksum=(str(resort.id)[0] * 64)[:64], source_format="png", source_width=10,
            source_height=10, source_size_bytes=10, status=status, detected_at=self.now)
        defaults.update(values); return StationLogoCandidate.create(station=resort, **defaults)

    def piste_map(self, resort, status="pending", **values):
        defaults=dict(external_station_id="A", anmsm_media_id="map", source_url="https://source/map.pdf",
            source_checksum=(str(resort.id)[0] * 64)[:64], source_format="pdf", source_size_bytes=10,
            original_s3_key="original.pdf", status=status, detected_at=self.now)
        defaults.update(values); return StationPisteMapCandidate.create(station=resort, **defaults)

    def get(self, query=""):
        response = self.client.get("/api/admin/anmsm/coverage" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_all_workflows_unknown_provenance_contact_and_anmsm_only(self):
        published = self.resort("published", logo_url="https://cdn/logo.webp",
                                pistes_large_map_url="https://cdn/map.webp")
        self.mapping(published, "PUB"); self.snapshot("PUB", logo_available=True, piste_map_available=True)
        self.logo(published, status="approved", optimized_s3_key="logo.webp", approved_at=self.now)
        self.piste_map(published, status="approved", display_s3_key="map.webp", approved_at=self.now)

        available = self.resort("available"); self.mapping(available, "AVAILABLE")
        self.snapshot("AVAILABLE", logo_available=True, logo_url="https://source/logo", piste_map_available=None)
        prepare = self.resort("prepare"); self.mapping(prepare, "PREPARE")
        self.snapshot("PREPARE", logo_available=None, piste_map_available=True)
        self.piste_map(prepare, original_s3_key="raw.pdf", display_s3_key=None)
        review = self.resort("review"); self.mapping(review, "REVIEW")
        self.snapshot("REVIEW", logo_available=True, piste_map_available=None)
        self.logo(review, optimized_s3_key="review.webp", optimized_url="https://preview/review.webp")
        error = self.resort("error"); self.mapping(error, "ERROR")
        self.snapshot("ERROR", logo_available=True, piste_map_available=None)
        self.logo(error, status="error", error_code="bad", error_message="broken")
        contact = self.resort("contact"); self.mapping(contact, "CONTACT")
        self.snapshot("CONTACT", logo_available=False, piste_map_available=False)
        manual = self.resort("manual", logo_url="https://manual/logo.png")
        self.mapping(manual, "MANUAL"); self.snapshot("MANUAL", logo_available=True, piste_map_available=None)
        self.resort("unmapped")
        self.snapshot("ONLY", station_name="Only ANMSM", logo_available=True, piste_map_available=False)
        self.snapshot("EMPTY", station_name="No media", logo_available=False,
                      piste_map_available=False)

        body = self.get("?per_page=100")
        rows = {row["station_id"]: row for row in body["snow_explorer_stations"]}
        self.assertEqual(rows["published"]["resources"]["logo"]["workflow_status"], "published")
        self.assertEqual(rows["published"]["resources"]["piste_map"]["workflow_status"], "published")
        self.assertEqual(rows["available"]["resources"]["logo"]["workflow_status"], "available_not_imported")
        self.assertEqual(rows["prepare"]["resources"]["piste_map"]["workflow_status"], "to_prepare")
        self.assertEqual(rows["review"]["resources"]["logo"]["workflow_status"], "ready_to_review")
        self.assertEqual(rows["error"]["resources"]["logo"]["workflow_status"], "error")
        self.assertEqual(rows["manual"]["resources"]["logo"]["published_source"], "unknown")
        self.assertEqual(rows["unmapped"]["mapping_status"], "unmatched")
        self.assertEqual(rows["unmapped"]["resources"]["logo"]["availability_status"], "unknown")
        self.assertFalse(rows["unmapped"]["needs_station_contact"])
        self.assertTrue(rows["contact"]["needs_station_contact"])
        self.assertEqual(rows["contact"]["missing_resource_types"], ["logo", "piste_map"])
        only = {row["anmsm_external_station_id"]: row for row in body["anmsm_only_stations"]}
        self.assertEqual(set(only), {"ONLY", "EMPTY"})
        self.assertFalse(only["EMPTY"]["logo_available"])
        self.assertFalse(only["EMPTY"]["piste_map_available"])
        self.assertEqual(body["stats"]["stations_needing_contact"], 1)
        self.assertEqual(body["stats"]["logos_published_by_anmsm"], 1)
        self.assertEqual(body["stats"]["piste_maps_published_by_anmsm"], 1)
        self.assertEqual(body["stats"]["errors"], 1)

    def test_search_filters_sorts_pagination_csv_and_no_writes(self):
        for key, available in (("alpha", False), ("beta", None), ("gamma", True)):
            resort = self.resort(key); self.mapping(resort, key.upper())
            self.snapshot(key.upper(), station_name=key.title(), logo_available=available,
                          piste_map_available=available)
        before = [model.select().count() for model in MODELS]
        searched = self.get("?search=beta")
        self.assertEqual(searched["pagination"]["total"], 1)
        filtered = self.get("?needs_station_contact=true&missing_resource=logo")
        self.assertEqual([row["station_id"] for row in filtered["snow_explorer_stations"]], ["alpha"])
        unknown = self.get("?needs_availability_control=true&availability_status=unknown")
        self.assertEqual([row["station_id"] for row in unknown["snow_explorer_stations"]], ["beta"])
        available = self.get("?resource=logo&workflow_status=available_not_imported")
        self.assertEqual([row["station_id"] for row in available["snow_explorer_stations"]], ["gamma"])
        page = self.get("?sort=name&direction=desc&per_page=1&page=2")
        self.assertEqual(page["pagination"], {"page": 2, "per_page": 1, "total": 3, "pages": 3})
        response = self.client.get("/api/admin/anmsm/coverage?format=csv")
        self.assertEqual(response.status_code, 200); self.assertIn(b"Alpha", response.data)
        self.assertEqual(before, [model.select().count() for model in MODELS])

    def test_latest_candidate_only_prevents_duplicates(self):
        resort = self.resort("duplicate"); self.mapping(resort, "D")
        self.snapshot("D", logo_available=True, piste_map_available=None)
        self.logo(resort, source_checksum="a" * 64, optimized_s3_key="old.webp",
                  optimized_url="https://preview/old.webp")
        newest = self.logo(resort, source_checksum="b" * 64, optimized_s3_key="new.webp",
                           optimized_url="https://preview/new.webp")
        body = self.get()
        self.assertEqual(len(body["snow_explorer_stations"]), 1)
        self.assertEqual(body["snow_explorer_stations"][0]["resources"]["logo"]["candidate_id"], newest.id)

    def test_incomplete_negative_plan_observation_remains_unknown(self):
        resort = self.resort("incomplete"); self.mapping(resort, "I")
        self.snapshot("I", piste_map_available=False,
                      piste_map_observation_complete=False)
        row = self.get()["snow_explorer_stations"][0]
        self.assertEqual(row["resources"]["piste_map"]["availability_status"], "unknown")
        self.assertFalse(row["resources"]["piste_map"]["needs_station_contact"])

    def test_historical_approved_candidates_prove_current_publications(self):
        resort = self.resort("history", logo_url="https://cdn/approved-logo.webp",
                             pistes_large_map_url="https://cdn/approved-map.webp")
        self.mapping(resort, "H"); self.snapshot("H", logo_available=True,
                                                 piste_map_available=True)
        self.logo(resort, status="approved", source_checksum="1" * 64,
                  optimized_s3_key="approved-logo.webp", approved_at=self.now)
        self.logo(resort, status="pending", source_checksum="2" * 64,
                  optimized_s3_key="new-logo.webp", optimized_url="https://preview/new-logo.webp")
        self.piste_map(resort, status="approved", anmsm_media_id="old",
                       source_checksum="3" * 64, display_s3_key="approved-map.webp",
                       approved_at=self.now)
        newest_map = self.piste_map(resort, status="pending", anmsm_media_id="new",
                                    source_checksum="4" * 64, display_s3_key="new-map.webp")
        with patch("app.services.anmsm_logos.fetch_stations") as logo_fetch, \
             patch("app.services.anmsm_piste_maps.fetch_maps") as map_fetch:
            row = self.get()["snow_explorer_stations"][0]
        self.assertEqual(row["resources"]["logo"]["published_source"], "anmsm")
        self.assertEqual(row["resources"]["piste_map"]["published_source"], "anmsm")
        self.assertEqual(row["resources"]["piste_map"]["candidate_id"], newest_map.id)
        logo_fetch.assert_not_called(); map_fetch.assert_not_called()


if __name__ == "__main__": unittest.main()
