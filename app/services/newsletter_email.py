import html
from urllib.parse import urlencode

import requests
from flask import current_app


BREVO_EMAIL_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def _preferences_urls(language, token):
    base_url = (current_app.config.get("FRONTEND_URL") or "").rstrip("/")
    path = "/fr/newsletter/preferences" if language == "fr" else "/newsletter/preferences"
    preferences_url = f"{base_url}{path}?{urlencode({'token': token})}"
    unsubscribe_url = f"{preferences_url}&{urlencode({'action': 'unsubscribe'})}"
    return preferences_url, unsubscribe_url


def build_welcome_email(subscriber, station_name=None):
    """Build the public Snow Explorer welcome message without exposing database IDs."""
    preferences_url, unsubscribe_url = _preferences_urls(
        subscriber.language, subscriber.preferences_token
    )
    safe_station_name = html.escape(station_name) if station_name else None
    if subscriber.language == "fr":
        subject = "Bienvenue sur Snow Explorer"
        title = "Bienvenue sur Snow Explorer"
        intro = "Votre inscription à Snow Explorer a bien été prise en compte."
        description = (
            "Vous pouvez suivre vos stations préférées et choisir les informations que "
            "vous souhaitez recevoir : météo, neige, ouvertures, forfaits et actualités."
        )
        station_text = (
            f"Vous suivez désormais la station {safe_station_name}." if safe_station_name else ""
        )
        button = "Gérer mes préférences"
        unsubscribe = "Se désabonner"
    else:
        subject = "Welcome to Snow Explorer"
        title = "Welcome to Snow Explorer"
        intro = "Your Snow Explorer subscription has been successfully registered."
        description = (
            "You can follow your favourite resorts and choose the information you want "
            "to receive: weather, snow, openings, ski passes and news."
        )
        station_text = (
            f"You are now following {safe_station_name}." if safe_station_name else ""
        )
        button = "Manage my preferences"
        unsubscribe = "Unsubscribe"

    html_content = f"""<!doctype html>
<html lang="{subscriber.language}"><body style="font-family:Arial,sans-serif;color:#16233b">
<h1>{title}</h1><p>{intro}</p><p>{description}</p>
{f'<p>{station_text}</p>' if station_text else ''}
<p><a href="{html.escape(preferences_url, quote=True)}" style="background:#1769e0;color:white;padding:12px 18px;text-decoration:none;border-radius:4px">{button}</a></p>
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
    payload = build_welcome_email(subscriber, station_name)
    try:
        response = requests.post(
            BREVO_EMAIL_ENDPOINT,
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "api-key": api_key,
            },
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        # No email address, API key or preferences token is included in this log.
        current_app.logger.error(
            "Snow Explorer welcome email failed via Brevo (%s)", type(exc).__name__
        )
        return False
