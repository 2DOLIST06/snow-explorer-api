import re
import secrets

from flask import Blueprint, current_app, jsonify, request
from peewee import IntegrityError

from app.datetime_utils import utcnow
from app.models.newsletter import (
    NewsletterStationPreference,
    NewsletterSubscriber,
    NewsletterSubscriberStation,
    SnowAlert,
    SnowNewsletterPreference,
)
from app.models.resort import Resort
from app.services.newsletter_email import send_welcome_email


bp_newsletter = Blueprint("newsletter", __name__, url_prefix="/api/newsletter")

LANGUAGES = {"fr", "en"}
NEWSLETTER_FREQUENCIES = {"immediate", "weekly", "monthly"}
WEATHER_FREQUENCIES = {"daily", "friday", "weekly", "disabled"}
FORECAST_PERIODS = {24, 48, 72}
GENERAL_BOOLEAN_FIELDS = {
    "snow_conditions", "snowfall", "weather", "resort_updates",
    "opening_closing", "lift_updates", "ski_pass_updates", "articles",
}
STATION_BOOLEAN_FIELDS = {
    "weather_enabled", "snow_conditions_enabled", "resort_updates_enabled",
}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GENERIC_SUBSCRIBE_RESPONSE = {"data": {"status": "active"}}


def _json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _normalize_email(value):
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if len(email) > 320 or not EMAIL_RE.fullmatch(email):
        return None
    local, domain = email.rsplit("@", 1)
    if len(local) > 64 or len(domain) > 255 or ".." in email:
        return None
    return email


def _bounded_text(data, field, max_length, required=True):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        return None if required else ""
    value = value.strip()
    return value if len(value) <= max_length else None


def _new_unique_token(field):
    for _ in range(5):
        token = secrets.token_urlsafe(48)
        if not NewsletterSubscriber.select().where(field == token).exists():
            return token
    raise RuntimeError("Unable to generate a unique newsletter token")


def _subscriber_for_token(token):
    if not isinstance(token, str) or not token or len(token) > 128:
        return None
    return NewsletterSubscriber.get_or_none(
        NewsletterSubscriber.preferences_token == token
    )


def _active_station(station_id):
    if not isinstance(station_id, str) or not station_id.strip() or len(station_id) > 255:
        return None
    return Resort.get_or_none(
        (Resort.id == station_id.strip()) & (Resort.is_active == True)
    )


def _follow_station(subscriber, station):
    followed, _ = NewsletterSubscriberStation.get_or_create(
        subscriber=subscriber, station=station
    )
    NewsletterStationPreference.get_or_create(
        subscriber=subscriber,
        station=station,
        defaults={"weather_frequency": "weekly"},
    )
    return followed


def _iso(value):
    return value.isoformat() if value else None


def _station_dict(station):
    return {"id": station.id, "name": station.name, "slug": station.slug}


def _general_dict(preference):
    data = {field: bool(getattr(preference, field)) for field in GENERAL_BOOLEAN_FIELDS}
    data["newsletter_frequency"] = preference.newsletter_frequency
    return data


def _station_preference_dict(preference):
    data = {field: bool(getattr(preference, field)) for field in STATION_BOOLEAN_FIELDS}
    data["weather_frequency"] = preference.weather_frequency
    return data


def _alert_dict(alert):
    return {
        "id": alert.id,
        "station_id": alert.station_id,
        "alert_type": alert.alert_type,
        "threshold_cm": alert.threshold_cm,
        "forecast_period_hours": alert.forecast_period_hours,
        "is_active": bool(alert.is_active),
        "created_at": _iso(alert.created_at),
        "updated_at": _iso(alert.updated_at),
    }


@bp_newsletter.post("/subscribe")
def subscribe():
    data = _json_object()
    if data is None:
        return jsonify({"error": "invalid_json"}), 400
    email = _normalize_email(data.get("email"))
    language = data.get("language")
    source = _bounded_text(data, "source", 100)
    consent_version = _bounded_text(data, "consentTextVersion", 50)
    if not consent_version:  # Backward-compatible spelling used by the first API version.
        consent_version = _bounded_text(data, "consent_text_version", 50)
    consent_source = _bounded_text(data, "consent_source", 100, required=False) or source
    if email is None:
        return jsonify({"error": "invalid_email"}), 400
    if language not in LANGUAGES:
        return jsonify({"error": "invalid_language", "allowed": sorted(LANGUAGES)}), 400
    if not source or not consent_version or not consent_source or data.get("consent") is not True:
        return jsonify({"error": "explicit_consent_required"}), 400

    station = None
    if data.get("station_id") is not None:
        station = _active_station(data["station_id"])
        if station is None:
            return jsonify({"error": "station_not_found"}), 404

    now = utcnow()
    send_welcome = False
    subscriber = None
    try:
        with NewsletterSubscriber._meta.database.atomic():
            subscriber = NewsletterSubscriber.get_or_none(
                NewsletterSubscriber.email == email
            )
            if subscriber is None:
                subscriber = NewsletterSubscriber.create(
                    email=email,
                    status="active",
                    language=language,
                    source=source,
                    consent_at=now,
                    consent_text_version=consent_version,
                    consent_source=consent_source,
                    confirmed_at=now,
                    confirmation_token=None,
                    preferences_token=_new_unique_token(NewsletterSubscriber.preferences_token),
                )
                SnowNewsletterPreference.create(subscriber=subscriber)
                send_welcome = True
            elif subscriber.status == "unsubscribed":
                subscriber.status = "active"
                subscriber.language = language
                subscriber.source = source
                subscriber.consent_at = now
                subscriber.consent_text_version = consent_version
                subscriber.consent_source = consent_source
                subscriber.confirmation_token = None
                subscriber.confirmed_at = now
                subscriber.unsubscribed_at = None
                subscriber.updated_at = now
                subscriber.save()
                SnowNewsletterPreference.get_or_create(subscriber=subscriber)
                send_welcome = True
            elif subscriber.status in {"active", "pending"}:
                SnowNewsletterPreference.get_or_create(subscriber=subscriber)

            if station is not None and subscriber.status in {"active", "pending"}:
                _follow_station(subscriber, station)
    except IntegrityError:
        # A concurrent identical request is intentionally indistinguishable.
        current_app.logger.info("Concurrent or duplicate newsletter subscription")
    except Exception:
        current_app.logger.exception("Unable to register newsletter subscription")
        return jsonify({"error": "subscription_unavailable"}), 503
    if send_welcome and subscriber is not None:
        send_welcome_email(subscriber, station.name if station is not None else None)
    return jsonify(GENERIC_SUBSCRIBE_RESPONSE), 201


@bp_newsletter.post("/confirm")
def confirm():
    data = _json_object()
    token = data.get("token") if data else None
    if not isinstance(token, str) or not token or len(token) > 128:
        return jsonify({"error": "invalid_confirmation_token"}), 400
    subscriber = NewsletterSubscriber.get_or_none(
        NewsletterSubscriber.confirmation_token == token
    )
    if subscriber is None or subscriber.status != "pending":
        return jsonify({"error": "invalid_confirmation_token"}), 400
    subscriber.status = "active"
    subscriber.confirmed_at = utcnow()
    subscriber.updated_at = subscriber.confirmed_at
    subscriber.confirmation_token = None
    subscriber.save()
    return jsonify({"status": "active", "confirmed_at": _iso(subscriber.confirmed_at)}), 200


@bp_newsletter.get("/preferences")
@bp_newsletter.get("/preferences/<token>")
def get_preferences(token=None):
    token = token or request.args.get("token")
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    preference, _ = SnowNewsletterPreference.get_or_create(subscriber=subscriber)
    station_preferences = {
        item.station_id: item
        for item in NewsletterStationPreference.select().where(
            NewsletterStationPreference.subscriber == subscriber
        )
    }
    stations = []
    followed = (
        NewsletterSubscriberStation.select(NewsletterSubscriberStation, Resort)
        .join(Resort)
        .where(NewsletterSubscriberStation.subscriber == subscriber)
        .order_by(Resort.name.asc(), Resort.id.asc())
    )
    for relation in followed:
        station_preference = station_preferences.get(relation.station_id)
        if station_preference is None:
            station_preference, _ = NewsletterStationPreference.get_or_create(
                subscriber=subscriber, station=relation.station
            )
        station_data = _station_dict(relation.station)
        station_data["preferences"] = _station_preference_dict(
            station_preference
        )
        stations.append(station_data)
    alerts = [
        _alert_dict(alert) for alert in
        SnowAlert.select().where(SnowAlert.subscriber == subscriber).order_by(SnowAlert.id)
    ]
    response_data = {
        "status": subscriber.status,
        "language": subscriber.language,
        "preferences": _general_dict(preference),
        "newsletter_frequency": preference.newsletter_frequency,
        "stations": stations,
        "alerts": alerts,
        "unsubscribed": subscriber.status == "unsubscribed",
    }
    return jsonify({"data": response_data}), 200


@bp_newsletter.put("/preferences")
@bp_newsletter.put("/preferences/<token>")
def update_preferences(token=None):
    token = token or request.args.get("token")
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    data = _json_object()
    allowed = GENERAL_BOOLEAN_FIELDS | {"newsletter_frequency"}
    if not data or set(data) - allowed:
        return jsonify({"error": "invalid_preferences_payload"}), 400
    for field in GENERAL_BOOLEAN_FIELDS & set(data):
        if type(data[field]) is not bool:
            return jsonify({"error": f"{field}_must_be_boolean"}), 400
    if "newsletter_frequency" in data and data["newsletter_frequency"] not in NEWSLETTER_FREQUENCIES:
        return jsonify({"error": "invalid_newsletter_frequency"}), 400
    preference, _ = SnowNewsletterPreference.get_or_create(subscriber=subscriber)
    for field, value in data.items():
        setattr(preference, field, value)
    preference.updated_at = utcnow()
    preference.save()
    return jsonify({"data": {"preferences": _general_dict(preference)}}), 200


@bp_newsletter.post("/preferences/<token>/stations")
def add_station(token):
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    data = _json_object()
    if not data or set(data) != {"station_id"}:
        return jsonify({"error": "invalid_station_payload"}), 400
    station = _active_station(data["station_id"])
    if station is None:
        return jsonify({"error": "station_not_found"}), 404
    with NewsletterSubscriber._meta.database.atomic():
        relation, created = NewsletterSubscriberStation.get_or_create(
            subscriber=subscriber, station=station
        )
        preference, _ = NewsletterStationPreference.get_or_create(
            subscriber=subscriber, station=station
        )
    return jsonify({
        "station": {**_station_dict(station), "preferences": _station_preference_dict(preference)},
        "created": created,
    }), 201 if created else 200


@bp_newsletter.delete("/preferences/<token>/stations/<station_id>")
def remove_station(token, station_id):
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    relation = NewsletterSubscriberStation.get_or_none(
        (NewsletterSubscriberStation.subscriber == subscriber)
        & (NewsletterSubscriberStation.station == station_id)
    )
    if relation is None:
        return jsonify({"error": "followed_station_not_found"}), 404
    with NewsletterSubscriber._meta.database.atomic():
        SnowAlert.delete().where(
            (SnowAlert.subscriber == subscriber) & (SnowAlert.station == station_id)
        ).execute()
        NewsletterStationPreference.delete().where(
            (NewsletterStationPreference.subscriber == subscriber)
            & (NewsletterStationPreference.station == station_id)
        ).execute()
        relation.delete_instance()
    return "", 204


@bp_newsletter.put("/preferences/<token>/stations/<station_id>")
def update_station_preferences(token, station_id):
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    followed = NewsletterSubscriberStation.get_or_none(
        (NewsletterSubscriberStation.subscriber == subscriber)
        & (NewsletterSubscriberStation.station == station_id)
    )
    if followed is None:
        return jsonify({"error": "followed_station_not_found"}), 404
    data = _json_object()
    allowed = STATION_BOOLEAN_FIELDS | {"weather_frequency"}
    if not data or set(data) - allowed:
        return jsonify({"error": "invalid_station_preferences_payload"}), 400
    for field in STATION_BOOLEAN_FIELDS & set(data):
        if type(data[field]) is not bool:
            return jsonify({"error": f"{field}_must_be_boolean"}), 400
    if "weather_frequency" in data and data["weather_frequency"] not in WEATHER_FREQUENCIES:
        return jsonify({"error": "invalid_weather_frequency"}), 400
    preference, _ = NewsletterStationPreference.get_or_create(
        subscriber=subscriber, station=station_id
    )
    for field, value in data.items():
        setattr(preference, field, value)
    if preference.weather_frequency == "disabled":
        preference.weather_enabled = False
    elif data.get("weather_enabled") is False:
        preference.weather_frequency = "disabled"
    preference.updated_at = utcnow()
    preference.save()
    return jsonify({"preferences": _station_preference_dict(preference)}), 200


def _validate_alert_payload(data, partial=False):
    allowed = {"station_id", "alert_type", "threshold_cm", "forecast_period_hours", "is_active"}
    required = {"station_id", "threshold_cm", "forecast_period_hours"}
    if not data or set(data) - allowed or (not partial and not required.issubset(data)):
        return "invalid_alert_payload"
    if "alert_type" in data and data["alert_type"] != "snowfall":
        return "invalid_alert_type"
    if "threshold_cm" in data and (
        type(data["threshold_cm"]) is not int or not 1 <= data["threshold_cm"] <= 500
    ):
        return "invalid_threshold_cm"
    if "forecast_period_hours" in data and (
        type(data["forecast_period_hours"]) is not int
        or data["forecast_period_hours"] not in FORECAST_PERIODS
    ):
        return "invalid_forecast_period_hours"
    if "is_active" in data and type(data["is_active"]) is not bool:
        return "is_active_must_be_boolean"
    return None


@bp_newsletter.post("/preferences/<token>/alerts")
def create_alert(token):
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    data = _json_object()
    error = _validate_alert_payload(data)
    if error:
        return jsonify({"error": error}), 400
    station = _active_station(data["station_id"])
    if station is None:
        return jsonify({"error": "station_not_found"}), 404
    if not NewsletterSubscriberStation.select().where(
        (NewsletterSubscriberStation.subscriber == subscriber)
        & (NewsletterSubscriberStation.station == station)
    ).exists():
        return jsonify({"error": "station_not_followed"}), 409
    alert = SnowAlert.create(
        subscriber=subscriber,
        station=station,
        alert_type=data.get("alert_type", "snowfall"),
        threshold_cm=data["threshold_cm"],
        forecast_period_hours=data["forecast_period_hours"],
        is_active=data.get("is_active", True),
    )
    return jsonify({"alert": _alert_dict(alert)}), 201


@bp_newsletter.put("/preferences/<token>/alerts/<int:alert_id>")
def update_alert(token, alert_id):
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    alert = SnowAlert.get_or_none(
        (SnowAlert.id == alert_id) & (SnowAlert.subscriber == subscriber)
    )
    if alert is None:
        return jsonify({"error": "alert_not_found"}), 404
    data = _json_object()
    error = _validate_alert_payload(data, partial=True)
    if error or "station_id" in data:
        return jsonify({"error": error or "alert_station_cannot_be_changed"}), 400
    for field, value in data.items():
        setattr(alert, field, value)
    alert.updated_at = utcnow()
    alert.save()
    return jsonify({"alert": _alert_dict(alert)}), 200


@bp_newsletter.delete("/preferences/<token>/alerts/<int:alert_id>")
def delete_alert(token, alert_id):
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    deleted = SnowAlert.delete().where(
        (SnowAlert.id == alert_id) & (SnowAlert.subscriber == subscriber)
    ).execute()
    if not deleted:
        return jsonify({"error": "alert_not_found"}), 404
    return "", 204


@bp_newsletter.post("/unsubscribe")
@bp_newsletter.post("/unsubscribe/<token>")
def unsubscribe(token=None):
    if token is None:
        data = _json_object()
        token = data.get("token") if data else None
    subscriber = _subscriber_for_token(token)
    if subscriber is None:
        return jsonify({"error": "invalid_preferences_token"}), 404
    if subscriber.status != "unsubscribed":
        subscriber.status = "unsubscribed"
        subscriber.unsubscribed_at = utcnow()
        subscriber.updated_at = subscriber.unsubscribed_at
        subscriber.save()
    return jsonify({"data": {
        "status": "unsubscribed", "unsubscribed_at": _iso(subscriber.unsubscribed_at)
    }}), 200
