"""APPLY tests: isolated SQLite writes only; PostgreSQL connections forbidden."""
from copy import deepcopy
from datetime import date, timedelta
import json
import os
import unittest
import uuid
from unittest.mock import MagicMock, patch

from peewee import IntegrityError, OperationalError, PostgresqlDatabase

from app.datetime_utils import utcnow
from app.models.admin_session import AdminSession
from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from app.models.station_widgets import StationWidgets
from app.services.station_ops.apply import apply_candidates, commit_transaction, lock_targets
from app.services.station_ops.apply_contract import (ApplyError, MAX_APPLY_OPERATIONS,
    SCALAR_WRITE_FIELDS, execution_order, storage_value)
from app.services.station_ops.operations import fingerprint
from app.services.station_ops.review import review_candidates
import app.services.station_ops.apply as apply_service
import app.services.station_ops.compare as compare_service
import app.services.station_ops.apply_execution as execution_service
import test_station_ops as scan_fixtures

URL = '/api/admin/station-ops/apply'


def apply_fixture_setup(self):
    # Existing commit tests explicitly enable the switch in isolated fixtures.
    # Restore the original environment afterwards; never enable production.
    enabled = patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'})
    enabled.start()
    self.addCleanup(enabled.stop)
    scan_fixtures.StationOpsTests.setUp(self)


def candidate(data=None, ref='external-001', **extra):
    return {'client_ref': ref, 'data': data if data is not None else {'id': 'a', 'altitude_max_m': 2600}, **extra}


class ApplyTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = apply_fixture_setup

    def plan(self, candidates=None, approve=True, select=None):
        candidates = candidates if candidates is not None else [candidate()]
        proposed = review_candidates({'candidates': candidates})
        decisions = [{'client_ref': row['client_ref'], 'operations': {
            op['operation_id']: 'approved' for op in row['operations'] if approve and (select is None or select(op))}}
            for row in proposed['results']]
        reviewed = review_candidates({'candidates': candidates, 'decisions': decisions})
        return {'candidates': candidates, 'decisions': decisions, 'plan_fingerprint': reviewed['apply_plan']['plan_fingerprint']}

    def apply(self, payload=None, commit=False, expected=200):
        payload = deepcopy(payload if payload is not None else self.plan())
        if commit:
            payload.update(mode='commit', confirm_apply=True)
        response = self.client.post(URL, json=payload, headers={'X-CSRF-Token': 'test-csrf'})
        self.assertEqual(response.status_code, expected, response.get_json())
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.get_json()

    def business_state(self):
        return {model._meta.table_name: list(model.select().dicts()) for model in scan_fixtures.MODELS
                if model not in {scan_fixtures.AdminUser, AdminSession} and not (self.legacy_regions and model is scan_fixtures.Region)}

    def test_default_dry_run_validates_scalar_without_business_writes(self):
        before = self.business_state()
        body = self.apply()
        self.assertEqual(body['mode'], 'dry_run')
        self.assertTrue(body['would_apply'])
        self.assertEqual(body['operations_ready'], 1)
        self.assertEqual(body['summary'], {'approved_operations': 1, 'validated_operations': 1,
                                          'written_operations': 0, 'applied_operations': 0})
        self.assertEqual(body['operations'][0]['status'], 'ready')
        self.assertEqual(body['apply_version'], '1.0')
        uuid.UUID(body['execution_id'])
        self.assertEqual(before, self.business_state())

    def test_dry_run_guard_blocks_accidental_business_write(self):
        plan = self.plan()
        original = apply_service.validate_plan
        def illegal(*args):
            original(*args)
            Resort.update(name='forbidden').execute()
        with patch.object(apply_service, 'validate_plan', side_effect=illegal):
            with self.assertLogs(self.app.logger, level='ERROR'):
                self.apply(plan, expected=500)
        self.assertEqual(Resort.get_by_id('a').name, '  Alpha  ')
        self.assertFalse(self.database.in_transaction())
        self.assertEqual(self.database.execute_sql('PRAGMA query_only').fetchone()[0], 0)

    def test_commit_requires_literal_true_confirmation(self):
        plan = self.plan()
        for value in (None, False, 'true', 1, []):
            with self.subTest(value=value):
                payload = {**plan, 'mode': 'commit', 'confirm_apply': value}
                body = self.apply(payload, expected=400)
                self.assertEqual(body['error'], 'apply_confirmation_required')
                uuid.UUID(body['execution_id'])
        self.apply({**plan, 'mode': 'commit'}, expected=400)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_bad_fingerprint_409_zero_writes(self):
        plan = self.plan(); before = self.business_state()
        body = self.apply({**plan, 'plan_fingerprint': '0' * 64}, commit=True, expected=409)
        self.assertEqual(body['error'], 'plan_fingerprint_mismatch')
        self.assertEqual(body['provided_plan_fingerprint'], '0' * 64)
        self.assertEqual(body['recomputed_plan_fingerprint'], plan['plan_fingerprint'])
        self.assertEqual(before, self.business_state())

    def test_recomputes_compare_once_in_same_commit_transaction(self):
        plan = self.plan()
        original = compare_service._compare
        def calculate(*args, **kwargs):
            self.assertTrue(self.database.in_transaction())
            self.assertEqual(self.database.transaction_depth(), 1)
            self.assertEqual(self.database.execute_sql('PRAGMA query_only').fetchone()[0], 0)
            return original(*args, **kwargs)
        with patch.object(compare_service, '_compare', side_effect=calculate) as compared:
            self.apply(plan, commit=True)
        self.assertEqual(compared.call_count, 1)

    def test_client_operations_and_apply_plan_are_rejected(self):
        plan = self.plan()
        for key in ('operations', 'apply_plan', 'approve_all', 'compare_result'):
            with self.subTest(key=key):
                body = self.apply({**plan, key: [{'field': 'id', 'candidate': 'other'}]}, commit=True, expected=400)
                self.assertEqual(body['error'], 'invalid_apply_payload')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_stale_scalar_precondition_and_retry_are_409(self):
        plan = self.plan()
        Resort.update(altitude_max_m=2550).where(Resort.id == 'a').execute()
        body = self.apply(plan, commit=True, expected=409)
        self.assertEqual(body['error'], 'stale_precondition')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2550)
        fresh = self.plan(); self.apply(fresh, commit=True)
        after = Resort.get_by_id('a').updated_at
        self.assertEqual(self.apply(fresh, commit=True, expected=409)['error'], 'stale_precondition')
        self.assertEqual(Resort.get_by_id('a').updated_at, after)

    def test_preconditions_rechecked_after_target_locks(self):
        plan = self.plan()
        def intervene(database, operations):
            Resort.update(altitude_max_m=2550).where(Resort.id == 'a').execute()
        with patch.object(apply_service, 'lock_targets', side_effect=intervene):
            self.assertEqual(self.apply(plan, commit=True, expected=409)['error'], 'stale_precondition')
        # Even simulated intervening write was in this transaction, hence rollback.
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_set_scalar(self):
        payload = self.plan([candidate({'id': 'a', 'meta_title': 'New title'})])
        body = self.apply(payload, commit=True)
        self.assertEqual(body['operations'][0]['operation'], 'set')
        self.assertEqual(Resort.get_by_id('a').meta_title, 'New title')

    def test_replace_scalar_updated_at_post_verification_and_cache(self):
        payload = self.plan(); old = Resort.get_by_id('a').updated_at
        with patch.object(apply_service, 'verify_written', wraps=apply_service.verify_written) as verified, \
                patch.object(apply_service, 'invalidate_station') as invalidated, \
                patch.object(apply_service, 'bump_public_resorts_version') as bumped:
            body = self.apply(payload, commit=True)
        self.assertTrue(body['applied']); self.assertEqual(body['summary']['written_operations'], 1)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2600)
        self.assertGreater(Resort.get_by_id('a').updated_at, old)
        verified.assert_called_once(); invalidated.assert_called_once_with('alpha'); bumped.assert_called_once()

    def test_clear_explicit_sensitive(self):
        payload = self.plan([candidate({'id': 'a'}, clear_fields=['website_url'])])
        self.assertEqual(self.apply(payload, commit=True)['operations'][0]['operation'], 'clear')
        self.assertIsNone(Resort.get_by_id('a').website_url)

    def test_pending_clear_is_never_applied(self):
        payload = self.plan([candidate({'id': 'a'}, clear_fields=['website_url'])], approve=False)
        body = self.apply(payload, commit=True)
        self.assertFalse(body['applied']); self.assertEqual(body['reason'], 'no_approved_operations')
        self.assertEqual(Resort.get_by_id('a').website_url, 'https://example.com')

    def test_only_selected_approval_applied_pending_clear_remains(self):
        payload = self.plan([candidate({'id': 'a', 'altitude_max_m': 2600}, clear_fields=['website_url'])],
                            select=lambda op: op['operation'] != 'clear')
        body = self.apply(payload, commit=True)
        self.assertEqual(body['summary']['applied_operations'], 1)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2600)
        self.assertEqual(Resort.get_by_id('a').website_url, 'https://example.com')

    def test_non_nullable_physical_clear_refused(self):
        payload = self.plan([candidate({'id': 'a'}, clear_fields=['website_url'])])
        original = apply_service.validate_plan.__globals__['creation_constraints']
        def constraints(db):
            value = original(db); value['columns']['website_url']['required'] = True
            return value
        with patch('app.services.station_ops.apply_validation.creation_constraints', side_effect=constraints):
            self.assertEqual(self.apply(payload, commit=True, expected=400)['error'], 'field_not_nullable')
        self.assertEqual(Resort.get_by_id('a').website_url, 'https://example.com')

    def test_forbidden_clear_id_name_and_technical_fields(self):
        empty = fingerprint({'review_version': '1.0', 'operations': []})
        for field in ('id', 'name', 'is_active', 'updated_at', 'created_at'):
            body = self.apply({'candidates': [candidate({'id': 'a'}, clear_fields=[field])],
                               'plan_fingerprint': empty}, commit=True, expected=400)
            self.assertEqual(body['error'], 'invalid_apply_candidates')
        for field in ('created_at', 'updated_at', 'internal_secret'):
            self.apply({'candidates': [candidate({'id': 'a', field: 'arbitrary'})],
                        'plan_fingerprint': empty}, commit=True, expected=400)
        self.assertNotIn('id', SCALAR_WRITE_FIELDS); self.assertNotIn('slug', SCALAR_WRITE_FIELDS)

    def test_slug_replacement_outside_whitelist(self):
        payload = self.plan([candidate({'id': 'a', 'slug': 'renamed'})])
        self.assertEqual(self.apply(payload, commit=True, expected=400)['error'], 'field_not_writable')
        self.assertEqual(Resort.get_by_id('a').slug, 'alpha')

    def test_normalized_boolean_url_country_date_and_name(self):
        payload = self.plan([candidate({'id': 'a', 'is_active': 'false', 'country_code': ' ch ',
             'website_url': 'HTTPS://OTHER.EXAMPLE:443', 'season_open_date': '2026-12-15', 'name': '  Alpine Village  '})])
        self.apply(payload, commit=True)
        row = Resort.get_by_id('a')
        self.assertFalse(row.is_active); self.assertEqual(row.country_code, 'CH')
        self.assertEqual(row.website_url, 'https://other.example/')
        self.assertEqual(row.season_open_date, date(2026, 12, 15)); self.assertEqual(row.name, 'Alpine Village')

    def test_editorial_exact_whitespace_and_inline_media(self):
        text = ' <p>' + 'x' * 50000 + '</p> '
        # supplied raw content remains exact despite REVIEW's compact hashes
        media = 'data:image/png;base64,abcd'
        payload = self.plan([candidate({'id': 'a', 'description_html': text, 'logo_url': media})])
        body = self.apply(payload, commit=True)
        self.assertEqual(Resort.get_by_id('a').description_html, text)
        self.assertEqual(Resort.get_by_id('a').logo_url, media)
        self.assertLess(len(json.dumps(body)), 3000)

    def test_integer_physical_rejects_fractional_ski_km_without_truncation(self):
        payload = self.plan([candidate({'id': 'a', 'ski_area_km': 45.75})])
        self.assertEqual(self.apply(payload, commit=True, expected=400)['error'], 'physical_type_incompatible')
        self.assertEqual(Resort.get_by_id('a').ski_area_km, 20)

    def test_create_approved_uuid_defaults_and_no_collection_side_effects(self):
        payload = self.plan([candidate({'name': 'Brand New', 'slug': 'brand-new', 'country_code': 'fr'})])
        count = StationWidgets.select().count()
        dry = self.apply(payload); self.assertTrue(dry['would_apply'])
        self.assertIsNone(dry['operations'][0]['target_id'])
        body = self.apply(payload, commit=True)
        identity = body['operations'][0]['target_id']; uuid.UUID(identity)
        row = Resort.get_by_id(identity)
        self.assertEqual((row.name, row.slug, row.country_code), ('Brand New', 'brand-new', 'FR'))
        self.assertTrue(row.is_active); self.assertEqual(row.page_layout_version, 'legacy')
        self.assertIsNotNone(row.updated_at); self.assertEqual(StationWidgets.select().count(), count)

    def test_create_explicit_id_and_defaults_respected(self):
        payload = self.plan([candidate({'id': 'provided', 'name': 'New', 'slug': 'new', 'is_active': False,
                                      'page_layout_version': 'v2'})])
        self.assertEqual(self.apply(payload, commit=True)['operations'][0]['target_id'], 'provided')
        row = Resort.get_by_id('provided'); self.assertFalse(row.is_active)
        self.assertEqual(row.page_layout_version, 'v2')

    def test_unapproved_creation_no_writes_and_updated_at_unchanged(self):
        payload = self.plan([candidate({'name': 'New', 'slug': 'new'})], approve=False)
        before = self.business_state()
        self.assertFalse(self.apply(payload)['would_apply'])
        self.assertFalse(self.apply(payload, commit=True)['applied'])
        self.assertEqual(before, self.business_state())

    def test_creation_now_duplicate_stale_decision_409(self):
        payload = self.plan([candidate({'name': 'New', 'slug': 'new'})])
        Resort.create(id='intervening', name='New', slug='new')
        body = self.apply(payload, commit=True, expected=409)
        self.assertEqual(body['error'], 'stale_precondition')
        self.assertEqual(Resort.select().count(), 2)

    def test_creation_absence_rechecked_after_locks(self):
        payload = self.plan([candidate({'name': 'New', 'slug': 'new'})])
        with patch.object(apply_service, 'lock_targets', side_effect=lambda *args: Resort.create(id='intervening', name='New', slug='new')):
            self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'station_already_exists')
        self.assertEqual(Resort.select().count(), 1)

    def test_creation_constraint_metadata_changed_after_locks(self):
        payload = self.plan([candidate({'name': 'New', 'slug': 'new'})])
        original = apply_service.validate_plan
        def changed(operations, prepared, database, context):
            context['creation_constraints']['columns']['name']['required'] = False
            return original(operations, prepared, database, context)
        with patch.object(apply_service, 'validate_plan', side_effect=changed):
            self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'stale_precondition')
        self.assertEqual(Resort.select().count(), 1)

    def test_editorial_stale_after_locks_refused(self):
        payload = self.plan([candidate({'id': 'a', 'description_html': '<p>Approved</p>'})])
        with patch.object(apply_service, 'lock_targets', side_effect=lambda *args: Resort.update(description_html='Intervening').execute()):
            self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'stale_precondition')
        self.assertEqual(Resort.get_by_id('a').description_html, '<p>Actual fixture</p>')

    def test_two_same_logical_creations_different_slugs_refused(self):
        payload = self.plan([candidate({'name': 'New', 'slug': 'new-a', 'country_code': 'FR'}, 'first'),
                             candidate({'name': 'New', 'slug': 'new-b', 'country_code': 'FR'}, 'second')])
        self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'station_already_exists')
        self.assertEqual(Resort.select().count(), 1)

    def test_add_relation(self):
        area = SkiArea.create(name='Second', slug='second')
        payload = self.plan([candidate({'id': 'a', 'ski_areas': [{'id': self.area.id}, {'id': area.id}]})])
        self.apply(payload, commit=True)
        self.assertTrue(SkiAreaResort.select().where(SkiAreaResort.resort == 'a', SkiAreaResort.ski_area == area.id).exists())
        self.assertEqual(SkiArea.select().count(), 2)

    def test_remove_relation_and_pending_sensitive_removal(self):
        candidates = [candidate({'id': 'a', 'ski_areas': []})]
        self.assertFalse(self.apply(self.plan(candidates, approve=False), commit=True)['applied'])
        self.assertEqual(SkiAreaResort.select().count(), 1)
        self.apply(self.plan(candidates), commit=True)
        self.assertEqual(SkiAreaResort.select().count(), 0)
        self.assertEqual(SkiArea.select().count(), 1)

    def test_relation_stale_after_lock_zero_writes(self):
        payload = self.plan([candidate({'id': 'a', 'ski_areas': []})])
        with patch.object(apply_service, 'lock_targets', side_effect=lambda *args: SkiAreaResort.delete().execute()):
            self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'stale_precondition')
        self.assertEqual(SkiAreaResort.select().count(), 1)

    def test_create_then_relation_resolves_actual_id(self):
        payload = self.plan([candidate({'name': 'New', 'slug': 'new', 'ski_areas': [{'id': self.area.id}]})])
        body = self.apply(payload, commit=True)
        self.assertEqual([row['operation'] for row in body['operations']], ['create_station', 'add_ski_area_relation'])
        identity = body['operations'][0]['target_id']
        self.assertEqual(body['operations'][1]['target_id'], identity)
        self.assertTrue(SkiAreaResort.select().where(SkiAreaResort.resort == identity).exists())

    def test_new_client_ref_matching_existing_id_does_not_confuse_relation_targets(self):
        candidates = [candidate({'id': 'a', 'ski_areas': []}, 'old'),
                      candidate({'name': 'New', 'slug': 'new', 'ski_areas': [{'id': self.area.id}]}, 'a')]
        self.assertEqual(self.apply(self.plan(candidates), commit=True)['summary']['applied_operations'], 3)
        self.assertEqual(SkiAreaResort.select().count(), 1)

    def test_rollback_total_when_relation_insert_fails_after_station_update(self):
        area = SkiArea.create(name='Other', slug='other')
        payload = self.plan([candidate({'id': 'a', 'altitude_max_m': 2600, 'ski_areas': [{'id': area.id}]})])
        before = self.business_state(); original = execution_service.insert_rows
        def fail_after_update(db, model, rows):
            if model is SkiAreaResort:
                self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2600)
                raise IntegrityError('simulated foreign key conflict')
            return original(db, model, rows)
        with patch.object(execution_service, 'insert_rows', side_effect=fail_after_update):
            self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'constraint_conflict')
        self.assertEqual(before, self.business_state())

    def test_post_write_mismatch_rolls_back_no_cache_invalidation(self):
        payload = self.plan(); before = self.business_state()
        original = apply_service.verify_written
        def corrupt(validated, executed):
            Resort.update(altitude_max_m=999).where(Resort.id == 'a').execute()
            return original(validated, executed)
        with patch.object(apply_service, 'verify_written', side_effect=corrupt), patch.object(apply_service, 'invalidate_station') as cache:
            self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'post_write_verification_failed')
            cache.assert_not_called()
        self.assertEqual(before, self.business_state())

    def test_batch_mixed_creation_scalar_and_relations(self):
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600, 'ski_areas': []}, 'existing'),
                      candidate({'name': 'New', 'slug': 'new', 'ski_areas': [{'id': self.area.id}]}, 'new')]
        body = self.apply(self.plan(candidates), commit=True)
        self.assertEqual(body['summary']['applied_operations'], 4)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2600)
        self.assertEqual(SkiAreaResort.select().count(), 1)

    def test_auth_csrf_and_normal_session_touch(self):
        payload = self.plan(); before = AdminSession.get_by_id(self.session.id).last_seen_at
        response = self.client.post(URL, json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertGreater(AdminSession.get_by_id(self.session.id).last_seen_at, before)
        self.client.delete_cookie('admin_session')
        self.assertEqual(self.client.post(URL, json=payload, headers={'X-CSRF-Token': 'test-csrf'}).status_code, 401)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_limits_1000_and_real_16_mib(self):
        empty = fingerprint({'review_version': '1.0', 'operations': []})
        self.apply({'candidates': [candidate(ref=str(i)) for i in range(1001)], 'plan_fingerprint': empty}, expected=413)
        response = self.client.post(URL, data=b'x' * (16 * 1024 * 1024 + 1), content_type='application/json',
                                    headers={'X-CSRF-Token': 'test-csrf'})
        self.assertEqual(response.status_code, 413)

    def test_operation_limit_and_dry_run_large_plan(self):
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600, 'meta_title': 'New'}, str(i)) for i in range(501)]
        payload = self.plan(candidates)
        self.assertEqual(self.apply(payload)['operations_ready'], 1002)
        self.assertEqual(self.apply(payload, commit=True, expected=413)['error'], 'apply_operations_limit')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_duplicate_approved_effects_are_coalesced_without_double_writes(self):
        candidates = [candidate({'id': 'a', 'altitude_max_m': 2600, 'ski_areas': []}, str(i)) for i in range(5)]
        body = self.apply(self.plan(candidates), commit=True)
        self.assertEqual(body['summary']['applied_operations'], 10)
        self.assertEqual(SkiAreaResort.select().count(), 0)

    def test_batch_500_and_1000_reads_constant_writes_bounded(self):
        for i in range(1000):
            Resort.create(id='extra-' + str(i), name='Extra ' + str(i), slug='extra-' + str(i))
        stats = []
        for size in (500, 1000):
            candidates = [candidate({'id': 'extra-' + str(i), 'altitude_max_m': 2600 + size}, str(i)) for i in range(size)]
            payload = self.plan(candidates)
            with patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as sql:
                self.apply(payload, commit=True)
            commands = [call.args[0] for call in sql.call_args_list]
            selects = sum(command.startswith('SELECT') for command in commands)
            # Auth and field reads are constant; CASE updates use bounded chunks.
            updates = sum(command.startswith('UPDATE "resort"') for command in commands)
            stats.append((selects, updates))
        self.assertEqual(stats, [(7, 2), (7, 4)])

    def test_dry_run_with_clear_create_and_domains_only_selects_business(self):
        candidates = [candidate({'id': 'a', 'ski_areas': []}, 'old', clear_fields=['website_url']),
                      candidate({'name': 'New', 'slug': 'new', 'ski_areas': [{'id': self.area.id}]}, 'new')]
        payload = self.plan(candidates); before = self.business_state()
        with patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as sql:
            self.apply(payload)
        mutating = [call.args[0] for call in sql.call_args_list if call.args[0].split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}]
        self.assertTrue(all(query.startswith('UPDATE "admin_sessions"') for query in mutating), mutating)
        self.assertEqual(before, self.business_state())

    def test_audit_no_secret_content_and_execution_id(self):
        payload = self.plan([candidate({'id': 'a', 'description_html': '<p>SECRET BODY</p>'},
                                     field_sources={'description_html': [{'url': 'https://example.com/PRIVATE-URL'}]})])
        with self.assertLogs('station_ops.apply.audit', level='INFO') as logs:
            body = self.apply(payload, commit=True)
        text = '\n'.join(logs.output)
        self.assertIn(body['execution_id'], text); self.assertIn('committed', text)
        for secret in ('SECRET BODY', 'PRIVATE-URL', 'test-token', 'test-csrf', 'session:'):
            self.assertNotIn(secret, text)

    def test_audit_failure_before_commit_rolls_back(self):
        payload = self.plan(); before = self.business_state()
        original = apply_service._audit
        def fail(*args, **kwargs):
            if args[3] == 'verified_pending_commit':
                raise RuntimeError('audit unavailable')
            return original(*args, **kwargs)
        with patch.object(apply_service, '_audit', side_effect=fail):
            with self.assertLogs(self.app.logger, level='ERROR'):
                self.apply(payload, commit=True, expected=500)
        self.assertEqual(before, self.business_state())

    def test_cache_failure_after_commit_does_not_claim_rollback(self):
        with patch.object(apply_service, 'invalidate_station', side_effect=RuntimeError('cache unavailable')):
            body = self.apply(commit=True)
        self.assertTrue(body['applied']); self.assertEqual(body['cache_warning'], 'post_commit_invalidation_failed')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2600)

    def test_payload_modes_decisions_and_json_validation(self):
        payload = self.plan()
        for overrides in ({'mode': 'write'}, {'mode': []}, {'plan_fingerprint': 'bad'}, {'decisions': {}},
                          {'decisions': [{'client_ref': 'external-001', 'operations': {'unknown': 'approve_all'}}]}):
            self.apply({**payload, **overrides}, expected=400)
        self.apply({**payload, 'decisions': [{'client_ref': 'external-001', 'operations': {'unknown': 'approved'}}]}, expected=409)
        for body in ('{', '{"candidates":[],"candidates":[]}'):
            self.assertEqual(self.client.post(URL, data=body, content_type='application/json',
                                             headers={'X-CSRF-Token': 'test-csrf'}).status_code, 400)
        self.assertEqual(self.client.post(URL, data='text', headers={'X-CSRF-Token': 'test-csrf'}).status_code, 415)

    def test_unsupported_collections_remain_blocked_without_writes(self):
        candidates = [candidate({'id': 'a', 'pistes': [], 'altitude_max_m': 2600})]
        before = self.business_state()
        body = self.apply(self.plan(candidates), commit=True)
        self.assertFalse(body['applied']); self.assertEqual(before, self.business_state())

    def test_same_operation_ids_with_changed_provenance_mismatch(self):
        payload = self.plan()
        payload['candidates'][0]['field_sources'] = {'altitude_max_m': [{'url': 'https://example.com/new-source'}]}
        self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'plan_fingerprint_mismatch')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_dependency_missing_approval_refused_before_writes(self):
        payload = self.plan([candidate({'name': 'New', 'slug': 'new', 'ski_areas': [{'id': self.area.id}]})])
        proposed = review_candidates({'candidates': payload['candidates']})
        create = next(op for op in proposed['results'][0]['operations'] if op['operation'] == 'create_station')
        del payload['decisions'][0]['operations'][create['operation_id']]
        self.assertEqual(self.apply(payload, commit=True, expected=409)['error'], 'stale_precondition')
        self.assertEqual(Resort.select().count(), 1)

    def test_batch_500_creations_bulk_inserts_and_constant_reads(self):
        candidates = [candidate({'name': 'New ' + str(i), 'slug': 'new-' + str(i)}, str(i)) for i in range(500)]
        payload = self.plan(candidates)
        with patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as sql, \
                patch.object(apply_service, '_audit'), patch.object(apply_service, 'invalidate_station'):
            body = self.apply(payload, commit=True)
        selects = sum(call.args[0].startswith('SELECT') for call in sql.call_args_list)
        inserts = sum(call.args[0].startswith('INSERT INTO "resort"') for call in sql.call_args_list)
        self.assertEqual((selects, inserts), (6, 4))
        self.assertEqual(body['summary']['applied_operations'], 500)
        self.assertEqual(Resort.select().count(), 501)

    def test_no_approved_operations_no_business_mutations_or_cache(self):
        payload = self.plan(approve=False)
        with patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as sql, \
                patch.object(apply_service, 'invalidate_station') as cache:
            body = self.apply(payload, commit=True)
        self.assertFalse(body['applied']); cache.assert_not_called()
        commands = [call.args[0] for call in sql.call_args_list]
        self.assertFalse(any(command.startswith(('UPDATE "resort"', 'INSERT', 'DELETE')) for command in commands))

    def test_unexpected_error_returns_execution_id_and_rolls_back(self):
        payload = self.plan()
        with patch.object(apply_service, 'verify_written', side_effect=RuntimeError('private SQL parameters')), \
                self.assertLogs(self.app.logger, level='ERROR') as logs:
            body = self.apply(payload, commit=True, expected=500)
        uuid.UUID(body['execution_id'])
        self.assertNotIn('private SQL parameters', '\n'.join(logs.output))
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_sensitive_and_unknown_internal_operations_defense(self):
        # Client cannot reach this seam. Verify the executor also rejects a
        # future server-side builder bug before performing business writes.
        payload = self.plan([candidate({'id': 'a'}, clear_fields=['website_url'])])
        original = apply_service.validate_plan
        def mutate(operations, *args):
            operations[0]['sensitive'] = False
            return original(operations, *args)
        with patch.object(apply_service, 'validate_plan', side_effect=mutate):
            self.assertEqual(self.apply(payload, commit=True, expected=400)['error'], 'sensitive_approval_required')
        payload = self.plan()
        def unsupported(operations, *args):
            operations[0]['operation'] = 'delete_station'
            return original(operations, *args)
        with patch.object(apply_service, 'validate_plan', side_effect=unsupported):
            self.assertEqual(self.apply(payload, commit=True, expected=400)['error'], 'unsupported_operation')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)


class ApplyCommitSwitchTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = apply_fixture_setup
    plan = ApplyTests.plan
    apply = ApplyTests.apply
    business_state = ApplyTests.business_state

    def disabled(self, value):
        payload = self.plan()
        before = self.business_state()
        with patch.dict(os.environ):
            if value is None:
                os.environ.pop('STATION_OPS_APPLY_COMMIT_ENABLED', None)
            else:
                os.environ['STATION_OPS_APPLY_COMMIT_ENABLED'] = value
            with patch.object(apply_service, 'commit_transaction') as transaction, \
                    patch.object(apply_service, 'review_in_transaction') as recompute, \
                    patch.object(apply_service, 'execute_plan') as execute, \
                    patch.object(self.database, 'execute_sql', wraps=self.database.execute_sql) as sql:
                body = self.apply(payload, commit=True, expected=403)
            transaction.assert_not_called(); recompute.assert_not_called(); execute.assert_not_called()
            commands = [call.args[0] for call in sql.call_args_list]
            self.assertFalse(any(command.startswith(('BEGIN', 'INSERT', 'DELETE', 'UPDATE "resort"')) for command in commands))
        self.assertEqual(body, {'error': 'station_ops_apply_commit_disabled',
                               'message': 'Station Ops APPLY commit is disabled on this environment.'})
        self.assertEqual(before, self.business_state())

    def test_absent_variable_commit_disabled_before_transaction(self):
        self.disabled(None)

    def test_false_values_commit_disabled_without_business_writes(self):
        for value in ('false', '0', 'no', 'off', ' FALSE '):
            with self.subTest(value=value):
                self.disabled(value)

    def test_empty_and_unknown_values_fail_closed_without_disclosure(self):
        for value in ('', 'unknown-private-value', '2'):
            with self.subTest(value=value):
                self.disabled(value)

    def test_enabled_commit_still_requires_confirmation(self):
        payload = {**self.plan(), 'mode': 'commit'}
        with patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'}), \
                patch.object(apply_service, 'commit_transaction') as transaction:
            self.assertEqual(self.apply(payload, expected=400)['error'], 'apply_confirmation_required')
            transaction.assert_not_called()

    def test_enabled_values_and_confirmation_allow_commit(self):
        for i, value in enumerate(('true', '1', 'yes', 'on', ' TRUE ')):
            with self.subTest(value=value), patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': value}):
                payload = self.plan([candidate({'id': 'a', 'altitude_max_m': 2600 + i})])
                self.assertTrue(self.apply(payload, commit=True)['applied'])
                self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2600 + i)

    def test_absent_variable_dry_run_works(self):
        payload = self.plan(); before = self.business_state()
        with patch.dict(os.environ):
            os.environ.pop('STATION_OPS_APPLY_COMMIT_ENABLED', None)
            self.assertTrue(self.apply(payload)['would_apply'])
        self.assertEqual(before, self.business_state())

    def test_disabled_values_do_not_block_dry_run(self):
        payload = self.plan(); before = self.business_state()
        for value in ('false', '0', 'no', 'off', 'unknown-private-value'):
            with self.subTest(value=value), patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': value}):
                self.assertTrue(self.apply(payload)['would_apply'])
        self.assertEqual(before, self.business_state())


class LegacyApplyTests(unittest.TestCase):
    legacy_regions = True
    real_km_fixture = True
    setUp = apply_fixture_setup
    plan = ApplyTests.plan
    apply = ApplyTests.apply

    def test_real_ski_area_km_not_coerced_through_peewee_integer(self):
        body = self.apply(self.plan([candidate({'id': 'a', 'ski_area_km': '45.75'})]), commit=True)
        value = self.database.execute_sql('SELECT ski_area_km FROM resort WHERE id=?', ('a',)).fetchone()[0]
        self.assertEqual(value, 45.75)
        self.assertTrue(any(row['code'] == 'model_column_type_mismatch' for row in body['schema_findings']))

    def test_create_with_real_fractional_kilometres_and_legacy_regions(self):
        body = self.apply(self.plan([candidate({'name': 'New', 'slug': 'new', 'ski_area_km': 45.75})]), commit=True)
        identity = body['operations'][0]['target_id']
        self.assertEqual(self.database.execute_sql('SELECT ski_area_km FROM resort WHERE id=?', (identity,)).fetchone()[0], 45.75)


class PureApplyTests(unittest.TestCase):
    def setUp(self):
        enabled = patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'})
        enabled.start()
        self.addCleanup(enabled.stop)

    def test_unknown_dependency_and_cycle_refused(self):
        for operations in ([{'operation_id': 'a', 'depends_on': ['unknown']}],
                           [{'operation_id': 'a', 'depends_on': ['b']}, {'operation_id': 'b', 'depends_on': ['a']}]):
            with self.assertRaises(ApplyError) as raised:
                execution_order(operations)
            self.assertEqual(raised.exception.code, 'invalid_dependencies')

    def test_dependency_order_not_hash_order(self):
        creation = {'operation_id': 'z', 'operation': 'create_station', 'client_ref': 'new'}
        link = {'operation_id': 'a', 'operation': 'add_ski_area_relation', 'client_ref': 'new', 'depends_on': ['z']}
        self.assertEqual(execution_order([link, creation]), [creation, link])

    def test_physical_types_precision_ranges_lengths_and_null(self):
        def stored(field, value, physical, required=False, **extra):
            return storage_value(field, value, {'type': physical, 'required': required, **extra})
        self.assertEqual(stored('ski_area_km', '45.75', 'double precision'), 45.75)
        self.assertEqual(stored('ski_area_km', '45.75', 'numeric(6,2)'), 45.75)
        for field, value, physical in (('altitude_max_m', '32768', 'smallint'), ('ski_area_km', '1.234', 'numeric(6,2)'),
                                       ('ski_area_km', '10000', 'numeric(6,2)'), ('name', 'too long', 'varchar(2)'),
                                       ('name', 'nul\x00byte', 'text'), ('name', 'x', 'jsonb')):
            with self.subTest(field=field, physical=physical), self.assertRaises(ApplyError):
                stored(field, value, physical)
        with self.assertRaises(ApplyError):
            stored('ski_area_km', '45.2', 'real', postgresql=True)
        with self.assertRaises(ApplyError):
            stored('website_url', None, 'text', required=True)

    def test_pg_transaction_lock_tables_before_data_snapshot(self):
        database = PostgresqlDatabase('never-connected')
        with patch.object(database, 'atomic', return_value=MagicMock()) as atomic, patch.object(database, 'execute_sql') as sql:
            with commit_transaction(database):
                self.assertEqual(len(sql.call_args_list), 4)
                self.assertIn('LOCK TABLE "resort" IN SHARE ROW EXCLUSIVE MODE', sql.call_args_list[2].args[0])
                self.assertIn('LOCK TABLE "ski_area_resorts"', sql.call_args_list[3].args[0])
                self.assertFalse(any('READ ONLY' in call.args[0] for call in sql.call_args_list))
            atomic.assert_called_once_with(isolation_level='SERIALIZABLE')
        self.assertTrue(database.is_closed())

    def test_pg_for_update_queries_batched_parameterized_and_ordered(self):
        database = PostgresqlDatabase('never-connected')
        operations = [{'operation': 'replace', 'target_id': identity} for identity in ('b', 'a')] + [
                      {'operation': 'remove_ski_area_relation', 'target_id': 'a', 'related_id': 2}]
        calls = []
        def execute(query, **kwargs):
            calls.append(query.sql())
            cursor = MagicMock(); cursor.fetchone.return_value = None; cursor.description = []
            return cursor
        with database.bind_ctx([Resort, SkiArea, SkiAreaResort], bind_refs=False, bind_backrefs=False), \
                patch.object(database, 'execute', side_effect=execute):
            lock_targets(database, operations)
        self.assertEqual(len(calls), 3)
        for sql, params in calls:
            self.assertIn('FOR UPDATE', sql); self.assertIn('ORDER BY', sql); self.assertTrue(params)
        self.assertEqual(calls[0][1], ['a', 'b'])
        self.assertTrue(database.is_closed())

    def test_postgresql_read_only_dry_run_and_conflict_mapping(self):
        database = PostgresqlDatabase('never-connected')
        payload = {'candidates': [candidate()], 'plan_fingerprint': '0' * 64}
        class DriverConflict(Exception):
            pgcode = '40001'
        with patch.object(Resort._meta, 'database', database), \
                patch.object(database, 'atomic', return_value=MagicMock()) as atomic, \
                patch.object(database, 'execute_sql') as sql, \
                patch.object(apply_service, 'review_in_transaction', side_effect=OperationalError(DriverConflict('serialization'))):
            with self.assertRaises(ApplyError) as raised:
                apply_candidates(payload)
            self.assertEqual(raised.exception.code, 'concurrent_conflict')
            uuid.UUID(raised.exception.execution_id)
            sql.assert_called_once_with('SET TRANSACTION READ ONLY')
            atomic.assert_called_once_with(isolation_level='REPEATABLE READ')

    def test_pg_commit_exit_failure_no_success_response(self):
        database = PostgresqlDatabase('never-connected')
        payload = {'candidates': [candidate()], 'plan_fingerprint': '0' * 64, 'mode': 'commit', 'confirm_apply': True}
        for state in ('40001', '40P01', '55P03', '57014'):
            driver = Exception('driver conflict'); driver.pgcode = state
            transaction = MagicMock(); transaction.__exit__.side_effect = OperationalError(driver)
            with patch.object(Resort._meta, 'database', database), \
                    patch.object(database, 'atomic', return_value=transaction), patch.object(database, 'execute_sql'), \
                    patch.object(apply_service, 'review_in_transaction', return_value=({}, [])):
                with self.assertRaises(ApplyError) as raised:
                    apply_candidates(payload)
                self.assertEqual(raised.exception.code, 'concurrent_conflict')
                uuid.UUID(raised.exception.execution_id)


if __name__ == '__main__':
    unittest.main()
