"""MCP uses official protocol transports and isolated Station Ops fixtures only."""
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch, MagicMock

import anyio
from flask import Flask
from starlette.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx

from app.mcp.http import create_mcp_application, MCP_PATH
from app.mcp.tools import invoke, tool_definitions, MACHINE_ACTOR
from app.models.admin_session import AdminSession
from app.models.resort import Resort
from app.services.station_ops import apply, scan, compare, review
from app.services.station_ops.candidates import MAX_BODY_BYTES
from app.services.station_ops.research_schema import research_schema
import test_station_ops as fixtures
from test_station_ops_research import batch

TOKEN = 'test-private-mcp-token'
AUTH = {'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json, text/event-stream'}


def candidate(data=None):
    return {'client_ref': 'external-001', 'data': data if data is not None else {'id': 'a', 'altitude_max_m': 2600}}


class McpToolsTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False
    setUp = fixtures.StationOpsTests.setUp

    def call(self, name, arguments=None, error=False):
        result = invoke(self.app, name, arguments if arguments is not None else {})
        self.assertEqual(result.isError, error, result.structuredContent)
        self.assertEqual(json.loads(result.content[0].text), result.structuredContent)
        return result.structuredContent

    def plan(self, data=None):
        envelope = {'candidates': [candidate(data)]}
        first = self.call('review', envelope)
        decisions = [{'client_ref': 'external-001', 'operations': {op['operation_id']: 'approved'
                     for op in first['results'][0]['operations']}}]
        second = self.call('review', {**envelope, 'decisions': decisions})
        return {**envelope, 'decisions': decisions, 'plan_fingerprint': second['apply_plan']['plan_fingerprint']}

    def read_guard(self):
        original = self.database.execute_sql
        def execute(sql, *args, **kwargs):
            self.assertIn(sql.lstrip().split()[0].upper(), ('SELECT', 'PRAGMA', 'BEGIN', 'COMMIT', 'ROLLBACK'), sql)
            return original(sql, *args, **kwargs)
        return patch.object(self.database, 'execute_sql', side_effect=execute)

    def test_tools_exactly_seven_with_write_annotations(self):
        tools = {t.name: t for t in tool_definitions()}
        self.assertEqual(set(tools), {'station_scan', 'research_contract', 'research_validate', 'compare', 'review', 'apply_dry_run', 'apply_commit'})
        self.assertTrue(all(t.annotations.readOnlyHint for k, t in tools.items() if k != 'apply_commit'))
        self.assertFalse(tools['apply_commit'].annotations.readOnlyHint)
        self.assertTrue(tools['apply_commit'].annotations.destructiveHint)

    def test_scan_country_read_only_and_same_snapshot(self):
        with self.read_guard():
            result = self.call('station_scan', {'country_code': 'FR'})
        self.assertEqual(result['scope']['filters'], {'country_code': 'FR'})
        self.assertTrue(result['stations'])
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_research_valid_and_invalid_without_sql(self):
        with patch.object(self.database, 'execute_sql', side_effect=AssertionError('No SQL')):
            result = self.call('research_validate', batch())
            self.assertTrue(result['valid'])
            bad = self.call('research_validate', {'unknown': True}, error=True)
            self.assertEqual((bad['valid'], bad['status']), (False, 400))
            self.assertTrue(bad['errors'])

    def test_compare_unchanged_and_changed_read_only(self):
        for data, status in [({'id': 'a'}, 'unchanged'), ({'id': 'a', 'altitude_max_m': 2600}, 'changes_detected')]:
            with self.read_guard():
                result = self.call('compare', {'candidates': [candidate(data)]})
            self.assertEqual(result['results'][0]['status'], status)

    def test_review_pending_and_approved_without_session_touch(self):
        before = AdminSession.get_by_id(self.session.id).last_seen_at
        with self.read_guard():
            envelope = {'candidates': [candidate()]}
            pending = self.call('review', envelope)
            self.assertEqual(pending['results'][0]['status'], 'pending_review')
            op = pending['results'][0]['operations'][0]
            approved = self.call('review', {**envelope, 'decisions': [{'client_ref': 'external-001', 'operations': {op['operation_id']: 'approved'}}]})
        self.assertEqual(approved['results'][0]['status'], 'approved')
        self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, before)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_dry_run_forced_no_writes_and_no_mutation_of_arguments(self):
        plan = self.plan()
        before = deepcopy(plan)
        with self.read_guard(), patch.object(apply, 'apply_candidates', wraps=apply.apply_candidates) as applied:
            result = self.call('apply_dry_run', plan)
        self.assertEqual(result['mode'], 'dry_run')
        self.assertEqual(applied.call_args.kwargs['actor_id'], MACHINE_ACTOR)
        self.assertEqual(plan, before)
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_dry_run_rejects_commit_and_confirm_and_arbitrary_operations(self):
        plan = self.plan()
        for extra in ({'mode': 'commit'}, {'confirm_apply': True}, {'operations': []}, {'apply_plan': {}}, {'approve_all': True}):
            with patch.object(apply, 'apply_candidates') as applied:
                result = self.call('apply_dry_run', {**plan, **extra}, error=True)
                applied.assert_not_called()
            self.assertEqual(result['code'], 'invalid_tool_arguments')

    def test_commit_disabled_before_any_business_transaction(self):
        plan = {**self.plan(), 'confirm_apply': True}
        for value in ('', 'false', '0', 'no', 'off'):
            with patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': value}), \
                 patch.object(apply, 'commit_transaction', side_effect=AssertionError('No transaction')), \
                 patch.object(self.database, 'execute_sql', side_effect=AssertionError('No SQL')):
                result = self.call('apply_commit', plan, error=True)
            self.assertEqual((result['code'], result['status']), ('station_ops_apply_commit_disabled', 403))

    def test_commit_missing_switch_disabled(self):
        plan = {**self.plan(), 'confirm_apply': True}
        with patch.dict(os.environ, {}, clear=True):
            result = self.call('apply_commit', plan, error=True)
        self.assertEqual(result['code'], 'station_ops_apply_commit_disabled')

    def test_commit_confirmation_false(self):
        with patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'}):
            result = self.call('apply_commit', {**self.plan(), 'confirm_apply': False}, error=True)
        self.assertEqual(result['code'], 'apply_confirmation_required')

    def test_commit_fingerprint_mismatch_rolls_back(self):
        before = Resort.get_by_id('a').altitude_max_m
        with patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'}):
            result = self.call('apply_commit', {**self.plan(), 'confirm_apply': True, 'plan_fingerprint': '0' * 64}, error=True)
        self.assertEqual((result['code'], result['status']), ('plan_fingerprint_mismatch', 409))
        self.assertTrue(result['execution_id'])
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, before)

    def test_commit_authorized_only_through_existing_engine_isolated_sqlite(self):
        with patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'}), \
             patch.object(apply, 'commit_transaction', wraps=apply.commit_transaction) as transaction, \
             patch.object(apply, 'verify_written', wraps=apply.verify_written) as verification:
            result = self.call('apply_commit', {**self.plan(), 'confirm_apply': True})
        self.assertTrue(result['applied'])
        transaction.assert_called_once()
        verification.assert_called_once()
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2600)

    def test_commit_requires_explicit_approval(self):
        plan = self.plan()
        for decisions in ([], [{'client_ref': 'external-001', 'operations': {'old': 'rejected'}}],
                          [{'client_ref': 'external-001', 'operations': {'old': 'pending'}}]):
            with patch.object(apply, 'apply_candidates') as applied:
                result = self.call('apply_commit', {**plan, 'confirm_apply': True, 'decisions': decisions}, error=True)
                applied.assert_not_called()
            self.assertEqual(result['code'], 'explicit_review_approval_required')

    def test_forged_approval_cannot_bypass_review(self):
        plan = self.plan()
        plan['decisions'] = [{'client_ref': 'external-001', 'operations': {'forged': 'approved'}}]
        with patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'}):
            result = self.call('apply_commit', {**plan, 'confirm_apply': True}, error=True)
        self.assertEqual(result['status'], 409)
        self.assertTrue(result['issues'])
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_research_contract_canonical_no_sql(self):
        with patch.object(self.database, 'execute_sql', side_effect=AssertionError('No SQL')):
            contract = self.call('research_contract')
        self.assertEqual(contract['json_schema'], research_schema())
        self.assertEqual(contract['research_version'], '1.0')

    def test_batch_limit_preserves_413(self):
        result = self.call('compare', {'candidates': [candidate() for _ in range(1001)]}, error=True)
        self.assertEqual(result['status'], 413)

    def test_payload_limit_preserves_413(self):
        result = self.call('compare', {'candidates': [candidate({'id': 'a', 'description_html': 'x' * MAX_BODY_BYTES})]}, error=True)
        self.assertEqual(result['status'], 413)

    def test_invalid_scan_filter_keeps_logical_400(self):
        for args in ({'is_active': 'maybe'}, {'ski_area_id': '0'}, {'country_code': ''}):
            result = self.call('station_scan', args, error=True)
            self.assertEqual((result['code'], result['status']), ('invalid_filters', 400))

    def test_unknown_tool_and_unknown_filters_refused(self):
        self.assertEqual(self.call('shell', {}, error=True)['code'], 'unknown_tool')
        self.assertEqual(self.call('station_scan', {'sql': 'SELECT anything'}, error=True)['code'], 'invalid_tool_arguments')

    def test_safe_error_and_logging_no_editorial_content_or_exception(self):
        secret = 'do-not-log-this-secret'
        with patch.object(compare, 'compare_candidates', side_effect=RuntimeError('SELECT ' + secret)), \
             self.assertLogs('station_ops.mcp.audit', level='INFO') as logs:
            result = self.call('compare', {'candidates': [candidate({'id': 'a', 'description_html': secret})]}, error=True)
        self.assertEqual(result['status'], 500)
        self.assertNotIn(secret, json.dumps(result) + '\n'.join(logs.output))
        event = json.loads(logs.records[0].getMessage())
        self.assertEqual((event['tool'], event['result'], event['candidate_count']), ('compare', 'error', 1))

    def test_connection_opened_by_worker_is_closed_on_success_or_error(self):
        for failure in (False, True):
            database = MagicMock()
            database.is_closed.side_effect = [True, False]
            service = patch.object(scan, 'scan_stations', side_effect=RuntimeError('internal') if failure else None,
                                   return_value={'stations': []})
            with patch.object(Resort._meta, 'database', database), service:
                self.call('station_scan', {}, error=failure)
            database.close.assert_called_once()
            database.atomic.assert_not_called()

    def test_postgres_transaction_guarantees_are_delegated_unchanged(self):
        # The facade has no transaction of its own; existing PG-specific tests
        # cover REPEATABLE READ/READ ONLY, SERIALIZABLE/locks and rollback.
        with patch.object(scan, 'scan_stations', return_value={'stations': []}) as service, \
             patch.object(self.database, 'atomic', side_effect=AssertionError('No wrapper transaction')):
            self.call('station_scan', {'country_code': 'FR'})
        service.assert_called_once_with({'country_code': 'FR'})


class McpTransportTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'STATION_OPS_MCP_TOKEN': TOKEN})
        env.start(); self.addCleanup(env.stop)
        self.flask_app = Flask('mcp-test')
        self.flask_app.config['TESTING'] = True
        self.flask_app.add_url_rule('/existing', view_func=lambda: {'existing': True})
        self.asgi = create_mcp_application(self.flask_app)
        self.client = TestClient(self.asgi, base_url='https://localhost')
        self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)

    def rpc(self, method, params=None, headers=None, identifier=1):
        return self.client.post(MCP_PATH, json={'jsonrpc': '2.0', 'id': identifier, 'method': method,
                               **({'params': params} if params is not None else {})}, headers=headers if headers is not None else AUTH)

    def test_initialize_discovery_and_call_official_sdk(self):
        initialized = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1.0'}})
        self.assertEqual(initialized.status_code, 200, initialized.text)
        self.assertEqual(initialized.json()['result']['serverInfo']['name'], 'Snow Explorer Content Ops')
        tools = self.rpc('tools/list').json()['result']['tools']
        self.assertEqual(len(tools), 7)
        contract = self.rpc('tools/call', {'name': 'research_contract', 'arguments': {}}).json()['result']
        self.assertFalse(contract['isError'])
        self.assertEqual(contract['structuredContent']['research_version'], '1.0')

    def test_missing_wrong_correct_token_and_unconfigured(self):
        self.assertEqual(self.rpc('tools/list', headers={'Accept': AUTH['Accept']}).status_code, 401)
        self.assertEqual(self.rpc('tools/list', headers={**AUTH, 'Authorization': 'Bearer wrong'}).status_code, 401)
        self.assertEqual(self.rpc('tools/list').status_code, 200)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.rpc('tools/list').status_code, 503)

    def test_query_token_never_authenticates(self):
        response = self.client.post(MCP_PATH + '?token=' + TOKEN, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, headers={'Accept': AUTH['Accept']})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(TOKEN, response.text)
        response = self.client.get(MCP_PATH + '?token=' + TOKEN, headers=AUTH)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(TOKEN, response.text)

    def test_all_methods_and_namespace_require_auth(self):
        for method in ('get', 'delete', 'options'):
            self.assertEqual(getattr(self.client, method)(MCP_PATH).status_code, 401)
        self.assertEqual(self.client.get('/mcp/station-ops/').status_code, 401)
        self.assertEqual(self.client.get('/mcp/unknown').status_code, 401)

    def test_https_required_outside_testing(self):
        self.flask_app.config['TESTING'] = False
        response = self.client.post('http://localhost' + MCP_PATH, headers=AUTH, json={})
        self.assertEqual(response.status_code, 403)
        # An untrusted header does not promote plain HTTP to HTTPS.
        response = self.client.post('http://localhost' + MCP_PATH, headers={**AUTH, 'X-Forwarded-Proto': 'https'}, json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rpc('tools/list').status_code, 200)

    def test_token_never_logged_or_returned(self):
        with self.assertLogs('station_ops.mcp.audit', level='INFO') as logs:
            self.rpc('tools/list', headers={**AUTH, 'Authorization': 'Bearer wrong'})
            result = self.rpc('tools/call', {'name': 'research_contract', 'arguments': {}})
        self.assertNotIn(TOKEN, '\n'.join(logs.output) + result.text)
        self.assertNotIn('wrong', '\n'.join(logs.output))

    def test_protocol_tool_error_structured_not_http_500(self):
        response = self.rpc('tools/call', {'name': 'apply_dry_run', 'arguments': {'mode': 'commit'}})
        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertTrue(result['isError'])
        self.assertEqual(result['structuredContent']['status'], 400)

    def test_strict_json_and_size_limit(self):
        for body in ('{"jsonrpc":"2.0","jsonrpc":"2.0"}', '{bad', '{"id":NaN}', '{"id":1e999}'):
            response = self.client.post(MCP_PATH, content=body, headers={**AUTH, 'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 400)
        response = self.client.post(MCP_PATH, content='x' * (MAX_BODY_BYTES + 1), headers={**AUTH, 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    def test_dns_rebinding_guard(self):
        response = self.client.post('https://untrusted.example' + MCP_PATH, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, headers=AUTH)
        self.assertIn(response.status_code, (400, 421))

    def test_existing_flask_route_still_works_without_mcp_token(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get('/existing')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'existing': True})

    def test_standard_mcp_client_can_initialize_list_and_call(self):
        # Official client and server communicate through an ASGI HTTP transport,
        # not a handcrafted JSON-RPC client. No sockets or production access.
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.asgi), base_url='https://localhost', headers=AUTH) as http:
                async with streamable_http_client('https://localhost' + MCP_PATH, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        result = await session.initialize()
                        self.assertEqual(result.serverInfo.name, 'Snow Explorer Content Ops')
                        tools = await session.list_tools()
                        self.assertEqual(len(tools.tools), 7)
                        contract = await session.call_tool('research_contract', {})
                        self.assertFalse(contract.isError)
                        self.assertEqual(contract.structuredContent['json_schema'], research_schema())
        anyio.run(check)


class McpRuntimeIntegrationTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False

    def setUp(self):
        import uuid
        original = fixtures.PooledSqliteDatabase
        shared = 'file:mcp-' + uuid.uuid4().hex + '?mode=memory&cache=shared'
        # Separate worker connections share a test-only in-memory database;
        # the fixture thread keeps the anchor alive until all clients close.
        with patch.object(fixtures, 'PooledSqliteDatabase', side_effect=lambda path, **kwargs: original(shared, uri=True, check_same_thread=False, **kwargs)):
            fixtures.StationOpsTests.setUp(self)
        env = patch.dict(os.environ, {'STATION_OPS_MCP_TOKEN': TOKEN, 'STATION_OPS_APPLY_COMMIT_ENABLED': 'false'})
        env.start(); self.addCleanup(env.stop)
        self.asgi = create_mcp_application(self.app)
        self.http = TestClient(self.asgi, base_url='https://localhost')
        self.http.__enter__(); self.addCleanup(self.http.__exit__, None, None, None)

    def call(self, name, args):
        response = self.http.post(MCP_PATH, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': name, 'arguments': args}}, headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['result']

    def test_real_services_via_mcp_review_dry_run_and_disabled_commit(self):
        before_session = AdminSession.get_by_id(self.session.id).last_seen_at
        envelope = {'candidates': [candidate()]}
        compared = self.call('compare', envelope)
        self.assertFalse(compared['isError'], compared)
        self.assertEqual(compared['structuredContent']['results'][0]['status'], 'changes_detected')
        pending = self.call('review', envelope)['structuredContent']
        op = pending['results'][0]['operations'][0]
        decisions = [{'client_ref': 'external-001', 'operations': {op['operation_id']: 'approved'}}]
        approved = self.call('review', {**envelope, 'decisions': decisions})['structuredContent']
        plan = {**envelope, 'decisions': decisions, 'plan_fingerprint': approved['apply_plan']['plan_fingerprint']}
        dry = self.call('apply_dry_run', plan)
        self.assertFalse(dry['isError'], dry)
        self.assertEqual(dry['structuredContent']['mode'], 'dry_run')
        commit = self.call('apply_commit', {**plan, 'confirm_apply': True})
        self.assertTrue(commit['isError'])
        self.assertEqual(commit['structuredContent']['code'], 'station_ops_apply_commit_disabled')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)
        self.assertEqual(AdminSession.get_by_id(self.session.id).last_seen_at, before_session)

    def test_admin_cookie_csrf_unchanged_in_combined_runtime(self):
        envelope = {'candidates': [candidate({'id': 'a'})]}
        url = '/api/admin/station-ops/compare'
        # Machine credentials never authorize an admin route.
        self.assertEqual(self.http.post(url, json=envelope, headers=AUTH).status_code, 401)
        self.http.cookies.set('admin_session', 'test-token')
        self.assertEqual(self.http.post(url, json=envelope).status_code, 403)
        response = self.http.post(url, json=envelope, headers={'X-CSRF-Token': 'test-csrf'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['results'][0]['status'], 'unchanged')
        # Conversely, an admin cookie never authorizes MCP.
        self.assertEqual(self.http.post(MCP_PATH, json={}, headers={'X-CSRF-Token': 'test-csrf'}).status_code, 401)
