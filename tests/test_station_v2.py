import sys
import types
import unittest
from datetime import date
from pathlib import Path

from flask import Flask
from peewee import IntegrityError, SqliteDatabase

sys.modules.setdefault("boto3", types.SimpleNamespace())

from app.models.resort import Resort
from app.models.ski_pass import (
    SkiPassPeriod, SkiPassPrice, SkiPassProduct, SkiPassSeason,
)
from app.models.station_widgets import StationWidgets
from app.routes.admin_stations import bp_admin_st
from app.routes.public_resorts import bp_public_stations


class StationV2Tests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.models = [
            Resort, StationWidgets, SkiPassSeason, SkiPassPeriod,
            SkiPassProduct, SkiPassPrice,
        ]
        self.database.bind(self.models)
        self.database.connect()
        self.database.create_tables(self.models)
        app = Flask(__name__)
        app.register_blueprint(bp_admin_st)
        app.register_blueprint(bp_public_stations)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(reversed(self.models))
        self.database.close()

    def station(self, **values):
        defaults = {"id": "1", "name": "Auron", "slug": "auron"}
        defaults.update(values)
        station = Resort.create(**defaults)
        StationWidgets.create(station_slug=station.slug, config="{}")
        return station

    def add_pass_price(self, station):
        season = SkiPassSeason.create(
            resort=station, season="2026-2027", is_active=True,
        )
        period = SkiPassPeriod.create(
            season=season, external_id="winter", name="Hiver",
            start_date=date(2026, 12, 1), end_date=date(2027, 4, 1),
        )
        product = SkiPassProduct.create(
            season=season, external_id="day", name="Journée",
            duration_days=1, duration_label="1 jour",
        )
        SkiPassPrice.create(
            product=product, period=period, category="adult",
            category_label="Adulte", price_type="fixed", price="50.00",
        )

    def test_new_station_defaults_to_legacy_with_empty_v2_content(self):
        station = self.station()
        self.assertEqual(station.page_layout_version, "legacy")
        self.assertIsNone(station.v2_overview_html)
        self.assertIsNone(station.v2_webcam_html)

    def test_invalid_layout_version_is_rejected_by_model_constraint(self):
        with self.assertRaises(IntegrityError):
            Resort.create(id="bad", name="Bad", slug="bad", page_layout_version="v3")

    def test_content_patch_does_not_activate_v2_and_activation_is_explicit(self):
        self.station()
        response = self.client.patch(
            "/api/admin/stations/auron",
            json={"v2_overview_html": "<p>Présentation V2</p>"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["resort"]["page_layout_version"], "legacy")
        self.assertEqual(Resort.get_by_id("1").page_layout_version, "legacy")

        response = self.client.patch(
            "/api/admin/stations/auron", json={"page_layout_version": "v2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Resort.get_by_id("1").page_layout_version, "v2")

    def test_api_validation_rejects_unknown_layout_version(self):
        self.station()
        response = self.client.patch(
            "/api/admin/stations/auron", json={"page_layout_version": "future"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Resort.get_by_id("1").page_layout_version, "legacy")

    def test_admin_readiness_distinguishes_content_and_business_data(self):
        self.station(
            v2_overview_html="<p>Aperçu</p>",
            v2_weather_snow_html="<p>Météo</p>",
            v2_ski_pass_html="<p>Forfaits</p>",
            v2_piste_map_html="<p>Plan</p>",
            v2_webcam_html="<p>Webcams</p>",
        )
        response = self.client.get("/api/admin/stations/auron")
        sections = response.get_json()["v2_readiness"]["sections"]
        self.assertEqual(sections["overview"]["status"], "ready")
        for section in ("ski_pass", "piste_map"):
            self.assertEqual(sections[section]["status"], "business_data_missing")
        for section in ("weather_snow", "webcams"):
            self.assertEqual(sections[section]["status"], "not_available")

    def test_v2_publication_requires_content_and_real_business_data(self):
        station = self.station(
            page_layout_version="v2",
            v2_overview_html="<p>Aperçu</p>",
            v2_weather_snow_html="<p>Météo et neige</p>",
            v2_piste_map_html="<p>Plan</p>",
            v2_webcam_html="<p>Webcams</p>",
            pistes_large_map_url="https://cdn.example.test/map.jpg",
        )
        widgets = StationWidgets.get_by_id("auron")
        widgets.config = StationWidgets.to_json({
            "meteo": {"enabled": True, "iframeUrl": "https://weather.example.test"},
            "webcams": {"enabled": False, "items": []},
        })
        widgets.save()

        response = self.client.get("/api/stations/auron")
        data = response.get_json()
        self.assertEqual(data["page_layout_version"], "v2")
        self.assertEqual(data["v2_content"]["overview"], "<p>Aperçu</p>")
        self.assertEqual(data["v2_sections"], {
            "overview": True,
            "weather_snow": True,
            "ski_pass": False,
            "piste_map": True,
            "webcams": False,
        })
        # Une webcam est facultative : son absence ne désactive pas la fiche V2.
        self.assertEqual(Resort.get_by_id(station.id).page_layout_version, "v2")

    def test_ski_pass_needs_both_editorial_content_and_an_actual_price(self):
        station = self.station(page_layout_version="v2")
        self.add_pass_price(station)
        first = self.client.get("/api/stations/auron").get_json()
        self.assertFalse(first["v2_sections"]["ski_pass"])

        station.v2_ski_pass_html = "<p>Tarifs</p>"
        station.save()
        second = self.client.get("/api/stations/auron").get_json()
        self.assertTrue(second["v2_sections"]["ski_pass"])

    def test_legacy_public_contract_is_preserved_and_v2_sections_are_off(self):
        self.station(description_md="Description historique", v2_overview_html="<p>Prêt</p>")
        data = self.client.get("/api/stations/auron").get_json()
        self.assertEqual(data["description_md"], "Description historique")
        self.assertEqual(data["page_layout_version"], "legacy")
        self.assertFalse(any(data["v2_sections"].values()))

    def test_migration_explicitly_backfills_existing_rows_to_legacy(self):
        sql = Path("migrations/20261004_add_resort_page_layout_v2.sql").read_text()
        self.assertIn("SET page_layout_version = 'legacy'", sql)
        self.assertIn("WHERE page_layout_version IS NULL", sql)
        self.assertIn("ALTER COLUMN page_layout_version SET NOT NULL", sql)
        self.assertNotIn("SET page_layout_version = 'v2'", sql)


if __name__ == "__main__":
    unittest.main()
