import sys
import types
sys.modules.setdefault("boto3", types.SimpleNamespace())

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from flask import Flask

from app.routes.admin_resort_import import bp_resort_json
from app.services.resort_json import (SCHEMA_VERSION, ValidationProblem,
    apply_record, differences, sanitize_html, validate_document, valid_url)


class ResortJsonValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="secret-test")
        self.auth = patch("app.services.admin_auth.authenticate_admin_request", return_value=None)
        self.auth.start()
        self.app.register_blueprint(bp_resort_json)
        self.app.register_blueprint(
            bp_resort_json,
            url_prefix="/api/admin/stations",
            name="admin_station_json",
        )
        self.ctx = self.app.app_context(); self.ctx.push()

    def tearDown(self):
        self.auth.stop()
        self.ctx.pop()

    def document(self, **station):
        base = {"id": "id-1", "slug": "station-test", "name": "Station test"}
        base.update(station)
        return {"schema_version": SCHEMA_VERSION, "station": base}

    def assertInvalid(self, doc):
        with self.assertRaises(ValidationProblem): validate_document(doc)

    def test_schema_version_missing(self): self.assertInvalid({"station": {"id": "x", "slug": "x", "name": "X"}})
    def test_schema_version_unknown(self):
        doc = self.document(); doc["schema_version"] = "9.0"; self.assertInvalid(doc)
    def test_unknown_field(self):
        doc = self.document(secret="no"); self.assertInvalid(doc)
    def test_invalid_boolean_type(self): self.assertInvalid(self.document(is_active="true"))
    def test_invalid_slug(self): self.assertInvalid(self.document(slug="https://bad slug"))
    def test_invalid_url_schemes(self):
        for value in ("javascript:alert(1)", "data:text/plain,x", "file:///tmp/x", "broken"):
            with self.subTest(value=value): self.assertInvalid(self.document(website_url=value))
    def test_http_and_https_urls(self):
        self.assertTrue(valid_url("http://example.test/x")); self.assertTrue(valid_url("https://example.test/x"))
    def test_invalid_date(self): self.assertInvalid(self.document(season_open_date="01/08/2026"))
    def test_invalid_coordinates(self):
        self.assertInvalid(self.document(latitude=91)); self.assertInvalid(self.document(longitude=-181))
    def test_negative_number(self): self.assertInvalid(self.document(pistes_count=-1))
    def test_missing_field_is_not_added(self):
        record = validate_document(self.document())[0]
        self.assertNotIn("meta_title", record["station"])
    def test_null_optional_is_kept_for_clear(self):
        record = validate_document(self.document(meta_title=None))[0]
        self.assertIsNone(record["station"]["meta_title"])

    def test_existing_station_id_is_never_a_preview_change(self):
        resort = SimpleNamespace(id="existing-id", slug="station-test")
        current = {"station": {"id": "existing-id", "slug": "station-test", "name": "Old name"}}
        with patch("app.services.resort_json.serialize_station", return_value=current), \
             patch("app.services.resort_json._load_widgets", return_value={}):
            for imported_id in (None, "existing-id"):
                with self.subTest(imported_id=imported_id):
                    changes, unchanged = differences(resort, {"station": {
                        "id": imported_id, "slug": "station-test", "name": "New name",
                    }})
                    self.assertNotIn("station.id", unchanged)
                    self.assertFalse(any(change["path"] == "station.id" for change in changes))
                    self.assertEqual(changes[0]["path"], "station.name")

    def test_existing_station_absent_id_is_never_a_preview_change(self):
        resort = SimpleNamespace(id="existing-id", slug="station-test")
        current = {"station": {"id": "existing-id", "slug": "station-test", "name": "Old name"}}
        with patch("app.services.resort_json.serialize_station", return_value=current), \
             patch("app.services.resort_json._load_widgets", return_value={}):
            changes, unchanged = differences(
                resort, {"station": {"slug": "station-test", "name": "New name"}},
            )
        self.assertFalse(any(change["path"] == "station.id" for change in changes))
        self.assertNotIn("station.id", unchanged)

    def test_confirmation_never_writes_existing_primary_key(self):
        resort = SimpleNamespace(id="existing-id", slug="station-test", name="Old name")
        resort.save = MagicMock()
        with patch("app.services.resort_json._load_widgets", return_value={}), \
             patch("app.services.resort_json.StationWidgets.get_or_none", return_value=None):
            updated, _ = apply_record(resort, {"station": {
                "id": None, "slug": "station-test", "name": "New name",
            }})
        self.assertEqual(resort.id, "existing-id")
        self.assertNotIn("station.id", updated)
        self.assertEqual(resort.name, "New name")

    @patch("app.routes.admin_resort_import.Resort.get_or_none")
    def test_existing_slug_accepts_null_absent_and_identical_id(self, get_or_none):
        existing = SimpleNamespace(id="existing-id", slug="station-test")
        from app.routes.admin_resort_import import _resolve

        for identity in (
            {"slug": "station-test"},
            {"id": None, "slug": "station-test"},
            {"id": "existing-id", "slug": "station-test"},
        ):
            with self.subTest(identity=identity):
                get_or_none.side_effect = ([existing] if identity.get("id") else []) + [existing]
                self.assertIs(_resolve({"station": identity}), existing)

    @patch("app.routes.admin_resort_import.Resort.get_or_none")
    def test_existing_slug_rejects_different_id_as_blocking_conflict(self, get_or_none):
        from app.routes.admin_resort_import import _classify
        existing = SimpleNamespace(id="existing-id", slug="station-test")
        get_or_none.side_effect = [None, existing]
        record = {"station": {"id": "different-id", "slug": "station-test", "name": "Station test"}}

        stations, _, errors = _classify([record], create=True)

        self.assertEqual(stations[0]["status"], "conflict")
        self.assertTrue(errors)

    def test_creation_with_null_id_is_allowed(self):
        record = validate_document(self.document(id=None))[0]
        self.assertIsNone(record["station"]["id"])

    @patch("app.routes.admin_resort_import.differences")
    @patch("app.routes.admin_resort_import._resolve")
    def test_la_clusaz_slug_match_remains_update_without_id_change(self, resolve, diff):
        from app.routes.admin_resort_import import _classify
        existing_id = "e4c1a8f3-7b2e-4bb5-86b9-3f2849e8b805"
        resolve.return_value = SimpleNamespace(id=existing_id, slug="la-clusaz")
        diff.return_value = ([{
            "path": "station.name", "old_value": "La Clusaz", "new_value": "La Clusaz Ski",
            "action": "update",
        }], [])
        record = {"station": {"id": None, "slug": "la-clusaz", "name": "La Clusaz Ski"}}

        stations, counts, errors = _classify([record], create=True)

        self.assertEqual(stations[0]["status"], "update")
        self.assertEqual(counts["existing"], 1)
        self.assertFalse(errors)
        self.assertFalse(any(change["path"] == "station.id" for change in stations[0]["changes"]))
        self.assertEqual(resolve.return_value.id, existing_id)
    def test_empty_optional_text_normalized(self):
        record = validate_document(self.document(meta_title=""))[0]
        self.assertIsNone(record["station"]["meta_title"])
    def test_empty_required_rejected(self): self.assertInvalid(self.document(name=""))
    def test_absent_relation_is_not_added(self): self.assertNotIn("pistes", validate_document(self.document())[0])
    def test_present_empty_relation_is_preserved_as_instruction(self):
        doc = self.document(); doc["pistes"] = {"items": []}
        self.assertEqual(validate_document(doc)[0]["pistes"]["items"], [])
    def test_html_sanitization(self):
        clean = sanitize_html('<p onclick="x">Sûr<script>alert(1)</script><a href="javascript:x">lien</a></p>')
        self.assertEqual(clean, "<p>Sûr<a>lien</a></p>")
    def test_bulk_limit(self):
        self.app.config["RESORT_IMPORT_MAX_STATIONS"] = 1
        self.assertInvalid({"schema_version": SCHEMA_VERSION, "stations": [self.document()["station"], self.document()["station"]]})

    def test_bulk_validation_accepts_single_station_document(self):
        document = self.document(id=None)

        records = validate_document(document, bulk=True)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["station"]["slug"], "station-test")
        self.assertIsNone(records[0]["station"]["id"])

    def test_bulk_validation_still_accepts_multiple_station_document(self):
        first = self.document()["station"]
        second = self.document(id="id-2", slug="station-2", name="Station 2")["station"]

        records = validate_document(
            {"schema_version": SCHEMA_VERSION, "stations": [{"station": first}, {"station": second}]},
            bulk=True,
        )

        self.assertEqual([record["station"]["slug"] for record in records], ["station-test", "station-2"])

    def test_bulk_validation_rejects_mixed_single_and_multiple_shapes(self):
        document = self.document()
        document["stations"] = [{"station": self.document()["station"]}]

        with self.assertRaises(ValidationProblem) as raised:
            validate_document(document, bulk=True)

        self.assertIn("use either station or stations", str(raised.exception.errors))

    @patch("app.routes.admin_resort_import._classify")
    def test_bulk_preview_accepts_single_station_json_from_browser(self, classify):
        classify.return_value = ([{"slug": "station-test", "status": "missing", "changes": []}],
                                 {"existing": 0, "missing": 1, "unchanged": 0}, [])

        response = self.app.test_client().post(
            "/api/admin/stations/import/preview?create_missing=true",
            json=self.document(id=None),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["valid"])

    @patch("app.routes.admin_resort_import._classify")
    def test_bulk_preview_accepts_parsed_file_envelope(self, classify):
        classify.return_value = ([{"slug": "station-test", "status": "missing", "changes": []}],
                                 {"existing": 0, "missing": 1, "unchanged": 0}, [])

        response = self.app.test_client().post(
            "/api/admin/stations/import/preview",
            json={"file": self.document(id=None), "create_missing": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["valid"])

    @patch("app.routes.admin_resort_import._classify")
    def test_bulk_preview_uses_admin_session_secret(self, classify):
        classify.return_value = ([{"slug": "station-test", "status": "missing", "changes": []}],
                                 {"existing": 0, "missing": 1, "unchanged": 0}, [])
        self.app.config.update(SECRET_KEY=None, ADMIN_SESSION_SECRET="a" * 32)

        response = self.app.test_client().post(
            "/api/admin/stations/import/preview",
            json={"file": self.document(id=None), "create_missing": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["valid"])
        self.assertTrue(response.get_json()["preview_token"])

    def test_bulk_preview_explains_empty_serialized_browser_file(self):
        response = self.app.test_client().post(
            "/api/admin/stations/import/preview",
            json={"file": {}, "create_missing": True},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "file_content_missing")

    @patch("app.routes.admin_resort_import._resolve", return_value=None)
    def test_bulk_preview_station_includes_display_name(self, resolve):
        response = self.app.test_client().post(
            "/api/admin/stations/import/preview",
            json={"file": self.document(id=None), "create_missing": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stations"][0]["name"], "Station test")

    @patch("app.routes.admin_resort_import.verify_token", return_value=False)
    def test_bulk_confirm_reads_preview_token_from_json_envelope(self, verify_token):
        response = self.app.test_client().post(
            "/api/admin/stations/import/confirm",
            json={
                "file": self.document(id=None),
                "create_missing": True,
                "preview_token": "browser-preview-token",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(verify_token.call_args.args[2], "browser-preview-token")

    def test_export_is_available_to_admin_front(self):
        with patch("app.routes.admin_resort_import.prefetch", return_value=[]), \
             patch("app.routes.admin_resort_import.Resort.select"):
            response = self.app.test_client().get("/api/admin/resorts/export")
        self.assertEqual(response.status_code, 200)

    def test_station_export_alias_is_registered(self):
        with patch("app.routes.admin_resort_import.prefetch", return_value=[]), \
             patch("app.routes.admin_resort_import.Resort.select"):
            response = self.app.test_client().get("/api/admin/stations/export")
        self.assertEqual(response.status_code, 200)

    def test_station_template_alias_is_registered(self):
        for path in ("template", "import-template", "import/template"):
            with self.subTest(path=path):
                response = self.app.test_client().get(f"/api/admin/stations/{path}")
                self.assertEqual(response.status_code, 200)

    def test_station_history_alias_is_registered(self):
        query = MagicMock()
        query.order_by.return_value.limit.return_value = []
        for path in ("history", "import-history", "imports/history"):
            with self.subTest(path=path):
                with patch("app.routes.admin_resort_import.ResortImportHistory.select", return_value=query):
                    response = self.app.test_client().get(f"/api/admin/stations/{path}")
                self.assertEqual(response.status_code, 200)

    def test_invalid_json(self):
        response = self.app.test_client().post("/api/admin/resorts/import/preview", data=b"{", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)

    def test_file_too_large(self):
        self.app.config["RESORT_IMPORT_MAX_FILE_SIZE"] = 2
        response = self.app.test_client().post("/api/admin/resorts/import/preview", data=b"{}x", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__": unittest.main()
