"""Database-backed, revocable administrator browser sessions."""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import current_app, g, jsonify, request
from peewee import fn

from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser

logger = logging.getLogger("security.admin")
_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def utcnow():
    return datetime.now(timezone.utc)


def normalize_email(email):
    return email.strip().lower() if isinstance(email, str) else ""


def validate_email(email):
    return bool(email and len(email) <= 320 and EMAIL_RE.fullmatch(email))


def validate_password(password):
    if not isinstance(password, str):
        return "password_invalid"
    if len(password) < 12:
        return "password_too_short"
    if len(password) > 1024:
        return "password_too_long"
    return None


def hash_password(password: str) -> str:
    error = validate_password(password)
    if error:
        raise ValueError(error)
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not isinstance(password, str) or len(password) > 1024:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
        return False


def password_needs_rehash(password_hash):
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, TypeError):
        return True


def _secret():
    secret = current_app.config.get("ADMIN_SESSION_SECRET")
    if not secret or len(str(secret)) < 32:
        raise RuntimeError("ADMIN_SESSION_SECRET must contain at least 32 characters")
    return str(secret).encode()


def _digest(value, purpose):
    return hmac.new(_secret(), purpose + value.encode(), hashlib.sha256).hexdigest()


def _csrf_for_session_token(token):
    return hmac.new(_secret(), b"csrf:" + token.encode(), hashlib.sha256).hexdigest()


def _client_ip():
    # Only trust proxy forwarding when explicitly configured.
    if current_app.config.get("TRUST_PROXY_HEADERS"):
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded[:64]
    return (request.remote_addr or "unknown")[:64]


def _user_json(user):
    return {"id": user.id, "email": user.email, "role": user.role}


def _cookie_settings():
    return dict(
        httponly=True,
        secure=bool(current_app.config["ADMIN_COOKIE_SECURE"]),
        samesite=current_app.config["ADMIN_COOKIE_SAMESITE"],
        path="/",
    )


def create_admin_session(user):
    raw_token = secrets.token_urlsafe(48)
    csrf_token = _csrf_for_session_token(raw_token)
    now = utcnow()
    ttl = current_app.config["ADMIN_SESSION_TTL_SECONDS"]
    session = AdminSession.create(
        admin_user=user,
        token_hash=_digest(raw_token, b"session:"),
        csrf_token_hash=_digest(csrf_token, b"csrf-hash:"),
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
        last_seen_at=now,
        ip_address=_client_ip(),
        user_agent=(request.user_agent.string or "")[:1000],
    )
    return session, raw_token, csrf_token


def revoke_all_sessions(user_id):
    now = utcnow()
    return (AdminSession.update(revoked_at=now)
            .where((AdminSession.admin_user_id == user_id) & AdminSession.revoked_at.is_null())
            .execute())


def change_admin_password(user, new_password):
    """Change a password and atomically invalidate all existing sessions."""
    now = utcnow()
    with AdminUser._meta.database.atomic():
        user.password_hash = hash_password(new_password)
        user.password_changed_at = now
        user.updated_at = now
        user.save(only=[AdminUser.password_hash, AdminUser.password_changed_at, AdminUser.updated_at])
        return revoke_all_sessions(user.id)


def _load_session():
    raw = request.cookies.get(current_app.config["ADMIN_SESSION_COOKIE_NAME"])
    if not raw:
        return None
    session = (AdminSession.select(AdminSession, AdminUser)
               .join(AdminUser)
               .where(AdminSession.token_hash == _digest(raw, b"session:"))
               .first())
    now = utcnow()
    if not session or session.revoked_at is not None or session.expires_at <= now:
        return None
    user = session.admin_user
    if not user.is_active or user.role != "admin" or user.password_changed_at > session.created_at:
        return None
    interval = current_app.config["ADMIN_SESSION_TOUCH_INTERVAL_SECONDS"]
    if session.last_seen_at + timedelta(seconds=interval) <= now:
        AdminSession.update(last_seen_at=now).where(AdminSession.id == session.id).execute()
        session.last_seen_at = now
    g.admin_session = session
    g.admin_user = user
    g.admin_raw_session_token = raw
    return session


def _csrf_is_valid(session):
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied:
        return False
    expected = _digest(supplied, b"csrf-hash:")
    return hmac.compare_digest(session.csrf_token_hash, expected)


def authenticate_admin_request():
    if request.method == "OPTIONS":
        return None
    session = _load_session()
    if session is None:
        logger.warning("admin access refused ip=%s path=%s", _client_ip(), request.path)
        if request.path == "/api/admin/auth/session":
            return jsonify({"authenticated": False}), 401
        return jsonify({"error": "admin_authentication_required"}), 401
    if request.method in UNSAFE_METHODS and not _csrf_is_valid(session):
        logger.warning("admin csrf failure user_id=%s ip=%s path=%s", g.admin_user.id, _client_ip(), request.path)
        return jsonify({"error": "csrf_validation_failed"}), 403
    return None


def protect_admin_routes(app):
    @app.before_request
    def require_admin_authentication():
        if not (request.path == "/api/admin" or request.path.startswith("/api/admin/")):
            return None
        if request.method == "OPTIONS" or (request.path == "/api/admin/auth/login" and request.method == "POST"):
            return None
        return authenticate_admin_request()


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        unauthorized = authenticate_admin_request()
        return unauthorized if unauthorized is not None else view(*args, **kwargs)
    return wrapped


def login_is_limited(ip, email):
    cutoff = utcnow() - timedelta(seconds=current_app.config["ADMIN_LOGIN_RATE_WINDOW_SECONDS"])
    AdminLoginAttempt.delete().where(AdminLoginAttempt.attempted_at < cutoff).execute()
    limit = current_app.config["ADMIN_LOGIN_RATE_LIMIT"]
    by_pair = (AdminLoginAttempt.select(fn.COUNT(AdminLoginAttempt.id))
               .where((AdminLoginAttempt.ip_address == ip) & (AdminLoginAttempt.email == email) &
                      (AdminLoginAttempt.attempted_at >= cutoff)).scalar())
    by_ip = (AdminLoginAttempt.select(fn.COUNT(AdminLoginAttempt.id))
             .where((AdminLoginAttempt.ip_address == ip) & (AdminLoginAttempt.attempted_at >= cutoff)).scalar())
    return by_pair >= limit or by_ip >= limit * 4


def record_login_failure(ip, email):
    AdminLoginAttempt.create(ip_address=ip, email=email, attempted_at=utcnow())


def clear_login_failures(ip, email):
    AdminLoginAttempt.delete().where(
        (AdminLoginAttempt.ip_address == ip) & (AdminLoginAttempt.email == email)
    ).execute()
