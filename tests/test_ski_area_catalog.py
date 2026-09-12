import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

class _PasswordHasher:
    def __init__(self, **_kwargs): pass
sys.modules.setdefault("argon2", types.SimpleNamespace(PasswordHasher=_PasswordHasher, Type=types.SimpleNamespace(ID=1)))
sys.modules.setdefault("argon2.exceptions", types.SimpleNamespace(InvalidHashError=ValueError, VerificationError=ValueError, VerifyMismatchError=ValueError))

from flask import Flask
from peewee import SqliteDatabase

from app.models.resort import Resort
from app.models.ski_area import (SkiArea, SkiAreaCatalogArea, SkiAreaCatalogImport,
                                 SkiAreaCatalogNotice, SkiAreaExpectedMembership,
                                 SkiAreaExpectedStation, SkiAreaResort)
from app.routes.admin_ski_area_catalog import bp_admin_ski_area_catalog
from app.services import ski_area_catalog


class SkiAreaCatalogTests(unittest.TestCase):
    def setUp(self):
        self.db = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.models = [Resort, SkiArea, SkiAreaResort, SkiAreaCatalogImport, SkiAreaCatalogArea,
                       SkiAreaExpectedStation, SkiAreaExpectedMembership, SkiAreaCatalogNotice]
        self.db.bind(self.models, bind_refs=False, bind_backrefs=False); self.db.create_tables(self.models)
        self.active = Resort.create(id="stable", name="Alpha", slug="alpha", country_code="FR", department="Savoie")
        self.inactive = Resort.create(id="sleeping", name="Dormante", slug="dormante", is_active=False,
                                      country_code="FR", department="Savoie")
        self.catalog = {"schema_version": ski_area_catalog.SCHEMA_VERSION, "catalog_id": "test", "batch_id": "one",
            "existing_resorts_inventory": [], "review_proposals": [], "alerts": [],
            "areas": [
                {"catalog_key": "shared", "name": "Shared", "slug": "shared", "status": "draft",
                 "area_kind": "linked_area", "notes": "bus", "sources": [], "members": [
                    {"station_ref": "s:alpha", "evidence_status": "confirmed", "relation_kind": "member_or_access_point"},
                    {"station_ref": "s:missing", "evidence_status": "confirmed", "relation_kind": "member_or_access_point"}]},
                {"catalog_key": "other", "name": "Other", "slug": "other", "status": "draft",
                 "area_kind": "local", "notes": "", "sources": [], "members": [
                    {"station_ref": "s:missing", "evidence_status": "confirmed", "relation_kind": "member_or_access_point"},
                    {"station_ref": "s:detail", "evidence_status": "confirmed", "relation_kind": "member_or_access_point"},
                    {"station_ref": "s:sleeping", "evidence_status": "confirmed", "relation_kind": "member_or_access_point"}]}],
            "station_catalog": [
                {"station_ref": "s:alpha", "name": "Alpha", "country_code": "FR", "department": "Savoie", "aliases": [],
                 "existing_resort_id": "stable", "existing_slug": "alpha", "resolution": "matched_existing", "covered_by_resort_ids": []},
                {"station_ref": "s:sleeping", "name": "Dormante", "country_code": "FR", "department": "Savoie", "aliases": [],
                 "existing_resort_id": "sleeping", "existing_slug": "dormante", "resolution": "matched_existing", "covered_by_resort_ids": []},
                {"station_ref": "s:missing", "name": "Future", "country_code": "FR", "department": "Savoie", "aliases": ["Futur"],
                 "existing_resort_id": None, "existing_slug": None, "resolution": "missing", "covered_by_resort_ids": []},
                {"station_ref": "s:detail", "name": "Village", "country_code": "FR", "department": "Savoie", "aliases": [],
                 "existing_resort_id": None, "existing_slug": None, "resolution": "optional_detail", "covered_by_resort_ids": ["stable"]}]}
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / "catalog.json"
        self.path.write_text(json.dumps(self.catalog), encoding="utf8")
        self.path_patch = patch.object(ski_area_catalog, "CATALOG_PATH", self.path); self.path_patch.start()
        app = Flask(__name__); app.register_blueprint(bp_admin_ski_area_catalog); self.client = app.test_client()

    def tearDown(self):
        self.path_patch.stop(); self.tmp.cleanup(); self.db.drop_tables(self.models); self.db.close()

    def test_preview_is_read_only_and_flags_inactive_and_optional(self):
        body = self.client.post("/api/admin/ski-area-catalog/preview").get_json()["preview"]
        self.assertEqual(body["counts"]["stations_inactive"], 1)
        self.assertEqual(body["counts"]["stations_optional_detail"], 1)
        self.assertEqual(SkiArea.select().count(), 0)

    def test_apply_is_idempotent_preserves_expectations_and_inactive(self):
        digest = self.client.post("/api/admin/ski-area-catalog/preview").get_json()["preview"]["sha256"]
        self.assertEqual(self.client.post("/api/admin/ski-area-catalog/imports", json={"expected_sha256": digest}).status_code, 201)
        self.assertTrue(all(area.status == "draft" for area in SkiArea.select()))
        self.assertEqual(SkiAreaExpectedStation.select().count(), 4)
        self.assertEqual(SkiAreaExpectedMembership.select().count(), 5)
        self.assertEqual(SkiAreaResort.select().count(), 2)
        self.assertFalse(Resort.get_by_id("sleeping").is_active)
        self.client.post("/api/admin/ski-area-catalog/imports", json={"expected_sha256": digest})
        self.assertEqual(SkiArea.select().count(), 2); self.assertEqual(SkiAreaExpectedMembership.select().count(), 5)

    def test_explicit_resolution_links_all_domains_and_ignore_is_durable(self):
        ski_area_catalog.apply_catalog()
        expected = SkiAreaExpectedStation.get(SkiAreaExpectedStation.station_ref == "s:missing")
        future = Resort.create(id="future", name="Future", slug="future", country_code="FR", department="Savoie")
        response = self.client.post(f"/api/admin/ski-area-catalog/expectations/{expected.id}/decision",
                                    json={"decision": "confirm", "resort_id": future.id})
        self.assertEqual(response.get_json()["memberships_linked"], 2)
        link = expected.expected_memberships.get()
        link.state = "ignored"; link.decision_origin = "manual"; link.save()
        SkiAreaResort.delete().where((SkiAreaResort.ski_area == link.catalog_area.ski_area) & (SkiAreaResort.resort == future)).execute()
        ski_area_catalog.apply_catalog()
        self.assertEqual(link.id and SkiAreaExpectedMembership.get_by_id(link.id).state, "ignored")
        self.assertFalse(SkiAreaResort.get_or_none((SkiAreaResort.ski_area == link.catalog_area.ski_area) & (SkiAreaResort.resort == future)))

    def test_name_only_is_a_proposal_and_bad_stable_id_is_conflict(self):
        Resort.create(id="lookalike", name="Future", slug="future", country_code=None, department=None)
        preview = ski_area_catalog.preview_catalog()
        future = next(row for row in preview["stations"] if row["station_ref"] == "s:missing")
        self.assertEqual(future["state"], "candidate")
        self.catalog["station_catalog"][0]["existing_slug"] = "wrong"
        self.path.write_text(json.dumps(self.catalog), encoding="utf8")
        preview = ski_area_catalog.preview_catalog()
        alpha = next(row for row in preview["stations"] if row["station_ref"] == "s:alpha")
        self.assertEqual(alpha["state"], "conflict")


if __name__ == "__main__": unittest.main()
