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
        self.assertEqual(MemberUser.select().count(),3)  # Signup creates its one account.
        self.assertEqual(MemberLoginLink.select().count(),1)
        self.assertEqual(self.app.test_client().get('/api/member/session').status_code,401)
        self.assertIn('a bien été prise en compte',self.sender.call_args.kwargs['json']['htmlContent'])
        self.assertNotIn('Aucune confirmation',self.sender.call_args.kwargs['json']['htmlContent'])
        self.assertIn('/connexion/lien#',self.sender.call_args.kwargs['json']['htmlContent'])

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

    def test_welcome_link_and_password_open_the_same_account_and_subscriptions(self):
        response=self.start()
        self.assertEqual(response.status_code,201)
        member=MemberUser.get(MemberUser.email=='new@example.com')
        self.assertIsNone(member.verified_at); self.assertIsNone(member.password_hash)
        token=self.sender.call_args.kwargs['json']['textContent'].split('#')[1].split()[0]
        email_client=self.app.test_client()
        signed=email_client.post('/api/member/login-link/consume',json={'token':token},headers={'Origin':ORIGIN})
        self.assertEqual(signed.status_code,200)
        self.assertEqual(signed.get_json()['user']['id'],member.id)
        stations_by_link=email_client.get('/api/member/newsletter/stations').get_json()['data']['stations']
        self.assertEqual(stations_by_link[0]['station']['id'],'FR_2')
        password='one shared account password'
        changed=email_client.post('/api/member/password',json={'password':password},headers={'Origin':ORIGIN,'X-CSRF-Token':signed.get_json()['csrf_token']})
        self.assertEqual(changed.status_code,200)
        password_client=self.app.test_client()
        logged=password_client.post('/api/member/login',json={'email':' NEW@EXAMPLE.COM ','password':password},headers={'Origin':ORIGIN})
        self.assertEqual(logged.status_code,200)
        self.assertEqual(logged.get_json()['user']['id'],member.id)
        self.assertEqual(password_client.get('/api/member/newsletter/stations').get_json()['data']['stations'],stations_by_link)
        self.assertEqual(MemberUser.select().where(MemberUser.email=='new@example.com').count(),1)
        self.assertEqual(NewsletterSubscriber.select().where(NewsletterSubscriber.email=='new@example.com').count(),1)

    def test_duplicate_signup_keeps_existing_password_and_only_one_account(self):
        self.start()
        account=MemberUser.get(MemberUser.email=='new@example.com')
        from app.services.admin_auth import hash_password
        account.password_hash=hash_password('existing secure password');account.verified_at=utcnow();account.save()
        old_hash=account.password_hash
        self.assertEqual(self.start(' NEW@EXAMPLE.COM ').get_json()['status'],'already_subscribed')
        account=MemberUser.get_by_id(account.id)
        self.assertEqual(account.password_hash,old_hash)
        self.assertEqual(MemberUser.select().where(MemberUser.email==account.email).count(),1)

    def test_old_preferences_link_can_request_email_access_but_cannot_authenticate(self):
        legacy=self.a.preferences_token
        client=self.app.test_client()
        before=client.post('/api/member/login-link/consume',json={'token':legacy},headers={'Origin':ORIGIN})
        self.assertEqual(before.status_code,401)
        response=self.request('account-link',{'token':legacy},client)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('Set-Cookie',response.headers)
        self.assertNotIn('token',response.get_json())
        self.assertEqual(client.get('/api/member/session').status_code,401)
        login_token=self.sender.call_args.kwargs['json']['textContent'].split('#')[1].split()[0]
        access=client.post('/api/member/login-link/consume',json={'token':login_token},headers={'Origin':ORIGIN})
        self.assertEqual(access.status_code,200)
        self.assertEqual(access.get_json()['user']['email'],'a@example.com')
        self.assertEqual(self.request('account-link',{'token':'invalid'},client).status_code,404)

    def test_newsletter_only_subscriber_can_use_normal_account_login_link(self):
        import secrets
        NewsletterSubscriber.create(email='legacy@example.com',status='active',language='fr',source='footer',consent_at=utcnow(),consent_text_version='v1',consent_source='footer',preferences_token=secrets.token_urlsafe(48))
        self.assertFalse(MemberUser.select().where(MemberUser.email=='legacy@example.com').exists())
        response=self.app.test_client().post('/api/member/login-link',json={'email':'legacy@example.com'},headers={'Origin':ORIGIN})
        self.assertEqual(response.status_code,200)
        self.assertEqual(MemberUser.select().where(MemberUser.email=='legacy@example.com').count(),1)
        self.assertEqual(NewsletterSubscriber.get(NewsletterSubscriber.email=='legacy@example.com').status,'active')

    def test_single_editor_reads_without_subscribing_and_saves_a_complete_selection(self):
        before=NewsletterSubscriber.select().count()
        data=self.client.get('/api/member/newsletter/settings').get_json()['data']
        self.assertEqual(data['status'],'active')
        self.assertEqual(data['intent']['stations'],[])
        self.assertEqual(NewsletterSubscriber.select().count(),before)
        intent={'articles':False,'newsletter_frequency':'monthly','stations':[{'station_id':'FR_1','topics':['weather'],'weather_frequency':'daily'},{'station_id':'FR_2','topics':['snowfall','ski_pass_updates'],'newsletter_frequency':'monthly'}],'consent':True,'source':'member-account'}
        saved=self.request('settings',{'intent':intent},self.client,self.csrf)
        self.assertEqual(saved.status_code,200)
        result=saved.get_json()['data']['intent']
        self.assertEqual({row['station_id'] for row in result['stations']},{'FR_1','FR_2'})
        self.assertFalse(result['articles']); self.assertEqual(result['newsletter_frequency'],'monthly')
        self.assertEqual(self.other.get('/api/member/newsletter/settings').get_json()['data']['intent']['stations'],[])

    def test_single_editor_removes_stations_unchecks_topics_and_keeps_paused_stations(self):
        first=dict(self.intent,stations=[{'station_id':'FR_1','topics':['weather','snowfall']},{'station_id':'FR_2','topics':['weather']}])
        self.assertEqual(self.request('settings',{'intent':first},self.client,self.csrf).status_code,200)
        paused=dict(self.intent,stations=[{'station_id':'FR_1','topics':[],'weather_frequency':'disabled','newsletter_frequency':'monthly'}])
        result=self.request('settings',{'intent':paused},self.client,self.csrf)
        self.assertEqual(result.status_code,200)
        rows=result.get_json()['data']['intent']['stations']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['station_id'],'FR_1');self.assertEqual(rows[0]['topics'],[])
        self.assertEqual(rows[0]['newsletter_frequency'],'monthly');self.assertEqual(rows[0]['weather_frequency'],'disabled')
        self.assertEqual(NewsletterSubscriberStation.select().where(NewsletterSubscriberStation.subscriber==self.a.id).count(),1)

    def test_single_editor_rejects_invalid_bulk_selection_without_partial_changes(self):
        self.request('settings',{'intent':self.intent},self.client,self.csrf)
        before=self.client.get('/api/member/newsletter/settings').get_json()
        invalid=dict(self.intent,stations=[{'station_id':'FR_1','topics':['snowfall']},{'station_id':'missing','topics':['weather']}])
        self.assertEqual(self.request('settings',{'intent':invalid},self.client,self.csrf).status_code,400)
        self.assertEqual(self.client.get('/api/member/newsletter/settings').get_json(),before)
        self.assertEqual(self.request('settings',{'intent':self.intent},self.client).status_code,403)
        self.assertEqual(self.request('settings',{'intent':self.intent}).status_code,401)
        self.assertEqual(self.request('settings',{'intent':dict(self.intent,subscriber_id=self.b.id)},self.client,self.csrf).status_code,400)

    def test_single_editor_preserves_existing_inactive_station_but_does_not_add_new_inactive_ones(self):
        NewsletterSubscriberStation.create(subscriber=self.a,station='hidden')
        NewsletterStationPreference.create(subscriber=self.a,station='hidden',weather_enabled=False)
        existing=self.client.get('/api/member/newsletter/settings').get_json()['data']['intent']
        self.assertEqual(self.request('settings',{'intent':existing},self.client,self.csrf).status_code,200)
        self.assertEqual(self.request('settings',{'intent':existing},self.other,self.other_csrf).status_code,400)

    def test_signup_and_account_editor_share_article_frequency_and_station_choices(self):
        signup=self.start().get_json();token=signup['draft_token']
        initial=dict(self.intent,articles=True,newsletter_frequency='monthly')
        self.assertEqual(self.request('draft',{'draft_token':token,'intent':initial}).status_code,200)
        login=self.sender.call_args.kwargs['json']['textContent'].split('#')[1].split()[0]
        client=self.app.test_client()
        result=client.post('/api/member/login-link/consume',json={'token':login},headers={'Origin':ORIGIN})
        self.assertEqual(result.status_code,200)
        settings=client.get('/api/member/newsletter/settings').get_json()['data']['intent']
        self.assertTrue(settings['articles']);self.assertEqual(settings['newsletter_frequency'],'monthly')
        self.assertEqual(settings['stations'][0]['station_id'],'FR_2')


    def test_station_cta_immediately_adds_only_requested_topic_and_preserves_other_settings(self):
        previous = dict(self.intent, articles=False, newsletter_frequency='monthly', stations=[
            {'station_id': 'FR_1', 'topics': ['snowfall'], 'newsletter_frequency': 'monthly'},
            {'station_id': 'FR_2', 'topics': ['weather'], 'weather_frequency': 'daily', 'newsletter_frequency': 'monthly'}])
        self.request('settings', {'intent': previous}, self.client, self.csrf)
        response = self.request('follow', {'station_id': 'FR_2', 'topic': 'resort_updates'}, self.client, self.csrf)
        self.assertEqual(response.status_code, 200); self.assertFalse(response.get_json()['already_followed'])
        settings = self.client.get('/api/member/newsletter/settings').get_json()['data']['intent']
        rows = {row['station_id']: row for row in settings['stations']}
        self.assertEqual(set(rows['FR_2']['topics']), {'weather', 'resort_updates'})
        self.assertEqual(rows['FR_2']['weather_frequency'], 'daily'); self.assertEqual(rows['FR_2']['newsletter_frequency'], 'monthly')
        self.assertEqual(rows['FR_1']['topics'], ['snowfall']); self.assertFalse(settings['articles'])
        self.assertEqual(settings['newsletter_frequency'], 'monthly')
        repeated = self.request('follow', {'station_id': 'FR_2', 'topic': 'weather'}, self.client, self.csrf)
        self.assertFalse(repeated.get_json()['already_followed'])
        after = self.client.get('/api/member/newsletter/settings').get_json()['data']['intent']
        weather = next(row for row in after['stations'] if row['station_id'] == 'FR_2')
        self.assertEqual(set(weather['topics']), {'weather', 'snowfall', 'snow_conditions', 'resort_updates'})
        self.assertEqual(weather['weather_frequency'], 'daily')
        self.assertTrue(self.request('follow', {'station_id': 'FR_2', 'topic': 'weather'}, self.client, self.csrf).get_json()['already_followed'])
        self.assertEqual(self.other.get('/api/member/newsletter/settings').get_json()['data']['intent']['stations'], [])

    def test_station_cta_weather_enables_paused_weather_and_reactivates_subscription(self):
        previous = dict(self.intent, stations=[{'station_id': 'FR_2', 'topics': ['weather'], 'weather_frequency': 'disabled'}])
        self.request('settings', {'intent': previous}, self.client, self.csrf)
        self.request('unsubscribe', {}, self.client, self.csrf)
        response = self.request('follow', {'station_id': 'FR_2', 'topic': 'weather'}, self.client, self.csrf)
        self.assertEqual(response.status_code, 200); self.assertFalse(response.get_json()['already_followed'])
        settings = self.client.get('/api/member/newsletter/settings').get_json()['data']
        self.assertEqual(settings['status'], 'active'); self.assertEqual(settings['intent']['stations'][0]['weather_frequency'], 'friday')

    def test_station_cta_requires_session_csrf_valid_station_and_owner(self):
        data = {'station_id': 'FR_2', 'topic': 'weather'}
        self.assertEqual(self.request('follow', data).status_code, 401)
        self.assertEqual(self.request('follow', data, self.client).status_code, 403)
        for invalid in [dict(data, email=self.b.email), dict(data, station_id='hidden'), dict(data, station_id='missing'), dict(data, topic='bad')]:
            self.assertEqual(self.request('follow', invalid, self.client, self.csrf).status_code, 400)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 0)
        self.a.status = 'complained'; self.a.save()
        self.assertEqual(self.request('follow', data, self.client, self.csrf).status_code, 409)
        self.assertEqual(NewsletterSubscriberStation.select().count(), 0)

    def test_station_cta_limit_prevents_adding_but_allows_existing_station(self):
        from app.models.resort import Resort
        for index in range(20):
            station = Resort.create(id='limit_' + str(index), name='Limit ' + str(index), slug='limit-' + str(index), is_active=True)
            NewsletterSubscriberStation.create(subscriber=self.a, station=station)
        response = self.request('follow', {'station_id': 'FR_2', 'topic': 'weather'}, self.client, self.csrf)
        self.assertEqual(response.status_code, 409); self.assertEqual(response.get_json()['error'], 'station_limit_reached')
        self.assertEqual(self.request('follow', {'station_id': 'limit_0', 'topic': 'weather'}, self.client, self.csrf).status_code, 200)
        self.assertEqual(NewsletterSubscriberStation.select().where(NewsletterSubscriberStation.subscriber == self.a).count(), 20)


    def test_member_unsubscribe_stops_all_emails_and_preserves_preferences(self):
        self.request('settings', {'intent': self.intent}, self.client, self.csrf)
        before = self.client.get('/api/member/newsletter/settings').get_json()['data']['intent']
        self.assertEqual(self.request('unsubscribe', {}, self.client).status_code, 403)
        result = self.request('unsubscribe', {}, self.client, self.csrf)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['data']['status'], 'unsubscribed')
        settings = self.client.get('/api/member/newsletter/settings').get_json()['data']
        self.assertEqual(settings['status'], 'unsubscribed')
        self.assertEqual(settings['intent'], before)
        self.assertEqual(self.other.get('/api/member/newsletter/settings').get_json()['data']['status'], 'active')
        self.assertEqual(self.request('unsubscribe', {}, self.client, self.csrf).status_code, 200)
