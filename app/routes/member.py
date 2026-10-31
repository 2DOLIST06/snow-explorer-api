import json
from datetime import timedelta
from flask import Blueprint, current_app, g, jsonify, request
from app.datetime_utils import utcnow
from app.models.member import MemberUser, MemberSession, MemberLoginLink
from app.models.newsletter import NewsletterSubscriber, SnowNewsletterPreference
from app.services.admin_auth import normalize_email, validate_email, validate_password, hash_password, verify_password, _cookie_settings
from app.services.member_auth import authenticated, limited, send_member_link, resend_member_link, consume_link, session_response, user_json, csrf_for, COOKIE
from app.routes import newsletter
from app.services import newsletter_stations, newsletter_onboarding
from app.services.newsletter_email import send_welcome_email

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
    if not member and NewsletterSubscriber.select().where(NewsletterSubscriber.email == email).exists():
        member, _ = MemberUser.get_or_create(email=email)
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


@bp_member.post("/login-link/resend")
def resend_link():
    value = body().get("token")
    if limited("member-resend:" + str(value or "")[:128]):
        return jsonify({"error": "too_many_login_attempts"}), 429
    error, status = resend_member_link(value)
    return (jsonify({"error": error}), status) if error else jsonify({"ok": True})


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


@bp_member.post("/newsletter/start")
def newsletter_start():
    data = body()
    email = normalize_email(data.get("email"))
    if not validate_email(email):
        return jsonify({"error": "invalid_email"}), 400
    if limited(email):
        return jsonify({"error": "too_many_login_attempts"}), 429
    try:
        intent = newsletter_onboarding.normalize_intent(data.get("intent"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with NewsletterSubscriber._meta.database.atomic():
        # One normalized email identifies the same account for newsletter,
        # password sign-in and email sign-in. Signup never verifies or logs in.
        MemberUser.get_or_create(email=email)
        subscriber = newsletter_onboarding.capture_pending(email, intent)
        created = subscriber._newsletter_created
        if not created and subscriber.status == "active":
            return jsonify({"ok": True, "status": "already_subscribed", "verification_required": False})
        if subscriber.status in {"bounced", "complained"}:
            return jsonify({"error": "newsletter_unavailable"}), 409
        subscriber = newsletter_onboarding.apply_intent(email, intent, consent_version="v2-newsletter-button")
        subscriber = NewsletterSubscriber.get_by_id(subscriber.id)
        capability = newsletter_onboarding.signup_token(subscriber) if created else None
    # Newsletter activation does not depend on email delivery or account verification.
    mail_sent = send_welcome_email(subscriber, intent["stations"][0]["station_name"] if intent["stations"] else None)
    return jsonify({"ok": True, "status": "active", "verification_required": False,
                    "email_sent": bool(mail_sent), "draft_token": capability, "intent": intent}), 201


@bp_member.post("/newsletter/login-link")
def newsletter_login_link():
    data = body()
    email = normalize_email(data.get("email"))
    if not validate_email(email):
        return jsonify({"error": "invalid_email"}), 400
    if limited(email):
        return jsonify({"error": "too_many_login_attempts"}), 429
    try:
        intent = newsletter_onboarding.normalize_intent(data.get("intent"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    member, _ = MemberUser.get_or_create(email=email)
    if not member.is_active:
        return jsonify({"error": "account_unavailable"}), 403
    error, status = send_member_link(member, newsletter_intent=intent)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"ok": True})


@bp_member.post("/newsletter/account-link")
def newsletter_account_link():
    # Old preference links can request an account link by email, but are never
    # exchanged directly for a full member session or password-setting access.
    if limited("legacy-newsletter-account"):
        return jsonify({"error": "too_many_login_attempts"}), 429
    subscriber = newsletter._subscriber_for_token(body().get("token"))
    if not subscriber:
        return jsonify({"error": "invalid_preferences_token"}), 404
    member, _ = MemberUser.get_or_create(email=subscriber.email)
    error, status = send_member_link(member)
    return (jsonify({"error": error}), status) if error else jsonify({"ok": True})


@bp_member.post("/newsletter/draft")
def newsletter_draft():
    data = body()
    if limited("newsletter-draft"):
        return jsonify({"error": "too_many_login_attempts"}), 429
    try:
        intent = newsletter_onboarding.normalize_intent(data.get("intent"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    token = data.get("draft_token")
    if not (newsletter_onboarding.update_signup(token, intent) or newsletter_onboarding.update_draft(token, intent)):
        return jsonify({"error": "invalid_or_expired_link"}), 401
    return jsonify({"ok": True})


@bp_member.post("/newsletter/apply")
@authenticated
def newsletter_apply():
    try:
        intent = newsletter_onboarding.normalize_intent(body().get("intent"))
        newsletter_onboarding.apply_intent(g.member_user.email, intent)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409 if str(exc) == "newsletter_unavailable" else 400
    MemberLoginLink.update(newsletter_intent=None).where(MemberLoginLink.member == g.member_user.id).execute()
    return jsonify({"ok": True})


@bp_member.route("/newsletter/request", methods=["GET", "POST"])
@authenticated
def newsletter_request():
    link = MemberLoginLink.get_or_none((MemberLoginLink.member == g.member_user.id) & (MemberLoginLink.requested_at > utcnow() - timedelta(days=1)))
    if request.method == "POST":
        MemberLoginLink.update(newsletter_intent=None).where(MemberLoginLink.member == g.member_user.id).execute()
        return jsonify({"ok": True})
    return jsonify({"intent": json.loads(link.newsletter_intent) if link and link.newsletter_intent else None})


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


# Session-owned station settings: subscriber identity never comes from a client-supplied ID.


def _member_subscriber():
    return NewsletterSubscriber.get_or_none((NewsletterSubscriber.email == g.member_user.email) & NewsletterSubscriber.status.in_(["active", "pending"]))


@bp_member.route("/newsletter/stations", methods=["GET", "POST"])
@authenticated
def member_stations():
    subscriber = _member_subscriber()
    if not subscriber:
        return jsonify({"error": "newsletter_inactive"}), 409
    return newsletter_stations.list_settings(subscriber) if request.method == "GET" else newsletter_stations.add_station(subscriber, body())


@bp_member.post("/newsletter/stations/<station_id>")
@authenticated
def member_station_update(station_id):
    subscriber = _member_subscriber()
    if not subscriber:
        return jsonify({"error": "newsletter_inactive"}), 409
    return newsletter_stations.update_station(subscriber, station_id, body())


@bp_member.post("/newsletter/stations/<station_id>/remove")
@authenticated
def member_station_remove(station_id):
    subscriber = _member_subscriber()
    if not subscriber:
        return jsonify({"error": "newsletter_inactive"}), 409
    return newsletter_stations.remove_station(subscriber, station_id)


@bp_member.route("/newsletter/settings", methods=["GET", "POST"])
@authenticated
def newsletter_settings():
    if request.method == "POST":
        existing = newsletter_onboarding.current_preferences(g.member_user.email)
        retained = {str(row['station_id']) for row in existing['intent']['stations']}
        try:
            intent = newsletter_onboarding.normalize_intent(body().get('intent'), retained_station_ids=retained)
            newsletter_onboarding.replace_preferences(g.member_user.email, intent)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 409 if str(exc) == 'newsletter_unavailable' else 400
        MemberLoginLink.update(newsletter_intent=None).where(MemberLoginLink.member == g.member_user.id).execute()
    return jsonify({'data': newsletter_onboarding.current_preferences(g.member_user.email)})


@bp_member.post("/newsletter/follow")
@authenticated
def newsletter_follow():
    data = body()
    if set(data) - {'station_id', 'topic', 'language'}:
        return jsonify({'error': 'invalid_newsletter_intent'}), 400
    try:
        result = newsletter_onboarding.follow_station(g.member_user.email, data.get('station_id'), data.get('topic'), data.get('language', 'fr'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 409 if str(exc) in {'newsletter_unavailable', 'station_limit_reached'} else 400
    MemberLoginLink.update(newsletter_intent=None).where(MemberLoginLink.member == g.member_user.id).execute()
    return jsonify(result)
