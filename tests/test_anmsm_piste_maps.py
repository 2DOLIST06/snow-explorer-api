import unittest
from unittest.mock import Mock

from flask import Flask

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
        }],
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
            "credit": "Office de tourisme", "modified_at": None,
            "plan_type": None,
        }])

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


if __name__ == "__main__":
    unittest.main()
