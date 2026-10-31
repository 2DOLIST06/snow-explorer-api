"""Member-only sessions. Administrator credentials and cookies are never accepted."""
import html
import json
import hmac
import re
import secrets
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import requests
from flask import current_app, g, jsonify, make_response, request
from app.datetime_utils import ensure_utc, utcnow
from app.models.member import MemberUser, MemberSession, MemberLoginLink, MemberLoginAttempt
from app.services.admin_auth import _digest, _client_ip, _cookie_settings
from app.services.newsletter_email import BREVO_EMAIL_ENDPOINT, build_welcome_email

COOKIE = "member_session"


def user_json(member):
    return {"id": member.id, "email": member.email, "password_set": bool(member.password_hash)}


def csrf_for(raw):
    return _digest(raw, b"member-csrf:")


def create_session(member):
    raw = secrets.token_urlsafe(48)
    MemberSession.create(member=member, token_hash=_digest(raw, b"member-session:"),
                         expires_at=utcnow() + timedelta(seconds=current_app.config["ADMIN_SESSION_TTL_SECONDS"]))
    return raw


def session_response(member):
    raw = create_session(member)
    response = make_response(jsonify({"authenticated": True, "user": user_json(member), "csrf_token": csrf_for(raw),
                                     "newsletter_intent": getattr(g, "member_newsletter_intent", None)}))
    response.set_cookie(COOKIE, raw, max_age=current_app.config["ADMIN_SESSION_TTL_SECONDS"], **_cookie_settings())
    response.headers["Cache-Control"] = "no-store"
    return response


def current_session():
    """A server-issued session authenticates independently of email verification."""
    raw = request.cookies.get(COOKIE, "")
    session = (MemberSession.select(MemberSession, MemberUser).join(MemberUser)
               .where(MemberSession.token_hash == _digest(raw, b"member-session:")).first()) if raw else None
    member = session.member if session else None
    if (not session or session.revoked_at or ensure_utc(session.expires_at) <= utcnow() or not member.is_active
            or (member.password_changed_at and ensure_utc(member.password_changed_at) > ensure_utc(session.created_at))):
        return None, raw
    return session, raw


def authenticated(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        session, raw = current_session()
        if not session:
            return jsonify({"error": "member_authentication_required", "authenticated": False}), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not hmac.compare_digest(request.headers.get("X-CSRF-Token", "").encode("utf-8"), csrf_for(raw).encode("ascii")):
            return jsonify({"error": "csrf_validation_failed"}), 403
        g.member_user, g.member_session, g.member_raw = session.member, session, raw
        return view(*args, **kwargs)
    return wrapped


def limited(key=""):
    key_hash = _digest(str(key), b"member-rate:")
    now = utcnow()
    cutoff = now - timedelta(minutes=15)
    expired = MemberLoginAttempt.select(MemberLoginAttempt.id).where(MemberLoginAttempt.attempted_at < cutoff).limit(100)
    MemberLoginAttempt.delete().where(MemberLoginAttempt.id.in_(expired)).execute()
    ip = _client_ip()
    recent = MemberLoginAttempt.select().where((MemberLoginAttempt.ip_address == ip) & (MemberLoginAttempt.attempted_at >= cutoff))
    if recent.count() >= 300 or recent.where(MemberLoginAttempt.key_hash == key_hash).count() >= 10:
        return True
    MemberLoginAttempt.create(ip_address=ip, key_hash=key_hash)
    return False


def send_member_link(member, newsletter_intent=None, welcome_subscriber=None, station_name=None):
    if not member.is_active:
        return "account_unavailable", 403
    config = current_app.config
    parsed = urlsplit(config.get("FRONTEND_URL") or "")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if (not all(config.get(k) for k in ("BREVO_API_KEY", "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME"))
            or parsed.scheme not in {"https", "http"} or parsed.username or parsed.password
            or origin not in config.get("ADMIN_ALLOWED_ORIGINS", [])):
        current_app.logger.error("Member email configuration is incomplete or invalid")
        return "email_delivery_failed", 503
    now = utcnow()
    token = secrets.token_hex(32)
    digest = _digest(token, b"member-link:")
    link, _ = MemberLoginLink.get_or_create(member=member)
    claimed = (MemberLoginLink.update(token_hash=digest, requested_at=now, expires_at=now + timedelta(minutes=15),
                                     newsletter_intent=json.dumps(newsletter_intent) if newsletter_intent is not None else None)
               .where((MemberLoginLink.id == link.id) & (MemberLoginLink.requested_at.is_null() | (MemberLoginLink.requested_at < now-timedelta(minutes=1)))).execute())
    if not claimed:
        return "login_link_cooldown", 429
    # This extra signature permits only an email resend, never authentication.
    # It lets an old/replaced link request a fresh link without asking the email
    # address again. The login token itself still expires after 15 minutes.
    resend_claim = URLSafeTimedSerializer(config["ADMIN_SESSION_SECRET"], salt="member-resend-v1").dumps({"member": member.id, "link": digest})
    url = f"{origin}/connexion/lien#{token}~{resend_claim}"
    text = "Ce lien est valable 15 minutes et utilisable une seule fois. Si vous ne l’avez pas demandé, ignorez ce message."
    payload = build_welcome_email(welcome_subscriber, station_name, account_url=url) if welcome_subscriber is not None else {
        "sender": {"email": config["BREVO_SENDER_EMAIL"], "name": config["BREVO_SENDER_NAME"]},
        "to": [{"email": member.email}], "subject": "Votre lien de connexion à votre compte Snow Explorer",
        "htmlContent": f'<p>Snow Explorer</p><p><a href="{html.escape(url, quote=True)}">Accéder à mon compte</a></p><p>{text}</p>',
        "textContent": f"Accéder à mon compte : {url}\n\n{text}",
    }
    try:
        response = requests.post(BREVO_EMAIL_ENDPOINT, headers={"accept": "application/json", "content-type": "application/json", "api-key": config["BREVO_API_KEY"]}, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        MemberLoginLink.update(token_hash=None, requested_at=None, expires_at=None, newsletter_intent=None).where((MemberLoginLink.id == link.id) & (MemberLoginLink.token_hash == digest)).execute()
        current_app.logger.error("Member email delivery failed")
        return "email_delivery_failed", 503
    return None, 200


def _raw_link_token(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    raw = value.partition("~")[0]
    return raw if re.fullmatch(r"[a-f0-9]{64}", raw) else None


def resend_member_link(value):
    raw = _raw_link_token(value)
    if not raw:
        return "resend_email_required", 400
    digest = _digest(raw, b"member-link:")
    _, separator, capability = value.partition("~")
    member = None
    if separator:
        try:
            claim = URLSafeTimedSerializer(current_app.config["ADMIN_SESSION_SECRET"], salt="member-resend-v1").loads(capability, max_age=90 * 24 * 60 * 60)
        except (BadSignature, SignatureExpired):
            return "resend_email_required", 400
        if not isinstance(claim, dict) or claim.get("link") != digest:
            return "resend_email_required", 400
        member = MemberUser.get_or_none(MemberUser.id == claim.get("member"))
    else:
        # Compatibility with an old, expired token still stored in the database.
        link = MemberLoginLink.get_or_none(MemberLoginLink.token_hash == digest)
        member = MemberUser.get_or_none(MemberUser.id == link.member_id) if link else None
    if not member:
        return "resend_email_required", 400
    pending = MemberLoginLink.get_or_none((MemberLoginLink.member == member) & (MemberLoginLink.requested_at > utcnow() - timedelta(days=1)))
    intent = json.loads(pending.newsletter_intent) if pending and pending.newsletter_intent else None
    return send_member_link(member, newsletter_intent=intent)


def consume_link(token):
    token = _raw_link_token(token)
    if not token:
        return None
    now = utcnow()
    digest = _digest(token, b"member-link:")
    link = MemberLoginLink.get_or_none(MemberLoginLink.token_hash == digest)
    if not link:
        return None
    member = MemberUser.get_or_none((MemberUser.id == link.member_id) & MemberUser.is_active)
    if not member:
        return None
    condition = (MemberLoginLink.id == link.id) & (MemberLoginLink.token_hash == digest) & (MemberLoginLink.expires_at > now)
    if member.password_changed_at:
        condition &= MemberLoginLink.requested_at >= member.password_changed_at
    # Claim and read the latest draft in one transaction. Draft updates also compare
    # token_hash, so a consumed link cannot subsequently edit the draft anonymously.
    consumed = MemberLoginLink.update(token_hash=None, expires_at=None).where(condition).execute()
    if consumed != 1:
        return None
    latest = MemberLoginLink.get_by_id(link.id)
    g.member_newsletter_intent = json.loads(latest.newsletter_intent) if latest.newsletter_intent else None
    if not member.verified_at:
        member.verified_at = now
        member.save(only=[MemberUser.verified_at])
    return member
