"""Real SQLite persistence tests; PostgreSQL locking is additionally documented."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask, g
from peewee import SqliteDatabase
from app.models.resort import Resort
from app.models.activities import StationActivities, ActivityTemplate, ActivityBatch, ActivityBatchResult
from app.routes.activities import bp_activities
from app.services.activities import validate_block


def block(label="original"):
    return {"enabled": True, "title": "Activités à faire", "items": [
        {"id": "img", "type": "image", "enabled": True, "src": "https://cdn.test/image.png", "alt": "Image", "href": "https://partner.test/?ref=A%2FB&utm_source=snow&x=1&x=2", "newTab": True},
        {"id": "rich", "type": "rich", "enabled": True, "html": f'<h2>{label}</h2><p><a href="https://partner.test/?x=1&amp;y=2" class="activity-button">Découvrir</a></p>'},
        {"id": "embed", "type": "embed", "enabled": True, "html": '<div data-id="abc"></div><script async src="https://partner.test/widget.js"></script>', "origins": ["https://api.partner.test"]},
    ]}


class ActivitiesTests(unittest.TestCase):
    def setUp(self):
        self.db = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.models = [Resort, StationActivities, ActivityTemplate, ActivityBatch, ActivityBatchResult]
        self.binding = self.db.bind_ctx(self.models); self.binding.__enter__(); self.db.connect(); self.db.create_tables(self.models)
        for i in range(3): Resort.create(id=str(i), slug=f"s{i}", name=f"Station {i}", country_code="FR", department="73", description_md="preserve", is_active=i != 2)
        self.app = Flask(__name__); self.app.testing = True
        self.role = "admin"; self.owner = 1
        @self.app.before_request
        def auth(): g.admin_user = SimpleNamespace(id=self.owner, role=self.role)
        self.app.register_blueprint(bp_activities); self.client = self.app.test_client()
        self.patch = patch("app.routes.activities.db", self.db); self.patch.start()
    def tearDown(self):
        self.patch.stop(); self.db.close(); self.binding.__exit__(None, None, None)
    def save(self, slug="s0", revision=0, value=None):
        return self.client.put(f"/api/admin/stations/{slug}/activities", json={"revision": revision, "block": value or block()})
    def template(self):
        response = self.client.post("/api/admin/activity-templates", json={"name": "Modèle", "block": block()})
        self.assertEqual(response.status_code, 201); return response.json
    def prepare(self, template, mode="add", ids=None):
        response = self.client.post(f'/api/admin/activity-templates/{template["id"]}/prepare', json={"revision": template["revision"], "mode": mode, "station_ids": ids or ["0", "1", "2"]})
        self.assertEqual(response.status_code, 201); return response.json
    def execute(self, batch): return self.client.post(f'/api/admin/activity-batches/{batch["id"]}/execute', json={})
    def test_three_formats_order_tracking_and_persistence(self):
        original = block(); original["items"] = list(reversed(original["items"])); original["items"][0]["enabled"] = False
        self.assertEqual(self.save(value=original).status_code, 200)
        actual = self.client.get("/api/admin/stations/s0/activities").json
        self.assertEqual(actual["block"], original); self.assertEqual(actual["revision"], 1)
        self.assertEqual(self.client.get("/api/stations/s0/activities").json, original)
        self.assertEqual(self.client.get("/api/stations/s2/activities").status_code, 404)
        self.assertEqual(Resort.get_by_id("0").description_md, "preserve")
    def test_individual_conflict_does_not_overwrite(self):
        self.save(); self.assertEqual(self.save(value=block("stale")).status_code, 409)
        self.assertEqual(self.save(revision=1, value=block("new")).status_code, 200)
    def test_default_add_skips_even_disabled_blocks_and_retry_is_idempotent(self):
        value = block(); value["enabled"] = False; self.save(value=value)
        batch = self.prepare(self.template())
        response = self.execute(batch); self.assertEqual(response.status_code, 200)
        self.assertEqual([x["status"] for x in response.json["items"]], ["ignored", "added", "added"])
        self.execute(batch); self.assertEqual(StationActivities.select().count(), 3)
        self.assertEqual(StationActivities.get_by_id("1").revision, 1)
        self.assertFalse(json.loads(StationActivities.get_by_id("0").block_json)["enabled"])
    def test_replace_is_explicit_and_uses_frozen_copy(self):
        self.save(); template = self.template(); batch = self.prepare(template, "replace")
        self.client.put(f'/api/admin/activity-templates/{template["id"]}', json={"name": "Edited", "revision": 1, "block": block("changed later")})
        result = self.execute(batch).json
        self.assertEqual(result["items"][0]["status"], "replaced")
        self.assertEqual(json.loads(StationActivities.get_by_id("0").block_json), block())
        self.assertTrue(all(x.description_md == "preserve" for x in Resort.select()))
    def test_batch_preserves_intervening_individual_change_including_absent_block(self):
        batch = self.prepare(self.template(), "replace")
        self.save(value=block("individual"))
        result = self.execute(batch).json
        self.assertEqual(result["items"][0]["status"], "conflict")
        self.assertIn("individual", StationActivities.get_by_id("0").block_json)
        self.assertEqual(result["items"][1]["status"], "added")
    def test_partial_failure_rolls_back_and_can_resume(self):
        batch = self.prepare(self.template())
        from app.routes.activities import write
        def fail_once(station_id, value, revision):
            result = write(station_id, value, revision)
            if station_id == "1": raise RuntimeError("simulated database failure after write")
            return result
        with patch("app.routes.activities.write", side_effect=fail_once): result = self.execute(batch).json
        self.assertEqual([x["status"] for x in result["items"]], ["added", "error", "added"])
        self.assertIsNone(StationActivities.get_or_none(StationActivities.station == "1"))
        self.assertEqual(self.execute(batch).json["items"][1]["status"], "added")
        self.assertTrue(all(x.revision == 1 for x in StationActivities.select()))
    def test_batch_ownership_and_permissions(self):
        batch = self.prepare(self.template()); self.owner = 2
        self.assertEqual(self.execute(batch).status_code, 404)
        self.role = "editor"
        self.assertEqual(self.save().status_code, 403)
        self.assertEqual(self.client.post("/api/admin/activity-templates", json={"name": "x", "block": block()}).status_code, 403)
    def test_filters_and_template_revisions(self):
        self.save(); response = self.client.get("/api/admin/activity-stations?has_block=true&country_code=FR&department=73&active=true&q=Station")
        self.assertEqual([row["id"] for row in response.json["items"]], ["0"])
        template = self.template()
        self.assertEqual(self.client.put(f'/api/admin/activity-templates/{template["id"]}', json={"revision": 0, "name": "x", "block": block()}).status_code, 409)
    def test_rejects_unsafe_or_incompatible_embeds_and_duplicate_ids(self):
        for html in ['<script>alert(1)</script>', '<iframe src="javascript:alert(1)"></iframe>', '<img src="https://test.test/x" onerror="alert(1)">', '<form></form>', '<iframe srcdoc="x"></iframe>']:
            value = block(); value["items"][2]["html"] = html
            self.assertEqual(self.save(value=value).status_code, 400, html)
        value = block(); value["items"][1]["id"] = "img"
        self.assertEqual(self.save(value=value).status_code, 400)
        value = block(); value["items"][0]["href"] = "javascript:alert(1)"
        self.assertEqual(self.save(value=value).status_code, 400)
    def test_large_batch_is_chunked_and_repeated_execution_preserves_revisions(self):
        ids = []
        for i in range(30):
            ident = f"large-{i}"; ids.append(ident)
            Resort.create(id=ident, slug=ident, name=ident, description_md="untouched")
        batch = self.prepare(self.template(), ids=ids)
        first = self.execute(batch).json
        self.assertEqual(first["pending"], 5)
        self.assertEqual(sum(x["status"] == "added" for x in first["items"]), 25)
        self.assertEqual(self.execute(batch).json["pending"], 0)
        self.execute(batch)
        self.assertEqual(StationActivities.select().count(), 30)
        self.assertTrue(all(row.revision == 1 for row in StationActivities.select()))

    def test_template_duplication_and_independent_station_copy(self):
        original = self.template()
        duplicate = self.client.post("/api/admin/activity-templates", json={"name": "Copie", "block": original["block"]}).json
        self.assertNotEqual(original["id"], duplicate["id"])
        batch = self.prepare(duplicate, ids=["0", "1"]); self.execute(batch)
        self.save(revision=1, value=block("individual copy"))
        self.assertEqual(json.loads(StationActivities.get_by_id("1").block_json), original["block"])
        self.assertEqual(self.client.get("/api/admin/activity-templates").json["items"][0]["block"], original["block"])

    def test_rich_content_cannot_execute_code(self):
        value = block(); value["items"][1]["html"] = '<p onclick="x()">Hello</p><iframe src="https://bad.test"></iframe><script>bad()</script>'
        result = validate_block(value)["items"][1]["html"]
        self.assertNotIn("onclick", result); self.assertNotIn("<iframe", result); self.assertNotIn("<script", result)


class ActivitiesUnauthenticatedTests(unittest.TestCase):
    def test_all_admin_activity_routes_require_the_existing_session(self):
        from app.services.admin_auth import protect_admin_routes
        app = Flask(__name__)
        app.config.update(ADMIN_SESSION_SECRET="s" * 64, ADMIN_SESSION_COOKIE_NAME="admin_session")
        protect_admin_routes(app); app.register_blueprint(bp_activities)
        client = app.test_client()
        for method, path in [
            ("GET", "/api/admin/stations/s0/activities"),
            ("PUT", "/api/admin/stations/s0/activities"),
            ("GET", "/api/admin/activity-stations"),
            ("POST", "/api/admin/activity-templates"),
            ("POST", "/api/admin/activity-templates/t/prepare"),
            ("POST", "/api/admin/activity-batches/b/execute"),
        ]:
            self.assertEqual(client.open(path, method=method).status_code, 401, path)


if __name__ == "__main__": unittest.main()
