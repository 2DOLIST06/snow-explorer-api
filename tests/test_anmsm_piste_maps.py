import json
import os
import tempfile
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
from app.services.anmsm_piste_maps import PISTE_MAPS_FEED_URL, _convert, fetch_maps, parse_record


# Real Donnees Stations/Tourinsoft V3 shape from the Monts Jura example.
REAL_DONNEES_STATIONS_RECORD = {
    "SyndicObjectID": "PARENT-OBJECT-ID",
    "SyndicObjectName": "Monts Jura",
    "Object": {
        "NOM": "Monts Jura",
        "PLANPISTESs": [{
            "SyndicObjectId": "STATANMSM01010012",
            "Plandespistes": {
                "MediaID": "1ff8893b-e626-4801-9d3f-1b5af61cc825",
                "Titre": "Plan des pistes hiver 2023-2024",
                "Credit": "Monts Jura",
                "Url": "https://anmsm.media.tourinsoft.eu/upload/MONTS-JURA-General-hiver-2023-2024-V7-HD.pdf",
            },
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

    def test_parses_exact_monts_jura_nested_plan_collection(self):
        station = parse_record(REAL_DONNEES_STATIONS_RECORD)
        self.assertEqual(station["external_station_id"], "STATANMSM01010012")
        self.assertEqual(station["external_name"], "Monts Jura")
        self.assertEqual(station["piste_maps"], [{
            "media_id": "1ff8893b-e626-4801-9d3f-1b5af61cc825",
            "url": "https://anmsm.media.tourinsoft.eu/upload/MONTS-JURA-General-hiver-2023-2024-V7-HD.pdf",
            "format": "pdf", "title": "Plan des pistes hiver 2023-2024",
            "credit": "Monts Jura", "modified_at": None, "plan_type": None,
        }])

    def test_supports_collection_at_root_when_object_envelope_is_absent(self):
        record = {"NOM": "Second station", "PLANPISTESs": {
            "SyndicObjectId": "EN-2", "Plandespistes": {
                "ID": "pdf-2", "Url": "https://media/map.pdf", "Format": "PDF"}}}
        station = parse_record(record)
        self.assertEqual([media["media_id"] for media in station["piste_maps"]], ["pdf-2"])
        self.assertEqual(station["piste_maps"][0]["format"], "pdf")

    def test_fetch_uses_donnees_stations_and_reports_counts(self):
        station_without_plan = {"SyndicObjectID": "STAT-WITHOUT-PLAN",
                                "Object": {"NOM": "No map"}}
        response = Response([REAL_DONNEES_STATIONS_RECORD, station_without_plan])
        session = Mock(); session.get.return_value = response
        app = self.app()
        with app.app_context():
            result = fetch_maps(session)
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], PISTE_MAPS_FEED_URL)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["piste_maps"], [])
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
            (Response([{"SyndicObjectID": "STAT1", "Object": {"PLANPISTES": []}}]),
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
                                   external_station_id="  statanmsm01010012  ", verified=True)
        app = Flask(__name__); app.register_blueprint(bp_admin_piste_maps)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(MODELS); self.database.close()

    def test_workspace_matches_normalized_id_preserves_values_and_does_not_mutate(self):
        second = {"SyndicObjectID": "UNMATCHED", "Object": {"NOM": "Unmatched",
            "PLANPISTESs": [{"SyndicObjectId": "UNMATCHED", "Plandespistes": {
                "MediaID": "map-2", "Url": "https://media/map-2.pdf",
                "Extension": "PDF"}}]}}
        before = {model: model.select().count() for model in MODELS}
        with patch("app.services.anmsm_piste_maps.fetch_maps",
                   return_value=[parse_record(REAL_DONNEES_STATIONS_RECORD), parse_record(second)]), \
             patch("app.services.anmsm_piste_maps.download") as download, \
             patch("app.services.anmsm_piste_maps.StationPisteMapCandidate.create") as create, \
             patch("app.routes.admin_piste_maps.s3.preview_url") as preview:
            response = self.client.get("/api/admin/anmsm/piste-maps/workspace")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["stats"]["stations_detected"], 2)
        self.assertEqual(body["stats"]["plans_detected"], 2)
        self.assertEqual(body["stats"]["stations_matched"], 1)
        self.assertEqual(body["stats"]["stations_unmatched"], 1)
        self.assertEqual(body["stats"]["plans_to_prepare"], 1)
        unmatched = next(row for row in body["rows"] if row["mapping_status"] == "unmatched")
        self.assertFalse(unmatched["preparation_required"])
        self.assertEqual(len(body["stations"]), 2)
        mapped = next(row for row in body["rows"] if row["mapping_status"] == "matched")
        self.assertEqual(mapped["external_station_id"], "STATANMSM01010012")
        self.assertEqual(mapped["anmsm_media_id"], "1ff8893b-e626-4801-9d3f-1b5af61cc825")
        self.assertEqual(mapped["source_format"], "pdf")
        self.assertEqual(mapped["anmsm_title"], "Plan des pistes hiver 2023-2024")
        self.assertEqual(AnmsmStationMapping.get().external_station_id,
                         "  statanmsm01010012  ")
        self.assertEqual({model: model.select().count() for model in MODELS}, before)
        download.assert_not_called()
        create.assert_not_called()
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

    def _candidate(self, **changes):
        values=dict(station=self.resort, external_station_id="STATANMSM01010012",
            anmsm_media_id="1ff8893b-e626-4801-9d3f-1b5af61cc825", source_url="https://media/map.pdf",
            source_checksum="a"*64, source_format="pdf", source_size_bytes=100,
            original_s3_key="maps/original.pdf", display_s3_key=None, warnings='["pdf_display_not_generated"]')
        values.update(changes); return StationPisteMapCandidate.create(**values)

    def test_resume_pdf_uses_s3_original_without_duplicate_or_source_download(self):
        candidate=self._candidate(); paths=[]
        def stored(key,path,limit):
            paths.append(path); open(path,"wb").write(b"%PDF-valid"); return 10
        def converted(source,output,fmt):
            paths.append(output); open(output,"wb").write(b"webp")
            return {"source_format":"pdf","source_width":4000,"source_height":2800,
                    "display_width":4000,"display_height":2800,"display_size_bytes":4,"warnings":[]}
        with patch("app.services.anmsm_piste_maps.fetch_maps",return_value=[parse_record(REAL_DONNEES_STATIONS_RECORD)]), \
             patch("app.services.anmsm_piste_maps.download") as source_download, \
             patch("app.services.anmsm_piste_maps.s3.download_file",side_effect=stored), \
             patch("app.services.anmsm_piste_maps._convert",side_effect=converted), \
             patch("app.services.anmsm_piste_maps.s3.put_file") as upload, \
             patch("app.routes.admin_piste_maps.s3.preview_url",side_effect=lambda key:f"https://signed/{key}"):
            response=self.client.post("/api/admin/anmsm/piste-maps/prepare",json={
                "external_station_id":"STATANMSM01010012","anmsm_media_id":candidate.anmsm_media_id})
        self.assertEqual(response.status_code,200); self.assertEqual(StationPisteMapCandidate.select().count(),1)
        source_download.assert_not_called(); upload.assert_called_once()
        candidate=StationPisteMapCandidate.get_by_id(candidate.id)
        self.assertEqual(candidate.original_s3_key,"maps/original.pdf")
        self.assertEqual(candidate.display_s3_key,"maps/display.webp")
        self.assertEqual(candidate.warning_codes(),[])
        self.assertEqual(response.get_json()["row"]["candidate_preview_url"],"https://signed/maps/display.webp")
        self.assertTrue(all(not os.path.exists(path) for path in paths))

    def test_new_pdf_keeps_original_and_creates_webp_display(self):
        source=tempfile.NamedTemporaryFile(delete=False); source.write(b"%PDF-valid"); source.close()
        def converted(src,out,fmt):
            open(out,"wb").write(b"webp")
            return {"source_format":"pdf","source_width":5000,"source_height":3000,
                    "display_width":5000,"display_height":3000,"display_size_bytes":4,"warnings":[]}
        with patch("app.services.anmsm_piste_maps.fetch_maps",return_value=[parse_record(REAL_DONNEES_STATIONS_RECORD)]), \
             patch("app.services.anmsm_piste_maps.download",return_value=(source.name,10,"application/pdf","b"*64)), \
             patch("app.services.anmsm_piste_maps._convert",side_effect=converted), \
             patch("app.services.anmsm_piste_maps.s3.put_file") as upload, \
             patch("app.routes.admin_piste_maps.s3.preview_url",side_effect=lambda key:f"https://signed/{key}"):
            response=self.client.post("/api/admin/anmsm/piste-maps/prepare",json={
                "external_station_id":"STATANMSM01010012","anmsm_media_id":"1ff8893b-e626-4801-9d3f-1b5af61cc825"})
        self.assertEqual(response.status_code,200); candidate=StationPisteMapCandidate.get()
        self.assertTrue(candidate.original_s3_key.endswith("/original.pdf")); self.assertTrue(candidate.display_s3_key.endswith("/display.webp"))
        self.assertEqual(candidate.display_width,5000); self.assertEqual(upload.call_count,2)
        self.assertFalse(os.path.exists(source.name))

    def test_converter_error_leaves_resumable_candidate_and_original_untouched(self):
        candidate=self._candidate()
        with patch("app.services.anmsm_piste_maps.fetch_maps",return_value=[parse_record(REAL_DONNEES_STATIONS_RECORD)]), \
             patch("app.services.anmsm_piste_maps.s3.download_file",side_effect=ValueError("bad")), \
             patch("app.services.anmsm_piste_maps.download") as source_download:
            response=self.client.post("/api/admin/anmsm/piste-maps/prepare",json={
                "external_station_id":"STATANMSM01010012","anmsm_media_id":candidate.anmsm_media_id})
        self.assertEqual(response.status_code,422); self.assertEqual(response.get_json()["error"],"stored_original_unavailable")
        source_download.assert_not_called(); self.assertEqual(StationPisteMapCandidate.select().count(),1)
        self.assertEqual(StationPisteMapCandidate.get().original_s3_key,"maps/original.pdf")

    def test_pdf_without_display_cannot_be_published_and_old_plan_is_unchanged(self):
        self.resort.pistes_large_map_url="https://old/map.webp"; self.resort.save(); candidate=self._candidate()
        with patch("app.routes.admin_piste_maps.s3.validate_object") as validate:
            response=self.client.post("/api/admin/anmsm/piste-maps/bulk-approve",json={"candidate_ids":[candidate.id]})
        self.assertEqual(response.get_json()["results"][0]["error"],"invalid_candidate_object")
        validate.assert_not_called(); self.assertEqual(Resort.get_by_id(self.resort.id).pistes_large_map_url,"https://old/map.webp")

    def test_publish_writes_only_requested_station_display_and_preserves_previous(self):
        self.resort.pistes_large_map_url="https://old/map.webp"; self.resort.save()
        other=Resort.create(id="station-2",name="Other",slug="other",pistes_large_map_url="https://old/other")
        candidate=self._candidate(display_s3_key="maps/display.webp")
        with patch("app.routes.admin_piste_maps.s3.validate_object",return_value=True), \
             patch("app.routes.admin_piste_maps.s3.public_url",return_value="https://public/display.webp"), \
             patch("app.routes.admin_piste_maps.invalidate_station"):
            response=self.client.post("/api/admin/anmsm/piste-maps/bulk-approve",json={"candidate_ids":[candidate.id]})
        self.assertEqual(response.status_code,200); candidate=StationPisteMapCandidate.get_by_id(candidate.id)
        self.assertEqual(candidate.previous_plan_url,"https://old/map.webp")
        self.assertEqual(Resort.get_by_id(self.resort.id).pistes_large_map_url,"https://public/display.webp")
        self.assertEqual(Resort.get_by_id(other.id).pistes_large_map_url,"https://old/other")


class PisteMapConverterBoundaryTests(unittest.TestCase):
    def test_pdfium_renders_only_first_page_to_webp(self):
        from PIL import Image
        app=Flask(__name__); app.config.update(
            ANMSM_PISTE_MAP_DISPLAY_MAX_DIMENSION=1200,
            ANMSM_PISTE_MAP_MAX_PIXELS=2_000_000)
        source=tempfile.NamedTemporaryFile(delete=False,suffix=".pdf"); source.close()
        output=source.name+".webp"
        pages=[Image.new("RGB",(600,400),color) for color in ("white","black")]
        try: pages[0].save(source.name,"PDF",save_all=True,append_images=pages[1:])
        finally:
            for page in pages: page.close()
        try:
            with app.app_context(): metadata=_convert(source.name,output,"pdf")
            self.assertEqual(metadata["source_format"],"pdf")
            self.assertEqual((metadata["display_width"],metadata["display_height"]),(1200,800))
            self.assertGreater(metadata["display_size_bytes"],0)
            with Image.open(output) as display: self.assertEqual(display.format,"WEBP")
        finally:
            for path in (source.name,output):
                try: os.unlink(path)
                except FileNotFoundError: pass

    def test_pdfium_ignores_later_pages(self):
        from PIL import Image
        app=Flask(__name__)
        source=tempfile.NamedTemporaryFile(delete=False,suffix=".pdf"); source.close()
        output=source.name+".webp"; pages=[Image.new("RGB",(100,100)) for _ in range(2)]
        try: pages[0].save(source.name,"PDF",save_all=True,append_images=pages[1:])
        finally:
            for page in pages: page.close()
        try:
            with app.app_context(): metadata=_convert(source.name,output,"pdf")
            self.assertEqual((metadata["display_width"],metadata["display_height"]),(3000,3000))
            self.assertLessEqual(metadata["display_width"]*metadata["display_height"],9_000_000)
        finally:
            for path in (source.name,output):
                try: os.unlink(path)
                except FileNotFoundError: pass

    def test_child_converter_failure_is_controlled(self):
        app=Flask(__name__); process=Mock(pid=123,returncode=2)
        process.communicate.return_value=(json.dumps({"ok":False,"error":"pdf_conversion_failed"}),"")
        with app.app_context(), patch("app.services.anmsm_piste_maps.subprocess.Popen",return_value=process), \
             self.assertRaises(LogoImportError) as raised:
            _convert("in.pdf","out.webp","pdf")
        self.assertEqual(raised.exception.code,"pdf_conversion_failed")

    def test_sigkill_is_reported_as_memory_limit(self):
        app=Flask(__name__); process=Mock(pid=123,returncode=-__import__('signal').SIGKILL)
        process.communicate.return_value=("","")
        with app.app_context(), patch("app.services.anmsm_piste_maps.subprocess.Popen",return_value=process), \
             self.assertRaises(LogoImportError) as raised:
            _convert("in.pdf","out.webp","pdf")
        self.assertEqual(raised.exception.code,"conversion_memory_limit")

    def test_sigsegv_and_sigabrt_are_controlled(self):
        for child_signal, code in ((__import__('signal').SIGSEGV,"conversion_sigsegv"),
                                   (__import__('signal').SIGABRT,"conversion_sigabrt")):
            process=Mock(pid=123,returncode=-child_signal); process.communicate.return_value=("","")
            with self.subTest(signal=child_signal), Flask(__name__).app_context(), \
                 patch("app.services.anmsm_piste_maps.subprocess.Popen",return_value=process), \
                 self.assertRaises(LogoImportError) as raised:
                _convert("in.pdf","out.webp","pdf")
            self.assertEqual(raised.exception.code,code)

    def test_timeout_kills_isolated_process_group(self):
        app=Flask(__name__); app.config["ANMSM_CONVERSION_TIMEOUT"]=0.01
        process=Mock(pid=123,returncode=None); process.communicate.side_effect=[__import__('subprocess').TimeoutExpired("worker",.01),("","")]
        with app.app_context(), patch("app.services.anmsm_piste_maps.subprocess.Popen",return_value=process), \
             patch("app.services.anmsm_piste_maps.os.killpg") as kill, self.assertRaises(LogoImportError) as raised:
            _convert("in.pdf","out.webp","pdf")
        self.assertEqual(raised.exception.code,"conversion_timeout"); kill.assert_called_once()

    def test_invalid_pdf_is_reported_by_child(self):
        app=Flask(__name__); source=tempfile.NamedTemporaryFile(delete=False,suffix=".pdf")
        source.write(b"not a pdf"); source.close(); output=source.name+".webp"
        try:
            with app.app_context(), self.assertRaises(LogoImportError) as raised: _convert(source.name,output,"pdf")
            self.assertEqual(raised.exception.code,"invalid_pdf")
            self.assertFalse(os.path.exists(output))
        finally:
            os.unlink(source.name)


if __name__ == "__main__":
    unittest.main()
