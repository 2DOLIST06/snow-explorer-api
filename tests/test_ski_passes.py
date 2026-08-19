import copy
import unittest
from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock, patch

from flask import Flask

from app.routes.ski_passes import bp_admin_station_ski_passes, bp_ski_passes
from app.services.ski_passes import preview, replace_grid, serialize_season


def grid():
    return {
        "station_slug": "chamonix", "season": "2026-2027", "currency": "EUR",
        "source_url": "https://example.test/tarifs", "updated_at": "2026-08-19",
        "periods": [
            {"id": "low", "name": "Basse", "start_date": "2026-11-01", "end_date": "2026-12-18"},
            {"id": "high", "name": "Haute", "start_date": "2026-12-19", "end_date": "2027-03-21"},
        ],
        "passes": [
            {"id": "1-day", "name": "1 jour", "duration_days": 1, "duration_label": "1 jour", "prices": [
                {"period_id": "high", "category": "adult", "category_label": "Adulte", "price_type": "fixed", "price": 74},
                {"period_id": "high", "category": "child", "category_label": "Enfant", "price_type": "dynamic", "price_min": 55, "price_max": 65, "dynamic_label": "Selon réservation"},
                {"period_id": "low", "category": "student", "category_label": "Étudiant", "price_type": "fixed", "price": 49},
            ]},
            {"id": "3-days", "name": "3 jours", "duration_days": 3, "duration_label": "3 jours", "prices": []},
        ],
    }


class SkiPassValidationTests(unittest.TestCase):
    def lookup(self, slug):
        return object() if slug == "chamonix" else None

    def test_fixed_dynamic_periods_categories_and_products(self):
        result = preview(grid(), self.lookup)
        self.assertEqual((result["valid"], result["periods_count"], result["passes_count"], result["prices_count"]), (True, 2, 2, 3))

    def test_invalid_json_and_unknown_station(self):
        self.assertFalse(preview([], self.lookup)["valid"])
        payload = grid(); payload["station_slug"] = "unknown"
        self.assertIn("station inexistante", [e["message"] for e in preview(payload, self.lookup)["errors"]])

    def test_invalid_date_and_reversed_period(self):
        payload = grid(); payload["periods"][0]["start_date"] = "not-a-date"
        self.assertFalse(preview(payload, self.lookup)["valid"])
        payload = grid(); payload["periods"][0]["start_date"] = "2027-01-01"
        self.assertIn("start_date doit précéder end_date", [e["message"] for e in preview(payload, self.lookup)["errors"]])

    def test_unknown_period_and_invalid_range(self):
        payload = grid(); payload["passes"][0]["prices"][0]["period_id"] = "missing"
        self.assertIn("period_id inexistant", [e["message"] for e in preview(payload, self.lookup)["errors"]])
        payload = grid(); payload["passes"][0]["prices"][1]["price_min"] = 70
        self.assertIn("price_min doit être inférieur ou égal à price_max", [e["message"] for e in preview(payload, self.lookup)["errors"]])

    def test_duplicate_and_price_type_rules(self):
        payload = grid(); payload["passes"][0]["prices"].append(copy.deepcopy(payload["passes"][0]["prices"][0]))
        self.assertIn("couple forfait/période/catégorie dupliqué", [e["message"] for e in preview(payload, self.lookup)["errors"]])
        payload = grid(); del payload["passes"][0]["prices"][0]["price"]
        self.assertFalse(preview(payload, self.lookup)["valid"])

    def test_empty_grid_cannot_report_a_successful_import(self):
        payload = grid(); payload["periods"] = []; payload["passes"] = []
        messages = [error["message"] for error in preview(payload, self.lookup)["errors"]]
        self.assertIn("au moins une période est obligatoire", messages)
        self.assertIn("au moins un forfait est obligatoire", messages)


class SkiPassPersistenceTests(unittest.TestCase):
    @patch("app.services.ski_passes.SkiPassProduct.delete")
    @patch("app.services.ski_passes.SkiPassPeriod.delete")
    @patch("app.services.ski_passes.SkiPassPrice.create", side_effect=RuntimeError("insert failed"))
    @patch("app.services.ski_passes.SkiPassProduct.create")
    @patch("app.services.ski_passes.SkiPassPeriod.create")
    @patch("app.services.ski_passes.SkiPassSeason.get_or_create")
    @patch("app.services.ski_passes.Resort.get_or_none")
    @patch("app.services.ski_passes.db.atomic")
    def test_insert_error_escapes_atomic_block_for_rollback(self, atomic, resort_get, season_get, period_create, product_create, price_create, period_delete, product_delete):
        atomic.return_value = nullcontext(); resort_get.return_value = object()
        season = MagicMock(); season_get.return_value = (season, False)
        period_create.return_value = object(); product_create.return_value = object()
        with self.assertRaisesRegex(RuntimeError, "insert failed"):
            replace_grid(grid())
        atomic.assert_called_once_with()

    @patch("app.services.ski_passes.SkiPassProduct.delete")
    @patch("app.services.ski_passes.SkiPassPeriod.delete")
    @patch("app.services.ski_passes.SkiPassPrice.create")
    @patch("app.services.ski_passes.SkiPassProduct.create")
    @patch("app.services.ski_passes.SkiPassPeriod.create")
    @patch("app.services.ski_passes.SkiPassSeason.get_or_create")
    @patch("app.services.ski_passes.Resort.get_or_none")
    @patch("app.services.ski_passes.db.atomic")
    def test_existing_season_is_fully_replaced(self, atomic, resort_get, season_get, period_create, product_create, price_create, period_delete, product_delete):
        atomic.return_value = nullcontext(); resort_get.return_value = object()
        season = MagicMock(); season_get.return_value = (season, False)
        period_create.side_effect = [object(), object()]
        replace_grid(grid())
        season_get.assert_called_once(); self.assertEqual(period_create.call_count, 2)
        self.assertEqual(product_create.call_count, 2); self.assertEqual(price_create.call_count, 3)


class PublicSerializationTests(unittest.TestCase):
    def test_public_shape_preserves_order_and_current_period(self):
        period = MagicMock(id=1, external_id="high", name="Haute", start_date=date(2026, 12, 1), end_date=date(2027, 3, 1), sort_order=0)
        fixed = MagicMock(id=1, period=period, category="teen", category_label="Jeune", price_type="fixed", price=42, price_min=None, price_max=None, dynamic_label=None, sort_order=0)
        product = MagicMock(id=1, external_id="day", name="Journée", duration_days=1, duration_label="1 jour", sort_order=0, prices=[fixed])
        season = MagicMock(id=9, season="2026-2027", currency="EUR", source_url="https://example.test", resort=MagicMock(slug="chamonix"), periods=[period], products=[product])
        body = serialize_season(season, date(2027, 1, 2))
        self.assertEqual(body["id"], 9)
        self.assertEqual(body["periods"][0]["db_id"], 1)
        self.assertEqual(body["passes"][0]["prices"][0]["id"], 1)
        self.assertEqual(body["current_period_id"], "high")
        self.assertEqual(body["passes"][0]["prices"][0]["category"], "teen")

    @patch("app.routes.ski_passes.serialize_season", return_value={"station_slug": "chamonix", "season": "2026-2027"})
    @patch("app.routes.ski_passes._season_for", return_value=object())
    @patch("app.routes.ski_passes.Resort.get_or_none", return_value=MagicMock(is_active=True))
    def test_public_endpoint_returns_selected_season(self, resort_get, season_for, serializer):
        app = Flask(__name__); app.register_blueprint(bp_ski_passes)
        response = app.test_client().get("/api/forfaits/stations/chamonix?season=2026-2027")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["season"], "2026-2027")
        season_for.assert_called_once_with("chamonix", "2026-2027")


if __name__ == "__main__":
    unittest.main()
