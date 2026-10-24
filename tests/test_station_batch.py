import copy
import io
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from PIL import Image

from app.services import station_batch as batch
from app.services.resort_json import ValidationProblem


def document(country="CH"):
    return {
        "batch_version": "1.0",
        "scope": {"country_code": country, "massif": "Test", "discipline": "alpine", "season": "2026-2027"},
        "document": {"schema_version": "1.0", "stations": [{"station": {
            "slug": "test-station", "name": "Test Station", "country_code": country,
            "is_active": False, "latitude": 46.1, "longitude": 7.1,
        }}]},
        "media": [{"station_slug": "test-station", "role": role, "status": "not_found"}
                  for role in ("cover", "logo", "piste_map")],
    }


class StationBatchTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "test-secret"
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_foreign_country_and_missing_media_are_supported_without_fake_urls(self):
        data = document()
        with patch("app.routes.admin_resort_import._resolve", return_value=None), \
             patch.object(batch.anmsm_logos, "fetch_stations") as feed:
            result = batch.resolve_batch(data)
        feed.assert_not_called()
        self.assertEqual(result["jobs"], [])
        self.assertEqual(len(result["warnings"]), 3)

    def test_incomplete_media_inventory_and_active_creations_are_rejected(self):
        data = document()
        data["media"].pop()
        with self.assertRaises(ValidationProblem):
            batch.validate_batch(data)
        data = document()
        data["document"]["stations"][0]["station"]["is_active"] = True
        with self.assertRaises(ValidationProblem):
            batch.validate_batch(data)

    def test_existing_station_cannot_be_deactivated_by_creation_batch(self):
        with patch("app.routes.admin_resort_import._resolve", return_value=object()):
            with self.assertRaises(ValidationProblem):
                batch.resolve_batch(document())

    def test_creation_scope_rejects_unrequested_fields_and_lower_description(self):
        for key, value in (("description", {"html": "<p>Wrong field</p>"}), ("webcams", {"enabled": False})):
            data = document()
            data["document"]["stations"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValidationProblem):
                batch.validate_batch(data)
        data = document()
        data["document"]["stations"][0]["station"]["meta_title"] = "Not requested"
        with self.assertRaises(ValidationProblem):
            batch.validate_batch(data)

    def test_combined_advanced_passes_preserve_legacy_label_without_mutating_input(self):
        from app.services.resort_json import validate_document
        from app.services.ski_passes import validate_grid
        season = {"season": "2026-2027", "currency": "EUR", "periods": [
            {"id": "winter", "name": "Winter", "start_date": "2026-12-01", "end_date": "2027-03-31"}],
            "passes": [{"id": "day", "name": "Day", "duration_days": 1, "duration_label": "1 day", "prices": [
                {"period_id": "winter", "category": "adult", "category_label": "Adult", "price_type": "fixed", "price": 42,
                 "label": "Card extra", "note": "Online purchase"}]}]}
        data = document()
        data["document"]["stations"][0]["forfaits_avances"] = {"seasons": [season]}
        record = validate_document(data["document"], bulk=True)[0]
        price = record["forfaits_avances"]["seasons"][0]["passes"][0]["prices"][0]
        self.assertEqual(price["note"], "Online purchase\nCard extra")
        self.assertEqual(price["price"], 42)
        self.assertNotIn("label", price)
        self.assertIn("label", season["passes"][0]["prices"][0])
        grid, errors = validate_grid({**season, "station_slug": "test-station"}, resort_lookup=lambda _: object())
        self.assertEqual(errors, [])
        self.assertEqual(grid["products"][0]["prices"][0]["note"], price["note"])

    def test_widget_counters_are_written_without_fabricated_lifts_or_lower_description(self):
        from types import SimpleNamespace
        from app.services.resort_json import apply_record
        row = Mock()
        record = {"station": {}, "pistes": {"green": 0, "blue": 3},
                  "remontees": {"tire_fesses": 2, "telesieges": 4}, "snowpark": {"count": 1}}
        with patch("app.services.resort_json._load_widgets", return_value={"description": {"html": "Keep"}}), \
             patch("app.services.resort_json.StationWidgets.get_or_none", return_value=row), \
             patch("app.services.resort_json._replace_lifts") as lifts:
            apply_record(SimpleNamespace(slug="test-station", id="s1"), record)
        import json
        cfg = json.loads(row.config)
        self.assertEqual(cfg["remontees"]["tireFesses"], 2)
        self.assertEqual(cfg["remontees"]["telesieges"], 4)
        self.assertNotIn("telepheriques", cfg["remontees"])
        self.assertEqual(cfg["pistes"]["colors"], {"green": 0, "blue": 3})
        self.assertEqual(cfg["snowparks"]["count"], 1)
        self.assertEqual(cfg["description"]["html"], "Keep")
        lifts.assert_not_called()

    def test_anmsm_logo_takes_precedence_over_official_fallback(self):
        data = document("FR")
        data["media"][1].update(status="found", source_url="https://official.example/logo.png", source_page_url="https://official.example/")
        logos = [{"external_station_id": "STAT1", "external_name": "Test Station", "logo": {"url": "https://anmsm.example/logo.png"}}]
        with patch("app.routes.admin_resort_import._resolve", return_value=None), \
             patch.object(batch.anmsm_logos, "fetch_stations", return_value=logos), \
             patch.object(batch.anmsm_piste_maps, "fetch_maps", return_value=[]), \
             patch.object(batch, "public_target"):
            resolved = batch.resolve_batch(data)
        self.assertEqual(resolved["jobs"][0]["source_url"], "https://anmsm.example/logo.png")
        self.assertEqual(resolved["jobs"][0]["source_provider"], "anmsm")

    def test_anmsm_feed_failure_does_not_silently_mean_missing(self):
        with patch("app.routes.admin_resort_import._resolve", return_value=None), \
             patch.object(batch.anmsm_logos, "fetch_stations", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                batch.resolve_batch(document("FR"))

    def test_explicit_current_viewer_is_not_overwritten_by_old_anmsm_map(self):
        data = document("FR")
        data["media"][2].update(status="found", format="interactive", source_url="https://official.example/current", source_page_url="https://official.example/")
        logos = [{"external_station_id": "STAT1", "external_name": "Test Station", "logo": {}}]
        maps = [{"external_station_id": "STAT1", "piste_maps": [{"media_id": "old", "url": "https://example.org/2016.pdf"}]}]
        with patch("app.routes.admin_resort_import._resolve", return_value=None), \
             patch.object(batch.anmsm_logos, "fetch_stations", return_value=logos), \
             patch.object(batch.anmsm_piste_maps, "fetch_maps", return_value=maps), \
             patch.object(batch, "public_target"):
            result = batch.resolve_batch(data)
        self.assertEqual(result["jobs"][0]["source_url"], "https://official.example/current")
        self.assertEqual(result["jobs"][0]["format"], "interactive")

    def test_existing_ski_area_references_are_kept_and_checked_before_media(self):
        data = document()
        data["document"]["stations"][0]["domaines_skiables"] = {"items": [{"id": 12, "slug": "existing-area"}]}
        self.assertEqual(batch.validate_batch(data)[0]["domaines_skiables"]["items"][0]["id"], 12)
        with patch("app.routes.admin_resort_import._resolve", return_value=None), \
             patch.object(batch, "validate_references", return_value=[{"path": "domaines_skiables", "message": "not found"}]), \
             patch.object(batch.anmsm_logos, "fetch_stations") as feed:
            with self.assertRaises(ValidationProblem):
                batch.resolve_batch(data)
        feed.assert_not_called()

    def test_official_image_link_is_decoded_and_kept_without_duplicate_upload(self):
        job = {"kind": "job", "station_slug": "test-station", "role": "piste_map", "source_provider": "official",
               "source_url": "https://official.example/map.jpg", "batch_checksum": "test"}
        with tempfile.NamedTemporaryFile(delete=False) as source:
            source.write(b"downloaded")
        with tempfile.NamedTemporaryFile(suffix=".webp", delete=False) as display:
            display.write(b"decoded")
        import json
        metadata = {"ok": True, "path": display.name, "width": 1200, "height": 800}
        with patch.object(batch, "download", return_value=(source.name, "image/jpeg", 10)), \
             patch.object(batch.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(metadata))), \
             patch.object(batch.s3, "put_file") as upload:
            result = batch.prepare_media(batch.signed(job))
        upload.assert_not_called()
        self.assertTrue(result["linked_image"])
        self.assertEqual(result["url"], job["source_url"])
        self.assertIsNone(result["s3_key"])

    def test_linked_image_assembly_uses_official_field_and_keeps_domains(self):
        data = document()
        data["document"]["stations"][0]["domaines_skiables"] = {"items": [{"slug": "existing-area"}]}
        job = {"station_slug": "test-station", "role": "piste_map", "source_provider": "official", "source_url": "https://official.example/map.jpg"}
        receipt = batch.signed({**job, "kind": "prepared", "batch_checksum": batch.checksum(data), "url": job["source_url"], "linked_image": True, "render_mode": "image", "s3_key": None})
        with patch.object(batch, "resolve_batch", return_value={"jobs": [job], "warnings": []}), \
             patch.object(batch.s3, "validate_object") as storage:
            output, _ = batch.assemble_batch(data, [receipt])
        storage.assert_not_called()
        self.assertEqual(output["stations"][0]["pistes"]["official_map_url"], job["source_url"])
        self.assertEqual(output["stations"][0]["domaines_skiables"]["items"], [{"slug": "existing-area"}])

    def test_ambiguous_anmsm_plan_does_not_choose_first_result(self):
        logos = [{"external_station_id": "STAT1", "external_name": "Test Station", "logo": {}}]
        maps = [{"external_station_id": "STAT1", "piste_maps": [
            {"media_id": "a", "url": "https://example.org/alpine.png"},
            {"media_id": "b", "url": "https://example.org/nordic.png"},
        ]}]
        with patch("app.routes.admin_resort_import._resolve", return_value=None), \
             patch.object(batch.anmsm_logos, "fetch_stations", return_value=logos), \
             patch.object(batch.anmsm_piste_maps, "fetch_maps", return_value=maps):
            with self.assertRaises(ValidationProblem):
                batch.resolve_batch(document("FR"))

    def test_private_destinations_and_credentials_are_rejected(self):
        for url in ("http://example.org/a", "https://user:pass@example.org/a", "https://example.org:444/a", "https://example.org/a#fragment"):
            with self.subTest(url=url), self.assertRaises(ValidationProblem):
                batch.public_target(url)
        with patch.object(batch.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValidationProblem):
                batch.public_target("https://example.org/media.png")

    def test_download_rejects_html_instead_of_accepting_http_200(self):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.return_value = "text/html"
        with patch.object(batch, "public_target", return_value=(batch.urlsplit("https://example.org/page"), "8.8.8.8")), \
             patch.object(batch, "PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(ValidationProblem):
                batch.download("https://example.org/page", "piste_map")
        connection.close.assert_called_once()

    def test_receipts_are_bound_to_exact_batch_and_source(self):
        data = document()
        job = {"station_slug": "test-station", "role": "cover", "source_url": "https://example.org/photo.png"}
        value = {**job, "kind": "prepared", "batch_checksum": batch.checksum(data), "url": "https://cdn.example/photo.webp", "s3_key": "key"}
        receipt = batch.signed(value)
        resolved = {"jobs": [job], "warnings": []}
        with patch.object(batch, "resolve_batch", return_value=resolved), \
             patch.object(batch.s3, "validate_object", return_value=True):
            output, _ = batch.assemble_batch(data, [receipt])
            self.assertEqual(output["stations"][0]["station"]["cover_image_url"], value["url"])
            changed = copy.deepcopy(data)
            changed["scope"]["massif"] = "Changed"
            with self.assertRaises(ValidationProblem):
                batch.assemble_batch(changed, [receipt])
            with self.assertRaises(ValidationProblem):
                batch.assemble_batch(data, [receipt + "tampered"])

    def test_real_image_conversion_stores_webp_and_keeps_logo_transparency(self):
        raw = io.BytesIO()
        Image.new("RGBA", (200, 100), (0, 100, 220, 180)).save(raw, "PNG")
        with tempfile.NamedTemporaryFile(delete=False) as source:
            source.write(raw.getvalue())
            path = source.name
        captured = {}
        def upload(key, filename, mime):
            with Image.open(filename) as image:
                self.assertEqual(image.format, "WEBP")
                captured["mode"] = image.mode
            captured["size"] = os.path.getsize(filename)
            return "https://cdn.example/" + key
        job = {"kind": "job", "batch_checksum": "abc", "station_slug": "test-station", "role": "logo", "source_url": "https://example.org/logo.png"}
        with patch.object(batch, "download", return_value=(path, "image/png", "hash")), \
             patch.object(batch.s3, "put_file", side_effect=upload), \
             patch.object(batch.s3, "preview_url", return_value="https://preview.example/logo.webp"):
            result = batch.prepare_media(batch.signed(job))
        self.assertEqual(captured["mode"], "RGBA")
        self.assertLessEqual(captured["size"], 50 * 1024)
        self.assertEqual(batch.verified(result["receipt"], "prepared")["url"], result["url"])
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
