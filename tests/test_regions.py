import unittest

from flask import Flask
from peewee import SqliteDatabase

from app.models.region import Region, slugify_region
from app.models.resort import Resort
from app.routes.admin_regions import bp_admin_regions
from app.routes.public_regions import bp_regions
from app.routes.public_resorts import bp_public
from app.services.admin_auth import protect_admin_routes


class RegionApiTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.bind([Region, Resort])
        self.database.connect()
        self.database.create_tables([Region, Resort])
        app = Flask(__name__)
        app.config.update(TESTING=True, ADMIN_SESSION_SECRET="x" * 64,
                          ADMIN_SESSION_COOKIE_NAME="admin_session")
        app.register_blueprint(bp_regions)
        app.register_blueprint(bp_public)
        self.client = app.test_client()
        self.region = Region.create(id="paca", name="Provence-Alpes-Côte d’Azur")

    def tearDown(self):
        self.database.drop_tables([Resort, Region])
        self.database.close()

    def test_slug_normalization_and_public_list_detail(self):
        self.assertEqual(slugify_region("Provence-Alpes-Côte d’Azur"),
                         "provence-alpes-cote-d-azur")
        listing = self.client.get("/api/regions")
        self.assertEqual(listing.status_code, 200)
        self.assertIsInstance(listing.get_json(), list)
        self.assertEqual(set(listing.get_json()[0]) & {"id", "name", "slug"},
                         {"id", "name", "slug"})
        detail = self.client.get("/api/regions/provence-alpes-cote-d-azur")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["id"], "paca")
        self.assertEqual(self.client.get("/api/regions/missing").status_code, 404)

    def test_resort_contract_and_region_filter(self):
        Resort.create(id="active", name="Auron", slug="auron", region_id=self.region.id)
        Resort.create(id="inactive", name="Closed", slug="closed", region_id=self.region.id,
                      is_active=False)
        search = self.client.get("/api/resorts/?q=auron")
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.get_json()[0]["region"], {
            "id": "paca", "name": "Provence-Alpes-Côte d’Azur",
            "slug": "provence-alpes-cote-d-azur", "country_code": "FR",
        })
        resorts = self.client.get(
            "/api/regions/provence-alpes-cote-d-azur/resorts?active=true"
        ).get_json()
        self.assertEqual([item["id"] for item in resorts], ["active"])

    def test_legacy_region_schema_never_breaks_public_stations(self):
        # Reproduce the production rollout state which caused /api/resorts to
        # fail: regions existed but did not have slug/SEO columns yet.
        self.database.drop_tables([Region])
        self.database.execute_sql(
            "CREATE TABLE regions (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "country_code TEXT DEFAULT 'FR')"
        )
        self.database.execute_sql(
            "INSERT INTO regions VALUES (?, ?, ?)",
            ("paca", "Provence-Alpes-Côte d’Azur", "FR"),
        )
        Resort.create(id="legacy", name="Auron", slug="auron", region_id="paca",
                      region_name="Provence-Alpes-Côte d’Azur")

        stations = self.client.get("/api/resorts/?q=auron")
        regions = self.client.get("/api/regions")
        detail = self.client.get("/api/regions/provence-alpes-cote-d-azur")

        self.assertEqual(stations.status_code, 200)
        self.assertEqual(stations.get_json()[0]["region"]["slug"],
                         "provence-alpes-cote-d-azur")
        self.assertEqual(regions.status_code, 200)
        self.assertIn("provence-alpes-cote-d-azur",
                      {item["slug"] for item in regions.get_json()})
        self.assertEqual(detail.status_code, 200)


class AdminRegionProtectionTests(unittest.TestCase):
    def test_global_admin_auth_and_csrf_cover_region_routes(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, ADMIN_SESSION_SECRET="x" * 64,
                          ADMIN_SESSION_COOKIE_NAME="admin_session")
        protect_admin_routes(app)
        app.register_blueprint(bp_admin_regions)
        client = app.test_client()
        self.assertEqual(client.get("/api/admin/regions").status_code, 401)
        self.assertEqual(client.patch("/api/admin/regions/paca", json={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
