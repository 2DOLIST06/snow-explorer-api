"""OAuth + official MCP client, on shared SQLite only; production DB forbidden."""
import base64
import hashlib
import json
import os
import re
import secrets
import unittest
import uuid
from pathlib import Path
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient
from peewee import PostgresqlDatabase

import test_station_ops as fixtures
from app.datetime_utils import utcnow
from app.mcp.http import MCP_PATH, create_mcp_application
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_user import AdminUser
from app.models.resort import Resort
from app.models.station_ops_oauth import OAUTH_MODELS, OAuthCode, OAuthFlow, OAuthGrant, OAuthToken, OAuthRateBucket
from app.routes.station_ops_oauth import COOKIE
from app.services import station_ops_oauth as oauth
from app.services.admin_auth import hash_password

CLIENT = 'https://chatgpt.com/oauth/client.json'
REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect'
VERIFIER = 'v' * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()


class OAuthTests(unittest.TestCase):
    legacy_regions = False
    real_km_fixture = False

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('correct horse battery')

    def setUp(self):
        original = fixtures.PooledSqliteDatabase
        shared = 'file:oauth-' + uuid.uuid4().hex + '?mode=memory&cache=shared'
        with patch.object(fixtures, 'PooledSqliteDatabase', side_effect=lambda path, **kwargs:
                          original(shared, uri=True, check_same_thread=False, **kwargs)):
            fixtures.StationOpsTests.setUp(self)
        binding = self.database.bind_ctx(OAUTH_MODELS + [AdminLoginAttempt], bind_refs=False, bind_backrefs=False)
        binding.__enter__(); self.addCleanup(binding.__exit__, None, None, None)
        self.database.create_tables(OAUTH_MODELS + [AdminLoginAttempt])
        self.user.password_hash = self.password_hash
        self.user.save()
        env = patch.dict(os.environ, {'STATION_OPS_MCP_LEGACY_TOKEN_ENABLED': 'false',
            'STATION_OPS_MCP_TOKEN': 'compromised-static', 'STATION_OPS_APPLY_COMMIT_ENABLED': 'false'})
        env.start(); self.addCleanup(env.stop)
        response = MagicMock()
        response.status_code = 200
        response.iter_content.return_value = [json.dumps({'client_id': CLIENT, 'redirect_uris': [REDIRECT],
            'token_endpoint_auth_method': 'private_key_jwt',
            'token_endpoint_auth_methods_supported': ['none', 'private_key_jwt'],
            'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']}).encode()]
        response.__enter__.return_value = response
        fetch = patch.object(oauth.requests, 'get', return_value=response)
        self.fetch = fetch.start(); self.addCleanup(fetch.stop)
        self.asgi = create_mcp_application(self.app)
        self.http = TestClient(self.asgi, base_url=oauth.ORIGIN)
        self.http.__enter__(); self.addCleanup(self.http.__exit__, None, None, None)
        self.http.cookies.set('admin_session', 'test-token')

    def params(self, **extra):
        return {'client_id': CLIENT, 'redirect_uri': REDIRECT, 'response_type': 'code',
                'resource': oauth.RESOURCE, 'scope': oauth.READ, 'state': 's & unicode é',
                'code_challenge': CHALLENGE, 'code_challenge_method': 'S256', **extra}

    def begin(self, **extra):
        result = self.http.get(oauth.PREFIX + '/authorize', params=self.params(**extra), follow_redirects=False)
        self.assertEqual(result.status_code, 302, result.text)
        if 'flow=' not in result.headers['location']:
            return result
        page = self.http.get(result.headers['location'])
        self.assertEqual(page.status_code, 200, page.text)
        fields = dict(re.findall(r'name="(flow|csrf)" value="([^"]+)"', page.text))
        return fields, page

    def code(self, scope=oauth.READ):
        fields, _ = self.begin(scope=scope)
        result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'allow'}, follow_redirects=False)
        values = parse_qs(urlsplit(result.headers['location']).query)
        self.assertEqual(values['iss'], [oauth.ORIGIN])
        self.assertEqual(values['state'], ['s & unicode é'])
        return values['code'][0]

    def exchange(self, code=None, **extra):
        return self.http.post(oauth.PREFIX + '/token', data={'grant_type': 'authorization_code',
            'client_id': CLIENT, 'redirect_uri': REDIRECT, 'resource': oauth.RESOURCE,
            'code': code or self.code(), 'code_verifier': VERIFIER, **extra})

    def rpc(self, token, name='research_contract', arguments=None):
        return self.http.post(MCP_PATH, headers={'Authorization': 'Bearer ' + token,
            'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1,
            'method': 'tools/call', 'params': {'name': name, 'arguments': arguments or {}}})

    def refresh(self, token, **extra):
        return self.http.post(oauth.PREFIX + '/token', data={'grant_type': 'refresh_token',
            'client_id': CLIENT, 'resource': oauth.RESOURCE, 'refresh_token': token, **extra})

    def test_discovery_exact_and_challenge(self):
        resource = self.http.get(oauth.RESOURCE_METADATA_PATH).json()
        self.assertEqual(resource['resource'], oauth.RESOURCE)
        self.assertEqual(resource['authorization_servers'], [oauth.ORIGIN])
        self.assertEqual(resource['scopes_supported'], oauth.SCOPES)
        meta = self.http.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(meta['issuer'], oauth.ORIGIN)
        self.assertEqual(meta['code_challenge_methods_supported'], ['S256'])
        self.assertEqual(meta['token_endpoint_auth_methods_supported'], ['none'])
        self.assertTrue(meta['client_id_metadata_document_supported'])
        self.assertTrue(meta['authorization_response_iss_parameter_supported'])
        result = self.http.get(MCP_PATH)
        self.assertEqual(result.status_code, 401)
        self.assertIn(oauth.ORIGIN + oauth.RESOURCE_METADATA_PATH, result.headers['www-authenticate'])

    def test_invalid_client_and_redirect_do_not_redirect_or_fetch(self):
        for extra in ({'client_id': 'https://evil.example/client.json'}, {'redirect_uri': 'https://evil.example'},
                      {'redirect_uri': REDIRECT + '/'}, {'client_id': CLIENT + '?x=1'}):
            with self.subTest(extra=extra):
                self.fetch.reset_mock()
                result = self.http.get(oauth.PREFIX + '/authorize', params=self.params(**extra), follow_redirects=False)
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.json()['iss'], oauth.ORIGIN)
                self.fetch.assert_not_called()

    def test_authorization_errors_preserve_state_and_issuer(self):
        for extra in ({'resource': oauth.RESOURCE + '/'}, {'scope': 'unknown'}, {'scope': oauth.WRITE},
                      {'code_challenge': ''}, {'code_challenge_method': 'plain'}, {'response_type': 'token'}):
            with self.subTest(extra=extra):
                result = self.begin(**extra)
                values = parse_qs(urlsplit(result.headers['location']).query)
                self.assertIn('error', values)
                self.assertEqual(values['iss'], [oauth.ORIGIN])
                self.assertEqual(values['state'], ['s & unicode é'])

    def test_cimd_document_required_and_bounded_no_redirects(self):
        self.fetch.return_value.status_code = 302
        result = self.http.get(oauth.PREFIX + '/authorize', params=self.params(), follow_redirects=False)
        self.assertEqual(result.json()['error'], 'invalid_client')
        self.assertFalse(self.fetch.call_args.kwargs['allow_redirects'])
        self.fetch.return_value.status_code = 200
        for raw in (b'[]', b'{"client_id":"wrong"}', b'x' * 65537):
            self.fetch.return_value.iter_content.return_value = [raw]
            result = self.http.get(oauth.PREFIX + '/authorize', params=self.params(), follow_redirects=False)
            self.assertEqual(result.status_code, 400)

    def test_authorize_csp_allows_chatgpt_callback_without_widening_other_directives(self):
        expected_csp = ("default-src 'none'; form-action 'self' https://chatgpt.com; "
                        "frame-ancestors 'none'; base-uri 'none'")
        fields, page = self.begin()
        self.assertEqual(page.headers['Content-Security-Policy'], expected_csp)
        result = self.http.post(oauth.PREFIX + '/authorize',
                                data={**fields, 'decision': 'allow'}, follow_redirects=False)
        self.assertEqual(result.status_code, 302, result.text)
        self.assertEqual(result.headers['Content-Security-Policy'], expected_csp)
        callback = urlsplit(result.headers['location'])
        self.assertEqual((callback.scheme, callback.netloc, callback.path),
                         ('https', 'chatgpt.com', '/connector_platform_oauth_redirect'))
        values = parse_qs(callback.query)
        self.assertTrue(values['code'][0])
        self.assertEqual(values['state'], ['s & unicode é'])
        self.assertEqual(values['iss'], [oauth.ORIGIN])

    def test_shared_admin_login_and_consent(self):
        self.http.cookies.delete('admin_session')
        fields, page = self.begin(scope=' '.join(oauth.SCOPES))
        self.assertIn('Mot de passe', page.text)
        result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'login',
            'email': self.user.email, 'password': 'correct horse battery'}, follow_redirects=False)
        self.assertEqual(result.status_code, 302, result.text)
        consent = self.http.get(result.headers['location'])
        self.assertIn('Lecture Station Ops', consent.text)
        self.assertIn('Écriture Station Ops', consent.text)
        self.assertNotIn('Mot de passe', consent.text)
        self.assertIn('Secure', result.headers['set-cookie'])
        self.assertIn('HttpOnly', result.headers['set-cookie'])

    def test_consent_refused_csrf_and_browser_binding(self):
        fields, _ = self.begin()
        result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'deny'}, follow_redirects=False)
        self.assertIn('error=access_denied', result.headers['location'])
        self.assertEqual(OAuthCode.select().count(), 0)
        fields, _ = self.begin()
        result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'csrf': 'bad', 'decision': 'allow'}, follow_redirects=False)
        self.assertIn('error=access_denied', result.headers['location'])
        self.http.cookies.delete(COOKIE)
        result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'allow'}, follow_redirects=False)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(OAuthCode.select().count(), 0)

    def test_code_pkce_binding_expiry_and_one_time(self):
        code = self.code()
        for extra in ({'code_verifier': 'w' * 64}, {'redirect_uri': REDIRECT + '/'}, {'resource': oauth.ORIGIN},
                      {'client_id': CLIENT + '/'}, {'code_verifier': 'v' * 42}):
            self.assertEqual(self.exchange(code, **extra).status_code, 400)
        result = self.exchange(code)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.exchange(code).json()['error'], 'invalid_grant')
        code = self.code()
        OAuthCode.update(expires_at=utcnow() - timedelta(seconds=1)).where(OAuthCode.code_hash == oauth.digest(code)).execute()
        self.assertEqual(self.exchange(code).json()['error'], 'invalid_grant')

    def test_opaque_hash_only_and_no_secret_logs(self):
        with self.assertLogs('security.station_ops_oauth', level='INFO') as logs:
            code = self.code()
            tokens = self.exchange(code).json()
        stored = json.dumps([list(row.values()) for model in OAUTH_MODELS for row in model.select().dicts()], default=str)
        for raw in (code, tokens['access_token'], tokens['refresh_token'], VERIFIER):
            self.assertNotIn(raw, stored)
            self.assertNotIn(raw, '\n'.join(logs.output))
        self.assertEqual(OAuthToken.select().where(OAuthToken.token_hash == oauth.digest(tokens['access_token'])).count(), 1)

    def test_access_expiry_revocation_resource_admin_status(self):
        tokens = self.exchange().json()
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 200)
        row = OAuthToken.get(OAuthToken.token_hash == oauth.digest(tokens['access_token']))
        for field, value in (('expires_at', utcnow() - timedelta(seconds=1)), ('revoked_at', utcnow())):
            old = getattr(row, field); setattr(row, field, value); row.save()
            self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)
            setattr(row, field, old); row.save()
        grant = row.grant
        grant.resource = oauth.ORIGIN; grant.save()
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)
        grant.resource = oauth.RESOURCE; grant.save()
        for field, value in (('is_active', False), ('role', 'viewer'), ('password_changed_at', utcnow() + timedelta(seconds=1))):
            old = getattr(self.user, field); setattr(self.user, field, value); self.user.save()
            self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)
            setattr(self.user, field, old); self.user.save()

    def test_station_catalog_with_read_only_oauth_token(self):
        token = self.exchange().json()['access_token']
        Resort.insert_many([{'id': f'catalog-{i:03d}', 'slug': f'catalog-{i:03d}',
                             'name': f'French station {i}', 'country_code': 'FR'}
                            for i in range(72)]).execute()
        first = self.rpc(token, 'station_catalog', {'country_code': 'FR', 'limit': 50})
        self.assertEqual(first.status_code, 200)
        result = first.json()['result']
        self.assertFalse(result['isError'], result)
        page = result['structuredContent']
        self.assertEqual((page['total'], page['returned'], page['next_offset']), (73, 50, 50))
        second = self.rpc(token, 'station_catalog', {'country_code': 'FR', 'limit': 50,
                                                   'offset': page['next_offset']}).json()['result']
        self.assertFalse(second['isError'], second)
        self.assertEqual(second['structuredContent']['returned'], 23)
        self.assertFalse(second['structuredContent']['has_more'])
        ids = [row['id'] for row in page['stations'] + second['structuredContent']['stations']]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertNotIn('description_html', page['stations'][0])

    def test_scopes_declared_and_enforced_before_business_call(self):
        read = self.exchange().json()['access_token']
        tools = self.http.post(MCP_PATH, headers={'Authorization': 'Bearer ' + read,
            'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).json()['result']['tools']
        for tool in tools:
            required = oauth.SCOPES if tool['name'] == 'apply_commit' else [oauth.READ]
            self.assertEqual(tool['securitySchemes'], [{'type': 'oauth2', 'scopes': required}])
            self.assertEqual(tool['_meta']['securitySchemes'], tool['securitySchemes'])
        with patch('app.mcp.tools._dispatch', side_effect=AssertionError('No business call')):
            result = self.rpc(read, 'apply_commit').json()['result']
        self.assertEqual(result['structuredContent']['code'], 'insufficient_scope')
        OAuthGrant.update(scopes=oauth.WRITE).execute()
        self.assertEqual(self.rpc(read).status_code, 403)

    def test_oauth_write_retains_apply_kill_switch_and_confirmation(self):
        token = self.exchange(self.code(' '.join(oauth.SCOPES))).json()['access_token']
        envelope = {'candidates': [{'client_ref': 'external-001', 'data': {'id': 'a', 'altitude_max_m': 2600}}]}
        first = self.rpc(token, 'review', envelope).json()['result']['structuredContent']
        decisions = [{'client_ref': 'external-001', 'operations': {op['operation_id']: 'approved'
                     for op in first['results'][0]['operations']}}]
        plan = self.rpc(token, 'review', {**envelope, 'decisions': decisions}).json()['result']['structuredContent']['apply_plan']
        args = {**envelope, 'decisions': decisions, 'plan_fingerprint': plan['plan_fingerprint']}
        self.assertFalse(self.rpc(token, 'apply_dry_run', args).json()['result']['isError'])
        result = self.rpc(token, 'apply_commit', {**args, 'confirm_apply': True}).json()['result']
        self.assertEqual(result['structuredContent']['code'], 'station_ops_apply_commit_disabled')
        with patch.dict(os.environ, {'STATION_OPS_APPLY_COMMIT_ENABLED': 'true'}):
            result = self.rpc(token, 'apply_commit', {**args, 'confirm_apply': False}).json()['result']
        self.assertEqual(result['structuredContent']['code'], 'apply_confirmation_required')
        self.assertEqual(Resort.get_by_id('a').altitude_max_m, 2200)

    def test_refresh_rotation_replay_revokes_family(self):
        first = self.exchange().json()
        second = self.refresh(first['refresh_token'])
        self.assertEqual(second.status_code, 200, second.text)
        second = second.json()
        self.assertNotEqual(first['refresh_token'], second['refresh_token'])
        self.assertEqual(self.rpc(second['access_token']).status_code, 200)
        self.assertEqual(self.refresh(first['refresh_token']).json()['error'], 'invalid_grant')
        self.assertEqual(self.rpc(second['access_token']).status_code, 401)
        self.assertEqual(self.refresh(second['refresh_token']).json()['error'], 'invalid_grant')

    def test_refresh_expiry_resource_client_scopes_and_revocation(self):
        first = self.exchange().json()
        for extra in ({'resource': oauth.ORIGIN}, {'client_id': 'other'}, {'scope': ' '.join(oauth.SCOPES)}):
            self.assertEqual(self.refresh(first['refresh_token'], **extra).status_code, 400)
        OAuthToken.update(expires_at=utcnow() - timedelta(seconds=1)).where(
            OAuthToken.token_hash == oauth.digest(first['refresh_token'])).execute()
        self.assertEqual(self.refresh(first['refresh_token']).json()['error'], 'invalid_grant')
        first = self.exchange().json()
        result = self.http.post(oauth.PREFIX + '/revoke', data={'client_id': CLIENT, 'token': first['refresh_token']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.rpc(first['access_token']).status_code, 401)
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 400)

    def test_legacy_compromised_token_refused_by_default(self):
        self.assertEqual(self.rpc('compromised-static').status_code, 401)
        with patch.dict(os.environ, {'STATION_OPS_MCP_LEGACY_TOKEN_ENABLED': 'true'}):
            self.assertEqual(self.rpc('compromised-static').status_code, 200)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.rpc('compromised-static').status_code, 401)

    def test_rate_limiting_shared_atomic_and_cleanup_bounded(self):
        oauth.rate_limit('test', 'ip', limit=1)
        with self.assertRaises(oauth.OAuthError):
            oauth.rate_limit('test', 'ip', limit=1)
        for i in range(105):
            OAuthRateBucket.create(key_hash=oauth.digest(str(i)), expires_at=utcnow() - timedelta(seconds=1))
        oauth.cleanup(limit=10)
        self.assertEqual(OAuthRateBucket.select().where(OAuthRateBucket.expires_at < utcnow()).count(), 95)

    def test_duplicate_parameters_and_csrf_not_used_at_token_endpoint(self):
        result = self.http.get(oauth.PREFIX + '/authorize', params=list(self.params().items()) + [('resource', oauth.RESOURCE)])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.exchange().status_code, 200)
        result = self.http.post(oauth.PREFIX + '/token', content='client_id=x&client_id=y',
                                headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(result.status_code, 400)

    def test_official_client_oauth_token_initialize_tools_scan(self):
        token = self.exchange().json()['access_token']
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.asgi), base_url=oauth.ORIGIN,
                                         headers={'Authorization': 'Bearer ' + token}) as http:
                async with streamable_http_client(oauth.RESOURCE, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.assertEqual(len((await session.list_tools()).tools), 9)
                        result = await session.call_tool('station_scan', {'country_code': 'FR'})
                        self.assertFalse(result.isError, result.structuredContent)
                        self.assertTrue(result.structuredContent['stations'])
        anyio.run(check)

    def test_consent_single_use_expired_and_no_unrequested_write(self):
        fields, page = self.begin()
        self.assertNotIn('Écriture Station Ops', page.text)
        first = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'allow'}, follow_redirects=False)
        second = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'allow'}, follow_redirects=False)
        self.assertIn('code=', first.headers['location'])
        self.assertIn('error=invalid_request', second.headers['location'])
        self.assertEqual(OAuthCode.select().count(), 1)
        fields, _ = self.begin()
        OAuthFlow.update(expires_at=utcnow() - timedelta(seconds=1)).execute()
        result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'allow'}, follow_redirects=False)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(OAuthCode.select().count(), 1)

    def test_oauth_login_failure_disabled_admin_and_rate_limit(self):
        self.http.cookies.delete('admin_session')
        fields, _ = self.begin()
        with self.assertLogs('security.admin', level='WARNING') as logs:
            for _ in range(5):
                result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'login',
                    'email': self.user.email, 'password': 'do not log this password'})
                self.assertEqual(result.status_code, 401)
            result = self.http.post(oauth.PREFIX + '/authorize', data={**fields, 'decision': 'login',
                'email': self.user.email, 'password': 'correct horse battery'})
            self.assertEqual(result.status_code, 429)
        self.assertNotIn('do not log this password', '\n'.join(logs.output))
        self.assertEqual(OAuthCode.select().count(), 0)

    def test_admin_revocation_between_consent_and_exchange(self):
        code = self.code()
        self.user.is_active = False; self.user.save()
        self.assertEqual(self.exchange(code).json()['error'], 'invalid_grant')
        self.assertEqual(OAuthToken.select().count(), 0)

    def test_token_endpoint_limits_and_public_client_only(self):
        for _ in range(60):
            self.http.post(oauth.PREFIX + '/token', data={'grant_type': 'password'})
        self.assertEqual(self.http.post(oauth.PREFIX + '/token', data={}).status_code, 429)
        self.assertGreater(OAuthRateBucket.select().count(), 0)

    def test_mcp_missing_migration_and_duplicate_headers_fail_closed(self):
        token = self.exchange().json()['access_token']
        with patch.object(OAuthToken, 'get_or_none', side_effect=RuntimeError('secret db error')):
            result = self.rpc(token)
        self.assertEqual(result.status_code, 401)
        self.assertNotIn('secret db error', result.text)
        result = self.http.post(MCP_PATH, headers=[('Authorization', 'Bearer ' + token),
            ('Authorization', 'Bearer other')], json={})
        self.assertEqual(result.status_code, 401)
        self.assertIn('resource_metadata', result.headers['www-authenticate'])

    def test_issuer_not_silently_normalized(self):
        for value in (oauth.ORIGIN + '/', 'http://example.com', 'https://User@example.com', 'https://EXAMPLE.com'):
            with patch.dict(os.environ, {'STATION_OPS_OAUTH_ISSUER': value}):
                with self.assertRaises(RuntimeError):
                    oauth.issuer()

    def test_oauth_html_secure_headers_and_https(self):
        _, page = self.begin()
        self.assertEqual(page.headers['cache-control'], 'no-store')
        self.assertEqual(page.headers['referrer-policy'], 'no-referrer')
        self.assertIn("frame-ancestors 'none'", page.headers['content-security-policy'])
        self.app.testing = False
        result = self.http.get('http://snow-explorer-api-3.onrender.com' + oauth.PREFIX + '/authorize',
                               params=self.params(), follow_redirects=False)
        self.assertEqual(result.status_code, 403)
        self.app.testing = True

    def test_postgresql_lock_queries_without_connection(self):
        postgres = PostgresqlDatabase('oauth_test_not_connected')
        with postgres.bind_ctx([OAuthGrant, OAuthCode], bind_refs=False, bind_backrefs=False):
            for model in (OAuthGrant, OAuthCode):
                sql, _ = oauth.lock(model.select().where(model.id == 1)).sql()
                self.assertIn('FOR UPDATE', sql)
        self.assertTrue(postgres.is_closed())

    def test_oauth_admin_foreign_keys_compile_as_postgresql_integer(self):
        postgres = PostgresqlDatabase('oauth_test_not_connected')
        with postgres.bind_ctx([AdminUser, OAuthCode, OAuthGrant], bind_refs=False, bind_backrefs=False):
            for model in (OAuthCode, OAuthGrant):
                with self.subTest(model=model.__name__):
                    self.assertIs(model.admin_user.rel_field, AdminUser.id)
                    sql, _ = model._schema._create_table().query()
                    self.assertIn('"admin_user_id" INTEGER NOT NULL', sql)
                    self.assertIn('FOREIGN KEY ("admin_user_id") REFERENCES "admin_users" ("id") ON DELETE CASCADE', sql)
        self.assertTrue(postgres.is_closed())

    def test_migration_admin_foreign_keys_match_integer_models(self):
        migration = (Path(__file__).resolve().parents[1] / 'migrations' /
                     '20261010_add_station_ops_oauth.sql').read_text()
        for model in (OAuthCode, OAuthGrant):
            with self.subTest(model=model.__name__):
                table = re.search(r'CREATE TABLE ' + model._meta.table_name + r' \((.*?)\n\);',
                                  migration, re.DOTALL)
                self.assertIsNotNone(table)
                self.assertIn('admin_user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,',
                              table.group(1))
                self.assertNotRegex(table.group(1), r'admin_user_id\s+BIGINT')

    def test_refresh_tombstone_retained_during_cleanup(self):
        first = self.exchange().json()
        second = self.refresh(first['refresh_token']).json()
        oauth.cleanup()
        consumed = OAuthToken.get(OAuthToken.token_hash == oauth.digest(first['refresh_token']))
        self.assertIsNotNone(consumed.consumed_at)
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 400)
        self.assertEqual(self.rpc(second['access_token']).status_code, 401)

    def test_client_auth_secrets_and_oversized_forms_refused(self):
        code = self.code()
        for extra in ({'client_secret': 'not-accepted'}, {'client_assertion': 'not-accepted'}):
            result = self.exchange(code, **extra)
            self.assertEqual(result.json()['error'], 'invalid_client')
        result = self.http.post(oauth.PREFIX + '/token', content='x=' + 'x' * 16385,
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(result.status_code, 413)
        self.assertIsNone(OAuthCode.get(OAuthCode.code_hash == oauth.digest(code)).consumed_at)

    def test_revoked_grant_tokens_cleaned_without_deleting_active_tokens(self):
        first = self.exchange().json()
        second = self.exchange().json()
        oauth.revoke({'client_id': CLIENT, 'token': first['refresh_token']})
        oauth.cleanup()
        self.assertEqual(OAuthToken.select().count(), 2)
        self.assertEqual(OAuthGrant.select().count(), 1)
        self.assertEqual(self.rpc(second['access_token']).status_code, 200)
