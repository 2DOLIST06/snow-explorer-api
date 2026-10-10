"""Co-located browser authorization, shared admin login, consent and OAuth tokens."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlencode

from flask import Blueprint, current_app, g, jsonify, make_response, redirect, render_template_string, request

from app.datetime_utils import ensure_utc, utcnow
from app.models.station_ops_oauth import OAuthCode, OAuthFlow
from app.services.admin_auth import (
    _client_ip, _cookie_settings, _load_session, authenticate_admin_credentials,
    create_admin_session, normalize_email, validate_email,
)
from app.services import station_ops_oauth as oauth

bp_station_ops_oauth = Blueprint('station_ops_oauth', __name__, url_prefix=oauth.PREFIX)
logger = logging.getLogger('security.station_ops_oauth')
COOKIE = '__Host-station_ops_oauth_browser'
# __Host- cookies require Path=/, Secure and no Domain.
PAGE = '''<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Snow Explorer — Autorisation ChatGPT</title>
<h1>Autoriser ChatGPT à accéder à Snow Explorer Station Ops</h1>
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<form method="post" action="{{ action }}">
<input type="hidden" name="flow" value="{{ handle }}">
<input type="hidden" name="csrf" value="{{ csrf }}">
{% if user %}<p>Administrateur : {{ user.email }}</p>
<ul>{% for scope in scopes %}<li>{{ labels[scope] }}</li>{% endfor %}</ul>
<p>L’écriture reste soumise aux décisions explicites et aux protections APPLY.</p>
<button name="decision" value="allow">Autoriser</button>
<button name="decision" value="deny">Refuser</button>
{% else %}<p>Connectez-vous avec votre compte administrateur Snow Explorer.</p>
<label>Email <input name="email" type="email" required autocomplete="username"></label>
<label>Mot de passe <input name="password" type="password" required autocomplete="current-password"></label>
<button name="decision" value="login">Se connecter</button>{% endif %}
</form></html>'''


def metadata():
    root = oauth.issuer()
    return {'issuer': root, 'authorization_endpoint': root + oauth.PREFIX + '/authorize',
            'token_endpoint': root + oauth.PREFIX + '/token',
            'revocation_endpoint': root + oauth.PREFIX + '/revoke',
            'response_types_supported': ['code'], 'response_modes_supported': ['query'],
            'grant_types_supported': ['authorization_code', 'refresh_token'],
            'code_challenge_methods_supported': ['S256'], 'scopes_supported': oauth.SCOPES,
            'token_endpoint_auth_methods_supported': ['none'],
            'revocation_endpoint_auth_methods_supported': ['none'],
            'client_id_metadata_document_supported': True,
            'authorization_response_iss_parameter_supported': True}


def unique_data(values):
    if any(len(v) != 1 for v in values.values()):
        raise oauth.OAuthError('invalid_request')
    return {k: v[0] for k, v in values.items()}


def form_data():
    if request.mimetype != 'application/x-www-form-urlencoded':
        raise oauth.OAuthError('invalid_request')
    raw = request.stream.read(16385)
    if len(raw) > 16384:
        raise oauth.OAuthError('invalid_request', 413)
    try:
        return unique_data(parse_qs(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=30))
    except (UnicodeError, ValueError):
        raise oauth.OAuthError('invalid_request') from None


def authorization_response(flow, **fields):
    fields['iss'] = oauth.issuer()
    if flow.state is not None:
        fields['state'] = flow.state
    return redirect(flow.redirect_uri + ('&' if '?' in flow.redirect_uri else '?') + urlencode(fields), 302)


def csrf_value(browser, handle):
    return hmac.new(browser.encode(), b'oauth-consent:' + handle.encode(), hashlib.sha256).hexdigest()


def page(flow, handle, browser, error=None):
    session = _load_session(touch_session=False)
    return make_response(render_template_string(PAGE, action=oauth.PREFIX + '/authorize',
        handle=handle, csrf=csrf_value(browser, handle), user=g.admin_user if session else None,
        scopes=flow.scopes.split(), labels={oauth.READ: 'Lecture Station Ops', oauth.WRITE: 'Écriture Station Ops'},
        error=error))


@bp_station_ops_oauth.before_request
def secure_oauth():
    if not request.is_secure and not current_app.testing:
        raise oauth.OAuthError('invalid_request', 403)
    oauth.rate_limit(request.endpoint or 'oauth', _client_ip())
    # Fixed bounded work; only OAuth tables, never station data.
    oauth.cleanup()


@bp_station_ops_oauth.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'none'; form-action 'self' https://chatgpt.com; frame-ancestors 'none'; base-uri 'none'"
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp_station_ops_oauth.errorhandler(oauth.OAuthError)
def oauth_error(error):
    logger.info('oauth refused reason=%s', error.error)
    body = {'error': error.error}
    if request.endpoint == 'station_ops_oauth.authorize':
        body['iss'] = oauth.issuer()
    return jsonify(body), error.status


@bp_station_ops_oauth.errorhandler(Exception)
def unavailable(_error):
    # No raw SQL, form values or exception string in logs/responses.
    logger.error('oauth unavailable')
    body = {'error': 'temporarily_unavailable'}
    if request.endpoint == 'station_ops_oauth.authorize':
        body['iss'] = oauth.issuer()
    return jsonify(body), 503


@bp_station_ops_oauth.route('/authorize', methods=['GET', 'POST'])
def authorize():
    if len(request.query_string) > 8192:
        raise oauth.OAuthError('invalid_request', 413)
    data = unique_data(request.args.to_dict(flat=False)) if request.method == 'GET' else form_data()
    browser = request.cookies.get(COOKIE, '')
    handle = data.get('flow')
    if not handle:
        if request.method != 'GET':
            raise oauth.OAuthError('invalid_request')
        # Establish redirect trust before redirecting ANY OAuth error.
        oauth.validate_client(data.get('client_id', ''), data.get('redirect_uri', ''))
        class ErrorContext:
            redirect_uri = data['redirect_uri']
            state = data.get('state')
        try:
            if data.get('response_type') != 'code':
                raise oauth.OAuthError('unsupported_response_type')
            if data.get('resource') != oauth.RESOURCE:
                raise oauth.OAuthError('invalid_target')
            scopes = oauth.validate_scopes(data.get('scope'))
            if data.get('code_challenge_method') != 'S256' or not oauth.PKCE_RE.fullmatch(data.get('code_challenge', '')):
                raise oauth.OAuthError('invalid_request')
            if data.get('response_mode', 'query') != 'query':
                raise oauth.OAuthError('invalid_request')
            if len(data.get('state', '')) > 2048:
                raise oauth.OAuthError('invalid_request')
        except oauth.OAuthError as error:
            return authorization_response(ErrorContext(), error=error.error)
        handle = secrets.token_urlsafe(32)
        browser = browser if 32 <= len(browser) <= 128 else secrets.token_urlsafe(32)
        OAuthFlow.create(handle_hash=oauth.digest(handle), browser_hash=oauth.digest(browser),
                         client_id=data['client_id'], redirect_uri=data['redirect_uri'],
                         resource=oauth.RESOURCE, scopes=scopes, state=data.get('state'),
                         code_challenge=data['code_challenge'],
                         expires_at=utcnow() + timedelta(seconds=oauth.FLOW_SECONDS))
        response = redirect(oauth.PREFIX + '/authorize?' + urlencode({'flow': handle}))
        response.set_cookie(COOKIE, browser, secure=True, httponly=True, samesite='Lax',
                            path='/', max_age=oauth.FLOW_SECONDS)
        return response
    if len(handle) > 128 or not browser or len(browser) > 128:
        raise oauth.OAuthError('invalid_request')
    flow = OAuthFlow.get_or_none(OAuthFlow.handle_hash == oauth.digest(handle))
    if (not flow or not hmac.compare_digest(flow.browser_hash, oauth.digest(browser))):
        raise oauth.OAuthError('invalid_request')
    if not oauth.client_allowed(flow.client_id) or not oauth.redirect_allowed(flow.redirect_uri):
        raise oauth.OAuthError('invalid_client')
    if flow.consumed_at or ensure_utc(flow.expires_at) <= utcnow():
        return authorization_response(flow, error='invalid_request')
    if request.method == 'GET':
        return page(flow, handle, browser)
    if not hmac.compare_digest(data.get('csrf', '').encode(), csrf_value(browser, handle).encode()):
        return authorization_response(flow, error='access_denied')
    session = _load_session(touch_session=False)
    if not session:
        oauth.rate_limit('oauth-login', _client_ip(), limit=20, window=900)
        if data.get('decision') != 'login':
            return page(flow, handle, browser)
        email, password = normalize_email(data.get('email')), data.get('password')
        if not validate_email(email) or not isinstance(password, str) or len(password) > 1024:
            return page(flow, handle, browser, 'Identifiants invalides.'), 400
        user, error, status = authenticate_admin_credentials(email, password)
        if error:
            return page(flow, handle, browser, 'Connexion refusée.'), status
        if user.role != 'admin':
            return page(flow, handle, browser, 'Connexion refusée.'), 403
        _, raw, _ = create_admin_session(user)
        response = redirect(oauth.PREFIX + '/authorize?' + urlencode({'flow': handle}))
        response.set_cookie(current_app.config['ADMIN_SESSION_COOKIE_NAME'], raw,
                            max_age=current_app.config['ADMIN_SESSION_TTL_SECONDS'],
                            **{**_cookie_settings(), 'secure': True})
        logger.info('oauth login succeeded admin_id=%s', user.id)
        return response
    if data.get('decision') not in {'allow', 'deny'}:
        return authorization_response(flow, error='invalid_request')
    now = utcnow()
    with OAuthFlow._meta.database.atomic():
        consumed = OAuthFlow.update(consumed_at=now).where(
            (OAuthFlow.id == flow.id) & OAuthFlow.consumed_at.is_null() & (OAuthFlow.expires_at > now)).execute()
        if consumed != 1:
            return authorization_response(flow, error='invalid_request')
        if data['decision'] == 'deny':
            return authorization_response(flow, error='access_denied')
        code = secrets.token_urlsafe(48)
        OAuthCode.create(code_hash=oauth.digest(code), admin_user=g.admin_user,
                         client_id=flow.client_id, redirect_uri=flow.redirect_uri, resource=flow.resource,
                         scopes=flow.scopes, code_challenge=flow.code_challenge,
                         expires_at=now + timedelta(seconds=oauth.CODE_SECONDS))
    logger.info('oauth consent admin_id=%s result=allowed scopes=%s', g.admin_user.id, flow.scopes)
    return authorization_response(flow, code=code)


@bp_station_ops_oauth.post('/token')
def token():
    if request.headers.get('Authorization'):
        raise oauth.OAuthError('invalid_client')
    result = oauth.exchange(form_data())
    logger.info('oauth token result=issued')
    return jsonify(result)


@bp_station_ops_oauth.post('/revoke')
def revoke():
    if request.headers.get('Authorization'):
        raise oauth.OAuthError('invalid_client')
    data = form_data()
    if any(k in data for k in ('client_secret', 'client_assertion', 'client_assertion_type')):
        raise oauth.OAuthError('invalid_client')
    oauth.revoke(data)
    return '', 200
