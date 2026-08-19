import unittest
from unittest.mock import patch

from flask import Flask

from app.models.station_widgets import StationWidgets
from app.routes.stations_widgets import (
    _canonical_forfaits,
    _has_price,
    bp_widgets,
)


class PublicForfaitsTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(bp_widgets)
        self.client = app.test_client()

    def test_canonical_format_is_sanitized_and_values_stay_strings(self):
        result = _canonical_forfaits({"forfaits": {
            "enabled": 1,
            "secret": "not-public",
            "columns": [{"id": "adult", "label": "Adulte"}],
            "items": [{"id": "day", "title": "1 journée", "prices": {"adult": 45}}],
        }})

        self.assertEqual(result, {
            "enabled": True,
            "columns": [{"id": "adult", "label": "Adulte"}],
            "items": [{"id": "day", "title": "1 journée", "prices": {"adult": "45"}}],
        })

    def test_legacy_price_format_is_read_as_canonical(self):
        result = _canonical_forfaits({"forfaits": {
            "enabled": True,
            "items": [{"id": "day", "title": "Journée", "price": "45,00"}],
        }})
        self.assertEqual(result["columns"], [{"id": "price", "label": "Prix"}])
        self.assertEqual(result["items"][0]["prices"], {"price": "45,00"})

    def test_legacy_item_columns_are_read_as_canonical(self):
        result = _canonical_forfaits({"forfaits": {
            "enabled": True,
            "items": [{"title": "Journée", "columns": [
                {"id": "adult", "label": "Adulte", "value": "45"},
            ]}],
        }})
        self.assertEqual(result["columns"], [{"id": "adult", "label": "Adulte"}])
        self.assertEqual(result["items"][0]["prices"], {"adult": "45"})

    def test_empty_and_missing_prices_are_not_usable(self):
        self.assertFalse(_has_price({"items": [{"prices": {"adult": "  "}}]}))
        self.assertFalse(_has_price({"items": [{"prices": {}}]}))

    def test_unknown_or_inactive_station_returns_json_404(self):
        with patch(
            "app.routes.stations_widgets.get_public_active_resort_or_404",
            side_effect=__import__("werkzeug.exceptions", fromlist=["NotFound"]).NotFound(),
        ):
            response = self.client.get("/api/stations/unknown/widgets")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json()["error"], "station_not_found")

    def test_public_widget_response_exposes_canonical_forfaits(self):
        row = type("Row", (), {"config": StationWidgets.to_json({
            "internal": "preserved existing widget contract",
            "forfaits": {"enabled": True, "items": [{"title": "Journée", "price": "42"}]},
        })})()
        with patch("app.routes.stations_widgets.get_public_active_resort_or_404"), patch(
            "app.routes.stations_widgets._active_normalized_grid", return_value=None
        ), patch(
            "app.routes.stations_widgets.StationWidgets.get_or_none", return_value=row
        ):
            response = self.client.get("/api/stations/auron/widgets")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["forfaits"]["items"][0]["prices"], {"price": "42"})

    def test_published_normalized_grid_supersedes_legacy_switch(self):
        row = type("Row", (), {"config": StationWidgets.to_json({
            "forfaits": {"enabled": False, "items": [{"title": "Ancien", "price": "1"}]},
        })})()
        normalized = {"id": 7, "is_active": True, "periods": [], "passes": []}
        with patch("app.routes.stations_widgets.get_public_active_resort_or_404"), patch(
            "app.routes.stations_widgets._active_normalized_grid", return_value=normalized
        ), patch("app.routes.stations_widgets.StationWidgets.get_or_none", return_value=row):
            response = self.client.get("/api/stations/auron/widgets")
        forfaits = response.get_json()["forfaits"]
        self.assertTrue(forfaits["enabled"])
        self.assertEqual(forfaits["tariff_mode"], "normalized")
        self.assertEqual(forfaits["normalized"], normalized)


if __name__ == "__main__":
    unittest.main()
