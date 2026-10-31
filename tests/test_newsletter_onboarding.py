import json
import unittest
from datetime import timedelta
from unittest.mock import patch
from tests.test_newsletter_station_settings import StationSettingsTests, ORIGIN
from app.datetime_utils import utcnow
from app.models.member import MemberUser, MemberLoginLink
from app.models.newsletter import NewsletterSubscriber, NewsletterStationPreference, NewsletterSubscriberStation, SnowNewsletterPreference


class NewsletterOnboardingTests(StationSettingsTests):
    def setUp(self):
        super().setUp()
        self.app.config.update(FRONTEND_URL=ORIGIN, BREVO_API_KEY='test', BREVO_SENDER_EMAIL='snow@example.com', BREVO_SENDER_NAME='Snow Explorer')
        self.sender_patch = patch('app.services.member_auth.requests.post')
        self.sender = self.sender_patch.start()
        self.addCleanup(self.sender_patch.stop)
        self.intent = {'stations': [{'station_id': 'FR_2', 'topics': ['weather'], 'weather_frequency': 'friday', 'newsletter_frequency': 'weekly'}], 'consent': True, 'language': 'fr', 'source': 'station_page'}

    def request(self, path, data, client=None, csrf=None):
        return (client or self.app.test_client()).post('/api/member/newsletter/' + path, json=data, headers={'Origin': ORIGIN, **({'X-CSRF-Token': csrf} if csrf else {})})

    def start(self, email='a@example.com', intent=None):
        return self.request('start', {'email': email, 'intent': intent or self.intent})

    def email_token(self):
        return self.sender.call_args.kwargs['json']['textContent'].split('#')[1].split()[0]

    def test_existing_subscriber_uses_same_account_and_draft_does_not_modify_preferences(self):
        self.post('', {'station_id': 'FR_1'})
        self.post('/FR_1', {'weather_enabled': True, 'weather_frequency': 'daily', 'articles_enabled': False})
        before = NewsletterStationPreference.get().weather_frequency
        response = self.start(' A@EXAMPLE.COM ')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(MemberUser.select().where(MemberUser.email == 'a@example.com').count(), 1)
        self.assertEqual(NewsletterSubscriber.select().where(NewsletterSubscriber.email == 'a@example.com').count(), 1)
        self.assertNotIn('preferences_token', response.get_json())
        self.assertNotIn('user', response.get_json())
        self.assertEqual(NewsletterSubscriberStation.select().count(), 1)
        self.assertEqual(NewsletterStationPreference.get().weather_frequency, before)
        self.assertEqual(self.request('apply', {'intent': self.intent}).status_code, 401)
        result = self.request('apply', {'intent': self.intent}, self.client, self.csrf)
        self.assertEqual(result.status_code, 200)
        prefs = NewsletterStationPreference.get(NewsletterStationPreference.station == 'FR_1')
        self.assertEqual(prefs.weather_frequency, 'daily')
        self.assertFalse(prefs.articles_enabled)
        added = NewsletterStationPreference.get(NewsletterStationPreference.station == 'FR_2')
        self.assertTrue(added.weather_enabled)
        self.assertEqual(added.weather_frequency, 'friday')
        self.assertFalse(added.snowfall_enabled)
        self.assertFalse(added.resort_updates_enabled)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 2)
        self.request('apply', {'intent': self.intent}, self.client, self.csrf)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 2)

    def test_optional_inline_choices_survive_email_login_and_draft_cannot_be_reused(self):
        response = self.start('new@example.com', {'stations': [], 'articles': True, 'consent': True, 'source': 'footer'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(NewsletterSubscriber.get(NewsletterSubscriber.email == 'new@example.com').status, 'pending')
        self.assertIsNone(MemberUser.get(MemberUser.email == 'new@example.com').verified_at)
        draft_token = response.get_json()['draft_token']
        updated = self.request('draft', {'draft_token': draft_token, 'intent': self.intent})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 0)
        client = self.app.test_client()
        result = client.post('/api/member/login-link/consume', json={'token': self.email_token()}, headers={'Origin': ORIGIN})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['newsletter_intent']['stations'][0]['station_id'], 'FR_2')
        # The same request is available after a reload or password login, only to its owner.
        self.assertEqual(client.get('/api/member/newsletter/request').get_json()['intent']['stations'][0]['station_id'], 'FR_2')
        self.assertEqual(self.request('draft', {'draft_token': draft_token, 'intent': self.intent}).status_code, 401)
        self.assertEqual(self.request('apply', {'intent': result.get_json()['newsletter_intent']}, client, result.get_json()['csrf_token']).status_code, 200)
        self.assertEqual(NewsletterSubscriber.get(NewsletterSubscriber.email == 'new@example.com').status, 'active')
        self.assertIsNone(client.get('/api/member/newsletter/request').get_json()['intent'])

    def test_add_weather_preserves_other_topics_and_frequency_on_same_station(self):
        self.post('', {'station_id': 'FR_2'})
        self.post('/FR_2', {'articles_enabled': True, 'newsletter_frequency': 'monthly', 'opening_closing_enabled': True})
        self.request('apply', {'intent': self.intent}, self.client, self.csrf)
        pref = NewsletterStationPreference.get()
        self.assertTrue(pref.articles_enabled)
        self.assertTrue(pref.opening_closing_enabled)
        self.assertEqual(pref.newsletter_frequency, 'monthly')
        self.assertTrue(pref.weather_enabled)

    def test_invalid_inputs_csrf_expired_draft_and_foreign_identity_are_rejected(self):
        for intent in [dict(self.intent, consent=False), dict(self.intent, stations=[{'station_id':'hidden','topics':['weather']}]), dict(self.intent, stations=[{'station_id':'FR_2','topics':['unknown']}]), dict(self.intent, stations=[{'station_id':'FR_2','topics':['weather'],'weather_frequency':[]}])]:
            self.assertEqual(self.start(intent=intent).status_code, 400)
        response = self.start()
        token = response.get_json()['draft_token']
        self.assertEqual(self.request('draft', {'draft_token': token + 'tampered', 'intent': self.intent}).status_code, 401)
        self.assertEqual(self.request('apply', {'intent': self.intent}, self.client).status_code, 403)
        self.assertIsNone(self.other.get('/api/member/newsletter/request').get_json()['intent'])
        MemberLoginLink.update(expires_at=utcnow()-timedelta(seconds=1)).execute()
        self.assertEqual(self.request('draft', {'draft_token':token,'intent':self.intent}).status_code, 401)
        self.assertEqual(self.request('apply', {'intent':dict(self.intent,subscriber_id=self.b.id)}, self.client, self.csrf).status_code, 400)

    def test_unsubscribed_is_not_reactivated_before_confirmation_and_suppression_is_preserved(self):
        NewsletterSubscriber.update(status='unsubscribed').where(NewsletterSubscriber.id == self.a.id).execute()
        self.start()
        self.assertEqual(NewsletterSubscriber.get_by_id(self.a.id).status, 'unsubscribed')
        self.assertEqual(self.request('apply', {'intent':self.intent}, self.client, self.csrf).status_code, 200)
        NewsletterSubscriber.update(status='complained').where(NewsletterSubscriber.id == self.a.id).execute()
        self.assertEqual(self.request('apply', {'intent':self.intent}, self.client, self.csrf).status_code, 409)
        self.assertEqual(NewsletterSubscriber.get_by_id(self.a.id).status, 'complained')

    def test_delivery_failure_does_not_return_a_draft_or_expose_credentials(self):
        import requests
        self.sender.side_effect = requests.RequestException('offline')
        response = self.start('new@example.com')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('draft_token', response.get_json())
        self.assertIsNone(MemberLoginLink.get().token_hash)
        self.assertIsNone(MemberLoginLink.get().newsletter_intent)

if __name__ == '__main__':
    unittest.main()
