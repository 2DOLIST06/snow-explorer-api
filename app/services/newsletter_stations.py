"""Station newsletter choices shared by member sessions and existing mail links."""
from flask import jsonify
from app.datetime_utils import utcnow
from app.models.newsletter import NewsletterSubscriberStation, NewsletterStationPreference, SnowNewsletterPreference, SnowAlert
from app.models.resort import Resort

FIELDS = {"snowfall_enabled", "snow_conditions_enabled", "weather_enabled", "resort_updates_enabled", "articles_enabled",
          "opening_closing_enabled", "lift_updates_enabled", "ski_pass_updates_enabled"}
FREQUENCIES = {"immediate", "weekly", "monthly"}
WEATHER_FREQUENCIES = {"daily", "friday", "weekly", "disabled"}


def ensure_preference(subscriber, station):
    general, _ = SnowNewsletterPreference.get_or_create(subscriber=subscriber)
    preference, _ = NewsletterStationPreference.get_or_create(subscriber=subscriber, station=station, defaults={
        "snowfall_enabled": general.snowfall, "snow_conditions_enabled": general.snow_conditions,
        "weather_enabled": general.weather, "resort_updates_enabled": general.resort_updates,
        "articles_enabled": general.articles, "newsletter_frequency": general.newsletter_frequency,
    })
    return preference


def serialize(subscriber, station):
    preference = ensure_preference(subscriber, station)
    return {"station": {"id": station.id, "name": station.name, "slug": station.slug, "is_active": station.is_active},
            "preferences": {**{field: bool(getattr(preference, field)) for field in FIELDS}, "newsletter_frequency": preference.newsletter_frequency,
                            "weather_frequency": preference.weather_frequency}}


def list_settings(subscriber):
    followed = (NewsletterSubscriberStation.select(NewsletterSubscriberStation, Resort).join(Resort)
                .where(NewsletterSubscriberStation.subscriber == subscriber).order_by(Resort.name, Resort.id))
    return jsonify({"data": {"stations": [serialize(subscriber, row.station) for row in followed]}})


def add_station(subscriber, data):
    if not isinstance(data, dict) or set(data) != {"station_id"} or not isinstance(data["station_id"], (str, int)) or isinstance(data["station_id"], bool):
        return jsonify({"error": "invalid_station_payload"}), 400
    station = Resort.get_or_none((Resort.id == str(data["station_id"])) & (Resort.is_active == True))
    if not station:
        return jsonify({"error": "station_not_found"}), 404
    with NewsletterSubscriberStation._meta.database.atomic():
        _, created = NewsletterSubscriberStation.get_or_create(subscriber=subscriber, station=station)
        if created:
            NewsletterStationPreference.get_or_create(subscriber=subscriber, station=station, defaults={
                **{field: False for field in FIELDS}, "weather_frequency": "disabled",
            })
        result = serialize(subscriber, station)
    return jsonify({"data": result}), 201 if created else 200


def update_station(subscriber, station_id, data):
    followed = NewsletterSubscriberStation.get_or_none((NewsletterSubscriberStation.subscriber == subscriber) & (NewsletterSubscriberStation.station == station_id))
    if not followed:
        return jsonify({"error": "followed_station_not_found"}), 404
    if not isinstance(data, dict) or not data or set(data) - (FIELDS | {"newsletter_frequency", "weather_frequency"}):
        return jsonify({"error": "invalid_station_preferences_payload"}), 400
    if any(type(data[field]) is not bool for field in FIELDS & set(data)):
        return jsonify({"error": "invalid_station_preferences_payload"}), 400
    if "newsletter_frequency" in data and (not isinstance(data["newsletter_frequency"], str) or data["newsletter_frequency"] not in FREQUENCIES):
        return jsonify({"error": "invalid_newsletter_frequency"}), 400
    if "weather_frequency" in data and (not isinstance(data["weather_frequency"], str) or data["weather_frequency"] not in WEATHER_FREQUENCIES):
        return jsonify({"error": "invalid_weather_frequency"}), 400
    preference = ensure_preference(subscriber, followed.station)
    for field, value in data.items():
        setattr(preference, field, value)
    preference.updated_at = utcnow()
    preference.save()
    return jsonify({"data": serialize(subscriber, followed.station)})


def remove_station(subscriber, station_id):
    followed = NewsletterSubscriberStation.get_or_none((NewsletterSubscriberStation.subscriber == subscriber) & (NewsletterSubscriberStation.station == station_id))
    if not followed:
        return jsonify({"error": "followed_station_not_found"}), 404
    with NewsletterSubscriberStation._meta.database.atomic():
        SnowAlert.delete().where((SnowAlert.subscriber == subscriber) & (SnowAlert.station == station_id)).execute()
        NewsletterStationPreference.delete().where((NewsletterStationPreference.subscriber == subscriber) & (NewsletterStationPreference.station == station_id)).execute()
        followed.delete_instance()
    return jsonify({"ok": True})
