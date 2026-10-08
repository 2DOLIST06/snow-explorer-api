"""External RESEARCH contract: pure conversion plus existing admin integration."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.models.admin_session import AdminSession
from app.models.resort import Resort
from app.services.station_ops.candidates import ComparePayloadError, MAX_BODY_BYTES, parse_batch
from app.services.station_ops.research import validate_research
from app.services.station_ops.research_schema import research_schema
import test_station_ops as fixtures

URL = '/api/admin/station-ops/research/validate'
HEADERS = {'X-CSRF-Token': 'test-csrf'}


def source(value=2600, **extras):
    return {'url': 'https://example.com/station', 'source_type': 'official',
            'publisher': 'Station operator', 'observed_at': '2026-10-08T12:00:00Z',
            'value_observed': value, **extras}


def candidate(data=None, ref='external-001', kind='existing', status='resolved'):
    data = data if data is not None else {'id': 'a', 'altitude_max_m': 2600}
    return {'client_ref': ref, 'identity': {'kind': kind, 'status': status},
            'research_level': 'core', 'target_fields': list(data), 'data': deepcopy(data),
            'field_statuses': {field: 'found' for field in data},
            'field_sources': {'altitude_max_m': [source()]} if 'altitude_max_m' in data else {},
            'notes': ['External research; not a database update.']}


def batch(rows=None):
    return {'research_version': '1.0', 'scope': {'type': 'country', 'country_code': 'FR'},
            'candidates': rows if rows is not None else [candidate()]}


class ResearchContractTests(unittest.TestCase):
    def invalid(self, payload, code=None):
        result = validate_research(payload)
        self.assertFalse(result['valid'], result)
        self.assertIsNone(result['compare_payload'])
        if code:
            self.assertIn(code, [error['code'] for error in result['errors']])
        return result

    def test_existing_complete_and_exact_compare_payload(self):
        payload = batch()
        result = validate_research(payload)
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['results'][0]['audit']['research_status'], 'complete')
        expected = {'candidates': [{k: deepcopy(payload['candidates'][0][k]) for k in ('client_ref', 'data', 'field_sources')}]}
        self.assertEqual(result['compare_payload'], expected)
        self.assertTrue(parse_batch(expected)[0]['valid'])

    def test_new_station_preserves_identity_and_provenance(self):
        row = candidate({'name': 'New station', 'slug': 'new-station', 'country_code': 'FR'}, kind='discovered')
        row['field_sources'] = {'name': [source('New station')]}
        result = validate_research(batch([row]))
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['compare_payload']['candidates'][0]['data'], row['data'])
        self.assertNotIn('id', result['compare_payload']['candidates'][0]['data'])
        self.assertEqual(result['compare_payload']['candidates'][0]['field_sources'], row['field_sources'])

    def test_discovered_id_forbidden(self):
        self.invalid(batch([candidate(kind='discovered')]), 'discovered_internal_id_forbidden')

    def test_new_station_missing_slug_is_not_invented(self):
        result = validate_research(batch([candidate({'name': 'New station'}, kind='discovered')]))
        self.assertEqual(result['results'][0]['audit']['research_status'], 'insufficient')
        self.assertEqual(result['compare_payload']['candidates'][0]['data'], {'name': 'New station'})

    def test_multiple_equivalent_sources_are_retained(self):
        payload = batch()
        rows = payload['candidates'][0]['field_sources']['altitude_max_m']
        rows.append(source('2600', source_type='government', publisher='Survey'))
        before = deepcopy(payload)
        result = validate_research(payload)
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['compare_payload']['candidates'][0]['field_sources']['altitude_max_m'], rows)
        self.assertEqual(payload, before)

    def test_conflicting_sources_are_excluded_without_losing_provenance(self):
        row = candidate()
        row['field_sources']['altitude_max_m'].append(source(2700, source_type='secondary'))
        del row['data']['altitude_max_m']
        row['field_statuses']['altitude_max_m'] = 'conflicting'
        result = validate_research(batch([row]))
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['results'][0]['audit']['research_status'], 'conflict')
        self.assertEqual(result['results'][0]['audit']['fields_conflicting'], ['altitude_max_m'])
        self.assertEqual(result['compare_payload']['candidates'][0]['data'], {'id': 'a'})
        self.assertEqual(result['compare_payload']['candidates'][0]['field_sources'], row['field_sources'])

    def test_undeclared_scalar_conflict_is_rejected(self):
        payload = batch()
        payload['candidates'][0]['field_sources']['altitude_max_m'].append(source(2700))
        self.invalid(payload, 'source_conflict_not_declared')

    def test_absence_and_unresearched_never_exported(self):
        row = candidate({'id': 'a'})
        row['target_fields'] += ['website_url', 'department', 'latitude']
        row['field_statuses'].update(website_url='not_found', latitude='ambiguous')
        result = validate_research(batch([row]))
        audit = result['results'][0]['audit']
        self.assertEqual(audit['fields_not_found'], ['website_url'])
        self.assertEqual(audit['fields_not_researched'], ['department'])
        self.assertEqual(audit['fields_ambiguous'], ['latitude'])
        self.assertEqual(audit['fields_checked'], ['id', 'latitude', 'website_url'])
        exported = result['compare_payload']['candidates'][0]
        self.assertEqual(exported['data'], {'id': 'a'})
        self.assertNotIn('clear_fields', exported)

    def test_partial_audit(self):
        row = candidate({'id': 'a'})
        row['target_fields'].append('website_url')
        row['field_statuses']['website_url'] = 'not_found'
        self.assertEqual(validate_research(batch([row]))['results'][0]['audit']['research_status'], 'partial')

    def test_found_without_source_warns_but_valid(self):
        payload = batch()
        payload['candidates'][0]['field_sources'] = {}
        result = validate_research(payload)
        self.assertTrue(result['valid'])
        self.assertIn('source_absent', [x['code'] for x in result['warnings']])

    def test_identity_ambiguous_or_unresolved_never_auto_selected(self):
        for status, audit in [('ambiguous', 'identity_ambiguous'), ('unresolved', 'insufficient')]:
            with self.subTest(status=status):
                result = validate_research(batch([candidate(status=status)]))
                self.assertTrue(result['valid'])
                self.assertIsNone(result['compare_payload'])
                self.assertEqual(result['results'][0]['audit']['research_status'], audit)
                self.assertEqual(result['excluded_candidates'], [{'client_ref': 'external-001', 'reason': 'identity_unresolved'}])

    def test_missing_identity_preserves_audit(self):
        result = validate_research(batch([candidate({'altitude_max_m': 2600}, status='unresolved')]))
        self.assertTrue(result['valid'], result)
        self.assertIsNone(result['compare_payload'])

    def test_all_data_validated_even_unresolved_identity(self):
        self.invalid(batch([candidate({'website_url': 'ftp://example.com'}, status='unresolved')]), 'field_value_invalid')

    def test_invalid_types_and_null_or_blank(self):
        for field, value in [('altitude_max_m', '2600'), ('altitude_max_m', True), ('is_active', 1),
                             ('name', None), ('name', '   '), ('website_url', []), ('widgets', []),
                             ('lifts', {}), ('ski_areas', [{'id': '2'}])]:
            with self.subTest(field=field, value=value):
                self.invalid(batch([candidate({'id': 'a', field: value})]))

    def test_unknown_keys_and_updated_at_forbidden(self):
        for path in ('batch', 'candidate', 'data', 'source'):
            payload = batch()
            target = {'batch': payload, 'candidate': payload['candidates'][0],
                      'data': payload['candidates'][0]['data'],
                      'source': payload['candidates'][0]['field_sources']['altitude_max_m'][0]}[path]
            target['updated_at'] = '2026-10-08'
            with self.subTest(path=path):
                self.invalid(payload, 'unknown_field')
        payload = batch()
        payload['candidates'][0]['clear_fields'] = ['website_url']
        self.invalid(payload, 'unknown_field')

    def test_state_value_mismatch(self):
        for state in ('not_researched', 'not_found', 'conflicting', 'ambiguous'):
            payload = batch()
            payload['candidates'][0]['field_statuses']['altitude_max_m'] = state
            self.invalid(payload, 'field_state_mismatch')
        payload = batch()
        del payload['candidates'][0]['data']['altitude_max_m']
        self.invalid(payload, 'field_state_mismatch')

    def test_sources_validation(self):
        for extra in ({'source_type': 'invented'}, {'url': 'https://user:secret@example.com'},
                      {'observed_at': '2026-10-08'}, {'publisher': ''}, {'score': 99}):
            payload = batch()
            payload['candidates'][0]['field_sources']['altitude_max_m'][0].update(extra)
            with self.subTest(extra=extra):
                self.invalid(payload)
        payload = batch()
        payload['candidates'][0]['field_sources']['unknown'] = [source()]
        self.invalid(payload, 'field_sources_invalid')

    def test_relation_requires_complete_nonempty_set(self):
        row = candidate({'id': 'a', 'ski_areas': [{'slug': 'linked-area'}]})
        self.invalid(batch([row]), 'relation_coverage_required')
        row['relation_coverage'] = {'ski_areas': 'complete'}
        result = validate_research(batch([row]))
        self.assertTrue(result['valid'], result)
        self.assertIn('relation_set_replacement', [w['code'] for w in result['warnings']])
        row['data']['ski_areas'] = []
        self.invalid(batch([row]), 'batch_or_list_size_invalid')

    def test_collections_remain_conservative(self):
        row = candidate({'id': 'a', 'lifts': [{'name': 'Lift'}], 'widgets': {'title': 'Example'}})
        result = validate_research(batch([row]))
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['compare_payload']['candidates'][0]['data'], row['data'])
        self.assertEqual(sum(w['code'] == 'collection_comparison_not_supported' for w in result['warnings']), 2)

    def test_client_ref_exact_and_unique(self):
        result = validate_research(batch([candidate(ref='  External-é-001  ')]))
        self.assertEqual(result['compare_payload']['candidates'][0]['client_ref'], '  External-é-001  ')
        self.invalid(batch([candidate(), candidate()]), 'duplicate_client_ref')

    def test_no_partial_export_on_invalid_batch(self):
        bad = candidate(ref='bad')
        bad['data']['unknown'] = 7
        self.invalid(batch([candidate(), bad]))

    def test_large_batch_and_chunks(self):
        result = validate_research({**batch([candidate(ref=f'ext-{i}') for i in range(1000)]), 'batch': {'index': 2, 'total': 3}})
        self.assertTrue(result['valid'])
        self.assertEqual(result['summary']['total_candidates'], 1000)
        self.assertEqual(result['summary']['complete'], 1000)
        self.assertEqual(len(result['compare_payload']['candidates']), 1000)
        with self.assertRaises(ComparePayloadError) as raised:
            validate_research(batch([candidate(ref=str(i)) for i in range(1001)]))
        self.assertEqual(raised.exception.status, 413)

    def test_scope_station_region_country(self):
        for scope in ({'type': 'station', 'country_code': 'FR', 'slug': 'alpha'},
                      {'type': 'region', 'country_code': 'FR', 'region_name': 'Alps'},
                      {'type': 'country', 'country_code': 'FR'}):
            self.assertTrue(validate_research({**batch(), 'scope': scope})['valid'])
        self.invalid({**batch(), 'scope': {'type': 'region', 'country_code': 'FR'}}, 'scope_identity_missing')
        self.invalid({**batch(), 'scope': {'type': 'station', 'country_code': 'FR'}}, 'scope_identity_missing')
        self.invalid(batch([candidate({'id': 'a', 'country_code': 'CH'})]), 'country_scope_mismatch')
        self.invalid({**batch(), 'batch': {'index': 3, 'total': 2}}, 'batch_index_invalid')

    def test_payload_limit_and_nonfinite_json(self):
        with self.assertRaises(ComparePayloadError) as raised:
            validate_research(batch([candidate({'id': 'a', 'description_html': 'x' * MAX_BODY_BYTES})]))
        self.assertEqual(raised.exception.status, 413)
        for value in (float('nan'), float('inf')):
            with self.assertRaises(ComparePayloadError):
                validate_research(batch([candidate({'id': 'a', 'latitude': value})]))


    def test_targets_and_status_keys_strict(self):
        payload = batch()
        payload['candidates'][0]['target_fields'] = ['id']
        self.invalid(payload, 'field_outside_targets')
        payload = batch()
        payload['candidates'][0]['target_fields'].append('unknown')
        self.invalid(payload, 'enum_invalid')
        payload = batch()
        payload['candidates'][0]['field_statuses']['unknown'] = 'found'
        self.invalid(payload, 'unknown_field')

    def test_raw_observation_and_nested_collection_provenance_preserved(self):
        row = candidate({'id': 'a', 'lifts': [{'name': 'Lift'}]})
        row['field_sources'] = {'lifts.0.name': [source('Lift')],
                               'altitude_max_m': [source({'quote': 'upper station'})]}
        row['target_fields'].append('altitude_max_m')
        row['field_statuses']['altitude_max_m'] = 'ambiguous'
        result = validate_research(batch([row]))
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['compare_payload']['candidates'][0]['field_sources'], row['field_sources'])
        self.assertIn('observation_not_comparable', [w['code'] for w in result['warnings']])

    def test_native_integer_json_number_and_bounds(self):
        row = candidate({'id': 'a', 'altitude_max_m': 2600.0})
        self.assertTrue(validate_research(batch([row]))['valid'])
        self.invalid(batch([candidate({'id': 'a', 'latitude': 95})]), 'field_value_invalid')
        self.invalid(batch([candidate({'id': 'a', 'season_open_date': 'invalid'})]), 'field_value_invalid')

    def test_schema_artifact_matches_shared_field_catalog(self):
        path = Path(__file__).resolve().parents[1] / 'docs/schemas/station-research.schema.json'
        self.assertEqual(json.loads(path.read_text()), research_schema())

    def test_pure_validator_uses_no_sql_or_http(self):
        with patch('peewee.Database.execute_sql', side_effect=AssertionError('No SQL')), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('No web')):
            self.assertTrue(validate_research(batch())['valid'])


class ResearchEndpointTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = fixtures.StationOpsTests.setUp

    def post(self, payload=None, expected=200):
        response = self.client.post(URL, json=payload if payload is not None else batch(), headers=HEADERS)
        self.assertEqual(response.status_code, expected, response.get_json())
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.get_json()

    def test_auth_and_csrf(self):
        self.assertEqual(self.client.post(URL, json=batch()).status_code, 403)
        self.assertEqual(self.client.post(URL, json=batch(), headers={'X-CSRF-Token': 'wrong'}).status_code, 403)
        self.client.delete_cookie('admin_session')
        self.assertEqual(self.client.post(URL, json=batch(), headers=HEADERS).status_code, 401)

    def test_only_auth_reads_no_business_sql_and_session_unchanged(self):
        before = AdminSession.get_by_id(self.session.id).last_seen_at
        statements = []
        original = self.database.execute_sql
        def read_only(sql, params=None, *args, **kwargs):
            statements.append(sql)
            self.assertTrue(sql.lstrip().upper().startswith('SELECT'), sql)
            return original(sql, params, *args, **kwargs)
        with patch.object(self.database, 'execute_sql', side_effect=read_only):
            self.post(batch([candidate(ref=str(i)) for i in range(500)]))
        self.assertTrue(statements)
        self.assertTrue(all('admin_' in sql for sql in statements), statements)
        self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, before)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)
        self.assertEqual(len(statements), 1)

    def test_endpoint_error_cases_and_limits(self):
        self.post({'research_version': 'wrong'}, expected=400)
        self.post(batch([candidate(ref=str(i)) for i in range(1001)]), expected=413)
        self.assertEqual(self.client.post(URL, data='{}', headers=HEADERS).status_code, 415)
        self.assertEqual(self.client.post(URL, data='{"candidates":[],"candidates":[]}', content_type='application/json', headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post(URL, data='{' + ' ' * MAX_BODY_BYTES, content_type='application/json', headers=HEADERS).status_code, 413)

    def test_refused_method_does_not_touch_session(self):
        before = AdminSession.get_by_id(self.session.id).last_seen_at
        self.assertEqual(self.client.get(URL).status_code, 405)
        self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, before)

    def test_provenance_compare_review_and_apply_dry_run(self):
        envelope = self.post()['compare_payload']
        compared = self.client.post('/api/admin/station-ops/compare', json=envelope, headers=HEADERS)
        self.assertEqual(compared.status_code, 200, compared.get_json())
        self.assertEqual(compared.get_json()['results'][0]['field_sources'], envelope['candidates'][0]['field_sources'])
        reviewed = self.client.post('/api/admin/station-ops/review', json=envelope, headers=HEADERS).get_json()
        operation = reviewed['results'][0]['operations'][0]
        self.assertEqual(operation['sources'], [source()])
        decisions = [{'client_ref': 'external-001', 'operations': {operation['operation_id']: 'approved'}}]
        approved_payload = {**envelope, 'decisions': decisions}
        approved = self.client.post('/api/admin/station-ops/review', json=approved_payload, headers=HEADERS).get_json()
        response = self.client.post('/api/admin/station-ops/apply', json={**approved_payload,
            'mode': 'dry_run', 'plan_fingerprint': approved['apply_plan']['plan_fingerprint']}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_unsupported_collection_compare_and_review_stay_blocked(self):
        envelope = self.post(batch([candidate({'id': 'a', 'lifts': [{'name': 'Unverified lift'}]})]))['compare_payload']
        compared = self.client.post('/api/admin/station-ops/compare', json=envelope, headers=HEADERS).get_json()
        self.assertEqual(compared['results'][0]['status'], 'review_required')
        reviewed = self.client.post('/api/admin/station-ops/review', json=envelope, headers=HEADERS).get_json()
        self.assertEqual(reviewed['results'][0]['status'], 'blocked')
        self.assertEqual(reviewed['apply_plan']['operations'], [])


    def test_discovered_station_reaches_pending_create_without_write(self):
        row = candidate({'name': 'Brand new station', 'slug': 'brand-new-station', 'country_code': 'FR'}, kind='discovered')
        row['field_sources'] = {'name': [source('Brand new station')]}
        envelope = self.post(batch([row]))['compare_payload']
        reviewed = self.client.post('/api/admin/station-ops/review', json=envelope, headers=HEADERS)
        self.assertEqual(reviewed.status_code, 200, reviewed.get_json())
        result = reviewed.get_json()['results'][0]
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(result['operations'][0]['operation'], 'create_station')
        self.assertEqual(result['operations'][0]['decision'], 'pending')
        self.assertEqual(result['operations'][0]['field_sources'], row['field_sources'])
        self.assertFalse(Resort.select().where(Resort.slug == 'brand-new-station').exists())
