import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from flask import Flask
from peewee import IntegrityError, PostgresqlDatabase, SqliteDatabase

from app.models.region import Region
from app.models.resort import Resort
from app.routes.admin_regions import bp_admin_regions
from app.routes.public_regions import bp_regions
from app.services.region_pages import RegionPagePreparationError, prepare_region_pages
from scripts.prepare_region_pages import main


# Actual station catalogue observed read-only on 2026-10-14, including inactive rows.
REGIONS = (
    ("auvergne", "Auvergne-Rhône-Alpes", 2, 0),
    ("auvergne-rhone-alpes", "Auvergne-Rhône-Alpes", 63, 52),
    ("bourgogne-franche-comte", "Bourgogne-Franche-Comté", 2, 0),
    ("grand-est", "Grand Est", 1, 0),
    ("nouvelle-aquitaine", "Nouvelle-Aquitaine", 1, 0),
    ("occitanie", "Occitanie", 9, 7),
    ("provence-alpes-cote-d-azur", "Provence-Alpes-Côte d'Azur", 16, 14),
)
EDITORIAL_REGIONS = REGIONS[1:]


class RegionPagePreparationTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.binding = self.database.bind_ctx([Region, Resort])
        self.binding.__enter__()
        self.database.connect()
        # Independently reproduce production NOT NULL and unique-slug constraints.
        self.database.execute_sql('''CREATE TABLE regions (
            id VARCHAR(255) PRIMARY KEY NOT NULL, name VARCHAR(255) NOT NULL,
            slug VARCHAR(255) UNIQUE NOT NULL, country_code VARCHAR(2) NOT NULL,
            seo_text TEXT, meta_title VARCHAR(70), meta_description VARCHAR(170),
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        )''')
        self.database.create_tables([Resort])
        number = 0
        for region_id, name, count, active in REGIONS:
            for index in range(count):
                number += 1
                Resort.create(id=str(number), name=f"Station {number:03d}", slug=f"station-{number}",
                              region_id=region_id, region_name=name, country_code="FR",
                              is_active=index < active)
        app = Flask(__name__)
        app.config.update(TESTING=True, PUBLIC_CACHE_ENABLED=False)
        app.register_blueprint(bp_admin_regions)
        app.register_blueprint(bp_regions)
        self.client = app.test_client()

    def tearDown(self):
        self.database.close()
        self.binding.__exit__(None, None, None)

    def station_rows(self):
        return self.database.execute_sql('SELECT * FROM resort ORDER BY id').fetchall()

    def region_rows(self):
        return self.database.execute_sql('SELECT * FROM regions ORDER BY id').fetchall()

    def existing(self, region_id="auvergne-rhone-alpes", slug=None):
        self.database.execute_sql('''INSERT INTO regions VALUES
            (?, ?, ?, 'FR', '<p>Historique</p>', 'Titre existant', 'Description existante',
             '2020-01-01 00:00:00', '2020-01-02 00:00:00')''',
            (region_id, "Nom éditorial existant", slug or region_id))

    def test_preview_is_read_only_and_lists_exactly_the_six_editorial_ids(self):
        before = self.station_rows()
        with patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
            result = prepare_region_pages(self.database)
        self.assertEqual(result["mode"], "preview")
        self.assertEqual([row["id"] for row in result["missing"]], [row[0] for row in EDITORIAL_REGIONS])
        self.assertEqual(sum(row["stations"] for row in result["missing"]), 92)
        self.assertEqual(result["excluded"], [{"id": "auvergne", "name": "Auvergne-Rhône-Alpes",
                         "country_code": "FR", "stations": 2, "active_stations": 0,
                         "reason": "not_an_editorial_region"}])
        self.assertTrue(all(row["slug"] == row["id"] for row in result["missing"]))
        self.assertEqual(self.region_rows(), [])
        self.assertEqual(self.station_rows(), before)
        self.assertTrue(all(call.args[0].lstrip().split()[0].upper() in {"SELECT", "PRAGMA", "BEGIN"}
                            for call in sql.call_args_list))

    def test_apply_is_idempotent_and_supplies_all_mandatory_columns(self):
        before = self.station_rows()
        with patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
            first = prepare_region_pages(self.database, apply=True)
        self.assertEqual(first["inserted_ids"], [row[0] for row in EDITORIAL_REGIONS])
        self.assertIsNone(Region.get_or_none(Region.id == "auvergne"))
        for region_id, slug, created, updated, seo, title, description in self.database.execute_sql(
                'SELECT id, slug, created_at, updated_at, seo_text, meta_title, meta_description FROM regions'):
            self.assertEqual(region_id, slug)
            self.assertTrue(created and updated)
            self.assertEqual((seo, title, description), (None, None, None))
        rows = self.region_rows()
        self.assertEqual(prepare_region_pages(self.database, apply=True)["inserted_ids"], [])
        self.assertEqual(self.region_rows(), rows)
        self.assertEqual(self.station_rows(), before)
        inserts = [call.args[0] for call in sql.call_args_list if call.args[0].lstrip().startswith("INSERT")]
        self.assertEqual(len(inserts), 6)
        self.assertTrue(all('INSERT INTO "regions"' in query and 'DO NOTHING' in query for query in inserts))
        self.assertTrue(all(call.args[0].lstrip().split()[0].upper() in {"SELECT", "PRAGMA", "BEGIN", "INSERT"}
                            for call in sql.call_args_list))

    def test_existing_editorial_content_identity_and_dates_are_never_overwritten(self):
        self.existing()
        before = self.region_rows()[0]
        result = prepare_region_pages(self.database, apply=True)
        self.assertEqual(len(result["inserted_ids"]), 5)
        self.assertEqual(self.database.execute_sql('SELECT * FROM regions WHERE id=?',
                         ("auvergne-rhone-alpes",)).fetchone(), before)

    def test_each_created_page_can_be_listed_opened_saved_and_reread(self):
        before = self.station_rows()
        prepare_region_pages(self.database, apply=True)
        listing = self.client.get("/api/admin/regions")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.get_json()), 6)
        html = '<h2>Titre</h2><h3>Suite</h3><p><strong>Gras</strong> <em>Italique</em></p><ul><li>Liste</li></ul><a href="https://example.com">Lien</a>'
        for row in listing.get_json():
            url = f'/api/admin/regions/{row["id"]}'
            self.assertEqual(self.client.get(url).get_json()["region"], row)
            response = self.client.patch(url, json={"description_html": html, "meta_title": "Titre SEO",
                                                    "meta_description": "Description SEO"})
            self.assertEqual(response.status_code, 200)
            saved = response.get_json()["region"]
            self.assertEqual(saved["description_html"], html)
            self.assertEqual(saved["seo_text"], html)
            self.assertEqual(self.client.get(url).get_json()["region"], saved)
            public = self.client.get(f'/api/regions/{row["slug"]}')
            self.assertEqual(public.status_code, 200)
            self.assertEqual(public.get_json()["description_html"], html)
            self.assertNotIn("seo_text", public.get_json())
        self.assertEqual(self.station_rows(), before)
        public_list = self.client.get("/api/regions").get_json()
        self.assertEqual(len(public_list), 6)
        for row in public_list:
            self.assertEqual(row["slug"], row["id"])
            self.assertEqual(row["description_html"], html)
            self.assertEqual(row["meta_title"], "Titre SEO")
            self.assertEqual(row["meta_description"], "Description SEO")

    def test_existing_frontend_payload_keeps_content_on_metadata_only_save(self):
        self.existing()
        url = "/api/admin/regions/auvergne-rhone-alpes"
        loaded = self.client.get(url).get_json()["region"]
        # This is the exact selection made by the existing React save handler.
        response = self.client.patch(url, json={"seo_text": loaded["seo_text"] or "",
                                                "meta_title": "Nouveau titre", "meta_description": "Nouvelle description"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["region"]["description_html"], "<p>Historique</p>")
        response = self.client.patch(url, json={"seo_text": "<p>Nouveau</p><script>x</script>"})
        self.assertEqual(response.get_json()["region"]["description_html"], "<p>Nouveau</p>")
        self.assertEqual(self.database.execute_sql('SELECT seo_text FROM regions WHERE id=?',
                         ("auvergne-rhone-alpes",)).fetchone()[0], "<p>Nouveau</p>")

    def test_disagreeing_content_aliases_are_rejected_without_writes(self):
        self.existing()
        before = self.region_rows()
        response = self.client.patch("/api/admin/regions/auvergne-rhone-alpes",
                                     json={"seo_text": "ancien", "description_html": "nouveau"})
        self.assertEqual((response.status_code, response.get_json()),
                         (400, {"error": "conflicting_content_fields"}))
        self.assertEqual(self.region_rows(), before)

    def test_public_station_urls_and_active_region_memberships_are_preserved(self):
        before = self.station_rows()
        prepare_region_pages(self.database, apply=True)
        for region_id, _name, _count, active in EDITORIAL_REGIONS:
            body = self.client.get(f"/api/regions/{region_id}").get_json()
            self.assertEqual(body["id"], region_id)
            self.assertEqual(body["slug"], region_id)
            self.assertEqual(len(body["stations"]), active)
        self.assertEqual(self.station_rows(), before)
        self.assertEqual(self.client.get("/api/regions/auvergne").status_code, 404)
        self.assertEqual(self.client.get("/api/admin/regions/auvergne").status_code, 404)

    def test_conflicting_station_names_abort_before_any_insert(self):
        Resort.update(region_name="Autre nom").where(Resort.id == "3").execute()
        with self.assertRaisesRegex(RegionPagePreparationError, "Conflicting"):
            prepare_region_pages(self.database, apply=True)
        self.assertEqual(self.region_rows(), [])

    def test_legacy_paca_station_id_remains_unchanged_and_opens_canonical_url(self):
        legacy = "provence-alpes-cote-dazur"
        Resort.update(region_id=legacy).where(Resort.region_id == "provence-alpes-cote-d-azur").execute()
        before = self.station_rows()
        prepare_region_pages(self.database, apply=True)
        self.assertIsNotNone(Region.get_or_none(Region.id == legacy))
        url = "/api/admin/regions/provence-alpes-cote-d-azur"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.patch(url, json={"description_html": "<p>PACA</p>"}).status_code, 200)
        self.assertEqual(Region.get_by_id(legacy).description_html, "<p>PACA</p>")
        public = self.client.get("/api/regions/provence-alpes-cote-d-azur").get_json()
        self.assertEqual(len(public["stations"]), 14)
        self.assertEqual(self.station_rows(), before)

    def test_existing_paca_alias_is_preserved_instead_of_creating_duplicate_page(self):
        self.existing("provence-alpes-cote-dazur")
        before = self.region_rows()[0]
        result = prepare_region_pages(self.database, apply=True)
        self.assertEqual(len(result["inserted_ids"]), 5)
        self.assertIsNone(Region.get_or_none(Region.id == "provence-alpes-cote-d-azur"))
        self.assertEqual(self.database.execute_sql('SELECT * FROM regions WHERE id=?',
                         ("provence-alpes-cote-dazur",)).fetchone(), before)

    def test_unique_slug_collision_aborts_without_overwriting_existing_record(self):
        self.existing("other", "occitanie")
        before = self.region_rows()
        with self.assertRaisesRegex(RegionPagePreparationError, "already belongs"):
            prepare_region_pages(self.database, apply=True)
        self.assertEqual(self.region_rows(), before)

    def test_late_insert_failure_rolls_back_the_entire_batch(self):
        execute = self.database.execute_sql
        def fail(sql, params=None, *args, **kwargs):
            if sql.lstrip().startswith('INSERT INTO "regions"') and params[0] == "occitanie":
                raise IntegrityError("simulated concurrent slug conflict")
            return execute(sql, params, *args, **kwargs)
        with patch.object(self.database, "execute_sql", side_effect=fail):
            with self.assertRaises(IntegrityError):
                prepare_region_pages(self.database, apply=True)
        self.assertEqual(self.region_rows(), [])

    def test_missing_required_schema_column_is_not_created_automatically(self):
        self.database.execute_sql("ALTER TABLE regions DROP COLUMN created_at")
        with self.assertRaisesRegex(RegionPagePreparationError, "created_at"):
            prepare_region_pages(self.database, apply=True)
        self.assertEqual(self.region_rows(), [])

    def test_preview_cli_never_initializes_flask_or_calls_apply(self):
        from click.testing import CliRunner
        with patch("scripts.prepare_region_pages.db", self.database), patch("app.create_app") as factory, \
                patch.object(self.database, "connection_context", return_value=nullcontext()):
            result = CliRunner().invoke(main, [])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"mode": "preview"', result.output)
        self.assertEqual(self.region_rows(), [])
        factory.assert_not_called()

    def test_concurrent_existing_id_is_skipped_without_replacing_content(self):
        execute = self.database.execute_sql
        injected = False
        def concurrent(sql, params=None, *args, **kwargs):
            nonlocal injected
            if not injected and sql.lstrip().startswith('INSERT INTO "regions"'):
                injected = True
                self.existing(params[0])
            return execute(sql, params, *args, **kwargs)
        with patch.object(self.database, "execute_sql", side_effect=concurrent):
            result = prepare_region_pages(self.database, apply=True)
        self.assertEqual(len(result["inserted_ids"]), 5)
        self.assertEqual(self.database.execute_sql('SELECT seo_text FROM regions WHERE id=?',
                         ("auvergne-rhone-alpes",)).fetchone()[0], "<p>Historique</p>")

    def test_explicit_public_slug_disambiguates_an_existing_historical_name(self):
        # Existing data must be preserved even when it was created outside this script.
        self.database.execute_sql('''INSERT INTO regions VALUES
            ('auvergne', 'Auvergne-Rhône-Alpes', 'auvergne', 'FR', '<p>Ancien</p>',
             NULL, NULL, '2020-01-01', '2020-01-01')''')
        before = self.region_rows()[0]
        prepare_region_pages(self.database, apply=True)
        rows = self.client.get("/api/regions").get_json()
        wanted = "auvergne-rhone-alpes"
        # Same precedence as frontend regionSlug/isRegionMatch: slug before name.
        matches = [row for row in rows if row["slug"] == wanted or row["id"] == wanted]
        self.assertEqual([row["id"] for row in matches], [wanted])
        self.assertEqual(self.database.execute_sql("SELECT * FROM regions WHERE id='auvergne'").fetchone(), before)

    def test_historical_auvergne_is_not_created_even_if_it_is_the_only_source(self):
        Resort.delete().where(Resort.region_id != "auvergne").execute()
        before = self.station_rows()
        result = prepare_region_pages(self.database, apply=True)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["inserted_ids"], [])
        self.assertEqual([row["id"] for row in result["excluded"]], ["auvergne"])
        self.assertEqual(self.region_rows(), [])
        self.assertEqual(self.station_rows(), before)


class PostgresPreparationContractTests(unittest.TestCase):
    def test_preview_transaction_is_read_only_and_repeatable_read(self):
        database = PostgresqlDatabase("never-connected")
        with patch.object(database, "atomic", return_value=MagicMock()) as atomic, \
                patch.object(database, "execute_sql") as sql, \
                patch("app.services.region_pages._plan", return_value={"missing": [], "existing_ids": []}):
            result = prepare_region_pages(database)
        atomic.assert_called_once_with(isolation_level="REPEATABLE READ")
        sql.assert_called_once_with("SET TRANSACTION READ ONLY")
        self.assertEqual(result["inserted_ids"], [])
