"""OAuth 2.1 public CIMD clients; opaque credentials and atomic DB consumption."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

import anyio
import requests
from mcp.server.auth.provider import AccessToken
from peewee import PostgresqlDatabase

from app.datetime_utils import ensure_utc, utcnow
from app.models.station_ops_oauth import (
    OAuthCode, OAuthFlow, OAuthGrant, OAuthRateBucket, OAuthToken,
)

ORIGIN = 'https://snow-explorer-api-3.onrender.com'
RESOURCE = ORIGIN + '/mcp/station-ops'
READ = 'station-ops:read'
WRITE = 'station-ops:write'
SCOPES = [READ, WRITE]
PREFIX = '/oauth/station-ops'
RESOURCE_METADATA_PATH = '/.well-known/oauth-protected-resource/mcp/station-ops'
PKCE_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
VERIFIER_RE = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400
CODE_SECONDS = 300
FLOW_SECONDS = 600


class OAuthError(ValueError):
    def __init__(self, error, status=400):
        self.error, self.status = error, status
        super().__init__(error)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def issuer():
    value = os.environ.get('STATION_OPS_OAUTH_ISSUER', ORIGIN)
    parsed = urlsplit(value)
    # This co-located AS uses an origin issuer; never normalize it silently.
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.path or parsed.query or parsed.fragment or
            parsed.netloc != parsed.netloc.lower()):
        raise RuntimeError('STATION_OPS_OAUTH_ISSUER must be an exact HTTPS origin without trailing slash')
    return value


def allowlist(name, default):
    return [v.strip() for v in os.environ.get(name, default).split(',') if v.strip()]


def client_allowed(client_id):
    return client_id in allowlist('STATION_OPS_OAUTH_CLIENT_IDS', 'https://chatgpt.com/oauth/client.json')


def redirect_allowed(uri):
    return uri in allowlist('STATION_OPS_OAUTH_REDIRECT_URIS',
                           'https://chatgpt.com/connector_platform_oauth_redirect')


def validate_client(client_id, redirect_uri):
    if not client_allowed(client_id):
        raise OAuthError('invalid_client')
    parsed = urlsplit(client_id)
    # Exact allowlist AND fixed trusted origin: no user-controlled SSRF, redirects,
    # private IPs, credentials, fragments, query strings, or alternate ports.
    if (parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com' or
            parsed.query or parsed.fragment or not parsed.path.startswith('/oauth/') or
            not parsed.path.endswith('/client.json')):
        raise OAuthError('invalid_client')
    if not redirect_allowed(redirect_uri):
        raise OAuthError('invalid_request')
    try:
        with requests.get(client_id, timeout=(3.05, 5), allow_redirects=False,
                          stream=True, headers={'Accept': 'application/json'}) as response:
            if response.status_code != 200:
                raise OAuthError('invalid_client')
            body = bytearray()
            for chunk in response.iter_content(8192):
                body.extend(chunk)
                if len(body) > 65536:
                    raise OAuthError('invalid_client')
            document = json.loads(body)
    except (requests.RequestException, ValueError, TypeError):
        raise OAuthError('invalid_client') from None
    if not isinstance(document, dict):
        raise OAuthError('invalid_client')
    methods = document.get('token_endpoint_auth_methods_supported')
    if methods is None:
        methods = [document.get('token_endpoint_auth_method', 'none')]
    grants = document.get('grant_types', ['authorization_code'])
    if (document.get('client_id') != client_id or not isinstance(methods, list) or not isinstance(grants, list) or
            'none' not in methods or document.get('response_types', ['code']) != ['code'] or
            'authorization_code' not in grants):
        raise OAuthError('invalid_client')
    redirects = document.get('redirect_uris')
    if not isinstance(redirects, list) or redirect_uri not in redirects:
        raise OAuthError('invalid_request')


def validate_scopes(value):
    scopes = value.split() if isinstance(value, str) else []
    if not scopes or not set(scopes) <= set(SCOPES) or READ not in scopes:
        raise OAuthError('invalid_scope')
    return ' '.join(s for s in SCOPES if s in scopes)


def active_admin(user, issued_at):
    changed = ensure_utc(user.password_changed_at)
    return (user.is_active and user.role == 'admin' and
            (changed is None or changed <= ensure_utc(issued_at)))


def lock(query):
    return query.for_update() if isinstance(query.model._meta.database, PostgresqlDatabase) else query


def rate_limit(endpoint, ip, limit=60, window=60):
    now = utcnow()
    slot = int(now.timestamp()) // window
    key = digest(f'{endpoint}:{ip}:{slot}')
    expires = now + timedelta(seconds=window * 2)
    database = OAuthRateBucket._meta.database
    with database.atomic():
        (OAuthRateBucket.insert(key_hash=key, count=0, expires_at=expires)
         .on_conflict_ignore().execute())
        accepted = (OAuthRateBucket.update(count=OAuthRateBucket.count + 1)
                    .where((OAuthRateBucket.key_hash == key) & (OAuthRateBucket.count < limit)).execute())
    if not accepted:
        raise OAuthError('temporarily_unavailable', 429)


def cleanup(limit=100):
    """Bounded indexed expiry sweeps; retain rotated refresh tokens until grant expiry."""
    now = utcnow()
    for model in (OAuthFlow, OAuthCode, OAuthRateBucket):
        ids = model.select(model.id).where(model.expires_at <= now).limit(limit)
        model.delete().where(model.id.in_(ids)).execute()
    revoked_grants = OAuthGrant.select(OAuthGrant.id).where(OAuthGrant.revoked_at.is_null(False))
    ids = OAuthToken.select(OAuthToken.id).where(
        (OAuthToken.expires_at <= now) | OAuthToken.revoked_at.is_null(False) |
        OAuthToken.grant.in_(revoked_grants)).limit(limit)
    OAuthToken.delete().where(OAuthToken.id.in_(ids)).execute()
    ids = (OAuthGrant.select(OAuthGrant.id).where(
        ((OAuthGrant.expires_at <= now) | OAuthGrant.revoked_at.is_null(False)) &
        ~OAuthGrant.id.in_(OAuthToken.select(OAuthToken.grant)))
        .limit(limit))
    OAuthGrant.delete().where(OAuthGrant.id.in_(ids)).execute()


def new_tokens(grant):
    now = utcnow()
    access, refresh = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    expiry = min(now + timedelta(seconds=ACCESS_SECONDS), ensure_utc(grant.expires_at))
    OAuthToken.create(token_hash=digest(access), grant=grant, kind='access', expires_at=expiry)
    OAuthToken.create(token_hash=digest(refresh), grant=grant, kind='refresh', expires_at=grant.expires_at)
    return {'access_token': access, 'token_type': 'Bearer', 'expires_in': max(0, int((expiry - now).total_seconds())),
            'refresh_token': refresh, 'scope': grant.scopes}


def exchange(data):
    if data.get('resource') != RESOURCE:
        raise OAuthError('invalid_target')
    client_id = data.get('client_id', '')
    if not client_allowed(client_id):
        raise OAuthError('invalid_client')
    if any(k in data for k in ('client_secret', 'client_assertion', 'client_assertion_type')):
        raise OAuthError('invalid_client')
    database = OAuthCode._meta.database
    now = utcnow()
    if data.get('grant_type') == 'authorization_code':
        verifier = data.get('code_verifier', '')
        if not VERIFIER_RE.fullmatch(verifier):
            raise OAuthError('invalid_grant')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with database.atomic():
            code = lock(OAuthCode.select().where(OAuthCode.code_hash == digest(data.get('code', '')))).first()
            now = utcnow()
            if (not code or code.consumed_at or ensure_utc(code.expires_at) <= now or
                    code.client_id != client_id or code.redirect_uri != data.get('redirect_uri') or
                    not redirect_allowed(code.redirect_uri) or code.resource != RESOURCE or
                    code.code_challenge_method != 'S256' or not hmac.compare_digest(code.code_challenge, challenge) or
                    not active_admin(code.admin_user, code.created_at)):
                raise OAuthError('invalid_grant')
            if data.get('scope') is not None and data['scope'] != code.scopes:
                raise OAuthError('invalid_scope')
            consumed = OAuthCode.update(consumed_at=now).where(
                (OAuthCode.id == code.id) & OAuthCode.consumed_at.is_null()).execute()
            if consumed != 1:
                raise OAuthError('invalid_grant')
            grant = OAuthGrant.create(admin_user=code.admin_user, client_id=client_id, resource=RESOURCE,
                                      scopes=code.scopes, expires_at=now + timedelta(seconds=REFRESH_SECONDS))
            return new_tokens(grant)
    if data.get('grant_type') == 'refresh_token':
        replay = False
        with database.atomic():
            token = OAuthToken.get_or_none(OAuthToken.token_hash == digest(data.get('refresh_token', '')))
            if not token or token.kind != 'refresh':
                raise OAuthError('invalid_grant')
            grant = lock(OAuthGrant.select().where(OAuthGrant.id == token.grant_id)).first()
            if grant is None:
                raise OAuthError('invalid_grant')
            # Reload under the family lock: parallel refresh/revocation serialize.
            token = OAuthToken.get_or_none(OAuthToken.id == token.id)
            if token is None:
                raise OAuthError('invalid_grant')
            now = utcnow()
            if grant.client_id != client_id or grant.resource != RESOURCE:
                raise OAuthError('invalid_grant')
            if token.consumed_at:
                OAuthGrant.update(revoked_at=now).where(OAuthGrant.id == grant.id).execute()
                replay = True
            elif (token.revoked_at or grant.revoked_at or ensure_utc(token.expires_at) <= now or
                  ensure_utc(grant.expires_at) <= now or not active_admin(grant.admin_user, grant.created_at)):
                raise OAuthError('invalid_grant')
            else:
                if data.get('scope') is not None and validate_scopes(data['scope']) != grant.scopes:
                    # This AS allows refresh of the originally consented scope set only.
                    raise OAuthError('invalid_scope')
                if OAuthToken.update(consumed_at=now).where(
                        (OAuthToken.id == token.id) & OAuthToken.consumed_at.is_null()).execute() != 1:
                    raise OAuthError('invalid_grant')
                result = new_tokens(grant)
        if replay:
            # Commit family revocation before returning invalid_grant.
            raise OAuthError('invalid_grant')
        return result
    raise OAuthError('unsupported_grant_type')


def revoke(data):
    if not client_allowed(data.get('client_id', '')):
        raise OAuthError('invalid_client')
    token = OAuthToken.get_or_none(OAuthToken.token_hash == digest(data.get('token', '')))
    if token:
        with OAuthGrant._meta.database.atomic():
            grant = lock(OAuthGrant.select().where(OAuthGrant.id == token.grant_id)).first()
            if grant is not None and grant.client_id == data['client_id']:
                OAuthGrant.update(revoked_at=utcnow()).where(OAuthGrant.id == grant.id).execute()


class StationOpsTokenVerifier:
    """SDK TokenVerifier protocol, using a worker-owned Peewee connection."""
    def __init__(self, app):
        self.app = app

    async def verify_token(self, raw):
        return await anyio.to_thread.run_sync(self._verify, raw)

    def _verify(self, raw):
        # Explicit temporary machine compatibility; disabled unless opted in.
        legacy = os.environ.get('STATION_OPS_MCP_LEGACY_TOKEN_ENABLED', '').lower() in {'true', '1', 'yes', 'on'}
        expected = os.environ.get('STATION_OPS_MCP_TOKEN', '')
        if legacy and expected.strip() and hmac.compare_digest(raw.encode(), expected.encode()):
            return AccessToken(token=raw, client_id='legacy-machine', scopes=SCOPES, resource=RESOURCE)
        database = OAuthToken._meta.database
        was_closed = database.is_closed()
        with self.app.app_context():
            try:
                token = OAuthToken.get_or_none(OAuthToken.token_hash == digest(raw))
                now = utcnow()
                if (not token or token.kind != 'access' or token.revoked_at or
                        ensure_utc(token.expires_at) <= now):
                    return None
                grant = token.grant
                if (grant.revoked_at or ensure_utc(grant.expires_at) <= now or grant.resource != RESOURCE or
                        not client_allowed(grant.client_id) or not active_admin(grant.admin_user, grant.created_at)):
                    return None
                return AccessToken(token=raw, client_id=grant.client_id, scopes=grant.scopes.split(),
                                   resource=grant.resource, expires_at=int(ensure_utc(token.expires_at).timestamp()),
                                   subject=str(grant.admin_user_id), claims={'iss': issuer()})
            except Exception:
                # Missing migration/database outage fails closed; no raw DB exception.
                return None
            finally:
                if was_closed and not database.is_closed():
                    database.close()
