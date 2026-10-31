from unittest.mock import patch
from datetime import timedelta
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
        self.intent = {'stations':[{'station_id':'FR_2','topics':['weather'],'weather_frequency':'friday','newsletter_frequency':'weekly'}], 'consent':True,'language':'fr','source':'station_page'}

    def request(self, path, data, client=None, csrf=None):
        return (client or self.app.test_client()).post('/api/member/newsletter/'+path,json=data,headers={'Origin':ORIGIN,**({'X-CSRF-Token':csrf} if csrf else {})})

    def start(self, email='new@example.com', intent=None):
        return self.request('start',{'email':email,'intent':intent or self.intent})

    def test_new_signup_is_immediately_active_without_account_validation(self):
        response = self.start()
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.get_json()['status'],'active')
        self.assertFalse(response.get_json()['verification_required'])
        self.assertEqual(NewsletterSubscriber.get(NewsletterSubscriber.email=='new@example.com').status,'active')
        pref=NewsletterStationPreference.get()
        self.assertTrue(pref.weather_enabled); self.assertFalse(pref.resort_updates_enabled)
        self.assertEqual(MemberUser.select().count(),2)  # Only the two fixture accounts.
        self.assertEqual(MemberLoginLink.select().count(),0)
        self.assertEqual(self.app.test_client().get('/api/member/session').status_code,401)
        self.assertIn('Aucune confirmation',self.sender.call_args.kwargs['json']['htmlContent'])
        self.assertNotIn('/connexion/lien',self.sender.call_args.kwargs['json']['htmlContent'])

    def test_five_duplicate_requests_do_not_resend_mail_or_change_preferences(self):
        self.start()
        count=self.sender.call_count
        for _ in range(5):
            response=self.start(' NEW@EXAMPLE.COM ',{'stations':[],'articles':True,'consent':True,'source':'footer'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.get_json()['status'],'already_subscribed')
            self.assertNotIn('draft_token',response.get_json())
        self.assertEqual(self.sender.call_count,count)
        self.assertEqual(NewsletterSubscriber.select().where(NewsletterSubscriber.email=='new@example.com').count(),1)
        self.assertTrue(NewsletterStationPreference.get().weather_enabled)
        self.assertEqual(NewsletterSubscriberStation.select().count(),1)

    def test_customization_saves_immediately_and_removing_a_station_does_not_reappear(self):
        response=self.start()
        token=response.get_json()['draft_token']
        changed={'stations':[{'station_id':'FR_1','topics':['snowfall']}],'articles':False,'consent':True,'source':'footer'}
        response=self.request('draft',{'draft_token':token,'intent':changed})
        self.assertEqual(response.status_code,200)
        pref=NewsletterStationPreference.get(); self.assertEqual(pref.station_id,'FR_1'); self.assertTrue(pref.snowfall_enabled);self.assertFalse(pref.weather_enabled)
        empty={'stations':[],'articles':True,'consent':True,'source':'footer'}
        self.assertEqual(self.request('draft',{'draft_token':token,'intent':empty}).status_code,200)
        self.assertEqual(NewsletterSubscriberStation.select().count(),0)
        self.assertEqual(NewsletterSubscriber.get(NewsletterSubscriber.email=='new@example.com').status,'active')

    def test_signup_edit_token_cannot_log_in_or_modify_an_existing_subscriber(self):
        token=self.start().get_json()['draft_token']
        self.assertEqual(self.app.test_client().post('/api/member/login-link/consume',json={'token':token},headers={'Origin':ORIGIN}).status_code,401)
        # Client-supplied identity is not accepted by normalization.
        self.assertEqual(self.request('draft',{'draft_token':token,'intent':dict(self.intent,subscriber_id=self.a.id)}).status_code,400)
        self.assertEqual(NewsletterSubscriberStation.select().where(NewsletterSubscriberStation.subscriber==self.a.id).count(),0)
        self.assertEqual(self.request('draft',{'draft_token':token+'bad','intent':self.intent}).status_code,401)
        NewsletterSubscriber.update(status='unsubscribed').where(NewsletterSubscriber.email=='new@example.com').execute()
        self.assertEqual(self.request('draft',{'draft_token':token,'intent':self.intent}).status_code,401)

    def test_email_failure_does_not_lose_the_subscription(self):
        import requests
        self.sender.side_effect=requests.RequestException('offline')
        response=self.start()
        self.assertEqual(response.status_code,201)
        self.assertFalse(response.get_json()['email_sent'])
        self.assertEqual(NewsletterSubscriber.get(NewsletterSubscriber.email=='new@example.com').status,'active')
        self.assertTrue(NewsletterStationPreference.get().weather_enabled)

    def test_login_is_requested_separately_and_preserves_station_intent(self):
        self.post('',{'station_id':'FR_1'})
        self.assertEqual(self.request('login-link',{'email':'a@example.com','intent':self.intent}).status_code,200)
        token=self.sender.call_args.kwargs['json']['textContent'].split('#')[1].split()[0]
        client=self.app.test_client()
        result=client.post('/api/member/login-link/consume',json={'token':token},headers={'Origin':ORIGIN})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.get_json()['newsletter_intent']['stations'][0]['station_id'],'FR_2')
        self.assertEqual(NewsletterSubscriberStation.select().count(),1)
        self.assertEqual(self.request('apply',{'intent':self.intent},client,result.get_json()['csrf_token']).status_code,200)
        self.assertEqual(NewsletterSubscriberStation.select().count(),2)

    def test_legacy_pending_can_subscribe_without_validation_and_suppressed_stays_blocked(self):
        NewsletterSubscriber.update(status='pending').where(NewsletterSubscriber.id==self.a.id).execute()
        self.assertEqual(self.start('a@example.com').status_code,201)
        self.assertEqual(NewsletterSubscriber.get_by_id(self.a.id).status,'active')
        NewsletterSubscriber.update(status='complained').where(NewsletterSubscriber.id==self.b.id).execute()
        self.assertEqual(self.start('b@example.com').status_code,409)
        self.assertEqual(NewsletterSubscriber.get_by_id(self.b.id).status,'complained')
