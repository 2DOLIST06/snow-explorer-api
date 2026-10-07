"""COMPARE fixtures only: PostgreSQL connections are explicitly forbidden."""
import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from peewee import PostgresqlDatabase

from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.models.region import Region
from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from app.services.station_ops.compare import compare_candidates
from app.services.station_ops.matching import MatchingIndex
from app.datetime_utils import utcnow
from app.services.station_ops.normalization import normalized_url
import test_station_ops as scan_fixtures

MODELS = scan_fixtures.MODELS

URL = "/api/admin/station-ops/compare"


class CompareTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = scan_fixtures.StationOpsTests.setUp

    def batch(self, candidates, expected=200):
        response = self.client.post(URL, json={"candidates": candidates}, headers={"X-CSRF-Token": "test-csrf"})
        self.assertEqual(response.status_code, expected, response.get_json())
        if expected == 200:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response.get_json()

    def result(self, data, **extras):
        return self.batch([{"client_ref": " external-001 ", "data": data, **extras}])["results"][0]

    def test_exact_id_and_preserved_client_ref(self):
        row = self.result({"id": "a"})
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["client_ref"], " external-001 ")
        self.assertEqual(row["matched_station"]["id"], "a")
        self.assertIn("exact_id", row["match_reasons"])
        self.assertEqual(row["validation"], {"errors": [], "warnings": [], "info": []})

    def test_exact_slug_with_country(self):
        row = self.result({"slug": " alpha ", "country_code": " fr "})
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["match_reasons"], ["exact_slug", "same_country"])

    def test_normalized_name_and_geography(self):
        Resort.update(name="Étoile du Nord").execute()
        row = self.result({"name": " etoile DU nord ", "country_code": "FR", "region_id": "region-a"})
        self.assertEqual(row["matched_station"]["id"], "a")
        self.assertIn("exact_normalized_name", row["match_reasons"])
        self.assertIn("same_region", row["match_reasons"])
        # Matching folds accents; the actual spelling difference is still a diff.
        self.assertEqual(row["status"], "changes_detected")

    def test_coordinates_confirm_name_even_without_country(self):
        row = self.result({"name": "alpha", "latitude": 45.20001, "longitude": 6.30001})
        self.assertEqual(row["matched_station"]["id"], "a")
        self.assertIn("coordinates_nearby", row["match_reasons"])
        self.assertLess(row["match_distance_m"], 150)

    def test_coordinates_alone_never_resolve_identity(self):
        row = self.result({"name": "Other unknown name", "latitude": 45.2, "longitude": 6.3})
        self.assertEqual(row["status"], "review_required")
        self.assertEqual(row["review_items"][0]["code"], "coordinates_only_match")
        self.assertIsNone(row["matched_station"])

    def test_150m_is_confirmation_not_a_universal_matching_cutoff(self):
        data = {"name": "Alpha", "country_code": "FR", "region_id": "region-a",
                "latitude": 45.25, "longitude": 6.3}
        row = self.result(data)
        self.assertEqual(row["matched_station"]["id"], "a")
        self.assertNotIn("coordinates_nearby", row["match_reasons"])
        row = self.result({**data, "latitude": 45.7})
        self.assertEqual(row["status"], "review_required")
        self.assertIn("coordinates_far_apart", row["review_items"][0]["candidates"][0]["conflicting_fields"])

    def test_slug_without_geography_requires_review(self):
        row = self.result({"slug": "alpha"})
        self.assertEqual(row["status"], "review_required")
        self.assertEqual(row["review_items"][0]["code"], "insufficient_matching_evidence")

    def test_slug_with_conflicting_country_requires_review(self):
        row = self.result({"slug": "alpha", "country_code": "CA"})
        self.assertEqual(row["status"], "review_required")
        self.assertIn("country_code", row["review_items"][0]["candidates"][0]["conflicting_fields"])

    def test_multiple_plausible_matches_require_review(self):
        Resort.create(id="b", name="Alpha", slug="second-alpha", country_code="FR", region_id="region-a")
        row = self.result({"name": "alpha", "country_code": "FR", "region_id": "region-a"})
        self.assertEqual(row["status"], "review_required")
        self.assertIsNone(row["matched_station"])
        self.assertEqual(row["changes"], [])
        self.assertEqual([r["id"] for r in row["review_items"][0]["candidates"]], ["a", "b"])
        self.assertIn("multiple_matches", row["match_reasons"])

    def test_geography_separates_homonyms(self):
        Resort.create(id="b", name="Alpha", slug="other-alpha", country_code="CA", region_id="other")
        row = self.result({"name": "Alpha", "country_code": "FR", "region_id": "region-a"})
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["matched_station"]["id"], "a")

    def test_conflicting_exact_identifiers_require_review(self):
        Resort.create(id="b", name="Beta", slug="beta")
        row = self.result({"id": "a", "slug": "beta"})
        self.assertEqual(row["status"], "review_required")
        self.assertEqual(row["review_items"][0]["code"], "conflicting_station_identifiers")

    def test_unknown_id_does_not_replace_existing_identity_via_slug(self):
        row = self.result({"id": "not-a", "slug": "alpha", "country_code": "FR"})
        self.assertEqual(row["status"], "review_required")
        self.assertEqual(row["changes"], [])

    def test_new_candidate_has_no_match(self):
        row = self.result({"name": "New station", "country_code": "CA"})
        self.assertEqual(row["status"], "new")
        self.assertIsNone(row["matched_station"])
        self.assertTrue(all(change["change"] == "added" for change in row["changes"]))

    def test_equivalent_scalar_values_generate_no_diff(self):
        row = self.result({"id": "a", "name": "ALPHA", "ski_area_km": "20.00", "is_active": "TRUE",
                           "website_url": "HTTPS://EXAMPLE.COM:443/", "season_open_date": "20261201"})
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["changes"], [])

    def test_numeric_diff_preserves_received_and_normalized_values(self):
        row = self.result({"id": "a", "altitude_max_m": "2550", "ski_area_km": "20.75"})
        self.assertEqual(row["status"], "changes_detected")
        diff = {r["field"]: r for r in row["changes"]}
        self.assertEqual(diff["altitude_max_m"], {"field": "altitude_max_m", "existing": 2200, "candidate": "2550",
                         "normalized_existing": "2200", "normalized_candidate": "2550", "change": "modified"})
        self.assertEqual(diff["ski_area_km"]["normalized_candidate"], "20.75")

    def test_url_date_and_boolean_diffs(self):
        row = self.result({"id": "a", "website_url": "https://other.example/path", "is_active": "false",
                           "season_close_date": "2027-05-01"})
        self.assertEqual({change["field"] for change in row["changes"]}, {"website_url", "is_active", "season_close_date"})
        self.assertEqual(next(change for change in row["changes"] if change["field"] == "is_active")["normalized_candidate"], False)

    def test_absent_fields_and_collections_generate_no_deletions(self):
        with patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
            row = self.result({"id": "a"})
        self.assertEqual(row["changes"], [])
        self.assertEqual(row["review_items"], [])
        selects = [call.args[0] for call in sql.call_args_list if call.args[0].startswith("SELECT")]
        self.assertFalse(any('FROM "ski_areas"' in query or 'FROM "ski_area_resorts"' in query for query in selects))
        self.assertFalse(any("description_html" in query or "website_url" in query for query in selects))

    def test_null_does_not_clear_scalars_or_collections(self):
        row = self.result({"id": "a", "website_url": None, "latitude": None, "ski_areas": None, "widgets": None})
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["changes"], [])
        self.assertEqual(len(row["validation"]["info"]), 4)
        self.assertEqual(Resort.get_by_id("a").website_url, "https://example.com")

    def test_blank_optional_values_are_no_information_not_deletion(self):
        row = self.result({"id": "a", "website_url": " ", "description_html": "", "meta_title": ""})
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["changes"], [])
        self.assertEqual(len(row["validation"]["info"]), 3)

    def test_clear_fields_only_represents_intent(self):
        row = self.result({"id": "a", "website_url": None}, clear_fields=["website_url"])
        self.assertEqual(row["status"], "changes_detected")
        self.assertEqual(row["changes"][0]["change"], "cleared")
        self.assertIsNone(row["changes"][0]["candidate"])
        self.assertEqual(Resort.get_by_id("a").website_url, "https://example.com")
        self.assertEqual(self.result({"id": "a"}, clear_fields=["description_md"])["status"], "unchanged")

    def test_conflicting_clear_and_required_clear_are_invalid(self):
        for data, clear in (({"id": "a", "website_url": "https://other.example"}, ["website_url"]),
                            ({"id": "a"}, ["id"]), ({"id": "a"}, ["ski_areas"]), ({"id": "a"}, "website_url")):
            with self.subTest(clear=clear):
                self.assertEqual(self.result(data, clear_fields=clear)["status"], "invalid")

    def test_multiple_domains_are_diffed_as_membership_sets(self):
        second = SkiArea.create(name="Second", slug="second")
        third = SkiArea.create(name="Third", slug="third")
        SkiAreaResort.create(resort=self.station, ski_area=second)
        row = self.result({"id": "a", "ski_areas": [{"slug": "linked-area"}, {"id": third.id}]})
        change = row["changes"][0]
        self.assertEqual(change["relations"], {"added": [third.id], "removed": [second.id], "unchanged": [self.area.id]})
        self.assertEqual(row["status"], "changes_detected")
        self.assertEqual(SkiAreaResort.select().count(), 2)
        row = self.result({"id": "a", "ski_areas": [{"id": second.id}, {"slug": "linked-area"}]})
        self.assertEqual(row["status"], "unchanged")

    def test_explicit_empty_domain_list_is_an_intent_not_a_write(self):
        row = self.result({"id": "a", "ski_areas": []})
        self.assertEqual(row["changes"][0]["relations"]["removed"], [self.area.id])
        self.assertEqual(SkiAreaResort.select().count(), 1)

    def test_domain_name_alone_and_unknown_domain_require_review(self):
        second = SkiArea.create(name="Linked area", slug="other")
        row = self.result({"id": "a", "ski_areas": [{"name": "Linked area"}]})
        self.assertEqual(row["status"], "review_required")
        self.assertEqual([a["id"] for a in row["review_items"][0]["candidates"]], [self.area.id, second.id])
        self.assertEqual(row["changes"], [])
        self.assertEqual(self.result({"id": "a", "ski_areas": [{"slug": "unknown"}]})["status"], "review_required")
        self.assertEqual(SkiArea.select().count(), 2)

    def test_supplied_collections_require_review_without_destructive_diff(self):
        for field in ("pistes", "lifts", "maps", "webcams", "ski_pass_seasons", "ski_pass_periods",
                      "ski_pass_products", "ski_pass_prices", "widgets"):
            with self.subTest(field=field):
                row = self.result({"id": "a", field: {} if field == "widgets" else []})
                self.assertEqual(row["status"], "review_required")
                self.assertEqual(row["changes"], [])
                self.assertEqual(row["review_items"], [{"code": "collection_comparison_not_supported", "field": field}])

    def test_provenance_is_validated_and_preserved_without_fetching(self):
        sources = {"altitude_max_m": [{"url": "https://official.example/page", "source_type": "official",
                                      "observed_at": "2026-10-07T12:00:00Z"}]}
        row = self.result({"id": "a", "altitude_max_m": 2550}, field_sources=sources)
        self.assertEqual(row["field_sources"], sources)
        for invalid in ([], {"name": "url"}, {"name": [{"url": "file:///etc/passwd"}]},
                        {"name": [{"url": "https://example.com", "observed_at": "2026-10-07"}]}):
            with self.subTest(sources=invalid):
                self.assertEqual(self.result({"id": "a"}, field_sources=invalid)["status"], "invalid")

    def test_invalid_candidates_are_isolated_and_optional_missing_is_allowed(self):
        for data in ({}, {"latitude": 45, "longitude": 6}, {"id": "a", "latitude": 91},
                     {"id": "a", "longitude": -181}, {"id": "a", "is_active": "maybe"},
                     {"id": "a", "altitude_max_m": []}, {"id": "a", "ski_area_km": True},
                     {"id": "a", "season_close_date": "invalid"}, {"id": "a", "website_url": "relative/path"},
                     {"id": "a", "unknown": 12}, {"id": "a", "ski_area_km": "1e999999"},
                     {"id": "a", "description_html": "\ud800"}, {"id": "a", "pistes": [False]}):
            with self.subTest(data=data):
                self.assertEqual(self.result(data)["status"], "invalid")
        self.assertEqual(self.result({"name": "No optional fields"})["status"], "new")

    def test_inconsistent_business_values_are_warnings_not_invalid(self):
        row = self.result({"id": "a", "altitude_min_m": 3000, "altitude_max_m": 1000, "pistes_count": -1})
        self.assertEqual(row["status"], "changes_detected")
        self.assertEqual({f["code"] for f in row["validation"]["warnings"]}, {"altitude_inconsistent", "negative_count"})

    def test_mixed_batch_summary_contains_every_status(self):
        candidates = [{"client_ref": str(i), "data": data} for i, data in enumerate((
            {"name": "New"}, {"id": "a"}, {"id": "a", "ski_area_km": 21}, {"slug": "alpha"}, {}))]
        body = self.batch(candidates)
        self.assertEqual(body["summary"], {"total_candidates": 5, "new": 1, "unchanged": 1,
                                          "changes_detected": 1, "review_required": 1, "invalid": 1})
        self.assertEqual([r["client_ref"] for r in body["results"]], [str(i) for i in range(5)])
        self.assertEqual(body["compare_version"], "1.0")

    def test_editorial_hashes_do_not_return_or_select_full_existing_content(self):
        self.assertEqual(self.result({"id": "a", "description_html": "<p>Actual fixture</p>"})["status"], "unchanged")
        content = "<p>" + "x" * 100000 + "</p>"
        with patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
            row = self.result({"id": "a", "description_html": content})
        change = row["changes"][0]
        self.assertEqual(change["candidate"], {"length": len(content), "md5": hashlib.md5(content.encode()).hexdigest()})
        self.assertEqual(change["existing"]["length"], 21)
        self.assertLess(len(json.dumps(row)), 4000)
        content_queries = [call.args[0] for call in sql.call_args_list if call.args[0].startswith("SELECT") and "description_html" in call.args[0]]
        self.assertEqual(len(content_queries), 1)
        self.assertIn("MD5(", content_queries[0])
        self.assertNotIn('SELECT "t1"."description_html"', content_queries[0])

    def test_authentication_and_csrf_are_required_without_session_touch(self):
        payload = {"candidates": [{"client_ref": "x", "data": {"id": "a"}}]}
        before = AdminSession.get_by_id(self.session.id).last_seen_at
        self.assertEqual(self.client.post(URL, json=payload).status_code, 403)
        self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, before)
        self.client.delete_cookie("admin_session")
        self.assertEqual(self.client.post(URL, json=payload, headers={"X-CSRF-Token": "test-csrf"}).status_code, 401)
        self.client.set_cookie("admin_session", "invalid")
        self.assertEqual(self.client.post(URL, json=payload).status_code, 401)
        self.client.set_cookie("admin_session", "test-token")
        AdminSession.update(expires_at=utcnow() - timedelta(seconds=1)).execute()
        self.assertEqual(self.client.post(URL, json=payload, headers={"X-CSRF-Token": "test-csrf"}).status_code, 401)
        AdminSession.update(expires_at=utcnow() + timedelta(hours=1), revoked_at=utcnow()).execute()
        self.assertEqual(self.client.post(URL, json=payload, headers={"X-CSRF-Token": "test-csrf"}).status_code, 401)
        AdminSession.update(revoked_at=None).execute()
        AdminUser.update(role="viewer").execute()
        self.assertEqual(self.client.post(URL, json=payload, headers={"X-CSRF-Token": "test-csrf"}).status_code, 401)

    def test_compare_and_auth_never_write_even_for_clear_and_domain_removals(self):
        before = {model: list(model.select().dicts()) for model in MODELS}
        original = self.database.execute_sql
        def observe(sql, params=None, *args, **kwargs):
            self.assertIn(sql.split()[0].upper(), {"SELECT", "BEGIN", "PRAGMA"}, sql)
            return original(sql, params, *args, **kwargs)
        with patch.object(self.database, "execute_sql", side_effect=observe):
            self.result({"id": "a", "ski_areas": [], "is_active": False}, clear_fields=["website_url"])
        self.assertEqual(before, {model: list(model.select().dicts()) for model in MODELS})

    def test_read_only_guard_blocks_an_accidental_write_and_restores_connection(self):
        with patch("app.services.station_ops.compare._compare", side_effect=lambda *args: Resort.update(name="forbidden").execute()):
            with self.assertLogs(self.app.logger, level="ERROR"):
                self.batch([{"client_ref": "x", "data": {"id": "a"}}], expected=500)
        self.assertTrue(self.database.is_closed())
        self.assertEqual(Resort.get_by_id("a").name, "  Alpha  ")
        self.assertEqual(self.database.execute_sql("PRAGMA query_only").fetchone()[0], 0)

    def test_wrong_methods_do_not_touch_session(self):
        before = AdminSession.get_by_id(self.session.id).last_seen_at
        for method in ("GET", "HEAD", "PATCH", "PUT", "DELETE"):
            response = self.client.open(URL, method=method, headers={"X-CSRF-Token": "test-csrf"})
            self.assertEqual(response.status_code, 405)
        self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, before)

    def test_malformed_envelope_and_body_limits(self):
        headers = {"X-CSRF-Token": "test-csrf"}
        for payload in ({}, {"candidates": []}, {"candidates": {}}, {"candidates": [], "apply": True}):
            self.assertEqual(self.client.post(URL, json=payload, headers=headers).status_code, 400)
        for body in ('{"candidates":[],"candidates":[]}', '{', '{"candidates":[NaN]}', '{"candidates":[1e999]}'):
            self.assertEqual(self.client.post(URL, data=body, content_type="application/json", headers=headers).status_code, 400)
        self.assertEqual(self.client.post(URL, data="text", headers=headers).status_code, 415)
        with patch("app.routes.admin_station_ops.MAX_BODY_BYTES", 128):
            self.assertEqual(self.client.post(URL, data="x" * 129, content_type="application/json", headers=headers).status_code, 413)
        body = self.batch([{"client_ref": "x", "data": {"id": "a"}}] * 1001, expected=413)
        self.assertEqual(body["error"], "invalid_compare_payload")
        self.assertEqual(self.batch([None, {}, {"client_ref": "x", "data": []}])["summary"]["invalid"], 3)

    def test_batch_500_and_1000_share_queries_and_only_read_requested_fields(self):
        for i in range(120):
            Resort.create(id=f"extra-{i}", name=f"Extra {i}", slug=f"extra-{i}", website_url="https://example.com")
        def run(size):
            candidates = [{"client_ref": str(i), "data": {"id": "a" if i == 0 else f"extra-{i % 120}",
                          "website_url": "https://example.com", "ski_areas": []}} for i in range(size)]
            with patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
                body = self.batch(candidates)
            selects = [call.args[0] for call in sql.call_args_list if call.args[0].startswith("SELECT")]
            self.assertFalse(any("description_html" in query or 'FROM "piste"' in query for query in selects))
            return len(selects), body
        small, _ = run(1)
        count, body = run(500)
        self.assertEqual(count, small)
        self.assertEqual(count, 6)  # SQLite: auth, identity, region existence, details, areas, links.
        self.assertEqual(body["summary"]["total_candidates"], 500)
        count_max, body_max = run(1000)
        self.assertEqual(count_max, count)
        self.assertEqual(body_max["summary"]["total_candidates"], 1000)


class LegacyCompareTests(unittest.TestCase):
    legacy_regions = True
    real_km_fixture = True
    setUp = scan_fixtures.StationOpsTests.setUp
    batch = CompareTests.batch
    result = CompareTests.result

    def test_legacy_empty_region_and_physical_fractional_kilometres(self):
        self.database.execute_sql('DELETE FROM regions')
        self.database.execute_sql('UPDATE resort SET ski_area_km=? WHERE id=?', (45.75, "a"))
        with patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
            body = self.batch([{"client_ref": "x", "data": {"id": "a", "ski_area_km": "45.750"}}])
        self.assertEqual(body["results"][0]["status"], "unchanged")
        self.assertEqual(body["catalog_findings"], [{"code": "region_catalog_empty", "severity": "info", "table": "regions"}])
        codes = {(f["table"], f.get("field"), f["code"]) for f in body["schema_findings"]}
        self.assertIn(("regions", "description_html", "model_column_missing_in_database"), codes)
        self.assertIn(("regions", "seo_text", "database_column_not_in_model"), codes)
        self.assertIn(("resort", "ski_area_km", "model_column_type_mismatch"), codes)
        region_reads = [call.args[0] for call in sql.call_args_list if call.args[0].startswith("SELECT") and '"regions"' in call.args[0]]
        self.assertTrue(all("description_html" not in query and "seo_text" not in query for query in region_reads))

    def test_missing_optional_column_returns_review_not_false_diff(self):
        original = self.database.get_columns
        def columns(table, *args, **kwargs):
            return [column for column in original(table, *args, **kwargs) if not (table == "resort" and column.name == "meta_title")]
        with patch.object(self.database, "get_columns", side_effect=columns):
            row = self.result({"id": "a", "meta_title": "Provided title"})
        self.assertEqual(row["status"], "review_required")
        self.assertEqual(row["changes"], [])
        self.assertEqual(row["review_items"], [{"code": "field_unavailable_in_database", "field": "meta_title"}])

    def test_missing_matching_column_does_not_claim_new_station(self):
        original = self.database.get_columns
        def columns(table, *args, **kwargs):
            return [column for column in original(table, *args, **kwargs) if not (table == "resort" and column.name == "name")]
        with patch.object(self.database, "get_columns", side_effect=columns):
            row = self.result({"name": "Alpha", "country_code": "FR"})
        self.assertEqual(row["status"], "review_required")
        self.assertEqual(row["changes"], [])
        self.assertEqual(row["review_items"], [{"code": "matching_columns_unavailable", "fields": ["name"]}])

    def test_missing_indispensable_id_returns_structured_error_before_data_reads(self):
        original = self.database.get_columns
        def columns(table, *args, **kwargs):
            return [column for column in original(table, *args, **kwargs) if not (table == "resort" and column.name == "id")]
        with patch.object(self.database, "get_columns", side_effect=columns), \
                patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
            body = self.batch([{"client_ref": "x", "data": {"id": "a"}}], expected=503)
        self.assertEqual(body["error"], "station_ops_schema_incompatible")
        self.assertTrue(any(f.get("field") == "id" for f in body["schema_findings"]))
        self.assertFalse(any(call.args[0].startswith("SELECT") and 'FROM "resort"' in call.args[0] for call in sql.call_args_list))

    def test_domain_id_comparison_survives_missing_optional_slug_column(self):
        original = self.database.get_columns
        def columns(table, *args, **kwargs):
            return [column for column in original(table, *args, **kwargs) if not (table == "ski_areas" and column.name == "slug")]
        with patch.object(self.database, "get_columns", side_effect=columns):
            self.assertEqual(self.result({"id": "a", "ski_areas": [{"id": self.area.id}]})["status"], "unchanged")
            self.assertEqual(self.result({"id": "a", "ski_areas": [{"slug": "linked-area"}]})["status"], "review_required")


class PureCompareTests(unittest.TestCase):
    def test_postgresql_read_only_transaction_precedes_all_compare_reads(self):
        database = PostgresqlDatabase("never-connected")
        with patch.object(Resort._meta, "database", database), \
                patch.object(database, "atomic", return_value=MagicMock()) as atomic, \
                patch.object(database, "execute_sql") as sql:
            def inspect(prepared, active_database):
                self.assertIs(active_database, database)
                sql.assert_called_once_with("SET TRANSACTION READ ONLY")
                return [], []
            with patch("app.services.station_ops.compare._compare", side_effect=inspect):
                compare_candidates({"candidates": [{"client_ref": "x", "data": {"id": "a"}}]})
            atomic.assert_called_once_with(isolation_level="REPEATABLE READ")
        self.assertTrue(database.is_closed())

    def test_url_normalization_preserves_path_query_fragment_and_port_zero(self):
        self.assertEqual(normalized_url("HTTPS://EXAMPLE.COM:443"), "https://example.com/")
        self.assertNotEqual(normalized_url("https://example.com/Path/?b=2&a=1#x"),
                            normalized_url("https://example.com/path/?a=1&b=2"))
        self.assertEqual(normalized_url("http://example.com:0"), "http://example.com:0/")

    def test_spatial_confirmation_handles_antimeridian_and_poles(self):
        for lat, lon, other in ((0, 179.9998, -179.9998), (89.9999, 0, 180)):
            index = MatchingIndex([{"id": "a", "slug": "alpha", "name": "Alpha", "latitude": lat, "longitude": lon}])
            decision = index.match({"name": "alpha", "latitude": lat, "longitude": other})
            self.assertEqual(decision["station"]["id"], "a")
            self.assertIn("coordinates_nearby", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
