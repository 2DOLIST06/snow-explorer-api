import secrets
import unittest
from flask import Flask
from peewee import SqliteDatabase
from app.datetime_utils import utcnow
from app.models.member import MemberUser, MemberSession, MemberLoginLink, MemberLoginAttempt
from app.models.resort import Resort
from app.models.newsletter import NewsletterSubscriber, SnowNewsletterPreference, NewsletterSubscriberStation, NewsletterStationPreference, SnowAlert
from app.routes.member import bp_member
from app.routes.newsletter import bp_newsletter
from app.services.member_auth import create_session, COOKIE

MODELS=[Resort,NewsletterSubscriber,SnowNewsletterPreference,NewsletterSubscriberStation,NewsletterStationPreference,SnowAlert,MemberUser,MemberSession,MemberLoginLink,MemberLoginAttempt]
ORIGIN='https://www.snow-explorer.com'
class StationSettingsTests(unittest.TestCase):
 def setUp(self):
  self.db=SqliteDatabase(':memory:',pragmas={'foreign_keys':1});self.db.bind(MODELS,bind_backrefs=False);self.db.connect();self.db.create_tables(MODELS)
  self.app=Flask(__name__);self.app.config.update(TESTING=True,ADMIN_SESSION_SECRET='s'*64,ADMIN_SESSION_TTL_SECONDS=28800,ADMIN_COOKIE_SECURE=True,ADMIN_COOKIE_SAMESITE='None',ADMIN_ALLOWED_ORIGINS=[ORIGIN],TRUST_PROXY_HEADERS=False)
  self.app.register_blueprint(bp_member);self.app.register_blueprint(bp_newsletter)
  self.a=self.subscriber('a@example.com');self.b=self.subscriber('b@example.com')
  SnowNewsletterPreference.create(subscriber=self.a,snowfall=False,weather=False,articles=True,newsletter_frequency='monthly')
  self.client,self.csrf=self.client_for('a@example.com');self.other,self.other_csrf=self.client_for('b@example.com')
  Resort.create(id='FR_1',name='Première station',slug='premiere',is_active=True)
  Resort.create(id='FR_2',name='Deuxième station',slug='deuxieme',is_active=True)
  Resort.create(id='hidden',name='Masquée',slug='masquee',is_active=False)
 def tearDown(self):
  self.db.drop_tables(list(reversed(MODELS)));self.db.close()
 def subscriber(self,email):
  return NewsletterSubscriber.create(email=email,status='active',language='fr',source='test',consent_at=utcnow(),consent_text_version='v1',consent_source='test',preferences_token=secrets.token_urlsafe(48))
 def client_for(self,email):
  member=MemberUser.create(email=email,verified_at=utcnow())
  with self.app.test_request_context('/'):
   raw=create_session(member)
  client=self.app.test_client();client.set_cookie(COOKIE,raw)
  return client,client.get('/api/member/session').get_json()['csrf_token']
 def post(self,suffix,payload=None,client=None,csrf=None):
  return (client or self.client).post('/api/member/newsletter/stations'+suffix,json=payload or {},headers={'Origin':ORIGIN,'X-CSRF-Token':csrf or self.csrf})
 def test_multiple_stations_start_disabled_then_keep_independent_topics_and_frequency(self):
  self.assertEqual(self.client.get('/api/member/newsletter/stations').get_json()['data']['stations'],[])
  first=self.post('',{'station_id':'FR_1'});self.assertEqual(first.status_code,201)
  prefs=first.get_json()['data']['preferences'];self.assertFalse(prefs['snowfall_enabled']);self.assertFalse(prefs['weather_enabled']);self.assertFalse(prefs['articles_enabled']);self.assertEqual(prefs['newsletter_frequency'],'weekly')
  self.assertEqual(self.post('',{'station_id':'FR_1'}).status_code,200)
  self.assertEqual(NewsletterSubscriberStation.select().count(),1)
  self.assertEqual(self.post('',{'station_id':'FR_2'}).status_code,201)
  changed=self.post('/FR_1',{'snowfall_enabled':True,'articles_enabled':False,'newsletter_frequency':'weekly'})
  self.assertEqual(changed.status_code,200)
  listed=self.client.get('/api/member/newsletter/stations').get_json()['data']['stations'];self.assertEqual(len(listed),2)
  by_id={row['station']['id']:row['preferences'] for row in listed}
  self.assertEqual(by_id['FR_1']['newsletter_frequency'],'weekly');self.assertEqual(by_id['FR_2']['newsletter_frequency'],'weekly')
  self.assertFalse(by_id['FR_2']['snowfall_enabled']);self.assertFalse(by_id['FR_2']['articles_enabled'])
  self.assertEqual(set(by_id['FR_1']),{'snowfall_enabled','snow_conditions_enabled','weather_enabled','resort_updates_enabled','articles_enabled','newsletter_frequency','weather_frequency','opening_closing_enabled','lift_updates_enabled','ski_pass_updates_enabled'})
 def test_removed_topics_invalid_types_frequencies_and_inactive_station_are_rejected(self):
  self.assertEqual(self.post('',{'station_id':'hidden'}).status_code,404)
  self.assertEqual(self.post('',{'station_id':'unknown'}).status_code,404)
  self.post('',{'station_id':'FR_1'})
  for body in [{'opening_closing':True},{'lift_updates':True},{'ski_pass_updates':True},{'weather_enabled':'yes'},{'newsletter_frequency':'daily'},{'newsletter_frequency':[]},{'newsletter_frequency':{}},{'subscriber_id':self.b.id}]:
   self.assertEqual(self.post('/FR_1',body).status_code,400)
 def test_member_settings_are_owned_and_csrf_protected(self):
  self.post('',{'station_id':'FR_1'})
  self.assertEqual(self.other.get('/api/member/newsletter/stations').get_json()['data']['stations'],[])
  self.assertEqual(self.post('/FR_1',{'articles_enabled':False},client=self.other,csrf=self.other_csrf).status_code,404)
  self.assertEqual(self.post('/FR_1/remove',client=self.other,csrf=self.other_csrf).status_code,404)
  self.assertEqual(self.client.post('/api/member/newsletter/stations',json={'station_id':'FR_2'},headers={'Origin':ORIGIN}).status_code,403)
  self.assertEqual(self.app.test_client().get('/api/member/newsletter/stations').status_code,401)
 def test_remove_only_own_station_settings_and_alerts(self):
  self.post('',{'station_id':'FR_1'});self.post('',{'station_id':'FR_1'},client=self.other,csrf=self.other_csrf)
  for subscriber in [self.a,self.b]:SnowAlert.create(subscriber=subscriber,station='FR_1',threshold_cm=10,forecast_period_hours=24)
  self.assertEqual(self.post('/FR_1/remove').status_code,200)
  self.assertEqual(NewsletterSubscriberStation.select().count(),1);self.assertEqual(NewsletterStationPreference.select().count(),1);self.assertEqual(SnowAlert.select().count(),1)
  self.assertEqual(SnowAlert.get().subscriber_id,self.b.id);self.assertEqual(NewsletterSubscriber.get_by_id(self.a.id).status,'active')
 def test_mail_link_and_member_editor_share_the_same_settings(self):
  base='/api/newsletter/preferences/'+self.a.preferences_token+'/station-settings'
  self.assertEqual(self.app.test_client().post(base,json={'station_id':'FR_1'}).status_code,201)
  changed=self.app.test_client().post(base+'/FR_1',json={'articles_enabled':False,'newsletter_frequency':'immediate'})
  self.assertEqual(changed.status_code,200)
  row=self.client.get('/api/member/newsletter/stations').get_json()['data']['stations'][0]
  self.assertFalse(row['preferences']['articles_enabled']);self.assertEqual(row['preferences']['newsletter_frequency'],'immediate')
  self.assertEqual(self.app.test_client().get('/api/newsletter/preferences/invalid/station-settings').status_code,404)
  NewsletterSubscriber.update(status='unsubscribed').where(NewsletterSubscriber.id==self.a.id).execute()
  self.assertEqual(self.client.get('/api/member/newsletter/stations').status_code,409)
  self.assertEqual(self.app.test_client().get(base).status_code,404)

if __name__=='__main__':unittest.main()
