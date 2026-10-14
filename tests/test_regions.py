import unittest
from datetime import datetime, timezone

from flask import Flask
from peewee import SqliteDatabase

from app.models.region import Region
from app.models.resort import Resort
from app.routes.admin_regions import bp_admin_regions
from app.routes.public_regions import bp_regions
from app.routes.public_departments import bp_departments


class RegionRoutesTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.database.bind([Region, Resort])
        self.database.connect()
        # Reproduce the production schema independently of Peewee's model.
        self.database.execute_sql('''CREATE TABLE regions (
            id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
            country_code VARCHAR NOT NULL DEFAULT 'FR', seo_text TEXT,
            meta_title TEXT, meta_description TEXT, updated_at TIMESTAMP
        )''')
        self.database.create_tables([Resort])
        app = Flask(__name__)
        app.register_blueprint(bp_regions)
        app.register_blueprint(bp_departments)
        app.register_blueprint(bp_admin_regions)
        self.client = app.test_client()
        Region.create(id="auvergne-rhone-alpes", name="Auvergne-Rhône-Alpes")
        Region.create(id="provence-alpes-cote-d-azur", name="Provence Alpes Côte d'azur")
        Resort.create(id="1", slug="chamonix", name="Chamonix",
                      region_id="auvergne-rhone-alpes")
        Resort.create(id="2", slug="fermee", name="Fermée",
                      region_id="auvergne-rhone-alpes", is_active=False)
        Resort.create(id="3", slug="ailleurs", name="Ailleurs", region_id="occitanie")
        Resort.create(id="4", slug="auron", name="Auron",
                      region_id="provence-alpes-cote-d-azur")

    def tearDown(self):
        self.database.close()

    def test_public_detail_contains_region_content_and_active_stations(self):
        response = self.client.get("/api/regions/auvergne-rhone-alpes")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["slug"], "auvergne-rhone-alpes")
        self.assertEqual([station["slug"] for station in body["stations"]], ["chamonix"])
        self.assertIsNone(body["description_html"])

    def test_unknown_region_returns_404(self):
        self.assertEqual(self.client.get("/api/regions/inconnue").status_code, 404)

    def test_admin_list_opens_existing_fiches_without_changing_data(self):
        Region.create(id="italie", name="Italie", country_code="IT")
        before = self.database.execute_sql("SELECT * FROM regions ORDER BY id").fetchall()
        listing = self.client.get("/api/admin/regions")
        self.assertEqual(listing.status_code, 200)
        regions = listing.get_json()
        self.assertEqual([region["name"] for region in regions],
                         sorted(region["name"] for region in regions))
        self.assertEqual(len(regions), 3)
        self.assertEqual(self.client.get("/api/admin/regions/").get_json(), regions)
        for region in regions:
            response = self.client.get(f'/api/admin/regions/{region["slug"]}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["region"], region)
        self.assertEqual(before, self.database.execute_sql("SELECT * FROM regions ORDER BY id").fetchall())
        self.assertEqual(len(self.client.get("/api/regions").get_json()), 2)

    def test_legacy_seo_text_is_read_and_saved_with_existing_json_contract(self):
        slug = "auvergne-rhone-alpes"
        original = "<p>Contenu existant</p>"
        self.database.execute_sql("UPDATE regions SET seo_text=? WHERE id=?", (original, slug))
        self.assertEqual(self.client.get(f"/api/admin/regions/{slug}").get_json()["region"]["description_html"], original)
        self.assertEqual(self.client.get(f"/api/regions/{slug}").get_json()["description_html"], original)
        # Updating a separate field must preserve the historical content.
        response = self.client.patch(f"/api/admin/regions/{slug}", json={"meta_title": "Titre"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["region"]["description_html"], original)
        response = self.client.patch(f"/api/admin/regions/{slug}", json={"description_html": "<p>Nouveau</p><script>x</script>"})
        self.assertEqual(response.status_code, 200)
        stored = self.database.execute_sql("SELECT seo_text FROM regions WHERE id=?", (slug,)).fetchone()[0]
        self.assertEqual(stored, "<p>Nouveau</p>")
        self.assertEqual(self.client.get(f"/api/regions/{slug}").get_json()["description_html"], stored)
        self.assertEqual(Region.select().count(), 2)

    def test_admin_can_open_and_save_historical_paca_from_list(self):
        Region.delete().where(Region.id == "provence-alpes-cote-d-azur").execute()
        Region.create(id="provence-alpes-cote-dazur", name="PACA", description_html="<p>Historique</p>")
        listed = next(region for region in self.client.get("/api/admin/regions").get_json()
                      if region["name"] == "PACA")
        url = f'/api/admin/regions/{listed["slug"]}'
        self.assertEqual(self.client.get(url).get_json()["region"], listed)
        response = self.client.patch(url, json={"meta_title": "Titre PACA"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Region.get_by_id("provence-alpes-cote-dazur").meta_title, "Titre PACA")

    def test_admin_prefers_canonical_paca_row_if_both_exist(self):
        Region.create(id="provence-alpes-cote-dazur", name="Ancienne PACA")
        listing = self.client.get("/api/admin/regions").get_json()
        self.assertEqual(len(listing), 2)
        for region in listing:
            self.assertEqual(self.client.get(f'/api/admin/regions/{region["slug"]}').get_json()["region"], region)
        response = self.client.get("/api/admin/regions/provence-alpes-cote-dazur")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["region"]["name"], "Provence Alpes Côte d'azur")

    def test_list_uses_database_region_identifiers(self):
        response = self.client.get("/api/regions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [region["id"] for region in response.get_json()],
            ["auvergne-rhone-alpes", "provence-alpes-cote-d-azur"],
        )
        self.assertTrue(
            all(region["updated_at"] for region in response.get_json())
        )

    def test_paca_detail_uses_corrected_database_identifier(self):
        response = self.client.get("/api/regions/provence-alpes-cote-d-azur")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["id"], "provence-alpes-cote-d-azur")
        self.assertEqual([station["slug"] for station in body["stations"]], ["auron"])

    def test_corrected_paca_url_supports_historical_database_identifier(self):
        Region.delete().where(
            Region.id == "provence-alpes-cote-d-azur"
        ).execute()
        Region.create(
            id="provence-alpes-cote-dazur",
            name="Provence-Alpes-Côte d'Azur",
        )
        Resort.create(
            id="5",
            slug="legacy-paca",
            name="Ancienne donnée PACA",
            region_id="provence-alpes-cote-dazur",
        )

        response = self.client.get("/api/regions/provence-alpes-cote-d-azur")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["id"], "provence-alpes-cote-d-azur")
        self.assertEqual(
            [station["slug"] for station in body["stations"]],
            # The public route orders stations by name (Ancienne before Auron).
            ["legacy-paca", "auron"],
        )

    def test_region_list_canonicalizes_and_deduplicates_historical_paca_id(self):
        Region.create(
            id="provence-alpes-cote-dazur",
            name="Ancien libellé PACA",
        )

        response = self.client.get("/api/regions")

        paca = [
            region for region in response.get_json()
            if region["id"] == "provence-alpes-cote-d-azur"
        ]
        self.assertEqual(len(paca), 1)
        self.assertEqual(paca[0]["name"], "Provence Alpes Côte d'azur")

    def test_departments_filter_uses_corrected_database_identifier(self):
        response = self.client.get(
            "/api/departments?region_id=provence-alpes-cote-d-azur"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [department["code"] for department in response.get_json()],
            ["04", "05", "06", "13", "83", "84"],
        )
        self.assertTrue(
            all(
                department["region_id"] == "provence-alpes-cote-d-azur"
                for department in response.get_json()
            )
        )

    def test_editor_can_read_and_update_sanitized_content(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        Region.update(updated_at=old).where(
            Region.id == "auvergne-rhone-alpes"
        ).execute()
        response = self.client.patch(
            "/api/admin/regions/auvergne-rhone-alpes",
            json={
                "description_html": "<p>Découvrez <strong>les Alpes</strong>.</p><script>x</script>",
                "meta_title": "Stations en Auvergne-Rhône-Alpes",
            },
        )
        self.assertEqual(response.status_code, 200)
        region = response.get_json()["region"]
        self.assertEqual(region["description_html"], "<p>Découvrez <strong>les Alpes</strong>.</p>")
        self.assertGreater(datetime.fromisoformat(region["updated_at"]), old)
        self.assertEqual(
            self.client.get("/api/admin/regions/auvergne-rhone-alpes").get_json()["region"],
            region,
        )

    def test_editor_preserves_heading_tags_in_saved_description_html(self):
        url = "/api/admin/regions/auvergne-rhone-alpes"
        html = "<h1>Titre</h1><h2>Section</h2><h3>Sous-section</h3>"
        response = self.client.patch(url, json={"description_html": html})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["region"]["description_html"], html)
        self.assertEqual(Region.get_by_id("auvergne-rhone-alpes").description_html, html)
        self.assertEqual(self.client.get(url).get_json()["region"]["description_html"], html)

    def test_editor_rejects_unknown_fields(self):
        response = self.client.patch(
            "/api/admin/regions/auvergne-rhone-alpes", json={"name": "Non"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "unknown_fields")


if __name__ == "__main__":
    unittest.main()
