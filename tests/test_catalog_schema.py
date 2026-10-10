"""Schema/rates tests use isolated SQLite, never the production database."""
import json
import unittest
from unittest.mock import patch
from mcp.server.auth.provider import AccessToken
from app.mcp.tools import invoke, tool_definitions
from app.models.resort import Resort
from app.models.ski_area import SkiArea
from app.models.ski_pass import SkiPassSeason, SkiPassPeriod, SkiPassProduct, SkiPassPrice
from app.models.station_widgets import StationWidgets
from app.services.station_ops.catalog_schema import catalog_schema, filled
import test_station_ops as fixtures


class CatalogSchemaTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = fixtures.StationOpsTests.setUp

    def fields(self, **args):
        return {f['path']: f for f in catalog_schema(args)['entities'][args.get('entity', 'station')]['fields']}

    def test_all_physical_model_fields(self):
        fields = self.fields()
        self.assertTrue(set(Resort._meta.fields) <= set(fields))
        self.assertEqual(fields['name']['fill_rate'], 1)
        self.assertEqual(fields['season_open_date']['type'], 'date')
        self.assertFalse(fields['name']['nullable'])

    def test_country_filter(self):
        Resort.create(id='ch', name='Swiss', slug='swiss', country_code='CH')
        self.assertEqual(catalog_schema({'country_code': 'FR'})['population']['total_records'], 1)
        self.assertEqual(catalog_schema({'country_code': 'CH'})['population']['total_records'], 1)

    def test_active_filter(self):
        Resort.update(is_active=True).where(Resort.id == 'a').execute()
        Resort.create(id='inactive', name='Inactive', slug='inactive', country_code='FR', is_active=False)
        self.assertEqual(catalog_schema({'is_active': True})['population']['total_records'], 1)

    def population(self):
        for n in range(9):
            Resort.create(id=str(n), name='Other', slug='other-' + str(n), country_code='FR')

    def test_min_zero(self):
        self.assertIn('snowpark_caption', self.fields(min_fill_rate=0))

    def test_min_tenth(self):
        self.population()
        self.assertEqual(self.fields(min_fill_rate=.1)['logo_url']['filled_count'], 1)
        self.assertNotIn('snowpark_caption', self.fields(min_fill_rate=.1))

    def test_min_nine_tenths(self):
        self.population()
        self.assertIn('name', self.fields(min_fill_rate=.9))
        self.assertNotIn('logo_url', self.fields(min_fill_rate=.9))

    def test_combined_range(self):
        self.population()
        fields = self.fields(min_fill_rate=.1, max_fill_rate=.9)
        self.assertIn('logo_url', fields)
        self.assertNotIn('name', fields)

    def test_nested_widgets(self):
        fields = self.fields()
        self.assertIn('widgets.webcams.items.url', fields)
        self.assertEqual(fields['widgets.snowparks.count']['filled_count'], 1)

    def test_empty_array(self):
        self.assertFalse(filled([]))
        self.assertEqual(self.fields()['maps']['filled_count'], 0)

    def test_empty_object(self):
        self.assertFalse(filled({}))
        self.assertFalse(filled({'a': {'b': '  '}, 'c': []}))
        StationWidgets.update(config=json.dumps({'pistes': {'url': '', 'items': []}})).execute()
        self.assertEqual(self.fields()['widgets']['filled_count'], 0)

    def test_zero(self):
        self.assertTrue(filled(0))
        self.assertEqual(self.fields()['widgets.snowparks.count']['fill_rate'], 1)

    def test_false(self):
        self.assertTrue(filled(False))
        self.assertEqual(self.fields()['widgets.webcams.enabled']['fill_rate'], 1)

    def test_internal_and_write_boundary(self):
        fields = self.fields()
        self.assertTrue(fields['id']['internal'])
        self.assertFalse(fields['id']['writable'])
        self.assertTrue(fields['name']['writable'])
        self.assertFalse(fields['widgets']['writable'])

    def test_collections(self):
        fields = self.fields()
        self.assertTrue(fields['pistes']['collection'])
        self.assertTrue(fields['widgets.webcams.items']['collection'])
        self.assertEqual(fields['pistes.name']['filled_count'], 1)  # Not four pistes.

    def test_passes_even_when_empty(self):
        fields = self.fields()
        self.assertIn('ski_pass_seasons.products.prices.price', fields)
        self.assertIn('ski_pass_seasons.periods.start_date', fields)

    def test_pass_prices(self):
        season = SkiPassSeason.create(resort='a', season='2026')
        period = SkiPassPeriod.create(season=season, external_id='p', name='Period', start_date='2026-01-01', end_date='2026-02-01')
        product = SkiPassProduct.create(season=season, external_id='day', name='Day', duration_label='1 day')
        SkiPassPrice.create(product=product, period=period, category='adult', category_label='Adult', price_type='fixed', price=0)
        self.assertEqual(self.fields()['ski_pass_seasons.products.prices.price']['filled_count'], 1)

    def test_ski_areas(self):
        fields = self.fields(entity='ski_area')
        self.assertTrue(set(SkiArea._meta.fields) <= set(fields))
        self.assertEqual(fields['snowparks_count']['filled_count'], 1)
        self.assertEqual(catalog_schema({'entity': 'ski_area', 'country_code': 'FR'})['population']['total_records'], 1)
        self.assertEqual(catalog_schema({'entity': 'ski_area', 'country_code': 'CH'})['population']['total_records'], 0)

    def test_no_n_plus_one(self):
        def count():
            with patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as queries:
                catalog_schema()
            return queries.call_count
        before = count()
        self.population()
        self.assertEqual(count(), before)

    def test_deterministic(self):
        first = catalog_schema()
        self.assertEqual(first, catalog_schema())
        paths = [f['path'] for f in first['entities']['station']['fields']]
        self.assertEqual(paths, sorted(paths))

    def test_read_only_database(self):
        original = self.database.execute_sql
        def execute(sql, *args, **kwargs):
            self.assertIn(sql.split()[0].upper(), {'SELECT', 'PRAGMA', 'BEGIN'})
            return original(sql, *args, **kwargs)
        with patch.object(self.database, 'execute_sql', side_effect=execute):
            catalog_schema()

    def test_oauth_read_scope(self):
        definition = next(t for t in tool_definitions() if t.name == 'catalog_schema')
        self.assertTrue(definition.annotations.readOnlyHint)
        self.assertFalse(definition.annotations.destructiveHint)
        self.assertEqual(definition.model_extra['securitySchemes'][0]['scopes'], ['station-ops:read'])
        for scopes, error in [(['station-ops:read'], False), (['station-ops:write'], True), ([], True)]:
            token = AccessToken(token='test', client_id='test', scopes=scopes)
            with patch('app.mcp.tools.get_access_token', return_value=token):
                result = invoke(self.app, 'catalog_schema', {})
            self.assertEqual(result.isError, error)

    def test_dynamic_new_physical_field(self):
        self.database.execute_sql('ALTER TABLE resort ADD COLUMN tomorrow_field TEXT')
        self.database.execute_sql("UPDATE resort SET tomorrow_field='new'")
        field = self.fields()['tomorrow_field']
        self.assertEqual(field['filled_count'], 1)
        self.assertFalse(field['writable'])

    def test_dynamic_json_outside_filtered_population(self):
        StationWidgets.update(config=json.dumps({'future_widget': {'new_field': 'new'}})).execute()
        fields = self.fields(country_code='CH')
        self.assertIn('widgets.future_widget.new_field', fields)
        self.assertEqual(fields['widgets.future_widget.new_field']['filled_count'], 0)

    def test_include_nested_false(self):
        self.assertTrue(all('.' not in path for path in self.fields(include_nested=False)))

    def test_invalid_range(self):
        self.assertTrue(invoke(self.app, 'catalog_schema', {'min_fill_rate': .9, 'max_fill_rate': .1}).isError)
        for args in ({'entity': 'other'}, {'min_fill_rate': 2}, {'is_active': 'true'}, {'sql': 'SELECT'}):
            self.assertTrue(invoke(self.app, 'catalog_schema', args).isError)

    def test_empty_scalars(self):
        for value in (None, '', ' \t\n', {}, [], {'nested': [None, ' ']}):
            self.assertFalse(filled(value))
        for value in (0, False, ['data'], {'nested': 0}):
            self.assertTrue(filled(value))

    def test_thousand_stations_one_profile(self):
        Resort.update(is_active=False).where(Resort.id == 'a').execute()
        Resort.insert_many([{'id': 'bulk-' + str(n), 'name': 'Bulk', 'slug': 'bulk-' + str(n),
                             'country_code': 'FR', 'is_active': True} for n in range(1000)]).execute()
        original = self.database.execute_sql
        with patch.object(self.database, 'execute_sql', wraps=original) as queries:
            result = catalog_schema({'country_code': 'FR', 'is_active': True})
        self.assertEqual(result['population']['total_records'], 1000)
        selects = [call.args[0] for call in queries.call_args_list if call.args[0].startswith('SELECT')]
        self.assertEqual(len(selects), 12)
        fields = {f['path']: f for f in result['entities']['station']['fields']}
        self.assertEqual(fields['name']['filled_count'], 1000)

    def test_linked_area_fields_are_discovered(self):
        self.assertIn('ski_area_links.ski_area_record.snowparks_count', self.fields())
        self.assertIn('station_links.resort_record.widgets.webcams.items.url', self.fields(entity='ski_area'))

    def test_nullable_and_heterogeneous_json_types(self):
        Resort.create(id='other', name='Other', slug='other')
        StationWidgets.update(config=json.dumps({'future': {'mixed': 'text'}})).execute()
        StationWidgets.create(station_slug='other', config=json.dumps({'future': {'mixed': 0}}))
        field = self.fields()['widgets.future.mixed']
        self.assertEqual(field['type'], 'integer|string')
        self.assertTrue(field['nullable'])
        self.assertEqual(field['filled_count'], 2)

    def test_dynamic_json_in_new_text_column_and_widget_storage(self):
        self.database.execute_sql('ALTER TABLE resort ADD COLUMN future_payload TEXT')
        self.database.execute_sql('UPDATE resort SET future_payload=?', [json.dumps({'fresh': {'count': 0}})])
        self.database.execute_sql('ALTER TABLE station_widgets ADD COLUMN future_storage TEXT')
        self.database.execute_sql('UPDATE station_widgets SET future_storage=?', ['new'])
        fields = self.fields()
        self.assertEqual(fields['future_payload.fresh.count']['filled_count'], 1)
        self.assertEqual(fields['station_widgets.future_storage']['filled_count'], 1)
        self.assertIn('region.name', fields)
