import html
from urllib.parse import urlencode

from flask import current_app


BREVO_EMAIL_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def _preferences_urls(language, token):
    base_url = (current_app.config.get("FRONTEND_URL") or "").rstrip("/")
    path = "/fr/newsletter/preferences" if language == "fr" else "/newsletter/preferences"
    preferences_url = f"{base_url}{path}?{urlencode({'token': token})}"
    unsubscribe_url = f"{preferences_url}&{urlencode({'action': 'unsubscribe'})}"
    return preferences_url, unsubscribe_url


def build_welcome_email(subscriber, station_name=None, account_url=None):
    """Build the welcome message with access to the shared member account."""
    _, unsubscribe_url = _preferences_urls(
        subscriber.language, subscriber.preferences_token
    )
    account_url = account_url or f"{(current_app.config.get('FRONTEND_URL') or '').rstrip('/')}/connexion"
    safe_station_name = html.escape(station_name) if station_name else None
    if subscriber.language == "fr":
        subject = "Bienvenue sur Snow Explorer"
        title = "Bienvenue sur Snow Explorer"
        intro = "Vous êtes inscrit à la newsletter Snow Explorer. Aucune confirmation n’est nécessaire."
        description = (
            "Vous pouvez suivre vos stations préférées et choisir les informations que "
            "vous souhaitez recevoir : chutes de neige, état de la neige, météo, actualités des stations et nouveaux articles."
        )
        station_text = (
            f"Vous suivez désormais la station {safe_station_name}." if safe_station_name else ""
        )
        button = "Accéder à mon compte"
        account_text = "Retrouvez vos abonnements dans votre compte Snow Explorer. Vous pourrez y définir un mot de passe pour vos prochaines connexions. Ce lien d’accès est valable 15 minutes ; votre inscription à la newsletter reste active même si vous ne l’utilisez pas."
        unsubscribe = "Se désabonner"
    else:
        subject = "Welcome to Snow Explorer"
        title = "Welcome to Snow Explorer"
        intro = "You are subscribed to the Snow Explorer newsletter. No confirmation is needed."
        description = (
            "You can follow your favourite resorts and choose the information you want "
            "to receive: snowfall, snow conditions, weather, resort news and new articles."
        )
        station_text = (
            f"You are now following {safe_station_name}." if safe_station_name else ""
        )
        button = "Access my account"
        account_text = "Your Snow Explorer account contains your subscriptions. You can set a password for future sign-ins. This access link is valid for 15 minutes; your newsletter subscription stays active even if you do not use it."
        unsubscribe = "Unsubscribe"

    html_content = f"""<!doctype html>
<html lang="{subscriber.language}"><body style="font-family:Arial,sans-serif;color:#16233b">
<h1>{title}</h1><p>{intro}</p><p>{description}</p>
{f'<p>{station_text}</p>' if station_text else ''}
<p>{account_text}</p>
<p><a href="{html.escape(account_url, quote=True)}" style="background:#1769e0;color:white;padding:12px 18px;text-decoration:none;border-radius:4px">{button}</a></p>
<p><a href="{html.escape(unsubscribe_url, quote=True)}">{unsubscribe}</a></p>
</body></html>"""
    return {
        "sender": {
            "email": current_app.config.get("BREVO_SENDER_EMAIL"),
            "name": current_app.config.get("BREVO_SENDER_NAME"),
        },
        "to": [{"email": subscriber.email}],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": f"{title}\n\n{intro}\n\n{account_text}\n\n{button} : {account_url}\n\n{unsubscribe} : {unsubscribe_url}",
    }


def send_welcome_email(subscriber, station_name=None):
    """Send after persistence; failures are deliberately non-fatal to subscription."""
    api_key = current_app.config.get("BREVO_API_KEY")
    sender_email = current_app.config.get("BREVO_SENDER_EMAIL")
    sender_name = current_app.config.get("BREVO_SENDER_NAME")
    frontend_url = current_app.config.get("FRONTEND_URL")
    if not api_key or not sender_email or not sender_name or not frontend_url:
        current_app.logger.warning(
            "Snow Explorer welcome email skipped: Brevo configuration is incomplete"
        )
        return False
    # Local import avoids a module cycle: all account email templates share the
    # member service's short-lived, one-use links and revocation rules.
    from app.models.member import MemberUser
    from app.services.member_auth import send_member_link
    member, _ = MemberUser.get_or_create(email=subscriber.email)
    error, _ = send_member_link(member, welcome_subscriber=subscriber, station_name=station_name)
    return error is None
