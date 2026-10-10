from flask import Blueprint, current_app, g, jsonify, make_response, request

from app.services.admin_auth import (
    authenticate_admin_credentials, _client_ip, _cookie_settings, _csrf_for_session_token,
    create_admin_session, logger, normalize_email, revoke_all_sessions, utcnow,
    validate_email,
)

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
