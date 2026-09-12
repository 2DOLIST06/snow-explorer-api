import sys
import types
import unittest

class _PasswordHasher:
    def __init__(self, **_kwargs): pass

sys.modules.setdefault("argon2", types.SimpleNamespace(PasswordHasher=_PasswordHasher, Type=types.SimpleNamespace(ID=1)))
sys.modules.setdefault("argon2.exceptions", types.SimpleNamespace(
    InvalidHashError=ValueError, VerificationError=ValueError, VerifyMismatchError=ValueError))
sys.modules.setdefault("requests", types.SimpleNamespace(
    get=lambda *_args, **_kwargs: None,
    exceptions=types.SimpleNamespace(RequestException=Exception),
    RequestException=Exception,
))

from flask import Flask
from peewee import SqliteDatabase

from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from app.routes.ski_areas import (bp_admin_ski_areas, bp_public_ski_areas,
                                  bp_station_ski_areas)
from app.services.admin_auth import protect_admin_routes


class SkiAreaApiTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.models = [Resort, SkiArea, SkiAreaResort]
        self.database.bind(self.models, bind_refs=False, bind_backrefs=False)
        self.database.create_tables(self.models)
        app = Flask(__name__)
        app.config.update(PUBLIC_CACHE_ENABLED=False, PUBLIC_CACHE_DIRECTORY_TTL_SECONDS=60)
        app.extensions["public_cache_redis"] = None
        app.register_blueprint(bp_admin_ski_areas)
        app.register_blueprint(bp_public_ski_areas)
        app.register_blueprint(bp_station_ski_areas)
        self.client = app.test_client()
        self.a = Resort.create(id="a", name="Alpha", slug="alpha", is_active=True)
        self.b = Resort.create(id="b", name="Beta", slug="beta", is_active=True)
        self.hidden = Resort.create(id="hidden", name="Hidden", slug="hidden", is_active=False)

    def tearDown(self):
        self.database.drop_tables(self.models)
        self.database.close()

    def test_minimal_draft_zero_null_and_explicit_clear(self):
        response = self.client.post("/api/admin/ski-areas", json={
            "name": "Domaine Démo", "slug": "domaine-demo",
            "pistes_count": 0, "lifts_count": None,
        })
        self.assertEqual(response.status_code, 201)
        body = response.get_json()["ski_area"]
        self.assertEqual(body["pistes_count"], 0)
        self.assertIsNone(body["lifts_count"])
        self.assertEqual(self.client.get("/api/ski-areas/domaine-demo").status_code, 404)

        area_id = body["id"]
        response = self.client.patch(f"/api/admin/ski-areas/{area_id}", json={"description": "texte"})
        self.assertEqual(response.status_code, 200)
        response = self.client.patch(f"/api/admin/ski-areas/{area_id}", json={"description": ""})
        self.assertIsNone(response.get_json()["ski_area"]["description"])
        self.assertEqual(response.get_json()["ski_area"]["pistes_count"], 0)

    def test_many_to_many_duplicate_and_remove_in_both_directions(self):
        area1 = SkiArea.create(name="One", slug="one")
        area2 = SkiArea.create(name="Two", slug="two")
        self.assertEqual(self.client.post(f"/api/admin/ski-areas/{area1.id}/stations/a").status_code, 201)
        self.assertEqual(self.client.post(f"/api/admin/ski-areas/{area1.id}/stations/a").status_code, 409)
        response = self.client.put("/api/admin/stations/a/ski-areas", json={"ski_area_ids": [area1.id, area2.id]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SkiAreaResort.select().where(SkiAreaResort.resort == self.a).count(), 2)
        self.assertEqual(self.client.delete(f"/api/admin/ski-areas/{area1.id}/stations/a").status_code, 204)
        self.assertEqual(SkiAreaResort.select().where(SkiAreaResort.resort == self.a).count(), 1)

    def test_public_filters_and_excludes_current_and_inactive_stations(self):
        area = SkiArea.create(name="Published", slug="published", status="published")
        for station in (self.a, self.b, self.hidden): SkiAreaResort.create(ski_area=area, resort=station)
        detail = self.client.get("/api/ski-areas/published").get_json()["ski_area"]
        self.assertEqual([s["slug"] for s in detail["stations"]], ["alpha", "beta"])

        from app.routes.ski_areas import public_station_domains
        with self.client.application.test_request_context():
            domains = public_station_domains(self.a)
        self.assertEqual([s["slug"] for s in domains[0]["stations"]], ["beta"])

    def test_validates_ranges_and_dates(self):
        response = self.client.post("/api/admin/ski-areas", json={
            "name": "Bad", "slug": "bad", "altitude_min_m": 2000,
            "altitude_max_m": 1000, "forecast_open_date": "2027-04-01",
            "forecast_close_date": "2026-12-01",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "validation_error")

    def test_application_protects_admin_routes(self):
        app = Flask(__name__)
        app.config["ADMIN_SESSION_COOKIE_NAME"] = "admin_session"
        protect_admin_routes(app)
        app.register_blueprint(bp_admin_ski_areas)
        response = app.test_client().get("/api/admin/ski-areas")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "admin_authentication_required")


if __name__ == "__main__":
    unittest.main()
