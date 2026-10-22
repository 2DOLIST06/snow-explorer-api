"""One-time admin recovery links using the existing Brevo configuration."""
import html
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
import requests
from flask import current_app, request
from app.models.admin_login_link import AdminLoginLink
from app.models.admin_user import AdminUser
from app.services.admin_auth import (
    _client_ip, _digest, create_admin_session, login_is_limited,
    record_login_failure, utcnow,
)
from app.services.newsletter_email import BREVO_EMAIL_ENDPOINT


def recovery_origin_allowed():
    return request.headers.get("Origin") in current_app.config.get("ADMIN_ALLOWED_ORIGINS", [])


def recovery_limited(key):
    ip = _client_ip()
    if login_is_limited(ip, key):
        return True
    record_login_failure(ip, key)
    return False


def send_login_link(user):
    config = current_app.config
    base = config.get("FRONTEND_URL") or ""
    parsed = urlsplit(base)
    allowed = config.get("ADMIN_ALLOWED_ORIGINS", [])
    if (not all(config.get(k) for k in ("BREVO_API_KEY", "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME"))
            or parsed.scheme not in ("https", "http") or parsed.username or parsed.password
            or f"{parsed.scheme}://{parsed.netloc}" not in allowed):
        current_app.logger.error("Admin recovery email configuration is incomplete or invalid")
        return "email_delivery_failed", 503
    now = utcnow()
    token = secrets.token_hex(32)
    digest = _digest(token, b"login-link:")
    link, _ = AdminLoginLink.get_or_create(admin_user=user)
    claimed = (AdminLoginLink.update(token_hash=digest, requested_at=now, expires_at=now + timedelta(minutes=15))
               .where((AdminLoginLink.id == link.id) &
                      (AdminLoginLink.requested_at.is_null() | (AdminLoginLink.requested_at < now - timedelta(minutes=1))))
               .execute())
    if not claimed:
        return "login_link_cooldown", 429
    url = f"{parsed.scheme}://{parsed.netloc}/admin/login-link#{token}"
    detail = "Ce lien est valable 15 minutes et utilisable une seule fois. Si vous ne l’avez pas demandé, ignorez ce message."
    try:
        response = requests.post(BREVO_EMAIL_ENDPOINT, headers={
            "accept": "application/json", "content-type": "application/json", "api-key": config["BREVO_API_KEY"],
        }, json={
            "sender": {"email": config["BREVO_SENDER_EMAIL"], "name": config["BREVO_SENDER_NAME"]},
            "to": [{"email": user.email}], "subject": "Votre lien de connexion Snow Explorer",
            "htmlContent": f'<p>Snow Explorer</p><p><a href="{html.escape(url, quote=True)}">Me connecter</a></p><p>{detail}</p>',
            "textContent": f"Me connecter : {url}\n\n{detail}",
        }, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        # Invalidate only this attempt; do not erase a newer token.
        AdminLoginLink.update(token_hash=None, requested_at=None, expires_at=None).where(
            (AdminLoginLink.id == link.id) & (AdminLoginLink.token_hash == digest)).execute()
        current_app.logger.error("Admin recovery email delivery failed")
        return "email_delivery_failed", 503
    return None, 200


def consume_login_link(token):
    digest = _digest(token, b"login-link:")
    now = utcnow()
    with AdminUser._meta.database.atomic():
        link = AdminLoginLink.get_or_none(AdminLoginLink.token_hash == digest)
        if not link:
            return None
        user = AdminUser.get_or_none((AdminUser.id == link.admin_user_id) & AdminUser.is_active & (AdminUser.role == "admin"))
        if not user:
            return None
        consumed = (AdminLoginLink.update(token_hash=None, expires_at=None)
                    .where((AdminLoginLink.id == link.id) & (AdminLoginLink.token_hash == digest) &
                           (AdminLoginLink.expires_at > now) & (AdminLoginLink.requested_at >= user.password_changed_at))
                    .execute())
        if consumed != 1:
            return None
        session = create_admin_session(user)
        AdminUser.update(last_login_at=now).where(AdminUser.id == user.id).execute()
        return user, session
