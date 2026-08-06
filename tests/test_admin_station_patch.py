import sys
import types
import unittest
from unittest.mock import patch

from flask import Flask

sys.modules.setdefault("boto3", types.SimpleNamespace())

from app import create_app
from app.routes.admin_stations import bp_admin_st


class DummyResort:
    def __init__(self):
        self.id = "station-id"
        self.slug = "station-test"
        self.name = "Station Test"
        self.logo_url = "https://cdn.example.test/old-logo.png"
        self.cover_image_url = "https://cdn.example.test/old-cover.jpg"
        self.website_url = "https://station.example.test"
        self.region_name = "Alpes"
        self.is_active = False
        self.save_count = 0

    def save(self):
        self.save_count += 1

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "logo_url": self.logo_url,
            "cover_image_url": self.cover_image_url,
            "website_url": self.website_url,
            "region_name": self.region_name,
            "is_active": self.is_active,
        }


class AdminStationPatchTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(bp_admin_st)
        self.client = app.test_client()
        self.resort = DummyResort()

    def patch_station(self, payload):
        with patch(
            "app.routes.admin_stations.Resort.get_or_none",
            return_value=self.resort,
        ), patch("app.routes.admin_stations.db.atomic"):
            return self.client.patch(
                "/api/admin/stations/station-test",
                json=payload,
            )

    def test_patch_only_logo_url_preserves_other_fields(self):
        original = self.resort.to_dict()
        response = self.patch_station(
            {"logo_url": "https://cdn.example.test/new-logo.png"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.resort.logo_url,
            "https://cdn.example.test/new-logo.png",
        )
        for field in (
            "name",
            "cover_image_url",
            "website_url",
            "region_name",
            "is_active",
        ):
            self.assertEqual(getattr(self.resort, field), original[field])

    def test_patch_only_cover_image_url(self):
        response = self.patch_station(
            {"cover_image_url": "https://cdn.example.test/new-cover.jpg"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.resort.cover_image_url,
            "https://cdn.example.test/new-cover.jpg",
        )
        self.assertEqual(
            self.resort.logo_url,
            "https://cdn.example.test/old-logo.png",
        )
        self.assertFalse(self.resort.is_active)

    def test_patch_only_is_active(self):
        response = self.patch_station({"is_active": True})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.resort.is_active)
        self.assertEqual(
            self.resort.logo_url,
            "https://cdn.example.test/old-logo.png",
        )
        self.assertEqual(
            self.resort.cover_image_url,
            "https://cdn.example.test/old-cover.jpg",
        )

    def test_empty_payload_is_a_successful_no_op(self):
        original = self.resort.to_dict()

        response = self.patch_station({})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.resort.to_dict(), original)

    def test_unknown_field_is_rejected_without_saving(self):
        response = self.patch_station({"unknown_field": "value"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.resort.save_count, 0)

    def test_missing_station_returns_404(self):
        with patch(
            "app.routes.admin_stations.Resort.get_or_none",
            return_value=None,
        ):
            response = self.client.patch(
                "/api/admin/stations/missing",
                json={"logo_url": "https://cdn.example.test/logo.png"},
            )

        self.assertEqual(response.status_code, 404)

    def test_application_uses_partial_station_patch_route(self):
        app = create_app({"SKIP_DATABASE_INIT": True})
        adapter = app.url_map.bind("example.test")

        endpoint, _ = adapter.match(
            "/api/admin/stations/station-test",
            method="PATCH",
        )

        self.assertEqual(endpoint, "admin_stations.patch_resort_admin")


if __name__ == "__main__":
    unittest.main()
