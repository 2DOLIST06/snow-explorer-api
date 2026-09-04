import unittest
from unittest.mock import Mock, patch

from flask import Flask
from peewee import SqliteDatabase

from app.models.admin_user import AdminUser
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.resort import Resort
from app.models.station_piste_map_candidate import StationPisteMapCandidate
from app.routes.admin_piste_maps import bp_admin_piste_maps
from app.services.anmsm_logos import LogoImportError
from app.services.anmsm_piste_maps import PISTE_MAPS_FEED_URL, fetch_maps, parse_record


# Shape returned by the ANMSM Espace neige Tourinsoft syndication.  In
# particular, PLANPISTES is a dedicated media column inside Object; it is not
# the generic image collection from the separate Media syndication.
REAL_ESPACE_NEIGE_RECORD = {
    "SyndicObjectID": "STATANMSM00000001",
    "SyndicObjectName": "Libellé de syndication",
    "Object": {
        "NOM": "Station réelle",
        "PLANPISTES": [{
            "MediaID": "a6b48780-7ec1-4b0a-a76d-abc123456789",
            "Url": "https://anmsm.media.tourinsoft.eu/upload/plan-des-pistes.jpg",
            "Titre": "Plan des pistes",
            "Credit": "Office de tourisme",
            "Extension": "JPG",
            "DateModification": "2026-08-31T14:05:22.0000000+02:00",
            "TypePlan": "Alpine ski map",
        }],
        # A title must never turn an unrelated generic image into a piste map.
        "PHOTOS": [{"MediaID": "generic", "Url": "https://media/generic.jpg",
                    "Titre": "Plan des pistes"}],
    },
}


class Response:
    def __init__(self, payload=None, status=200, json_error=None):
        self.payload = payload
        self.status_code = status
        self.json_error = json_error
        self.closed = False

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload

    def close(self):
        self.closed = True


class AnmsmPisteMapFeedTests(unittest.TestCase):
    def app(self, configured=None):
        app = Flask(__name__)
        app.config["ANMSM_PISTE_MAPS_FEED_URL"] = configured
        return app

    def test_parses_exact_espace_neige_plan_column(self):
        station = parse_record(REAL_ESPACE_NEIGE_RECORD)
        self.assertEqual(station["external_station_id"], "STATANMSM00000001")
        self.assertEqual(station["external_name"], "Station réelle")
        self.assertEqual(station["piste_maps"], [{
            "media_id": "a6b48780-7ec1-4b0a-a76d-abc123456789",
            "url": "https://anmsm.media.tourinsoft.eu/upload/plan-des-pistes.jpg",
            "format": "jpeg", "title": "Plan des pistes",
            "credit": "Office de tourisme",
            "modified_at": "2026-08-31T14:05:22.0000000+02:00",
            "plan_type": "Alpine ski map",
        }])

    def test_supports_alternate_dedicated_plan_des_pistes_column(self):
        record = {"SyndicObjectID": "EN-2", "Object": {"NOM": "Second station",
            "PLAN_DES_PISTES": {"ID": "pdf-2", "Url": "https://media/map.pdf",
                                  "Format": "PDF", "Titre": "Original English map"},
            "PHOTOS": [{"Url": "https://media/not-a-map.jpg", "Titre": "Piste map"}]}}
        station = parse_record(record)
        self.assertEqual([media["media_id"] for media in station["piste_maps"]], ["pdf-2"])
        self.assertEqual(station["piste_maps"][0]["format"], "pdf")

    def test_fetch_uses_espace_neige_and_reports_counts(self):
        response = Response([REAL_ESPACE_NEIGE_RECORD])
        session = Mock(); session.get.return_value = response
        app = self.app()
        with app.app_context():
            result = fetch_maps(session)
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], PISTE_MAPS_FEED_URL)
        self.assertEqual(len(result), 1)
        self.assertTrue(response.closed)

    def test_missing_explicit_url_is_an_error(self):
        app = self.app("")
        with app.app_context(), self.assertRaises(LogoImportError) as raised:
            fetch_maps(Mock())
        self.assertEqual(raised.exception.code, "missing_feed_url")

    def test_http_json_and_structure_errors_are_not_empty_lists(self):
        cases = [
            (Response(status=503), "source_feed_http_error"),
            (Response(json_error=ValueError("bad JSON")), "invalid_feed_json"),
            (Response([{"SyndicObjectID": "STAT1", "Object": {"PHOTOS": []}}]),
             "invalid_feed_structure"),
            (Response([]), "invalid_feed_structure"),
        ]
        for response, code in cases:
            with self.subTest(code=code):
                session = Mock(); session.get.return_value = response
                with self.app().app_context(), self.assertRaises(LogoImportError) as raised:
                    fetch_maps(session)
                self.assertEqual(raised.exception.code, code)
                self.assertTrue(response.closed)


MODELS = [Resort, AdminUser, AnmsmStationMapping, StationPisteMapCandidate]


class AnmsmPisteMapWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind(MODELS); self.database.connect(); self.database.create_tables(MODELS)
        self.resort = Resort.create(id="station-1", name="Mapped station", slug="mapped-station")
        AnmsmStationMapping.create(station=self.resort, source="anmsm",
                                   external_station_id="  statanmsm00000001  ", verified=True)
        app = Flask(__name__); app.register_blueprint(bp_admin_piste_maps)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(MODELS); self.database.close()

    def test_workspace_matches_normalized_id_preserves_values_and_does_not_mutate(self):
        second = {"SyndicObjectID": "UNMATCHED", "Object": {"NOM": "Unmatched",
            "PLANPISTES": [{"MediaID": "map-2", "Url": "https://media/map-2.pdf",
                              "Extension": "PDF"}]}}
        before = {model: model.select().count() for model in MODELS}
        with patch("app.services.anmsm_piste_maps.fetch_maps",
                   return_value=[parse_record(REAL_ESPACE_NEIGE_RECORD), parse_record(second)]), \
             patch("app.routes.admin_piste_maps.s3.preview_url") as preview:
            response = self.client.get("/api/admin/anmsm/piste-maps/workspace")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["stats"]["stations_detected"], 2)
        self.assertEqual(body["stats"]["plans_detected"], 2)
        self.assertEqual(body["stats"]["stations_matched"], 1)
        self.assertEqual(body["stats"]["stations_unmatched"], 1)
        self.assertEqual(body["stats"]["plans_to_prepare"], 2)
        self.assertEqual(len(body["stations"]), 2)
        mapped = next(row for row in body["rows"] if row["mapping_status"] == "matched")
        self.assertEqual(mapped["external_station_id"], "STATANMSM00000001")
        self.assertEqual(mapped["source_modified_at"], "2026-08-31T14:05:22.0000000+02:00")
        self.assertEqual(mapped["anmsm_title"], "Plan des pistes")
        self.assertEqual(AnmsmStationMapping.get().external_station_id,
                         "  statanmsm00000001  ")
        self.assertEqual({model: model.select().count() for model in MODELS}, before)
        preview.assert_not_called()

    def test_workspace_returns_explicit_upstream_http_status(self):
        error = LogoImportError("source_feed_http_error",
                                "ANMSM piste-map feed returned HTTP 503")
        error.source_http_status = 503
        with patch("app.services.anmsm_piste_maps.fetch_maps", side_effect=error):
            result = self.client.get("/api/admin/anmsm/piste-maps/workspace")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.get_json(), {
            "ok": False, "error": "source_feed_http_error",
            "message": "ANMSM piste-map feed returned HTTP 503",
            "source_http_status": 503,
        })
        self.assertEqual(StationPisteMapCandidate.select().count(), 0)


if __name__ == "__main__":
    unittest.main()
