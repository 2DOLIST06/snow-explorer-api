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
        with patch("app.routes.stations_widgets._get_active_widgets_row", return_value=None):
            response = self.client.get("/api/stations/unknown/widgets")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json()["error"], "station_not_found")

    def test_public_widget_response_exposes_canonical_forfaits(self):
        row = type("Row", (), {"config": StationWidgets.to_json({
            "internal": "preserved existing widget contract",
            "forfaits": {"enabled": True, "items": [{"title": "Journée", "price": "42"}]},
        })})()
        with patch(
            "app.routes.stations_widgets._get_active_widgets_row",
            return_value={"station_slug": "auron", "widgets_config": row.config},
        ):
            response = self.client.get("/api/stations/auron/widgets")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["forfaits"]["items"][0]["prices"],
            {"c-1-1": "Journée", "c-1-2": "42"},
        )


if __name__ == "__main__":
    unittest.main()
