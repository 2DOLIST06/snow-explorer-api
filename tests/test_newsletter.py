import sys
import types
import unittest

sys.modules.setdefault("boto3", types.SimpleNamespace())

from flask import Flask
from peewee import SqliteDatabase

from app.models.newsletter import (
    NewsletterStationPreference,
    NewsletterSubscriber,
    NewsletterSubscriberStation,
    SnowAlert,
    SnowNewsletterPreference,
)
from app.models.resort import Resort
from app.routes.newsletter import bp_newsletter
from app.routes.public_resorts import bp_public_stations


class NewsletterTests(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
        self.models = [
            Resort, NewsletterSubscriber, SnowNewsletterPreference,
            NewsletterSubscriberStation, NewsletterStationPreference, SnowAlert,
        ]
        self.database.bind(self.models)
        self.database.connect()
        self.database.create_tables(self.models)
        Resort.create(id="chamonix", name="Chamonix", slug="chamonix")
        Resort.create(id="hidden", name="Hidden", slug="hidden", is_active=False)
        app = Flask(__name__)
        app.register_blueprint(bp_newsletter)
        app.register_blueprint(bp_public_stations)
        self.client = app.test_client()

    def tearDown(self):
        self.database.drop_tables(self.models)
        self.database.close()

    def subscribe(self, **overrides):
        payload = {
            "email": " Rider@Example.COM ",
            "language": "fr",
            "source": "station_page",
            "station_id": "chamonix",
            "consent": True,
            "consent_text_version": "2026-09",
            "consent_source": "newsletter_form",
        }
        payload.update(overrides)
        return self.client.post("/api/newsletter/subscribe", json=payload)

    def confirmed_subscriber(self):
        self.subscribe()
        subscriber = NewsletterSubscriber.get()
        response = self.client.post(
            "/api/newsletter/confirm", json={"token": subscriber.confirmation_token}
        )
        self.assertEqual(response.status_code, 200)
        return NewsletterSubscriber.get_by_id(subscriber.id)

    def test_subscribe_normalizes_email_creates_defaults_and_follows_station(self):
        response = self.subscribe()
        self.assertEqual(response.status_code, 202)
        subscriber = NewsletterSubscriber.get()
        self.assertEqual(subscriber.email, "rider@example.com")
        self.assertEqual(subscriber.status, "pending")
        self.assertGreater(len(subscriber.confirmation_token), 40)
        self.assertGreater(len(subscriber.preferences_token), 40)
        self.assertTrue(SnowNewsletterPreference.get().articles)
        self.assertEqual(SnowNewsletterPreference.get().newsletter_frequency, "weekly")
        self.assertEqual(NewsletterSubscriberStation.get().station_id, "chamonix")
        self.assertEqual(NewsletterStationPreference.get().weather_frequency, "weekly")

    def test_duplicate_subscription_is_generic_and_does_not_duplicate_rows(self):
        first = self.subscribe()
        second = self.subscribe(email="rider@example.com")
        self.assertEqual(first.get_json(), second.get_json())
        self.assertEqual(NewsletterSubscriber.select().count(), 1)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 1)

    def test_invalid_input_and_inactive_station_are_rejected(self):
        self.assertEqual(self.subscribe(email="bad").status_code, 400)
        self.assertEqual(self.subscribe(consent=False).status_code, 400)
        response = self.subscribe(station_id="hidden")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "station_not_found")

    def test_confirm_and_manage_complete_preferences(self):
        subscriber = self.confirmed_subscriber()
        token = subscriber.preferences_token
        update = self.client.put(
            f"/api/newsletter/preferences/{token}",
            json={"articles": False, "newsletter_frequency": "monthly"},
        )
        self.assertEqual(update.status_code, 200)
        station_update = self.client.put(
            f"/api/newsletter/preferences/{token}/stations/chamonix",
            json={"weather_enabled": True, "weather_frequency": "friday"},
        )
        self.assertEqual(station_update.status_code, 200)
        alert_response = self.client.post(
            f"/api/newsletter/preferences/{token}/alerts",
            json={
                "station_id": "chamonix", "alert_type": "snowfall",
                "threshold_cm": 10, "forecast_period_hours": 24,
            },
        )
        self.assertEqual(alert_response.status_code, 201)
        preferences = self.client.get(f"/api/newsletter/preferences/{token}").get_json()
        self.assertEqual(preferences["status"], "active")
        self.assertFalse(preferences["preferences"]["articles"])
        self.assertEqual(preferences["stations"][0]["name"], "Chamonix")
        self.assertEqual(preferences["stations"][0]["preferences"]["weather_frequency"], "friday")
        self.assertEqual(preferences["alerts"][0]["threshold_cm"], 10)

    def test_alert_ownership_and_validation(self):
        owner = self.confirmed_subscriber()
        alert = SnowAlert.create(
            subscriber=owner, station="chamonix", threshold_cm=10,
            forecast_period_hours=24,
        )
        other = NewsletterSubscriber.create(
            email="other@example.com", status="active", language="en", source="footer",
            consent_at=owner.consent_at, consent_text_version="v1", consent_source="footer",
            preferences_token="other-token", confirmation_token=None,
        )
        response = self.client.put(
            f"/api/newsletter/preferences/{other.preferences_token}/alerts/{alert.id}",
            json={"threshold_cm": 20},
        )
        self.assertEqual(response.status_code, 404)
        invalid = self.client.put(
            f"/api/newsletter/preferences/{owner.preferences_token}/alerts/{alert.id}",
            json={"threshold_cm": 0},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_removing_station_removes_preferences_and_alerts(self):
        subscriber = self.confirmed_subscriber()
        SnowAlert.create(
            subscriber=subscriber, station="chamonix", threshold_cm=5,
            forecast_period_hours=48,
        )
        response = self.client.delete(
            f"/api/newsletter/preferences/{subscriber.preferences_token}/stations/chamonix"
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 0)
        self.assertEqual(NewsletterStationPreference.select().count(), 0)
        self.assertEqual(SnowAlert.select().count(), 0)

    def test_unsubscribe_preserves_data_and_resubscribe_requires_confirmation(self):
        subscriber = self.confirmed_subscriber()
        old_token = subscriber.preferences_token
        response = self.client.post(f"/api/newsletter/unsubscribe/{old_token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NewsletterSubscriber.get().status, "unsubscribed")
        self.assertEqual(NewsletterSubscriberStation.select().count(), 1)

        self.assertEqual(self.subscribe().status_code, 202)
        resubscribed = NewsletterSubscriber.get()
        self.assertEqual(resubscribed.status, "pending")
        self.assertNotEqual(resubscribed.preferences_token, old_token)
        self.assertIsNotNone(resubscribed.confirmation_token)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 1)

    def test_station_search_is_limited_to_small_active_projection(self):
        response = self.client.get("/api/stations/search?q=cham")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            [{"id": "chamonix", "name": "Chamonix", "slug": "chamonix"}],
        )
        self.assertEqual(self.client.get("/api/stations/search?q=c").status_code, 400)


if __name__ == "__main__":
    unittest.main()
