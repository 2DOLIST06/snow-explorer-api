import unittest

from flask import Flask
from peewee import SqliteDatabase

from app.models.admin_user import AdminUser
from app.models.resort import Resort
from app.models.station_logo_candidate import StationLogoCandidate
from app.routes.admin_station_logos import bp_admin_station_logos


MODELS = [Resort, AdminUser, StationLogoCandidate]


class AdminStationLogoListingTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind(MODELS)
        self.database.connect()
        self.database.create_tables(MODELS)

        self.station_with_logo = Resort.create(
            id="with-logo",
            name="Alpe d'Huez",
            slug="alpe-d-huez",
            logo_url="https://cdn.example.test/current.webp",
        )
        self.station_without_logo = Resort.create(
            id="without-logo",
            name="Station sans logo",
            slug="station-sans-logo",
            logo_url=None,
        )
        self.candidate_with_previous = self._create_candidate(
            self.station_with_logo,
            "candidate-with-previous",
            previous_logo_url="https://cdn.example.test/previous.webp",
        )
        self.candidate_without_previous = self._create_candidate(
            self.station_without_logo,
            "candidate-without-previous",
        )

        app = Flask(__name__)
        app.register_blueprint(bp_admin_station_logos)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    @staticmethod
    def _create_candidate(station, checksum, previous_logo_url=None):
        return StationLogoCandidate.create(
            station=station,
            external_station_id=f"external-{station.id}",
            source_url=f"https://anmsm.example.test/{station.id}.png",
            source_checksum=checksum,
            source_format="png",
            source_width=100,
            source_height=100,
            source_size_bytes=1024,
            optimized_url=f"https://cdn.example.test/{station.id}-candidate.webp",
            previous_logo_url=previous_logo_url,
        )

    def test_listing_includes_current_and_previous_logos_without_writing(self):
        resort_state_before = {
            resort.id: (resort.logo_url, resort.updated_at)
            for resort in Resort.select()
        }
        candidate_state_before = {
            candidate.id: (candidate.status, candidate.previous_logo_url, candidate.updated_at)
            for candidate in StationLogoCandidate.select()
        }

        response = self.client.get("/api/admin/anmsm/logos")

        self.assertEqual(response.status_code, 200)
        items = {item["station_id"]: item for item in response.get_json()["items"]}
        self.assertEqual(
            items["with-logo"]["current_logo_url"],
            "https://cdn.example.test/current.webp",
        )
        self.assertEqual(
            items["with-logo"]["previous_logo_url"],
            "https://cdn.example.test/previous.webp",
        )
        self.assertIsNone(items["without-logo"]["current_logo_url"])
        self.assertIsNone(items["without-logo"]["previous_logo_url"])
        self.assertEqual(
            items["without-logo"]["optimized_url"],
            "https://cdn.example.test/without-logo-candidate.webp",
        )

        resort_state_after = {
            resort.id: (resort.logo_url, resort.updated_at)
            for resort in Resort.select()
        }
        candidate_state_after = {
            candidate.id: (candidate.status, candidate.previous_logo_url, candidate.updated_at)
            for candidate in StationLogoCandidate.select()
        }
        self.assertEqual(resort_state_after, resort_state_before)
        self.assertEqual(candidate_state_after, candidate_state_before)


if __name__ == "__main__":
    unittest.main()
