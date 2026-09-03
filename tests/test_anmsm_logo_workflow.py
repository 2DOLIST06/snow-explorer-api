import unittest
from unittest.mock import patch

from flask import Flask
from peewee import SqliteDatabase

from app.models.admin_user import AdminUser
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.resort import Resort
from app.models.station_logo_candidate import StationLogoCandidate
from app.routes.admin_station_logos import bp_admin_station_logos


MODELS = [Resort, AdminUser, AnmsmStationMapping, StationLogoCandidate]


class AnmsmLogoWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind(MODELS); self.database.connect(); self.database.create_tables(MODELS)
        self.resort = Resort.create(id="station-real-id", name="Alpe d'Huez",
                                    slug="alpe-d-huez", logo_url="https://old/logo.webp")
        self.feed = [{"external_station_id": "A1", "external_name": "ALPE D HUEZ",
            "logo": {"url": "https://anmsm.media.tourinsoft.eu/a.png", "title": "Logo",
                     "credit": "OT", "media_id": "media-1"}}]
        app = Flask(__name__); app.register_blueprint(bp_admin_station_logos)
        self.app = app; self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(MODELS); self.database.close()

    def candidate(self, status="pending"):
        return StationLogoCandidate.create(station=self.resort, external_station_id="A1",
            source_url=self.feed[0]["logo"]["url"], anmsm_media_id="media-1",
            source_checksum="a" * 64, source_format="png", source_width=100,
            source_height=50, source_size_bytes=2000,
            optimized_s3_key="station-logos/candidates/station-real-id/a.webp",
            optimized_url="https://stable/a.webp", optimized_width=512,
            optimized_height=512, optimized_size_bytes=8000, status=status)

    def test_workspace_joins_existing_mapping_and_shows_old_and_new_logo(self):
        AnmsmStationMapping.create(station=self.resort, source="anmsm",
                                   external_station_id=" a1 ", verified=True)
        candidate = self.candidate()
        with patch("app.services.anmsm_logos.fetch_stations", return_value=self.feed), \
             patch("app.routes.admin_station_logos.s3.preview_url", return_value="https://signed/preview"):
            response = self.client.get("/api/admin/anmsm/logos/workspace")
        row = response.get_json()["rows"][0]
        self.assertEqual(row["station_id"], "station-real-id")
        self.assertEqual(row["mapping_method"], "existing")
        self.assertEqual(row["current_logo_url"], "https://old/logo.webp")
        self.assertEqual(row["candidate_id"], candidate.id)
        self.assertEqual(row["candidate_preview_url"], "https://signed/preview")
        self.assertTrue(AnmsmStationMapping.get().verified)

    def test_workspace_keeps_production_counts_and_all_pending_candidates(self):
        """Regression for the 43 mapped / 32 pending / 9 to prepare dataset."""
        feed = []
        pending_ids = set()
        for number in range(43):
            external_id = f"ANMSM-{number:02d}"
            resort = Resort.create(id=f"station-{number:02d}", name=f"Station {number:02d}",
                                   slug=f"station-{number:02d}")
            AnmsmStationMapping.create(station=resort, source="anmsm",
                                       external_station_id=f" {external_id.lower()} ", verified=True)
            has_logo = number < 41
            feed.append({"external_station_id": external_id,
                         "external_name": f"Nom ANMSM {number:02d}",
                         "logo": {"url": f"https://media/{number}.png" if has_logo else None,
                                  "title": None, "credit": None, "media_id": None}})
            if number < 32:
                candidate = StationLogoCandidate.create(
                    station=resort, external_station_id=f" {external_id.swapcase()} ",
                    source_url=f"https://media/{number}.png", source_checksum=f"{number:064x}",
                    source_format="png", source_width=512, source_height=280,
                    source_size_bytes=9000,
                    optimized_s3_key=f"station-logos/candidates/{number}.webp",
                    optimized_width=512, optimized_height=280, optimized_size_bytes=8120,
                    status="pending")
                pending_ids.add(candidate.id)

        with patch("app.services.anmsm_logos.fetch_stations", return_value=feed), \
             patch("app.routes.admin_station_logos.s3.preview_url",
                   side_effect=lambda key: f"https://signed/{key}"):
            body = self.client.get("/api/admin/anmsm/logos/workspace").get_json()

        self.assertEqual(body["stats"]["stations_matched"], 43)
        self.assertEqual(body["stats"]["candidates_pending"], 32)
        self.assertEqual(body["stats"]["candidates_to_prepare"], 9)
        self.assertEqual(sum(row["mapping_status"] == "matched" for row in body["rows"]), 43)
        self.assertEqual(sum(row["candidate_status"] == "pending" for row in body["rows"]), 32)
        self.assertEqual({row["candidate_id"] for row in body["rows"] if row["candidate_id"]},
                         pending_ids)
        pending = next(row for row in body["rows"] if row["candidate_id"])
        self.assertEqual((pending["candidate_size_bytes"], pending["candidate_width"],
                          pending["candidate_height"]), (8120, 512, 280))
        self.assertFalse(pending["preparation_required"])
        self.assertTrue(pending["candidate_preview_url"].startswith("https://signed/"))

    def test_workspace_has_no_unreliable_match(self):
        self.feed[0]["external_name"] = "Completely Different"
        with patch("app.services.anmsm_logos.fetch_stations", return_value=self.feed):
            row = self.client.get("/api/admin/anmsm/logos/workspace").get_json()["rows"][0]
        self.assertEqual(row["mapping_status"], "unmatched")
        self.assertIsNone(row["suggestion"])

    def test_unmatched_station_keeps_anmsm_name_and_source_logo(self):
        self.feed[0]["external_name"] = "Station ANMSM non associée"
        self.feed[0]["logo"].update({
            "url": "https://anmsm.media.tourinsoft.eu/unmatched.png",
            "media_id": "unmatched-media", "title": "Logo source", "credit": "ANMSM",
        })
        with patch("app.services.anmsm_logos.fetch_stations", return_value=self.feed):
            body = self.client.get("/api/admin/anmsm/logos/workspace").get_json()

        row = body["rows"][0]
        self.assertEqual(row["anmsm_station_name"], "Station ANMSM non associée")
        self.assertEqual(row["source_logo_url"],
                         "https://anmsm.media.tourinsoft.eu/unmatched.png")
        self.assertTrue(row["source_has_logo"])
        self.assertEqual(row["anmsm_media_id"], "unmatched-media")
        self.assertEqual(row["anmsm_title"], "Logo source")
        self.assertEqual(row["anmsm_credit"], "ANMSM")
        self.assertIsNone(row["station_id"])
        self.assertIsNone(row["candidate_id"])
        self.assertIsNone(row["candidate_preview_url"])
        self.assertEqual(body["stats"]["logos_available"], 1)

    def test_prepare_one_resumes_without_download(self):
        AnmsmStationMapping.create(station=self.resort, source="anmsm",
                                   external_station_id="A1", verified=True)
        candidate = self.candidate()
        with patch("app.services.anmsm_logos.fetch_stations", return_value=self.feed), \
             patch("app.services.anmsm_logos.download") as download, \
             patch("app.routes.admin_station_logos.s3.preview_url", return_value="https://preview"):
            body = self.client.post("/api/admin/anmsm/logos/prepare",
                                    json={"external_station_id": "A1"}).get_json()
        self.assertTrue(body["unchanged"]); self.assertEqual(body["candidate"]["id"], candidate.id)
        download.assert_not_called()

    def test_bulk_approval_is_partial_preserves_old_logo_and_is_idempotent(self):
        candidate = self.candidate()
        with patch("app.routes.admin_station_logos.s3.validate_webp", return_value=True), \
             patch("app.routes.admin_station_logos.s3.public_url", return_value="https://new/logo.webp"), \
             patch("app.routes.admin_station_logos.invalidate_station") as invalidate:
            first = self.client.post("/api/admin/anmsm/logos/bulk-approve",
                                     json={"candidate_ids": [candidate.id, 999]}).get_json()
            second = self.client.post("/api/admin/anmsm/logos/bulk-approve",
                                      json={"candidate_ids": [candidate.id]}).get_json()
        saved = StationLogoCandidate.get_by_id(candidate.id)
        self.assertEqual((first["approved_count"], first["failed_count"]), (1, 1))
        self.assertEqual(saved.previous_logo_url, "https://old/logo.webp")
        self.assertEqual(Resort.get_by_id(self.resort.id).logo_url, "https://new/logo.webp")
        self.assertTrue(second["results"][0]["unchanged"])
        self.assertEqual(saved.previous_logo_url, "https://old/logo.webp")
        invalidate.assert_called_once_with("alpe-d-huez")

    def test_private_preview_and_options(self):
        candidate = self.candidate()
        with patch.dict("os.environ", {"AWS_S3_PRIVATE": "true"}), \
             patch("app.services.s3.client") as client:
            client.return_value.generate_presigned_url.return_value = "https://signed/readable"
            from app.services.s3 import preview_url
            self.assertEqual(preview_url(candidate.optimized_s3_key), "https://signed/readable")
        self.assertEqual(self.client.options("/api/admin/anmsm/logos/bulk-approve").status_code, 200)


if __name__ == "__main__":
    unittest.main()
