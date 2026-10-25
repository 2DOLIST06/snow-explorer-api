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
    def test_batch_over_one_mib_is_accepted_and_technical_limit_is_kept(self):
        data = document()
        data['research'] = [{'notes': 'x' * (1024 * 1024)}]
        self.assertEqual(len(batch.validate_batch(data)), 1)
        with patch.object(batch, 'MAX_BATCH_BYTES', 1024):
            with self.assertRaises(ValidationProblem):
                batch.validate_batch(data)

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

    def test_official_cover_photograph_is_rejected(self):
        data = document()
        data["media"][0].update(status="found", source_url="https://official.example/photo.jpg", source_page_url="https://official.example/")
        with self.assertRaises(ValidationProblem):
            batch.validate_batch(data)

    def test_local_generated_files_can_be_embedded_in_one_importable_json(self):
        import base64
        import json
        import subprocess
        import sys
        from pathlib import Path
        script = Path(__file__).resolve().parents[1] / "plugins/snow-explorer-content-ops/skills/snow-explorer-orchestrator/scripts/embed-generated-media.py"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "generated.png"
            Image.new("RGBA", (512, 320), (0, 100, 220, 180)).save(source, "PNG")
            original = Path(directory) / "batch.json"
            output = Path(directory) / "prepared.json"
            original.write_text(json.dumps(document()))
            subprocess.run([sys.executable, str(script), str(original), str(output),
                            "--cover", f"test-station={source}", "--logo", f"test-station={source}"],
                           capture_output=True, text=True, check=True, timeout=30)
            data = json.loads(output.read_text())
            self.assertEqual(len(batch.validate_batch(data)), 1)
            for asset in data["media"]:
                if asset["role"] == "piste_map":
                    continue
                encoded = base64.b64decode(asset["data_base64"], validate=True)
                self.assertLessEqual(len(encoded), batch.GENERATED_LIMITS[asset["role"]])
                with Image.open(io.BytesIO(encoded)) as image:
                    self.assertEqual(image.format, "WEBP")
                    self.assertEqual(image.mode, "RGBA")

    def test_anmsm_workspace_exposes_only_existing_converted_urls(self):
        from types import SimpleNamespace
        row = SimpleNamespace(external_station_id="STAT1", anmsm_media_id="map1", display_s3_key="anmsm/existing/display.webp",
                              anmsm_title="Existing plan", anmsm_credit="Credit", plan_type="alpine", error_code=None,
                              station=SimpleNamespace(slug="test-station", name="Test Station"))
        query = Mock()
        query.where.return_value = [row]
        with patch.object(batch.StationPisteMapCandidate, "select", return_value=query), \
             patch.object(batch.s3, "public_url", side_effect=lambda key: "https://cdn.example/" + key), \
             patch.object(batch, "download") as download:
            result = batch.prepared_map_feed()
        download.assert_not_called()
        self.assertEqual(result[0]["piste_maps"][0]["url"], "https://cdn.example/anmsm/existing/display.webp")

    def test_generated_cover_and_badge_store_the_actual_webp_bytes(self):
        import base64
        import hashlib
        raw = io.BytesIO()
        Image.new("RGBA", (320, 200), (0, 100, 220, 180)).save(raw, "WEBP")
        content = raw.getvalue()
        for role in ("cover", "logo"):
            data = document()
            asset = next(m for m in data["media"] if m["role"] == role)
            asset.update(status="found", source_kind="generated", data_base64=base64.b64encode(content).decode())
            captured = []
            def upload(key, filename, mime):
                with open(filename, "rb") as image:
                    captured.append(image.read())
                return "https://cdn.example/" + key
            with patch("app.routes.admin_resort_import._resolve", return_value=None):
                resolved = batch.resolve_batch(data)
            job = resolved["jobs"][0]
            self.assertNotIn("data_base64", job)
            with patch.object(batch, "download") as download, \
                 patch.object(batch.s3, "put_file", side_effect=upload), \
                 patch.object(batch.s3, "preview_url", return_value="https://preview.example/image.webp"):
                prepared = batch.prepare_media(job["job_token"])
            download.assert_not_called()
            self.assertEqual(captured, [content])
            self.assertEqual(prepared["sha256"], hashlib.sha256(content).hexdigest())
            self.assertNotIn("data_base64", batch.verified(prepared["receipt"], "prepared"))
            with patch("app.routes.admin_resort_import._resolve", return_value=None), \
                 patch.object(batch.s3, "validate_object", return_value=True):
                output, _ = batch.assemble_batch(data, [prepared["receipt"]])
            field = "cover_image_url" if role == "cover" else "logo_url"
            self.assertEqual(output["stations"][0]["station"][field], prepared["url"])

    def test_generated_webp_limits_and_invalid_bytes_cannot_be_bypassed(self):
        import base64
        for role, content in (("logo", b"RIFFxxxxWEBP" + b"x" * (40 * 1024)),
                              ("cover", b"RIFFxxxxWEBP" + b"x" * (80 * 1024)),
                              ("cover", b"not a WebP")):
            data = document()
            asset = next(m for m in data["media"] if m["role"] == role)
            asset.update(status="found", source_kind="generated", data_base64=base64.b64encode(content).decode())
            with self.assertRaises(ValidationProblem):
                batch.validate_batch(data)
        job = {"kind": "job", "role": "cover", "source_kind": "generated", "data_base64": base64.b64encode(b"RIFFxxxxWEBPbad").decode()}
        with patch.object(batch.s3, "put_file") as upload:
            with self.assertRaises(ValidationProblem):
                batch.prepare_media(batch.signed(job))
        upload.assert_not_called()

    def test_missing_anmsm_logo_requires_original_generated_badge(self):
        data = document()
        data["media"][1].update(status="found", source_url="https://official.example/logo.png", source_page_url="https://official.example/")
        with patch("app.routes.admin_resort_import._resolve", return_value=None):
            with self.assertRaises(ValidationProblem):
                batch.resolve_batch(data)

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
             patch.object(batch, "prepared_map_feed", return_value=[]), \
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
             patch.object(batch, "prepared_map_feed", return_value=maps), \
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

    def test_plan_link_is_probed_without_downloading_converting_or_uploading(self):
        for provider in ("official", "anmsm"):
            job = {"kind": "job", "station_slug": "test-station", "role": "piste_map", "source_provider": provider,
                   "source_url": "https://example.org/already-converted.webp", "batch_checksum": "test"}
            with patch.object(batch, "probe_map_image") as probe, \
                 patch.object(batch, "download") as download, \
                 patch.object(batch.subprocess, "run") as convert, \
                 patch.object(batch.s3, "put_file") as upload:
                result = batch.prepare_media(batch.signed(job))
            probe.assert_called_once_with(job["source_url"])
            download.assert_not_called(); convert.assert_not_called(); upload.assert_not_called()
            self.assertTrue(result["linked_image"])
            self.assertEqual(result["url"], job["source_url"])
            self.assertIsNone(result["s3_key"])

    def test_pdf_plan_is_resolved_and_prepared_as_linked_reader(self):
        data = document()
        data["media"][2].update(status="found", source_url="https://example.org/map.pdf",
                                source_provider="official", source_page_url="https://example.org/")
        with patch("app.routes.admin_resort_import._resolve", return_value=None), patch.object(batch, "public_target"):
            resolved = batch.resolve_batch(data)
        job = resolved["jobs"][0]
        with patch.object(batch, "probe_map_image") as probe, patch.object(batch, "download") as download:
            prepared = batch.prepare_media(job["job_token"])
        probe.assert_called_once_with(job["source_url"], pdf=True)
        download.assert_not_called()
        self.assertEqual(prepared["render_mode"], "interactive")
        self.assertEqual(prepared["url"], job["source_url"])
        self.assertIsNone(prepared["s3_key"])
        with patch.object(batch, "resolve_batch", return_value=resolved):
            output, _ = batch.assemble_batch(data, [prepared["receipt"]])
        self.assertEqual(output["stations"][0]["pistes"]["official_map_url"], job["source_url"])

    def test_pdf_probe_rejects_invalid_or_unembeddable_responses(self):
        cases = [({}, b"%PDF-1.7", True), ({"Content-Type": "text/html"}, b"<html>", False),
                 ({}, b"not a pdf", False), ({"X-Frame-Options": "DENY"}, b"%PDF-1.7", False),
                 ({"Content-Disposition": "attachment; filename=map.pdf"}, b"%PDF-1.7", False),
                 ({"Content-Security-Policy": "frame-ancestors 'self'"}, b"%PDF-1.7", False)]
        for extra, content, accepted in cases:
            with self.subTest(headers=extra, content=content):
                connection = Mock()
                response = connection.getresponse.return_value
                response.status = 206
                headers = {"Content-Type": "application/pdf", **extra}
                response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
                response.read.return_value = content
                with patch.object(batch, "public_target", return_value=(batch.urlsplit("https://example.org/map.pdf"), "8.8.8.8")), patch.object(batch, "PinnedHTTPSConnection", return_value=connection):
                    if accepted:
                        self.assertEqual(batch.probe_map_image("https://example.org/map.pdf", pdf=True), "https://example.org/map.pdf")
                    else:
                        with self.assertRaises(ValidationProblem):
                            batch.probe_map_image("https://example.org/map.pdf", pdf=True)
                connection.close.assert_called_once()

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
             patch.object(batch, "prepared_map_feed", return_value=maps):
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

    def test_revision_rebinds_only_unchanged_signed_media(self):
        data = document()
        for asset in data["media"]:
            asset.update(status="found", source_provider="official", source_url="https://example.org/" + asset["role"] + ".webp", source_page_url="https://example.org/")
        def resolve(value):
            binding = batch.checksum(value)
            jobs = []
            for asset in value["media"]:
                job = {**asset, "kind": "job", "batch_checksum": binding}
                jobs.append({**asset, "job_token": batch.signed(job)})
            return {"jobs": jobs, "warnings": [], "batch_checksum": binding}
        original = resolve(data)
        receipts = []
        for item in original["jobs"]:
            job = batch.verified(item["job_token"], "job")
            value = {**job, "kind": "prepared", "job_fingerprint": batch.media_fingerprint(job),
                     "url": job["source_url"], "linked_image": True, "s3_key": None}
            receipts.append(batch.signed(value))
        changed = copy.deepcopy(data)
        changed["media"][2]["source_url"] = "https://example.org/new-map.pdf"
        changed["document"]["stations"][0]["station"]["name"] = "Corrected name"
        with patch.object(batch, "resolve_batch", side_effect=resolve):
            result = batch.revise_batch(changed, receipts)
        self.assertEqual(len(result["prepared"]), 2)
        self.assertEqual({asset["role"] for asset in result["prepared"]}, {"cover", "logo"})
        for asset in result["prepared"]:
            value = batch.verified(asset["receipt"], "prepared")
            self.assertEqual(value["batch_checksum"], batch.checksum(changed))
        with patch.object(batch, "resolve_batch", side_effect=resolve):
            with self.assertRaises(ValidationProblem):
                batch.revise_batch(changed, [receipts[0] + "tampered"])

    def test_revised_receipt_assembles_after_station_data_correction(self):
        data = document()
        data["media"][2].update(status="found", source_provider="official", source_url="https://example.org/map.webp", source_page_url="https://example.org/")
        with patch("app.routes.admin_resort_import._resolve", return_value=None), patch.object(batch, "public_target"):
            resolved = batch.resolve_batch(data)
            with patch.object(batch, "probe_map_image"):
                prepared = batch.prepare_media(resolved["jobs"][0]["job_token"])
            changed = copy.deepcopy(data)
            changed["document"]["stations"][0]["station"]["altitude_min_m"] = 1200
            result = batch.revise_batch(changed, [prepared["receipt"]])
            self.assertEqual(len(result["prepared"]), 1)
            output, _ = batch.assemble_batch(changed, [result["prepared"][0]["receipt"]])
        self.assertEqual(output["stations"][0]["station"]["altitude_min_m"], 1200)
        self.assertEqual(output["stations"][0]["pistes"]["official_map_url"], prepared["url"])
        with patch.object(batch, "resolve_batch", return_value=result):
            with self.assertRaises(ValidationProblem):
                batch.revise_batch(changed, [prepared["receipt"], prepared["receipt"]])

    def test_fingerprint_changes_with_generated_bytes_and_source_properties(self):
        job = {"kind": "job", "batch_checksum": "before", "station_slug": "test", "role": "cover", "data_base64": "AAAA", "source_kind": "generated"}
        same = {**job, "batch_checksum": "after"}
        self.assertEqual(batch.media_fingerprint(job), batch.media_fingerprint(same))
        self.assertNotEqual(batch.media_fingerprint(job), batch.media_fingerprint({**job, "data_base64": "BBBB"}))
        self.assertNotEqual(batch.media_fingerprint(job), batch.media_fingerprint({**job, "format": "interactive"}))

    def test_manual_generated_logo_is_not_overwritten_by_feed(self):
        data = document("FR")
        data["media"][1].update(status="found", source_kind="generated", source_selection="manual", data_base64="placeholder")
        logos = [{"external_station_id": "STAT1", "external_name": "Test Station", "logo": {"url": "https://example.org/old.png"}}]
        with patch.object(batch, "generated_bytes", return_value=b"generated"), \
             patch("app.routes.admin_resort_import._resolve", return_value=None), \
             patch.object(batch.anmsm_logos, "fetch_stations", return_value=logos), \
             patch.object(batch, "prepared_map_feed", return_value=[]):
            result = batch.resolve_batch(data)
        logo = next(job for job in result["jobs"] if job["role"] == "logo")
        self.assertEqual(logo["source_provider"], "generated")
        self.assertNotIn("source_url", logo)

    def test_logo_timeout_identifies_download_phase(self):
        job = {"kind": "job", "station_slug": "pralognan-la-vanoise", "role": "logo",
               "source_provider": "anmsm", "source_url": "https://example.org/logo.png"}
        with patch.object(batch, "download", side_effect=TimeoutError()), self.assertLogs(self.app.logger, level="INFO") as logs:
            with self.assertRaises(ValidationProblem) as raised:
                batch.prepare_media(batch.signed(job))
        message = raised.exception.errors[0]["message"]
        self.assertIn("pralognan-la-vanoise", message)
        self.assertIn("téléchargement du logo ANMSM", message)
        self.assertIn("délai dépassé", message)
        self.assertTrue(any("failed" in line and "TimeoutError" in line for line in logs.output))
        self.assertTrue(all(job["source_url"] not in line for line in logs.output))

    def test_batch_logo_selects_fast_encoder_and_preserves_40k_limit(self):
        job = {"kind": "job", "station_slug": "test", "role": "logo", "source_provider": "anmsm",
               "source_url": "https://example.org/logo.png"}
        with tempfile.NamedTemporaryFile(delete=False) as original:
            original.write(b"source")
            source = original.name
        def convert(source_path, output_path, **kwargs):
            self.assertEqual(kwargs, {"output_limit": 40 * 1024, "webp_method": 4})
            with open(output_path, "wb") as output:
                output.write(b"prepared")
            return {"optimized_width": 512, "optimized_height": 512}
        with patch.object(batch, "download", return_value=(source, "image/png", "digest")), \
             patch.object(batch.anmsm_logos, "_convert_subprocess", side_effect=convert), \
             patch.object(batch.s3, "put_file", return_value="https://cdn.example/logo.webp"), \
             patch.object(batch.s3, "preview_url", return_value="https://cdn.example/logo.webp"):
            result = batch.prepare_media(batch.signed(job))
        self.assertEqual(result["url"], "https://cdn.example/logo.webp")
        self.assertFalse(os.path.exists(source))

    def test_logo_conversion_error_preserves_code_and_names_phase(self):
        job = {"station_slug": "test", "role": "logo"}
        with self.assertRaises(batch.anmsm_logos.LogoImportError) as raised:
            with batch.media_stage(job, "conversion du logo en WebP"):
                raise batch.anmsm_logos.LogoImportError("conversion_timeout", "Conversion trop longue.")
        self.assertEqual(raised.exception.code, "conversion_timeout")
        self.assertIn("conversion du logo en WebP", str(raised.exception))

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
        job = {"kind": "job", "batch_checksum": "abc", "station_slug": "test-station", "role": "logo", "source_provider": "anmsm", "source_url": "https://example.org/logo.png"}
        with patch.object(batch, "download", return_value=(path, "image/png", "hash")), \
             patch.object(batch.s3, "put_file", side_effect=upload), \
             patch.object(batch.s3, "preview_url", return_value="https://preview.example/logo.webp"):
            result = batch.prepare_media(batch.signed(job))
        self.assertEqual(captured["mode"], "RGBA")
        self.assertLessEqual(captured["size"], 40 * 1024)
        self.assertEqual(batch.verified(result["receipt"], "prepared")["url"], result["url"])
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
