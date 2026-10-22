from flask import Blueprint, current_app, g, jsonify, make_response, request

from app.services.admin_auth import (
    authenticate_admin_credentials, _client_ip, _cookie_settings, _csrf_for_session_token,
    create_admin_session, logger, normalize_email, revoke_all_sessions, utcnow,
    validate_email, validate_password, change_admin_password,
)

from app.models.admin_user import AdminUser
from app.services.admin_recovery import recovery_origin_allowed, recovery_limited, send_login_link, consume_login_link
import re

bp_admin_auth = Blueprint("admin_auth", __name__, url_prefix="/api/admin/auth")


@bp_admin_auth.post("/login")
def login():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_request"}), 400
    email = normalize_email(payload.get("email"))
    password = payload.get("password")
    if not validate_email(email) or not isinstance(password, str) or len(password) > 1024:
        return jsonify({"error": "invalid_request"}), 400
    user, error, status = authenticate_admin_credentials(email, password)
    if error:
        return jsonify({"error": error}), status
    ip = _client_ip()
    _, raw_token, csrf_token = create_admin_session(user)
    response = make_response(jsonify({
        "authenticated": True,
        "user": {"id": user.id, "email": user.email, "role": user.role},
        "csrf_token": csrf_token,
    }))
    response.set_cookie(current_app.config["ADMIN_SESSION_COOKIE_NAME"], raw_token,
                        max_age=current_app.config["ADMIN_SESSION_TTL_SECONDS"], **_cookie_settings())
    logger.info("admin login succeeded user_id=%s ip=%s", user.id, ip)
    return response


@bp_admin_auth.get("/session")
def session_status():
    csrf_token = _csrf_for_session_token(g.admin_raw_session_token)
    return jsonify({"authenticated": True,
                    "user": {"id": g.admin_user.id, "email": g.admin_user.email, "role": g.admin_user.role},
                    "csrf_token": csrf_token})


@bp_admin_auth.post("/logout")
def logout():
    g.admin_session.revoked_at = utcnow()
    g.admin_session.save(only=[type(g.admin_session).revoked_at])
    response = make_response(jsonify({"authenticated": False}))
    response.delete_cookie(current_app.config["ADMIN_SESSION_COOKIE_NAME"], **_cookie_settings())
    logger.info("admin logout user_id=%s ip=%s", g.admin_user.id, _client_ip())
    return response


@bp_admin_auth.post("/logout-all")
def logout_all():
    count = revoke_all_sessions(g.admin_user.id)
    response = make_response(jsonify({"authenticated": False, "revoked_sessions": count}))
    response.delete_cookie(current_app.config["ADMIN_SESSION_COOKIE_NAME"], **_cookie_settings())
    logger.info("all admin sessions revoked user_id=%s count=%s", g.admin_user.id, count)
    return response


def _recovery_session_response(user, session):
    _, raw_token, csrf_token = session
    response = make_response(jsonify({"authenticated": True,
        "user": {"id": user.id, "email": user.email, "role": user.role}, "csrf_token": csrf_token}))
    response.set_cookie(current_app.config["ADMIN_SESSION_COOKIE_NAME"], raw_token,
                        max_age=current_app.config["ADMIN_SESSION_TTL_SECONDS"], **_cookie_settings())
    response.headers["Cache-Control"] = "no-store"
    return response


@bp_admin_auth.post("/login-link")
def request_login_link():
    if not recovery_origin_allowed():
        return jsonify({"error": "origin_not_allowed"}), 403
    payload = request.get_json(silent=True)
    email = normalize_email(payload.get("email")) if isinstance(payload, dict) else ""
    if not validate_email(email):
        return jsonify({"error": "invalid_request"}), 400
    if recovery_limited(email):
        return jsonify({"error": "too_many_login_attempts"}), 429
    user = AdminUser.get_or_none(AdminUser.email == email)
    if not user:
        return jsonify({"error": "account_not_found"}), 404
    if not user.is_active or user.role != "admin":
        return jsonify({"error": "account_unavailable"}), 403
    error, status = send_login_link(user)
    return (jsonify({"error": error}), status) if error else jsonify({"ok": True})


@bp_admin_auth.post("/login-link/consume")
def login_with_link():
    if not recovery_origin_allowed():
        return jsonify({"error": "origin_not_allowed"}), 403
    if recovery_limited("login-link-consume"):
        return jsonify({"error": "too_many_login_attempts"}), 429
    payload = request.get_json(silent=True)
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
        return jsonify({"error": "invalid_or_expired_link"}), 401
    result = consume_login_link(token)
    if not result:
        return jsonify({"error": "invalid_or_expired_link"}), 401
    user, session = result
    return _recovery_session_response(user, session)


@bp_admin_auth.post("/password")
def update_password():
    payload = request.get_json(silent=True)
    password = payload.get("password") if isinstance(payload, dict) else None
    error = validate_password(password)
    if error:
        return jsonify({"error": error}), 400
    with AdminUser._meta.database.atomic():
        change_admin_password(g.admin_user, password)
        session = create_admin_session(g.admin_user)
    return _recovery_session_response(g.admin_user, session)
