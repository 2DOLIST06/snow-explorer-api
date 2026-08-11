import unittest

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
        self.database.create_tables([Region, Resort])
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

    def test_list_uses_database_region_identifiers(self):
        response = self.client.get("/api/regions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [region["id"] for region in response.get_json()],
            ["auvergne-rhone-alpes", "provence-alpes-cote-d-azur"],
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
            ["auron", "legacy-paca"],
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
        self.assertEqual(
            self.client.get("/api/admin/regions/auvergne-rhone-alpes").get_json()["region"],
            region,
        )

    def test_editor_rejects_unknown_fields(self):
        response = self.client.patch(
            "/api/admin/regions/auvergne-rhone-alpes", json={"name": "Non"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "unknown_fields")


if __name__ == "__main__":
    unittest.main()
