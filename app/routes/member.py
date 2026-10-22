from flask import Blueprint, current_app, g, jsonify, request
from app.datetime_utils import utcnow
from app.models.member import MemberUser, MemberSession, MemberLoginLink
from app.models.newsletter import NewsletterSubscriber, SnowNewsletterPreference
from app.services.admin_auth import normalize_email, validate_email, validate_password, hash_password, verify_password, _cookie_settings
from app.services.member_auth import authenticated, limited, send_member_link, consume_link, session_response, user_json, csrf_for, COOKIE
from app.routes import newsletter

bp_member = Blueprint("member", __name__, url_prefix="/api/member")


@bp_member.before_request
def member_security():
    if request.method == "OPTIONS":
        return None
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("Origin") not in current_app.config.get("ADMIN_ALLOWED_ORIGINS", []):
        return jsonify({"error": "origin_not_allowed"}), 403


@bp_member.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def body():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def email_body():
    return normalize_email(body().get("email"))


@bp_member.post("/register")
def register():
    email = email_body()
    if not validate_email(email):
        return jsonify({"error": "invalid_email"}), 400
    if limited(email):
        return jsonify({"error": "too_many_login_attempts"}), 429
    # Signup is verified by mail. No attacker-chosen password is staged before proof of email ownership.
    member, _ = MemberUser.get_or_create(email=email)
    if member.verified_at:
        return jsonify({"error": "account_exists"}), 409
    if not member.is_active:
        return jsonify({"error": "account_unavailable"}), 403
    error, status = send_member_link(member)
    return (jsonify({"error": error}), status) if error else (jsonify({"ok": True, "verification_required": True}), 201)


@bp_member.post("/login-link")
def request_link():
    email = email_body()
    if not validate_email(email):
        return jsonify({"error": "invalid_email"}), 400
    if limited(email):
        return jsonify({"error": "too_many_login_attempts"}), 429
    member = MemberUser.get_or_none(MemberUser.email == email)
    if not member:
        return jsonify({"error": "account_not_found"}), 404
    if not member.is_active:
        return jsonify({"error": "account_unavailable"}), 403
    error, status = send_member_link(member)
    return (jsonify({"error": error}), status) if error else jsonify({"ok": True})


@bp_member.post("/login-link/consume")
def consume():
    if limited(str(body().get("token", ""))[:128]):
        return jsonify({"error": "too_many_login_attempts"}), 429
    with MemberUser._meta.database.atomic():
        member = consume_link(body().get("token"))
        if not member:
            return jsonify({"error": "invalid_or_expired_link"}), 401
        return session_response(member)


@bp_member.post("/login")
def login():
    data = body()
    email = normalize_email(data.get("email"))
    password = data.get("password")
    if not validate_email(email) or not isinstance(password, str) or len(password) > 1024:
        return jsonify({"error": "invalid_request"}), 400
    if limited(email):
        return jsonify({"error": "too_many_login_attempts"}), 429
    member = MemberUser.get_or_none(MemberUser.email == email)
    if not member or not member.is_active or not member.verified_at:
        return jsonify({"error": "invalid_credentials"}), 401
    if not member.password_hash:
        return jsonify({"error": "password_not_set"}), 401
    if not verify_password(member.password_hash, password):
        return jsonify({"error": "invalid_credentials"}), 401
    with MemberUser._meta.database.atomic():
        # Compare-and-lock prevents an old password check from creating a session after a concurrent password change.
        matched = (MemberUser.update(password_hash=member.password_hash)
                   .where((MemberUser.id == member.id) & (MemberUser.password_hash == member.password_hash) & MemberUser.is_active).execute())
        if matched != 1:
            return jsonify({"error": "invalid_credentials"}), 401
        return session_response(member)


@bp_member.get("/session")
@authenticated
def session():
    return jsonify({"authenticated": True, "user": user_json(g.member_user), "csrf_token": csrf_for(g.member_raw)})


@bp_member.post("/logout")
@authenticated
def logout():
    MemberSession.update(revoked_at=utcnow()).where(MemberSession.id == g.member_session.id).execute()
    response = jsonify({"authenticated": False})
    response.delete_cookie(COOKIE, **_cookie_settings())
    return response


@bp_member.post("/password")
@authenticated
def password():
    value = body().get("password")
    error = validate_password(value)
    if error:
        return jsonify({"error": error}), 400
    hashed = hash_password(value)
    now = utcnow()
    with MemberUser._meta.database.atomic():
        updated = (MemberUser.update(password_hash=hashed, password_changed_at=now)
                   .where((MemberUser.id == g.member_user.id) & (MemberUser.password_hash == g.member_user.password_hash) & MemberUser.is_active).execute())
        if updated != 1:
            return jsonify({"error": "member_authentication_required"}), 401
        MemberSession.update(revoked_at=now).where(MemberSession.member == g.member_user.id).execute()
        MemberLoginLink.delete().where(MemberLoginLink.member == g.member_user.id).execute()
        member = MemberUser.get_by_id(g.member_user.id)
        return session_response(member)


@bp_member.get("/newsletter/access")
@authenticated
def newsletter_access():
    # Email ownership is verified before a member session can exist.
    subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == g.member_user.email)
    if not subscriber:
        return jsonify({"status": "not_subscribed"})
    return jsonify({"status": subscriber.status})


@bp_member.post("/newsletter/subscribe")
@authenticated
def newsletter_subscribe():
    data = body()
    if normalize_email(data.get("email")) != g.member_user.email:
        return jsonify({"error": "forbidden"}), 403
    subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == g.member_user.email)
    if subscriber and subscriber.status in {"bounced", "complained"}:
        return jsonify({"error": "newsletter_unavailable"}), 409
    return newsletter.subscribe()


@bp_member.route("/newsletter/preferences", methods=["GET", "POST"])
@authenticated
def member_newsletter_preferences():
    subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == g.member_user.email)
    if not subscriber or subscriber.status not in {"active", "pending"}:
        return jsonify({"error": "newsletter_inactive"}), 409
    preference, _ = SnowNewsletterPreference.get_or_create(subscriber=subscriber)
    if request.method == "POST":
        data = body()
        allowed = newsletter.GENERAL_BOOLEAN_FIELDS | {"newsletter_frequency"}
        if not data or set(data) - allowed:
            return jsonify({"error": "invalid_preferences_payload"}), 400
        for field in newsletter.GENERAL_BOOLEAN_FIELDS & set(data):
            if type(data[field]) is not bool:
                return jsonify({"error": "invalid_preferences_payload"}), 400
        if "newsletter_frequency" in data and data["newsletter_frequency"] not in newsletter.NEWSLETTER_FREQUENCIES:
            return jsonify({"error": "invalid_newsletter_frequency"}), 400
        for field, value in data.items():
            setattr(preference, field, value)
        preference.updated_at = utcnow()
        preference.save()
    return jsonify({"data": {"preferences": newsletter._general_dict(preference), "newsletter_frequency": preference.newsletter_frequency}})


@bp_member.post("/newsletter/unsubscribe")
@authenticated
def member_newsletter_unsubscribe():
    subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == g.member_user.email)
    if not subscriber:
        return jsonify({"error": "newsletter_inactive"}), 409
    return newsletter.unsubscribe(token=subscriber.preferences_token)
