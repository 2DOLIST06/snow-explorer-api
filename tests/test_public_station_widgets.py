import sys
import types
import unittest
from unittest.mock import patch

from flask import Flask
from peewee import SqliteDatabase

sys.modules.setdefault("boto3", types.SimpleNamespace())

from app.models.resort import Resort  # noqa: E402
from app.models.station_widgets import StationWidgets  # noqa: E402
from app.routes.stations_widgets import DEFAULT_CFG, bp_widgets  # noqa: E402


class PublicStationWidgetsQueryTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.models = [Resort, StationWidgets]
        self.database.bind(self.models)
        self.database.connect()
        self.database.create_tables(self.models)

        app = Flask(__name__)
        app.register_blueprint(bp_widgets)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(self.models)
        self.database.close()

    def create_resort(self, slug="auron", active=True):
        return Resort.create(
            id=slug,
            name=slug.title(),
            slug=slug,
            is_active=active,
        )

    def request_with_sql(self, slug):
        statements = []
        execute_sql = self.database.execute_sql

        def record(sql, params=None, commit=None):
            statements.append(sql)
            return execute_sql(sql, params, commit)

        with patch.object(self.database, "execute_sql", side_effect=record):
            response = self.client.get(f"/api/stations/{slug}/widgets")
            # Force Flask's JSON decoding while SQL recording is still active:
            # serialization must not trigger a lazy query.
            payload = response.get_json()
        return response, payload, statements

    def test_nominal_response_uses_one_joined_query_without_lazy_reads(self):
        self.create_resort()
        StationWidgets.create(
            station_slug="auron",
            config=StationWidgets.to_json({
                "description": {"enabled": True, "html": "Bienvenue"},
                "forfaits": {
                    "enabled": True,
                    "items": [{"title": "Journée", "price": "42"}],
                },
                "private": {"token": "hidden"},
            }),
        )

        response, payload, statements = self.request_with_sql("auron")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(statements), 1)
        self.assertIn("LEFT OUTER JOIN", statements[0].upper())
        self.assertNotIn('"t1".*', statements[0])
        self.assertEqual(payload["stationSlug"], "auron")
        self.assertEqual(payload["description"]["html"], "Bienvenue")
        self.assertEqual(
            payload["forfaits"]["items"][0]["prices"],
            {"c-1-1": "Journée", "c-1-2": "42"},
        )
        self.assertNotIn("private", payload)

    def test_active_station_without_widgets_keeps_default_json_in_one_query(self):
        self.create_resort()

        response, payload, statements = self.request_with_sql("auron")

        expected = {**DEFAULT_CFG, "forfaits": dict(DEFAULT_CFG["forfaits"])}
        expected["stationSlug"] = "auron"
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload, expected)
        self.assertEqual(len(statements), 1)

    def test_missing_and_inactive_stations_keep_json_404_in_one_query(self):
        self.create_resort("inactive", active=False)

        for slug in ("missing", "inactive"):
            with self.subTest(slug=slug):
                response, payload, statements = self.request_with_sql(slug)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(payload, {
                    "error": "station_not_found",
                    "message": "Station not found",
                })
                self.assertEqual(len(statements), 1)


if __name__ == "__main__":
    unittest.main()
