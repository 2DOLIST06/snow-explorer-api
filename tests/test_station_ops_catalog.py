"""Compact catalogue reads use isolated fixtures, never production PostgreSQL."""
import unittest
from unittest.mock import MagicMock, patch

from peewee import PostgresqlDatabase

from app.mcp.tools import invoke, tool_definitions
from app.models.resort import Resort
from app.services.station_ops import catalog
from app.services.station_ops.candidates import ComparePayloadError
from app.services.station_ops.schema import SchemaCompatibilityError
import test_station_ops as fixtures


class CatalogTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = fixtures.StationOpsTests.setUp

    def seed(self, count):
        Resort.insert_many([{'id': f'fr-{i:04d}', 'slug': f'french-station-{i}',
                             'name': f'Station française {i}', 'country_code': 'FR',
                             'department': '74', 'latitude': 45.8, 'longitude': 6.6,
                             'website_url': f'https://station-{i}.example.com',
                             'altitude_min_m': 1200, 'altitude_max_m': 2800}
                            for i in range(count)]).execute()

    def test_68_plus_french_stations_paginate_in_stable_order(self):
        self.seed(72)
        Resort.create(id='foreign', slug='foreign', name='Foreign', country_code='CH')
        ids, offset = [], 0
        while True:
            page = catalog.catalog_stations({'country_code': 'FR', 'limit': 25, 'offset': offset})
            self.assertEqual(page['total'], 73)
            self.assertEqual(page['returned'], len(page['stations']))
            self.assertEqual(page['order_by'], 'id')
            ids.extend(row['id'] for row in page['stations'])
            if not page['has_more']:
                self.assertIsNone(page['next_offset'])
                break
            self.assertEqual(page['next_offset'], offset + page['returned'])
            offset = page['next_offset']
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 73)
        self.assertNotIn('foreign', ids)

    def test_default_and_maximum_page_limits(self):
        self.seed(270)
        page = catalog.catalog_stations()
        self.assertEqual((page['limit'], page['returned'], page['next_offset']), (100, 100, 100))
        page = catalog.catalog_stations({'limit': 250})
        self.assertEqual((page['returned'], page['total'], page['next_offset']), (250, 271, 250))

    def test_empty_and_out_of_range_pages(self):
        for args, total in (({'country_code': 'XX'}, 0), ({'offset': 100}, 1)):
            with self.subTest(args=args):
                page = catalog.catalog_stations(args)
                self.assertEqual(page['total'], total)
                self.assertEqual(page['returned'], 0)
                self.assertFalse(page['has_more'])
                self.assertIsNone(page['next_offset'])

    def test_all_geographic_filters_and_many_to_many(self):
        filters = {'country_code': 'FR', 'region_id': 'region-a', 'department': '73',
                   'is_active': 'true', 'ski_area_id': str(self.area.id)}
        self.assertEqual(catalog.catalog_stations(filters)['returned'], 1)
        for key, value in (('country_code', 'CH'), ('region_id', 'other'), ('department', '74'),
                           ('is_active', 'false'), ('ski_area_id', '999')):
            with self.subTest(key=key):
                self.assertEqual(catalog.catalog_stations({**filters, key: value})['returned'], 0)

    def test_invalid_arguments_refused_before_sql(self):
        invalid = [[], {'slug': 'alpha'}, {'limit': 0}, {'limit': 251}, {'limit': True},
                   {'limit': 1.5}, {'limit': '100'}, {'offset': -1}, {'offset': False},
                   {'offset': 1.5}, {'offset': 2**63}, {'country_code': ''},
                   {'is_active': True}, {'is_active': 'yes'}, {'ski_area_id': 'abc'}]
        with patch.object(self.database, 'execute_sql', side_effect=AssertionError('No SQL')):
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(ComparePayloadError):
                    catalog.catalog_stations(args)

    def test_compact_projection_media_flags_and_raw_identity(self):
        row = catalog.catalog_stations()['stations'][0]
        self.assertEqual(set(row), set(catalog.CATALOG_FIELDS) | set(catalog.MEDIA_FIELDS))
        self.assertEqual(row['name'], '  Alpha  ')
        self.assertEqual(row['region_name'], 'Stored region label')
        self.assertEqual(row['altitude_max_m'], 2200)
        self.assertEqual(row['website_url'], 'https://example.com')
        self.assertIsInstance(row['updated_at'], str)
        self.assertTrue(all(row[key] for key in catalog.MEDIA_FIELDS))
        Resort.update(cover_image_url='', logo_url=None, pistes_large_map_url='   ').execute()
        row = catalog.catalog_stations()['stations'][0]
        self.assertFalse(any(row[key] for key in catalog.MEDIA_FIELDS))

    def test_response_size_excludes_large_content_and_collections(self):
        self.seed(72)
        large = 'excluded-large-content-' * 10000
        Resort.update(description_html=large, cover_image_url='data:image/png;base64,' + large,
                      logo_url=large, pistes_large_map_url=large).execute()
        result = invoke(self.app, 'station_catalog', {'country_code': 'FR'})
        self.assertFalse(result.isError, result.structuredContent)
        encoded = result.model_dump_json().encode('utf-8')
        # Includes BOTH MCP text content and structuredContent, not only the service JSON.
        self.assertLess(len(encoded), 160 * 1024)
        self.assertNotIn(b'excluded-large-content', encoded)
        self.assertEqual(result.structuredContent['returned'], 73)

    def test_read_only_no_n_plus_one_and_session_unchanged(self):
        self.seed(150)
        before = {model: list(model.select().dicts()) for model in fixtures.MODELS}
        original = self.database.execute_sql
        queries = []
        def observe(sql, *args, **kwargs):
            self.assertIn(sql.split()[0].upper(), {'SELECT', 'BEGIN', 'PRAGMA'}, sql)
            queries.append(sql)
            return original(sql, *args, **kwargs)
        for limit in (1, 100, 250):
            queries.clear()
            with patch.object(self.database, 'execute_sql', side_effect=observe):
                catalog.catalog_stations({'country_code': 'FR', 'limit': limit})
            selects = [sql for sql in queries if sql.startswith('SELECT')]
            self.assertEqual(len(selects), 2, queries)
            self.assertNotIn('description_html', ' '.join(selects))
            self.assertNotIn('regions', ' '.join(selects))
            self.assertTrue(any('query_only = ON' in sql for sql in queries), queries)
        self.assertEqual(before, {model: list(model.select().dicts()) for model in fixtures.MODELS})

    def test_postgresql_repeatable_read_read_only_guard(self):
        database = PostgresqlDatabase('never-connect')
        with patch.object(Resort._meta, 'database', database), \
             patch.object(database, 'atomic', return_value=MagicMock()) as atomic, \
             patch.object(database, 'execute_sql') as execute, \
             patch.object(catalog, '_catalog', return_value={'stations': []}) as run:
            self.assertEqual(catalog.catalog_stations({'country_code': 'FR'}), {'stations': []})
        atomic.assert_called_once_with(isolation_level='REPEATABLE READ')
        execute.assert_called_once_with('SET TRANSACTION READ ONLY')
        run.assert_called_once_with({'country_code': 'FR'}, 100, 0)

    def test_missing_optional_column_is_null_with_schema_finding(self):
        self.database.execute_sql('ALTER TABLE resort DROP COLUMN logo_url')
        self.database.execute_sql('ALTER TABLE resort DROP COLUMN altitude_max_m')
        result = catalog.catalog_stations()
        self.assertIsNone(result['stations'][0]['has_logo'])
        self.assertIsNone(result['stations'][0]['altitude_max_m'])
        self.assertEqual({item['field'] for item in result['schema_findings']}, {'logo_url', 'altitude_max_m'})

    def test_missing_filter_column_refused_without_broadening_scope(self):
        self.database.execute_sql('ALTER TABLE resort DROP COLUMN department')
        with self.assertRaises(SchemaCompatibilityError):
            catalog.catalog_stations({'department': '73'})

    def test_mcp_schema_and_scope(self):
        tool = next(tool for tool in tool_definitions() if tool.name == 'station_catalog')
        data = tool.model_dump(by_alias=True)
        scheme = [{'type': 'oauth2', 'scopes': ['station-ops:read']}]
        self.assertEqual(data['securitySchemes'], scheme)
        self.assertEqual(data['_meta']['securitySchemes'], scheme)
        self.assertTrue(tool.annotations.readOnlyHint)
        with patch('app.mcp.tools.get_access_token', return_value=MagicMock(scopes=[], subject='test')), \
             patch.object(catalog, 'catalog_stations') as service:
            result = invoke(self.app, 'station_catalog', {})
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent['code'], 'insufficient_scope')
        service.assert_not_called()

    def test_mcp_invalid_pagination_is_structured(self):
        for args in ({'limit': 251}, {'offset': -1}, {'country_code': 'FR', 'unknown': 1}):
            result = invoke(self.app, 'station_catalog', args)
            self.assertTrue(result.isError)
            self.assertEqual(result.structuredContent['status'], 400)


class FractionalCatalogTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = True
    setUp = fixtures.StationOpsTests.setUp

    def test_physical_fractional_ski_area_km_not_truncated(self):
        self.database.execute_sql('UPDATE resort SET ski_area_km = 20.75')
        page = catalog.catalog_stations()
        self.assertEqual(page['stations'][0]['ski_area_km'], 20.75)
        self.assertTrue(any(item['code'] == 'model_column_type_mismatch' for item in page['schema_findings']))
