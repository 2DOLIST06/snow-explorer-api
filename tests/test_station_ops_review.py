"""REVIEW uses isolated fixtures; production connections are forbidden."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import MagicMock, patch

from peewee import PostgresqlDatabase

from app.datetime_utils import utcnow
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser
from app.models.region import Region
from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from app.services.station_ops.creation import creation_constraints, _known_layout_check
from app.services.station_ops.operations import fingerprint
from app.services.station_ops.review import review_candidates
import test_station_ops as scan_fixtures
import app.services.station_ops.compare as compare_service

URL = '/api/admin/station-ops/review'


def candidate(data=None, ref='external-001', **extras):
    return {'client_ref': ref, 'data': data if data is not None else {'id': 'a', 'altitude_max_m': 2600}, **extras}


def decide(ref, operations, decision='approved'):
    return {'client_ref': ref, 'operations': {op['operation_id']: decision for op in operations}}


class ReviewTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = scan_fixtures.StationOpsTests.setUp

    def review(self, candidates=None, decisions=None, expected=200):
        payload = {'candidates': candidates if candidates is not None else [candidate()]}
        if decisions is not None:
            payload['decisions'] = decisions
        response = self.client.post(URL, json=payload, headers={'X-CSRF-Token': 'test-csrf'})
        self.assertEqual(response.status_code, expected, response.get_json())
        if expected == 200:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.get_json()

    def operation(self, body):
        return body['results'][0]['operations'][0]

    def test_unchanged_becomes_no_action(self):
        body = self.review([candidate({'id': 'a'})])
        self.assertEqual(body['results'][0]['status'], 'no_action')
        self.assertEqual(body['results'][0]['operations'], [])
        self.assertEqual(body['apply_plan']['operations'], [])

    def test_scalar_change_is_pending_with_preconditions(self):
        body = self.review()
        op = self.operation(body)
        self.assertEqual(body['results'][0]['status'], 'pending_review')
        self.assertEqual((op['operation'], op['field'], op['existing'], op['candidate']), ('replace', 'altitude_max_m', 2200, 2600))
        self.assertEqual(op['decision'], 'pending')
        self.assertTrue(op['requires_explicit_approval'])
        self.assertEqual(op['preconditions'], {'station_id': 'a', 'station_exists': True, 'field': 'altitude_max_m',
                                             'comparison': 'serialized_stored_value', 'expected_existing': 2200})
        self.assertEqual(len(op['operation_id']), 64)
        self.assertEqual(body['apply_plan']['operations'], [])

    def test_approved_scalar_enters_apply_plan_without_writing(self):
        first = self.review()
        body = self.review(decisions=[decide('external-001', [self.operation(first)])])
        self.assertEqual(body['results'][0]['status'], 'approved')
        self.assertEqual(body['apply_plan']['operations'], body['results'][0]['operations'])
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_rejected_scalar_stays_outside_apply_plan(self):
        first = self.review()
        body = self.review(decisions=[decide('external-001', [self.operation(first)], 'rejected')])
        self.assertEqual(body['results'][0]['status'], 'rejected')
        self.assertEqual(body['summary']['rejected_operations'], 1)
        self.assertEqual(body['apply_plan']['operations'], [])

    def test_mixed_decisions_and_unapproved_rejected_pending(self):
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600, 'website_url': 'https://other.example', 'is_active': False})]
        first = self.review(candidates)
        operations = first['results'][0]['operations']
        decisions = [{'client_ref': 'external-001', 'operations': {operations[0]['operation_id']: 'approved', operations[1]['operation_id']: 'rejected'}}]
        body = self.review(candidates, decisions)
        self.assertEqual(body['results'][0]['status'], 'partially_approved')
        self.assertEqual([op['decision'] for op in body['results'][0]['operations']], ['approved', 'rejected', 'pending'])
        self.assertEqual(len(body['apply_plan']['operations']), 1)
        body = self.review(candidates, [decide('external-001', operations[:1], 'rejected')])
        self.assertEqual(body['results'][0]['status'], 'pending_review')

    def test_operation_id_stable_and_documented_hash_exact(self):
        first, second = self.operation(self.review()), self.operation(self.review())
        self.assertEqual(first['operation_id'], second['operation_id'])
        identity = {'review_version': '1.0', **{key: first.get(key) for key in (
            'client_ref', 'target_type', 'target_id', 'target_client_ref', 'operation', 'field', 'related_id',
            'normalized_candidate', 'preconditions', 'creation_policy', 'depends_on')}}
        self.assertEqual(first['operation_id'], fingerprint(identity))
        approved = self.operation(self.review(decisions=[decide('external-001', [first])]))
        self.assertEqual(approved['operation_id'], first['operation_id'])

    def test_changed_candidate_gets_new_id_and_pending(self):
        old = self.operation(self.review())
        new_candidate = [candidate({'id': 'a', 'altitude_max_m': 2700})]
        current = self.operation(self.review(new_candidate))
        self.assertNotEqual(old['operation_id'], current['operation_id'])
        self.assertEqual(current['decision'], 'pending')
        body = self.review(new_candidate, [decide('external-001', [old])], expected=409)
        self.assertEqual(body['issues'][0]['code'], 'unknown_or_stale_operation')

    def test_recomputation_after_database_change_invalidates_previous_approval(self):
        old = self.operation(self.review())
        Resort.update(altitude_max_m=2300).execute()  # Fixture mutation between calls.
        current = self.operation(self.review())
        self.assertEqual(current['existing'], 2300)
        self.assertNotEqual(current['operation_id'], old['operation_id'])
        self.review(decisions=[decide('external-001', [old])], expected=409)

    def test_disappeared_operation_cannot_reuse_old_decision(self):
        old = self.operation(self.review())
        Resort.update(altitude_max_m=2600).execute()
        self.assertEqual(self.review()['results'][0]['status'], 'no_action')
        self.review(decisions=[decide('external-001', [old])], expected=409)

    def test_clear_requires_explicit_operation_approval(self):
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600}, clear_fields=['website_url'])]
        first = self.review(candidates)
        ops = {op['field']: op for op in first['results'][0]['operations']}
        self.assertEqual(ops['website_url']['operation'], 'clear')
        self.assertTrue(ops['website_url']['sensitive'])
        body = self.review(candidates, [decide('external-001', [ops['altitude_max_m']])])
        self.assertEqual([op['field'] for op in body['apply_plan']['operations']], ['altitude_max_m'])
        self.assertEqual(Resort.get_by_id('a').website_url, 'https://example.com')

    def test_absent_and_null_never_become_clears(self):
        body = self.review([candidate({'id': 'a', 'website_url': None, 'ski_areas': None})])
        self.assertEqual(body['results'][0]['status'], 'no_action')
        self.assertEqual(body['results'][0]['operations'], [])

    def test_added_scalar_uses_set(self):
        op = self.operation(self.review([candidate({'id': 'a', 'meta_title': 'New title'})]))
        self.assertEqual(op['operation'], 'set')
        self.assertIsNone(op['preconditions']['expected_existing'])

    def test_domain_add_and_remove_are_separate_approved_operations(self):
        area = SkiArea.create(name='Other', slug='other')
        candidates = [candidate({'id': 'a', 'ski_areas': [{'id': area.id}]})]
        first = self.review(candidates)
        ops = {op['operation']: op for op in first['results'][0]['operations']}
        add, remove = ops['add_ski_area_relation'], ops['remove_ski_area_relation']
        self.assertFalse(add['preconditions']['expected_relation_exists'])
        self.assertEqual(add['related_id'], area.id)
        self.assertTrue(remove['preconditions']['expected_relation_exists'])
        self.assertTrue(remove['sensitive'])
        body = self.review(candidates, [decide('external-001', [add])])
        self.assertEqual([op['operation'] for op in body['apply_plan']['operations']], ['add_ski_area_relation'])
        self.assertEqual(list(SkiAreaResort.select().dicts())[0]['ski_area'], self.area.id)

    def test_relation_sources_follow_root_and_specific_reference(self):
        area = SkiArea.create(name='Other', slug='other')
        sources = {'ski_areas': [{'url': 'https://example.com/all'}],
                   'ski_areas.0.id': [{'url': 'https://example.com/other', 'source_type': 'official'}]}
        body = self.review([candidate({'id': 'a', 'ski_areas': [{'id': str(area.id) + '.0'}]}, field_sources=sources)])
        op = next(op for op in body['results'][0]['operations'] if op['operation'] == 'add_ski_area_relation')
        self.assertEqual(op['sources'], sources['ski_areas'] + sources['ski_areas.0.id'])
        self.assertEqual(op['source_count'], 2)

    def test_create_station_pending_and_approved_with_explicit_defaults(self):
        candidates = [candidate({'name': 'New Station', 'slug': 'new-station', 'country_code': 'CA'})]
        first = self.review(candidates)
        op = self.operation(first)
        self.assertEqual((op['operation'], op['decision']), ('create_station', 'pending'))
        self.assertIsNone(op['target_id'])
        self.assertEqual(op['candidate']['name'], 'New Station')
        self.assertEqual(op['creation_policy']['id']['policy'], 'server_uuid_at_apply')
        self.assertTrue(op['preconditions']['no_matching_station'])
        self.assertEqual(op['preconditions']['slug_must_be_absent'], 'new-station')
        body = self.review(candidates, [decide('external-001', [op])])
        self.assertEqual(body['results'][0]['status'], 'approved')
        self.assertEqual(Resort.select().count(), 1)

    def test_create_with_explicit_id_and_values_does_not_invent_defaults(self):
        body = self.review([candidate({'id': 'new', 'name': 'New', 'slug': 'new', 'is_active': False, 'page_layout_version': 'v2'})])
        op = self.operation(body)
        self.assertEqual(op['target_id'], 'new')
        self.assertEqual(op['creation_policy'], {'updated_at': {'policy': 'server_utcnow_at_apply'}})

    def test_station_appearing_after_creation_review_invalidates_old_decision(self):
        candidates = [candidate({'name': 'New Station', 'slug': 'new-station', 'country_code': 'CA'})]
        old = self.operation(self.review(candidates))
        Resort.create(id='appeared', name='New Station', slug='new-station', country_code='CA')
        self.assertEqual(self.review(candidates)['results'][0]['status'], 'no_action')
        self.review(candidates, [decide('external-001', [old])], expected=409)

    def test_changed_creation_constraints_do_not_reuse_old_approval(self):
        candidates = [candidate({'name': 'New', 'slug': 'new'})]
        old = self.operation(self.review(candidates))
        constraints = creation_constraints(self.database)
        constraints['columns']['is_active']['default'] = '0'
        with patch('app.services.station_ops.review.creation_constraints', return_value=constraints):
            current = self.operation(self.review(candidates))
            self.assertNotEqual(old['operation_id'], current['operation_id'])
            self.review(candidates, [decide('external-001', [old])], expected=409)

    def test_insufficient_new_station_is_blocked(self):
        for data in ({'name': 'New'}, {'id': 'new', 'slug': 'new'}):
            body = self.review([candidate(data)])
            self.assertEqual(body['results'][0]['status'], 'blocked')
            self.assertEqual(body['results'][0]['operations'], [])
            self.assertEqual(body['results'][0]['review_items'][0]['code'], 'create_required_fields_missing')

    def test_create_field_length_constraint_blocks_without_schema_change(self):
        body = self.review([candidate({'id': 'x' * 256, 'name': 'New', 'slug': 'new'})])
        self.assertEqual(body['results'][0]['status'], 'blocked')
        self.assertTrue(any(item['code'] == 'create_field_exceeds_length' for item in body['results'][0]['review_items']))

    def test_required_extra_physical_column_and_unknown_check_block_creation(self):
        constraints = {'columns': {'id': {'required': True, 'default': None, 'type': 'varchar(255)'},
                                   'name': {'required': True, 'default': None, 'type': 'text'},
                                   'slug': {'required': True, 'default': None, 'type': 'varchar(255)'},
                                   'legacy_required': {'required': True, 'default': None, 'type': 'text'}},
                       'unknown_checks': ['CHECK (something_unknown)']}
        with patch('app.services.station_ops.review.creation_constraints', return_value=constraints):
            body = self.review([candidate({'name': 'New', 'slug': 'new'})])
        self.assertEqual(body['results'][0]['status'], 'blocked')
        self.assertEqual({item['code'] for item in body['results'][0]['review_items']},
                         {'create_required_columns_missing', 'create_constraint_requires_review'})
        constraints['columns']['legacy_required']['default'] = 'NULL::text'
        constraints['unknown_checks'] = []
        with patch('app.services.station_ops.review.creation_constraints', return_value=constraints):
            body = self.review([candidate({'name': 'New', 'slug': 'new'})])
        self.assertEqual(body['results'][0]['status'], 'blocked')
        self.assertEqual(body['results'][0]['review_items'][0]['code'], 'create_required_columns_missing')

    def test_new_domain_relations_require_approved_creation_dependency(self):
        candidates = [candidate({'name': 'New', 'slug': 'new', 'ski_areas': [{'id': self.area.id}]})]
        first = self.review(candidates)
        ops = {op['operation']: op for op in first['results'][0]['operations']}
        creation, relation = ops['create_station'], ops['add_ski_area_relation']
        self.assertEqual(relation['depends_on'], [creation['operation_id']])
        error = self.review(candidates, [decide('external-001', [relation])], expected=409)
        self.assertEqual(error['issues'][0]['code'], 'operation_dependency_not_approved')
        body = self.review(candidates, [decide('external-001', [creation, relation])])
        self.assertEqual(body['results'][0]['status'], 'approved')
        self.assertEqual(len(body['apply_plan']['operations']), 2)
        self.assertNotIn('ski_areas', creation['candidate'])

    def test_ambiguity_and_collection_limitations_remain_blocked(self):
        for data in ({'slug': 'alpha'}, {'id': 'a', 'pistes': []}, {'id': 'a', 'ski_areas': [{'name': 'Linked area'}]}):
            with self.subTest(data=data):
                body = self.review([candidate(data)])
                self.assertEqual(body['results'][0]['status'], 'blocked')
                self.assertEqual(body['results'][0]['operations'], [])
                self.assertTrue(body['results'][0]['review_items'])

    def test_invalid_compare_becomes_invalid_review(self):
        body = self.review([candidate({'id': 'a', 'latitude': 91})])
        self.assertEqual(body['results'][0]['status'], 'invalid')
        self.assertEqual(body['apply_plan']['operations'], [])

    def test_sources_preserved_and_absent_source_allowed(self):
        sources = {'altitude_max_m': [{'url': 'https://official.example/page', 'source_type': 'official',
                                     'observed_at': '2026-10-08T10:00:00Z'}]}
        original = [candidate(field_sources=sources)]
        before = deepcopy(original)
        op = self.operation(self.review(original))
        self.assertEqual(op['sources'], sources['altitude_max_m'])
        self.assertEqual(op['source_count'], 1)
        self.assertEqual(original, before)
        self.assertEqual(self.operation(self.review())['source_count'], 0)

    def test_editorial_payload_and_fingerprint_precondition(self):
        content = '<p>New text</p>'
        candidates = [candidate({'id': 'a', 'description_html': content})]
        first = self.review(candidates)
        op = self.operation(first)
        self.assertEqual(op['preconditions']['comparison'], 'length_md5')
        self.assertEqual(op['preconditions']['expected_existing']['length'], 21)
        self.assertEqual(op['candidate_input'], content)
        body = self.review(candidates, [decide('external-001', [op])])
        self.assertEqual(body['apply_plan']['operations'][0]['candidate_input'], content)

    def test_unknown_or_wrong_candidate_operation_id_is_refused(self):
        self.review(decisions=[{'client_ref': 'external-001', 'operations': {'not-known': 'approved'}}], expected=409)
        op = self.operation(self.review())
        candidates = [candidate(ref='other')]
        self.review(candidates, [{'client_ref': 'other', 'operations': {op['operation_id']: 'approved'}}], expected=409)

    def test_invalid_decisions_contradictions_and_unknown_ref(self):
        op = self.operation(self.review())
        for decisions in ([{'client_ref': 'external-001', 'operations': {op['operation_id']: 'pending'}}],
                          [decide('missing', [op])],
                          [decide('external-001', [op]), decide('external-001', [op], 'rejected')],
                          {'approve_all': True}):
            with self.subTest(decisions=decisions):
                body = self.review(decisions=decisions, expected=400)
                self.assertEqual(body['error'], 'invalid_review_payload')
        self.review(decisions=[{'client_ref': 'external-001', 'operations': {'approve_all': 'approved'}}], expected=409)

    def test_repeated_identical_decision_is_idempotent(self):
        op = self.operation(self.review())
        body = self.review(decisions=[decide('external-001', [op]), decide('external-001', [op])])
        self.assertEqual(body['summary']['approved_operations'], 1)

    def test_approval_of_currently_blocked_candidate_is_refused(self):
        old = self.operation(self.review())
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600, 'widgets': {}})]
        body = self.review(candidates, [decide('external-001', [old])], expected=409)
        self.assertEqual(body['issues'][0]['code'], 'approval_on_blocked_candidate')

    def test_client_compare_output_and_global_approval_are_not_accepted(self):
        for payload in ({'candidates': [candidate()], 'compare_result': {'status': 'unchanged'}},
                        {'candidates': [candidate()], 'approve_all': True}):
            response = self.client.post(URL, json=payload, headers={'X-CSRF-Token': 'test-csrf'})
            self.assertEqual(response.status_code, 400)

    def test_plan_fingerprint_stable_exact_and_changes_with_plan(self):
        op = self.operation(self.review())
        first = self.review(decisions=[decide('external-001', [op])])
        second = self.review(decisions=[decide('external-001', [op])])
        self.assertEqual(first['apply_plan'], second['apply_plan'])
        self.assertEqual(first['apply_plan']['plan_fingerprint'], fingerprint({'review_version': '1.0', 'operations': first['apply_plan']['operations']}))
        self.assertNotEqual(first['apply_plan']['plan_fingerprint'], self.review()['apply_plan']['plan_fingerprint'])

    def test_batch_order_does_not_change_apply_plan_fingerprint(self):
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600}, 'one'), candidate({'id': 'a', 'website_url': 'https://new.example'}, 'two')]
        first = self.review(candidates)
        decisions = [decide(row['client_ref'], row['operations']) for row in first['results']]
        a, b = self.review(candidates, decisions), self.review(list(reversed(candidates)), list(reversed(decisions)))
        self.assertEqual(a['apply_plan'], b['apply_plan'])

    def test_batch_conflicting_creations_and_field_proposals_are_blocked(self):
        for candidates in ([candidate({'name': 'New', 'slug': 'same-new'}, 'one'), candidate({'name': 'Other', 'slug': 'same-new'}, 'two')],
                           [candidate({'id': 'a', 'altitude_max_m': 2600}, 'one'), candidate({'id': 'a', 'altitude_max_m': 2700}, 'two')]):
            body = self.review(candidates)
            self.assertEqual(body['summary']['blocked'], 2)
            self.assertEqual(body['apply_plan']['operations'], [])

    def test_mixed_batch_and_exact_summary(self):
        candidates = [candidate({'id': 'a'}, 'unchanged'), candidate(ref='pending'),
                      candidate({'id': 'a', 'website_url': 'https://new.example'}, 'approve'),
                      candidate({'id': 'a', 'is_active': False}, 'reject'),
                      candidate({'slug': 'alpha'}, 'blocked'), candidate({}, 'invalid')]
        first = self.review(candidates)
        by_ref = {row['client_ref']: row for row in first['results']}
        body = self.review(candidates, [decide('approve', by_ref['approve']['operations']), decide('reject', by_ref['reject']['operations'], 'rejected')])
        self.assertEqual(body['summary'], {'total_candidates': 6, 'no_action': 1, 'pending_review': 1, 'partially_approved': 0,
                                        'approved': 1, 'rejected': 1, 'blocked': 1, 'invalid': 1,
                                        'approved_operations': 1, 'pending_operations': 1, 'rejected_operations': 1})
        self.assertEqual(len(body['apply_plan']['operations']), 1)
        self.assertEqual(body['review_version'], '1.0')

    def test_limits_duplicate_refs_malformed_json_and_missing_auth_csrf(self):
        self.review([candidate(ref=str(i)) for i in range(1001)], expected=413)
        self.review([candidate(), candidate()], expected=400)
        headers = {'X-CSRF-Token': 'test-csrf'}
        with patch('app.routes.admin_station_ops.MAX_BODY_BYTES', 16 * 1024 * 1024):
            response = self.client.post(URL, data=' ' * (16 * 1024 * 1024 + 1), content_type='application/json', headers=headers)
            self.assertEqual(response.status_code, 413)
        for raw in ('{', '{"candidates":[],"candidates":[]}', '{"candidates":[NaN]}'):
            self.assertEqual(self.client.post(URL, data=raw, content_type='application/json', headers=headers).status_code, 400)
        self.assertEqual(self.client.post(URL, data='text', headers=headers).status_code, 415)
        self.assertEqual(self.client.post(URL, json={'candidates': [candidate()]}).status_code, 403)
        self.client.delete_cookie('admin_session')
        self.assertEqual(self.client.post(URL, json={'candidates': [candidate()]}, headers=headers).status_code, 401)

    def test_expired_revoked_non_admin_and_wrong_methods_never_touch_session(self):
        before = AdminSession.get_by_id(self.session.id).last_seen_at
        for method in ('GET', 'HEAD', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.client.open(URL, method=method, headers={'X-CSRF-Token': 'test-csrf'}).status_code, 405)
        self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, before)
        for changes in ({'expires_at': utcnow() - timedelta(seconds=1)}, {'revoked_at': utcnow()}):
            AdminSession.update(**changes).execute()
            self.review(expected=401)
            AdminSession.update(expires_at=utcnow() + timedelta(hours=1), revoked_at=None).execute()
        AdminUser.update(role='viewer').execute()
        self.review(expected=401)

    def test_no_write_including_session_clear_creation_and_relations(self):
        candidates = [candidate({'id': 'a', 'ski_areas': []}, clear_fields=['website_url']),
                      candidate({'name': 'New', 'slug': 'new', 'ski_areas': [{'id': self.area.id}]}, 'new')]
        first = self.review(candidates)
        decisions = [decide(row['client_ref'], row['operations']) for row in first['results']]
        before = {model: list(model.select().dicts()) for model in scan_fixtures.MODELS}
        original = self.database.execute_sql
        def observe(sql, params=None, *args, **kwargs):
            self.assertIn(sql.split()[0].upper(), {'SELECT', 'BEGIN', 'PRAGMA'}, sql)
            return original(sql, params, *args, **kwargs)
        with patch.object(self.database, 'execute_sql', side_effect=observe):
            self.review(candidates, decisions)
        self.assertEqual(before, {model: list(model.select().dicts()) for model in scan_fixtures.MODELS})

    def test_review_builder_cannot_write_and_read_guard_restores(self):
        with patch('app.services.station_ops.review._build_review', side_effect=lambda *args: Resort.update(name='forbidden').execute()):
            with self.assertLogs(self.app.logger, level='ERROR'):
                self.review(expected=500)
        self.assertTrue(self.database.is_closed())
        self.assertEqual(Resort.get_by_id('a').name, '  Alpha  ')
        self.assertEqual(self.database.execute_sql('PRAGMA query_only').fetchone()[0], 0)

    def test_batch_500_and_1000_recompute_compare_once_without_n_plus_one(self):
        for i in range(120):
            Resort.create(id=f'extra-{i}', name=f'Extra {i}', slug=f'extra-{i}', altitude_max_m=2200)
        def measure(size):
            candidates = [candidate({'id': 'a' if i == 0 else f'extra-{i % 120}', 'altitude_max_m': 2600, 'ski_areas': []}, str(i)) for i in range(size)]
            with patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as sql, \
                    patch('app.services.station_ops.review.compare_candidates', wraps=compare_service.compare_candidates) as compare:
                body = self.review(candidates)
            compare.assert_called_once()
            return sum(call.args[0].startswith('SELECT') for call in sql.call_args_list), body
        small, _ = measure(1)
        large, body = measure(500)
        maximum, _ = measure(1000)
        self.assertEqual((small, large, maximum), (6, 6, 6))
        self.assertEqual(body['summary']['pending_review'], 500)

    def test_creation_constraints_are_read_once_for_a_batch_of_500(self):
        def measure(size):
            candidates = [candidate({'name': f'New {i}', 'slug': f'new-{i}'}, str(i)) for i in range(size)]
            with patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as sql:
                body = self.review(candidates)
            return sum(call.args[0].startswith('SELECT') for call in sql.call_args_list), body
        self.assertEqual(measure(1)[0], 4)
        count, body = measure(500)
        self.assertEqual(count, 4)
        self.assertEqual(body['summary']['pending_operations'], 500)

    def test_partial_approval_summary_is_exact(self):
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600, 'website_url': 'https://other.example', 'is_active': False})]
        first = self.review(candidates)
        operations = first['results'][0]['operations']
        decisions = [{'client_ref': 'external-001', 'operations': {operations[0]['operation_id']: 'approved', operations[1]['operation_id']: 'rejected'}}]
        body = self.review(candidates, decisions)
        self.assertEqual(body['summary'], {'total_candidates': 1, 'no_action': 0, 'pending_review': 0, 'partially_approved': 1,
                                        'approved': 0, 'rejected': 0, 'blocked': 0, 'invalid': 0,
                                        'approved_operations': 1, 'pending_operations': 1, 'rejected_operations': 1})


class LegacyReviewTests(unittest.TestCase):
    legacy_regions = True
    real_km_fixture = True
    setUp = scan_fixtures.StationOpsTests.setUp
    review = ReviewTests.review

    def test_schema_drift_catalog_and_fractional_values_are_retained(self):
        self.database.execute_sql('DELETE FROM regions')
        self.database.execute_sql('UPDATE resort SET ski_area_km=? WHERE id=?', (45.75, 'a'))
        body = self.review([candidate({'id': 'a', 'ski_area_km': 46.5})])
        self.assertEqual(body['results'][0]['operations'][0]['existing'], 45.75)
        self.assertEqual(body['results'][0]['operations'][0]['preconditions']['expected_existing'], 45.75)
        self.assertEqual(body['catalog_findings'], [{'code': 'region_catalog_empty', 'severity': 'info', 'table': 'regions'}])
        self.assertTrue(any(f.get('field') == 'description_html' for f in body['schema_findings']))


class PureReviewTests(unittest.TestCase):
    def test_postgresql_repeatable_read_and_read_only_wrap_review_builder(self):
        database = PostgresqlDatabase('never-connected')
        with patch.object(Resort._meta, 'database', database), \
                patch.object(database, 'atomic', return_value=MagicMock()) as atomic, \
                patch.object(database, 'execute_sql') as sql:
            def compare(prepared, active, *, context):
                prepared[0]['result']['status'] = 'unchanged'
                return [], []
            def builder(*args):
                sql.assert_called_once_with('SET TRANSACTION READ ONLY')
                return {'verified': True}
            with patch('app.services.station_ops.compare._compare', side_effect=compare), \
                    patch('app.services.station_ops.review._build_review', side_effect=builder):
                self.assertEqual(review_candidates({'candidates': [candidate()]}), {'verified': True})
            atomic.assert_called_once_with(isolation_level='REPEATABLE READ')
        self.assertTrue(database.is_closed())

    def test_creation_metadata_is_parameterized_and_known_pg_checks_are_allowed(self):
        database = PostgresqlDatabase('never-connected')
        checks = ["CHECK (((page_layout_version)::text = ANY ((ARRAY['legacy'::character varying, 'v2'::character varying])::text[])))"]
        cursor = MagicMock()
        cursor.fetchall.return_value = [('id', True, None, 'character varying(255)', checks),
                                       ('name', True, None, 'text', checks), ('slug', True, None, 'character varying(255)', checks)]
        with patch.object(database, 'execute_sql', return_value=cursor) as sql:
            metadata = creation_constraints(database)
        sql.assert_called_once()
        self.assertEqual(sql.call_args.args[1], ('"resort"',))
        self.assertEqual(metadata['unknown_checks'], [])
        self.assertTrue(metadata['columns']['name']['required'])
        self.assertFalse(_known_layout_check('CHECK (altitude_max_m > 0)'))


if __name__ == '__main__':
    unittest.main()
