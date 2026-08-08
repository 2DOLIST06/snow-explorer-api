import sys
import types
import unittest
from unittest.mock import patch

from flask import Flask

sys.modules.setdefault("boto3", types.SimpleNamespace())

from app.models.station_widgets import StationWidgets
from app.routes.admin_stations import bp_admin_st
from app.routes.stations_widgets import bp_widgets


class DummyWidgets:
    def __init__(self, config):
        self.config = StationWidgets.to_json(config)
        self.save_count = 0

    def save(self):
        self.save_count += 1


class DummyResort:
    def to_dict(self):
        return {"slug": "station-test"}


class OfficialMapUrlTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(bp_admin_st)
        app.register_blueprint(bp_widgets)
        self.client = app.test_client()

    def patch_widgets(self, row, value):
        with patch("app.routes.admin_stations.StationWidgets.get_or_none", return_value=row):
            return self.client.patch(
                "/api/admin/stations/station-test/widgets",
                json={"pistes": {"officialMapUrl": value}},
            )

    def test_admin_patch_persists_absolute_https_url_without_changing_map_urls(self):
        row = DummyWidgets({"pistes": {"smallMapUrl": "small", "largeMapUrl": "large"}})
        response = self.patch_widgets(row, "https://station.example/plan-des-pistes")

        self.assertEqual(response.status_code, 200)
        pistes = StationWidgets.from_json(row.config)["pistes"]
        self.assertEqual(pistes["officialMapUrl"], "https://station.example/plan-des-pistes")
        self.assertEqual(pistes["smallMapUrl"], "small")
        self.assertEqual(pistes["largeMapUrl"], "large")

    def test_admin_patch_normalizes_empty_string_to_null(self):
        row = DummyWidgets({"pistes": {"enabled": True}})
        response = self.patch_widgets(row, "   ")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(StationWidgets.from_json(row.config)["pistes"]["officialMapUrl"])

    def test_admin_patch_rejects_non_http_protocol(self):
        row = DummyWidgets({"pistes": {"enabled": True}})
        response = self.patch_widgets(row, "ftp://station.example/plan")

        self.assertEqual(response.status_code, 400)
        self.assertIn("http: ou https:", response.get_data(as_text=True))
        self.assertEqual(row.save_count, 0)

    def test_admin_get_serializes_url(self):
        row = DummyWidgets({"pistes": {"officialMapUrl": "http://station.example/map"}})
        with patch("app.routes.admin_stations.Resort.get_or_none", return_value=DummyResort()), patch(
            "app.routes.admin_stations.StationWidgets.get_or_none", return_value=row
        ):
            response = self.client.get("/api/admin/stations/station-test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["widgets"]["pistes"]["officialMapUrl"], "http://station.example/map")

    def test_get_routes_are_backward_compatible_with_missing_field(self):
        row = DummyWidgets({"pistes": {"enabled": True}})
        with patch("app.routes.admin_stations.Resort.get_or_none", return_value=DummyResort()), patch(
            "app.routes.admin_stations.StationWidgets.get_or_none", return_value=row
        ):
            admin_response = self.client.get("/api/admin/stations/station-test")
        with patch("app.routes.stations_widgets.get_public_active_resort_or_404"), patch(
            "app.routes.stations_widgets.StationWidgets.get_or_none", return_value=row
        ):
            public_response = self.client.get("/api/stations/station-test/widgets")

        self.assertIsNone(admin_response.get_json()["widgets"]["pistes"]["officialMapUrl"])
        self.assertIsNone(public_response.get_json()["pistes"]["officialMapUrl"])


if __name__ == "__main__":
    unittest.main()
