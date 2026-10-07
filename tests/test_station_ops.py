"""Isolated in-memory fixtures; never create or connect to PostgreSQL."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

from peewee import OperationalError, PostgresqlDatabase
from playhouse.pool import PooledSqliteDatabase

import app as app_module
from app.datetime_utils import utcnow
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.models.lift import Lift
from app.models.piste import Piste
from app.models.region import Region
from app.models.resort import Resort
from app.models.resort_map import ResortMap
from app.models.ski_area import SkiArea, SkiAreaResort
from app.models.ski_pass import SkiPassSeason, SkiPassPeriod, SkiPassProduct, SkiPassPrice
from app.models.station_widgets import StationWidgets
from app.services.admin_auth import _digest
from app.services.station_ops.duplicates import potential_duplicates
from app.services.station_ops.scan import read_only_scan, scan_stations
from app.services.station_ops.validation import validate_station

URL = "/api/admin/station-ops/snapshot"
MODELS = [AdminUser, AdminSession, Region, Resort, Piste, Lift, ResortMap,
          StationWidgets, SkiArea, SkiAreaResort,
          SkiPassSeason, SkiPassPeriod, SkiPassProduct, SkiPassPrice]


class StationOpsTests(unittest.TestCase):
    def setUp(self):
        self.production_guard = patch.object(app_module.db, "connect", side_effect=AssertionError("Production DB forbidden"))
        self.production_guard.start()
        self.addCleanup(self.production_guard.stop)
        self.database = PooledSqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.database.register_function(lambda v: hashlib.md5(v.encode()).hexdigest() if v is not None else None, "MD5", 1)
        self.binding = self.database.bind_ctx(MODELS, bind_refs=False, bind_backrefs=False)
        self.binding.__enter__()
        self.addCleanup(self.binding.__exit__, None, None, None)
        self.addCleanup(self.database.close_all)
        self.database.create_tables(MODELS)
        self.app_db_patch = patch.object(app_module, "db", self.database)
        self.app_db_patch.start()
        self.addCleanup(self.app_db_patch.stop)
        self.app = app_module.create_app({
            "TESTING": True, "SKIP_DATABASE_INIT": True, "PUBLIC_CACHE_ENABLED": False,
            "ADMIN_SESSION_SECRET": "s" * 64, "ADMIN_SESSION_COOKIE_NAME": "admin_session",
        })
        self.client = self.app.test_client()
        now = utcnow()
        self.user = AdminUser.create(email="admin@example.com", password_hash="unused-test-hash",
                                     password_changed_at=now - timedelta(days=1))
        with self.app.test_request_context():
            self.session = AdminSession.create(
                admin_user=self.user, token_hash=_digest("test-token", b"session:"),
                csrf_token_hash=_digest("test-csrf", b"csrf-hash:"),
                created_at=now, expires_at=now + timedelta(hours=2),
                last_seen_at=now - timedelta(hours=1),
            )
        self.client.set_cookie("admin_session", "test-token")
        Region.create(id="region-a", name="Region A", country_code="FR")
        self.station = Resort.create(
            id="a", name="  Alpha  ", slug="alpha", country_code="FR", region_id="region-a",
            region_name="Stored region label", department="73", latitude=45.2, longitude=6.3,
            altitude_base_m=1100, altitude_top_m=2100, altitude_min_m=1000, altitude_max_m=2200,
            pistes_count=4, lifts_count=1, ski_area_km=20, website_url="https://example.com",
            cover_image_url="https://example.com/cover.webp", logo_url="https://example.com/logo.webp",
            pistes_large_map_url="https://example.com/map.pdf", season_open_date=date(2026, 12, 1),
            season_close_date=date(2027, 4, 1), description_html="<p>Actual fixture</p>",
        )
        self.area = SkiArea.create(name="Linked area", slug="linked-area", status="draft", pistes_count=4,
                                   green_pistes_count=1, blue_pistes_count=1, red_pistes_count=1,
                                   black_pistes_count=1, snowparks_count=0)
        SkiAreaResort.create(resort=self.station, ski_area=self.area)
        StationWidgets.create(station_slug="alpha", config=json.dumps({
            "webcams": {"enabled": False, "items": [{"url": "https://example.com/camera"}]},
            "snowparks": {"count": 0}, "description": {"html": "<p>Widget HTML</p>"},
        }))
        for colour in ("green", "blue", "red", "black"):
            Piste.create(id=colour, resort=self.station, name=colour, difficulty=colour)
        Lift.create(id="lift", resort=self.station, name="Lift", type="chair")

    def body(self, query=""):
        response = self.client.get(URL + query)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response.get_json()

    def test_complete_station_and_raw_values_are_preserved(self):
        body = self.body()
        row = body["stations"][0]
        self.assertEqual(row["name"], "  Alpha  ")
        self.assertEqual(row["region_name"], "Stored region label")
        self.assertEqual(row["region"]["name"], "Region A")
        self.assertEqual(row["altitude_min_m"], 1000)
        self.assertEqual(row["altitude_base_m"], 1100)
        self.assertEqual(row["widgets"]["snowparks"]["count"], 0)
        self.assertFalse(row["widgets"]["webcams"]["enabled"])
        self.assertEqual(row["pistes"]["counts_by_difficulty"], dict.fromkeys(("green", "blue", "red", "black"), 1))
        self.assertEqual(row["lifts"]["row_count"], 1)
        self.assertEqual(row["ski_areas"][0]["status"], "draft")
        self.assertEqual(row["findings"], [])
        self.assertEqual(body["summary"]["total_stations"], 1)
        self.assertNotIn("description_html", row)
        self.assertEqual(row["content"]["description_html"]["md5"], hashlib.md5(b"<p>Actual fixture</p>").hexdigest())
        self.assertTrue(row["content"]["description_html"]["present"])
        self.assertEqual(row["content"]["description_html"]["length"], 21)
        self.assertNotIn("<p>Widget HTML</p>", json.dumps(row))

    def test_inactive_and_missing_data_remain_in_snapshot(self):
        Resort.create(id="b", name="", slug="", is_active=False)
        body = self.body()
        self.assertEqual(body["summary"]["total_stations"], 2)
        self.assertEqual(body["summary"]["inactive_stations"], 1)
        self.assertEqual(body["summary"]["stations_with_errors"], 1)
        row = body["stations"][1]
        self.assertEqual(row["slug"], "")
        codes = {f["code"]: f["severity"] for f in row["findings"]}
        self.assertEqual(codes["missing_name"], "error")
        self.assertEqual(codes["missing_slug"], "error")
        self.assertEqual(codes["missing_coordinates"], "warning")
        self.assertEqual(codes["missing_logo"], "info")
        self.assertEqual(codes["missing_ski_area"], "info")

    def test_filters_all_supported_fields_and_combination(self):
        Resort.create(id="b", name="Beta", slug="beta", country_code="IT", region_id="other",
                      department="AO", is_active=False)
        for query in ("id=a", "slug=alpha", "country_code=FR", "region_id=region-a", "department=73",
                      "is_active=true", f"ski_area_id={self.area.id}",
                      "country_code=FR&region_id=region-a&department=73&is_active=true&slug=alpha&id=a"):
            with self.subTest(query=query):
                body = self.body("?" + query)
                self.assertEqual([r["id"] for r in body["stations"]], ["a"])
                self.assertEqual(body["summary"]["total_stations"], 1)
        self.assertEqual(self.body("?is_active=false")["stations"][0]["id"], "b")
        self.assertEqual(self.body("?id=absent")["summary"]["total_stations"], 0)

    def test_invalid_and_unsupported_filters_are_rejected(self):
        for query in ("is_active=maybe", "active=true", "ski_area_id=-1", "ski_area_id=abc",
                      "ski_area_id=9223372036854775808", "slug=", "id=a&id=b"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(URL + "?" + query).status_code, 400)

    def test_filter_injection_is_a_literal_value(self):
        self.assertEqual(self.body("?slug=alpha%27%20OR%201%3D1--")["stations"], [])

    def test_authentication_absent_invalid_expired_revoked_and_nonadmin(self):
        self.client.delete_cookie("admin_session")
        self.assertEqual(self.client.get(URL).status_code, 401)
        self.assertEqual(self.client.get(URL, headers={"Authorization": "Bearer invented-jwt"}).status_code, 401)
        self.client.set_cookie("admin_session", "invalid")
        self.assertEqual(self.client.get(URL).status_code, 401)
        self.client.set_cookie("admin_session", "test-token")
        now = utcnow()
        for changes in ({"expires_at": now - timedelta(seconds=1)}, {"revoked_at": now}):
            AdminSession.update(**changes).where(AdminSession.id == self.session.id).execute()
            self.assertEqual(self.client.get(URL).status_code, 401)
            AdminSession.update(expires_at=now + timedelta(hours=1), revoked_at=None).execute()
        for changes in ({"role": "viewer"}, {"is_active": False}, {"password_changed_at": now + timedelta(seconds=1)}):
            AdminUser.update(**changes).execute()
            self.assertEqual(self.client.get(URL).status_code, 401)
            AdminUser.update(role="admin", is_active=True, password_changed_at=now - timedelta(days=1)).execute()

    def test_auth_and_scan_execute_no_writes_even_with_old_session(self):
        before = {model: list(model.select().dicts()) for model in MODELS}
        original = self.database.execute_sql
        statements = []
        def observe(sql, params=None, *args, **kwargs):
            statements.append(sql)
            self.assertIn(sql.split()[0].upper(), {"SELECT", "BEGIN", "PRAGMA"}, sql)
            return original(sql, params, *args, **kwargs)
        with patch.object(self.database, "execute_sql", side_effect=observe):
            self.body()
        self.assertTrue(statements)
        after = {model: list(model.select().dicts()) for model in MODELS}
        self.assertEqual(before, after)
        self.assertFalse(self.database.in_transaction())
        self.assertEqual(self.database.execute_sql("PRAGMA query_only").fetchone()[0], 0)

    def test_other_admin_routes_keep_session_touch_behaviour(self):
        before = AdminSession.get_by_id(self.session.id).last_seen_at
        self.assertEqual(self.client.get("/api/admin/auth/session").status_code, 200)
        self.assertGreater(AdminSession.get_by_id(self.session.id).last_seen_at, before)

    def test_methods_are_get_head_options_only(self):
        self.assertEqual(self.client.head(URL).status_code, 200)
        self.client.delete_cookie("admin_session")
        self.assertEqual(self.client.head(URL).status_code, 401)
        self.assertEqual(self.client.options(URL).status_code, 200)
        self.client.set_cookie("admin_session", "test-token")
        last_seen = AdminSession.get_by_id(self.session.id).last_seen_at
        for method in ("POST", "PATCH", "PUT", "DELETE"):
            self.assertEqual(self.client.open(URL, method=method, headers={"X-CSRF-Token": "test-csrf"}).status_code, 405)
            self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, last_seen)

    def test_missing_altitude_and_public_fallback_does_not_fill_raw_fields(self):
        Resort.update(altitude_min_m=None, altitude_max_m=None).execute()
        row = self.body()["stations"][0]
        self.assertIsNone(row["altitude_min_m"])
        self.assertNotIn("missing_altitude", [f["code"] for f in row["findings"]])
        Resort.update(altitude_base_m=None).execute()
        self.assertIn("missing_altitude", [f["code"] for f in self.body()["stations"][0]["findings"]])

    def test_altitude_inconsistency_and_dates(self):
        Resort.update(altitude_min_m=3000, altitude_max_m=2000,
                      season_open_date=date(2027, 4, 1), season_close_date=date(2026, 12, 1)).execute()
        codes = {f["code"]: f["severity"] for f in self.body()["stations"][0]["findings"]}
        self.assertEqual(codes["altitude_inconsistent"], "error")
        self.assertEqual(codes["season_dates_inconsistent"], "error")

    def test_ski_area_piste_total_is_validated_without_using_partial_piste_rows(self):
        SkiArea.update(pistes_count=99).execute()
        row = self.body()["stations"][0]
        self.assertIn("piste_total_inconsistent", [f["code"] for f in row["findings"]])
        self.assertEqual(row["pistes_count"], 4)
        SkiArea.update(pistes_count=4).execute()
        Resort.update(pistes_count=99).execute()
        self.assertNotIn("piste_total_inconsistent", [f["code"] for f in self.body()["stations"][0]["findings"]])

    def test_widgets_malformed_missing_zero_and_large_content(self):
        StationWidgets.update(config="not json").execute()
        self.assertEqual(self.body()["stations"][0]["widgets_state"], "invalid_json")
        StationWidgets.update(config=json.dumps({"widgets": {"cfg": {"snowparks": {"count": 0},
                               "pistes": {"officialMapUrl": "https://example.com/official.pdf"},
                               "description": {"html": "<p>" + "x" * 100000 + "</p>"}}}, "private": "hidden"})).execute()
        body = self.body()
        self.assertEqual(body["stations"][0]["widgets"]["snowparks"]["count"], 0)
        self.assertNotIn("private", body["stations"][0]["widgets"])
        self.assertLess(len(json.dumps(body)), 20000)

    def test_inline_media_are_summarized_and_absent_html_remains_distinguishable(self):
        Resort.update(cover_image_url="data:image/png;base64," + "A" * 100000,
                      description_html="", description_md=None).execute()
        row = self.body()["stations"][0]
        self.assertTrue(row["cover_image_url"]["inline_media"])
        self.assertEqual(row["cover_image_url"]["length"], 100022)
        self.assertEqual(row["content"]["description_html"]["length"], 0)
        self.assertIsNone(row["content"]["description_md"]["length"])
        self.assertLess(len(json.dumps(row)), 10000)

    def test_multiple_ski_areas_are_preserved_without_station_stat_substitution(self):
        other = SkiArea.create(name="Other", slug="other", ski_area_km=999)
        SkiAreaResort.create(resort=self.station, ski_area=other)
        row = self.body()["stations"][0]
        self.assertEqual(len(row["ski_areas"]), 2)
        self.assertEqual(row["ski_area_km"], 20)

    def test_tariff_rows_include_inactive_seasons_and_exact_decimal_strings(self):
        season = SkiPassSeason.create(resort=self.station, season="2026-2027", is_active=False)
        period = SkiPassPeriod.create(season=season, external_id="period", name="Period",
                                     start_date=date(2026, 12, 1), end_date=date(2027, 4, 1))
        product = SkiPassProduct.create(season=season, external_id="day", name="Day", duration_label="1 day")
        SkiPassPrice.create(product=product, period=period, category="adult", category_label="Adult",
                            price_type="fixed", price="45.90")
        row = self.body()["stations"][0]
        self.assertTrue(row["has_ski_pass_data"])
        grid = row["ski_pass_seasons"][0]
        self.assertFalse(grid["is_active"])
        self.assertEqual(grid["products"][0]["prices"][0]["price"], "45.9")

    def test_query_count_does_not_grow_per_station(self):
        def count():
            with patch.object(self.database, "execute_sql", wraps=self.database.execute_sql) as sql:
                self.body()
                return sum(call.args[0].startswith("SELECT") for call in sql.call_args_list)
        initial = count()
        for i in range(20):
            Resort.create(id=f"extra-{i}", name=f"Extra {i}", slug=f"extra-{i}")
        self.assertEqual(count(), initial)

    def test_duplicate_candidates_report_and_filter_scope(self):
        Resort.create(id="b", name="Alpha", slug="another-alpha", latitude=45.20001, longitude=6.30001)
        body = self.body()
        self.assertEqual(body["summary"]["potential_duplicates"], 1)
        self.assertEqual(body["potential_duplicates"][0]["classification"], "potential_duplicates")
        self.assertEqual(body["potential_duplicates"][0]["reasons"], ["near_coordinates", "same_normalized_name"])
        self.assertEqual(self.body("?id=a")["potential_duplicates"], [])

    def test_service_read_only_guard_blocks_writes_and_restores_after_failure(self):
        with self.assertRaises(OperationalError):
            with read_only_scan(self.database):
                Resort.update(name="forbidden").execute()
        self.assertEqual(Resort.get_by_id("a").name, "  Alpha  ")
        self.assertEqual(self.database.execute_sql("PRAGMA query_only").fetchone()[0], 0)
        with self.database.atomic():
            with self.assertRaisesRegex(RuntimeError, "fresh read-only"):
                scan_stations()

    def test_scan_failure_closes_connection_and_returns_no_partial_snapshot(self):
        with patch("app.services.station_ops.scan._scan", side_effect=RuntimeError("failed")):
            with self.assertLogs(self.app.logger, level="ERROR"):
                response = self.client.get(URL)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "station_ops_scan_failed"})
        self.assertTrue(self.database.is_closed())


class PureStationOpsTests(unittest.TestCase):
    def test_same_slug_name_accents_and_similar_names_remain_potential(self):
        rows = [{"id": "a", "slug": "shared", "name": "Étoile"},
                {"id": "b", "slug": "shared", "name": "Etoile"}]
        self.assertEqual(potential_duplicates(rows)[0]["reasons"], ["same_normalized_name", "same_slug"])
        self.assertEqual(potential_duplicates([{"id": "a", "name": "Saint Martin"},
                                                {"id": "b", "name": "Saint Martine"}]), [])

    def test_coordinates_zero_dateline_and_invalid_coordinates(self):
        for longitude in (0, 179.9998):
            other = longitude + .0004 if longitude == 0 else -179.9998
            rows = [{"id": "a", "latitude": 0, "longitude": longitude},
                    {"id": "b", "latitude": 0, "longitude": other}]
            self.assertEqual(potential_duplicates(rows)[0]["reasons"], ["near_coordinates"])
        self.assertEqual(potential_duplicates([{"id": "a", "latitude": 999, "longitude": 0},
                                                {"id": "b", "latitude": 999, "longitude": 0}]), [])

    def test_validation_is_pure_and_optional_omissions_are_info(self):
        row = {"id": "a", "name": "Name", "slug": "name", "country_code": "FR",
               "latitude": 0, "longitude": 0, "altitude_base_m": 0, "altitude_top_m": 0,
               "pistes_count": 0, "lifts_count": 0, "ski_area_km": 0}
        original = deepcopy(row)
        self.assertTrue(all(item["severity"] == "info" for item in validate_station(row)))
        self.assertEqual(row, original)

    def test_postgres_transaction_is_repeatable_read_and_read_only_before_scan(self):
        database = PostgresqlDatabase("never-connected")
        with patch.object(database, "atomic", return_value=MagicMock()) as atomic, \
                patch.object(database, "execute_sql") as sql:
            with read_only_scan(database):
                sql.assert_called_once_with("SET TRANSACTION READ ONLY")
            atomic.assert_called_once_with(isolation_level="REPEATABLE READ")
        self.assertTrue(database.is_closed())


if __name__ == "__main__":
    unittest.main()
