import sys
import types
import unittest
from unittest.mock import Mock, patch

import requests

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
        app.config.update(
            BREVO_API_KEY="secret-key", BREVO_SENDER_EMAIL="hello@snow-explorer.com",
            BREVO_SENDER_NAME="Snow Explorer", FRONTEND_URL="https://www.snow-explorer.com",
        )
        app.register_blueprint(bp_newsletter)
        app.register_blueprint(bp_public_stations)
        self.client = app.test_client()
        self.brevo_response = Mock()
        self.brevo_response.raise_for_status.return_value = None
        self.brevo_patch = patch(
            "app.services.newsletter_email.requests.post", return_value=self.brevo_response
        )
        self.brevo_post = self.brevo_patch.start()

    def tearDown(self):
        self.brevo_patch.stop()
        self.database.drop_tables(self.models)
        self.database.close()

    def subscribe(self, **overrides):
        payload = {
            "email": " Rider@Example.COM ",
            "language": "fr",
            "source": "station_page",
            "station_id": "chamonix",
            "consent": True,
            "consentTextVersion": "2026-09",
        }
        payload.update(overrides)
        return self.client.post("/api/newsletter/subscribe", json=payload)

    def confirmed_subscriber(self):
        self.subscribe()
        return NewsletterSubscriber.get()

    def test_subscribe_normalizes_email_creates_defaults_and_follows_station(self):
        response = self.subscribe()
        self.assertEqual(response.status_code, 201)
        subscriber = NewsletterSubscriber.get()
        self.assertEqual(subscriber.email, "rider@example.com")
        self.assertEqual(subscriber.status, "active")
        self.assertIsNone(subscriber.confirmation_token)
        self.assertIsNotNone(subscriber.confirmed_at)
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
        self.assertEqual(self.brevo_post.call_count, 1)

    def test_footer_subscription_does_not_require_redundant_consent_source(self):
        response = self.subscribe(station_id=None, source="footer")
        self.assertEqual(response.status_code, 201)
        subscriber = NewsletterSubscriber.get()
        self.assertEqual(subscriber.consent_source, "footer")
        self.assertEqual(NewsletterSubscriberStation.select().count(), 0)

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
        preferences = self.client.get(f"/api/newsletter/preferences?token={token}").get_json()["data"]
        self.assertEqual(preferences["status"], "active")
        self.assertFalse(preferences["unsubscribed"])
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

    def test_add_station_update_weather_and_alert_crud(self):
        subscriber = self.confirmed_subscriber()
        token = subscriber.preferences_token
        self.client.delete(f"/api/newsletter/preferences/{token}/stations/chamonix")
        added = self.client.post(
            f"/api/newsletter/preferences/{token}/stations", json={"station_id": "chamonix"}
        )
        self.assertEqual(added.status_code, 201)
        duplicate = self.client.post(
            f"/api/newsletter/preferences/{token}/stations", json={"station_id": "chamonix"}
        )
        self.assertEqual(duplicate.status_code, 200)
        weather = self.client.put(
            f"/api/newsletter/preferences/{token}/stations/chamonix",
            json={"snow_conditions_enabled": False, "weather_frequency": "daily"},
        )
        self.assertEqual(weather.status_code, 200)
        self.assertFalse(weather.get_json()["preferences"]["snow_conditions_enabled"])
        created = self.client.post(
            f"/api/newsletter/preferences/{token}/alerts",
            json={"station_id": "chamonix", "threshold_cm": 12,
                  "forecast_period_hours": 72, "is_active": True},
        )
        self.assertEqual(created.status_code, 201)
        alert_id = created.get_json()["alert"]["id"]
        updated = self.client.put(
            f"/api/newsletter/preferences/{token}/alerts/{alert_id}",
            json={"threshold_cm": 20, "is_active": False},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["alert"]["threshold_cm"], 20)
        deleted = self.client.delete(
            f"/api/newsletter/preferences/{token}/alerts/{alert_id}"
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(SnowAlert.select().count(), 0)

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

    def test_unsubscribe_preserves_data_and_resubscribe_is_immediately_active(self):
        subscriber = self.confirmed_subscriber()
        old_token = subscriber.preferences_token
        response = self.client.post("/api/newsletter/unsubscribe", json={"token": old_token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NewsletterSubscriber.get().status, "unsubscribed")
        self.assertEqual(NewsletterSubscriberStation.select().count(), 1)

        self.assertEqual(self.subscribe().status_code, 201)
        resubscribed = NewsletterSubscriber.get()
        self.assertEqual(resubscribed.status, "active")
        self.assertEqual(resubscribed.preferences_token, old_token)
        self.assertIsNone(resubscribed.confirmation_token)
        self.assertIsNone(resubscribed.unsubscribed_at)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 1)

    def test_station_search_is_limited_to_small_active_projection(self):
        response = self.client.get("/api/stations/search?q=cham")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            [{"id": "chamonix", "name": "Chamonix", "slug": "chamonix"}],
        )
        self.assertEqual(self.client.get("/api/stations/search?q=c").status_code, 400)

    def test_invalid_token_and_invalid_station_are_rejected(self):
        self.assertEqual(
            self.client.get("/api/newsletter/preferences?token=unknown").status_code, 404
        )
        subscriber = self.confirmed_subscriber()
        response = self.client.post(
            f"/api/newsletter/preferences/{subscriber.preferences_token}/stations",
            json={"station_id": "missing"},
        )
        self.assertEqual(response.status_code, 404)

    def test_brevo_failure_does_not_rollback_subscription(self):
        self.brevo_post.side_effect = requests.RequestException("no response")
        response = self.subscribe()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(NewsletterSubscriber.get().status, "active")

    def test_brevo_french_and_english_payloads_and_urls(self):
        self.subscribe()
        french = self.brevo_post.call_args.kwargs
        self.assertEqual(french["timeout"], 10)
        self.assertEqual(french["headers"]["api-key"], "secret-key")
        self.assertEqual(french["json"]["subject"], "Bienvenue sur Snow Explorer")
        self.assertIn("/fr/newsletter/preferences?token=", french["json"]["htmlContent"])
        self.assertIn("&amp;action=unsubscribe", french["json"]["htmlContent"])
        self.assertIn("Chamonix", french["json"]["htmlContent"])

        self.subscribe(email="english@example.com", language="en", station_id=None)
        english = self.brevo_post.call_args.kwargs["json"]
        self.assertEqual(english["subject"], "Welcome to Snow Explorer")
        self.assertIn("/newsletter/preferences?token=", english["htmlContent"])
        self.assertNotIn("/fr/newsletter/preferences", english["htmlContent"])


if __name__ == "__main__":
    unittest.main()
