import secrets
import unittest
from datetime import timedelta
from unittest.mock import patch
from flask import Flask, jsonify
from peewee import SqliteDatabase
from app import create_app
from app.datetime_utils import utcnow
from app.models.member import MemberUser, MemberSession, MemberLoginLink, MemberLoginAttempt
from app.models.admin_user import AdminUser
from app.models.admin_session import AdminSession
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.newsletter import NewsletterSubscriber, SnowNewsletterPreference
from app.routes.member import bp_member
from app.routes.admin_auth import bp_admin_auth
from app.services.admin_auth import protect_admin_routes, hash_password

MODELS = [MemberUser, MemberSession, MemberLoginLink, MemberLoginAttempt, AdminUser, AdminSession, AdminLoginAttempt, NewsletterSubscriber, SnowNewsletterPreference]
ORIGIN = 'https://www.snow-explorer.com'
class MemberAccountTests(unittest.TestCase):
    def setUp(self):
        self.db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
        self.db.bind(MODELS, bind_backrefs=False)
        self.db.connect(); self.db.create_tables(MODELS)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, ADMIN_SESSION_SECRET='s'*64, ADMIN_SESSION_COOKIE_NAME='admin_session', ADMIN_SESSION_TTL_SECONDS=28800, ADMIN_SESSION_TOUCH_INTERVAL_SECONDS=300, ADMIN_COOKIE_SECURE=True, ADMIN_COOKIE_SAMESITE='None', ADMIN_LOGIN_RATE_LIMIT=5, ADMIN_LOGIN_RATE_WINDOW_SECONDS=900, TRUST_PROXY_HEADERS=False, ADMIN_ALLOWED_ORIGINS=[ORIGIN], FRONTEND_URL=ORIGIN, BREVO_API_KEY='test', BREVO_SENDER_EMAIL='hello@example.com', BREVO_SENDER_NAME='Snow Explorer')
        self.app.register_blueprint(bp_member); self.app.register_blueprint(bp_admin_auth); protect_admin_routes(self.app)
        self.app.add_url_rule('/api/admin/probe', view_func=lambda: jsonify({'ok':True}))
        self.client = self.app.test_client()
        self.sender_patch = patch('app.services.member_auth.requests.post'); self.sender = self.sender_patch.start()
        self.addCleanup(self.sender_patch.stop)
    def tearDown(self):
        self.db.drop_tables(list(reversed(MODELS))); self.db.close()
    def post(self,path,body=None,csrf=None,client=None,origin=ORIGIN):
        headers={'Origin':origin}
        if csrf: headers['X-CSRF-Token']=csrf
        return (client or self.client).post('/api/member'+path,json=body or {},headers=headers)
    def token(self):
        return self.sender.call_args.kwargs['json']['textContent'].split('#')[1].split()[0]
    def signup(self,email='member@example.com'):
        self.assertEqual(self.post('/register',{'email':email}).status_code,201)
        token=self.token()
        self.assertIn('/connexion/lien#',self.sender.call_args.kwargs['json']['textContent'])
        response=self.post('/login-link/consume',{'token':token})
        self.assertEqual(response.status_code,200)
        return response.get_json()['csrf_token']
    def test_registration_confirms_email_before_session_or_newsletter_access(self):
        response=self.post('/register',{'email':' MEMBER@EXAMPLE.COM '})
        self.assertEqual(response.status_code,201)
        self.assertNotIn('Set-Cookie',response.headers)
        member=MemberUser.get(); self.assertIsNone(member.verified_at); self.assertIsNone(member.password_hash)
        self.assertEqual(NewsletterSubscriber.select().count(),0)
        self.assertEqual(self.client.get('/api/member/newsletter/access').status_code,401)
        token=self.token(); self.assertNotEqual(MemberLoginLink.get().token_hash,token)
        self.assertEqual(self.post('/register',{'email':member.email}).status_code,429)
        response=self.post('/login-link/consume',{'token':token})
        self.assertEqual(response.status_code,200)
        self.assertIn('member_session=',response.headers['Set-Cookie']); self.assertIn('HttpOnly',response.headers['Set-Cookie']); self.assertIn('Secure',response.headers['Set-Cookie'])
        self.assertFalse(response.get_json()['user']['password_set']); self.assertNotIn('token',response.get_json())
        self.assertEqual(self.post('/login-link/consume',{'token':token}).status_code,401)
        self.assertEqual(self.post('/register',{'email':member.email}).status_code,409)
        self.assertEqual(self.post('/login',{'email':member.email,'password':'any password'}).get_json()['error'],'password_not_set')
        self.assertEqual(self.client.get('/api/member/newsletter/access').get_json(),{'status':'not_subscribed'})
    def test_member_and_admin_sessions_and_accounts_are_independent(self):
        AdminUser.create(email='member@example.com',password_hash=hash_password('admin secure password'))
        admin=self.app.test_client()
        response=admin.post('/api/admin/auth/login',json={'email':'member@example.com','password':'admin secure password'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(admin.get('/api/member/session').status_code,401)
        self.assertEqual(self.post('/login-link',{'email':'member@example.com'}).status_code,404)
        self.signup()
        self.assertEqual(self.client.get('/api/admin/probe').status_code,401)
        self.assertEqual(admin.get('/api/admin/probe').status_code,200)
        csrf=self.client.get('/api/member/session').get_json()['csrf_token']
        self.assertEqual(self.post('/logout',csrf=csrf).status_code,200)
        self.assertEqual(self.client.get('/api/member/session').status_code,401)
        self.assertEqual(admin.get('/api/admin/probe').status_code,200)
    def test_password_rotation_revokes_only_member_sessions_and_pending_links(self):
        csrf=self.signup()
        self.assertEqual(self.post('/password',{'password':'new secure password'}).status_code,403)
        self.assertEqual(self.post('/password',{'password':'short'},csrf).status_code,400)
        self.assertEqual(self.post('/password',{'password':'new secure password'},'é').status_code,403)
        first_cookie=self.client.get_cookie('member_session').value
        changed=self.post('/password',{'password':'new secure password'},csrf)
        self.assertEqual(changed.status_code,200)
        other=self.app.test_client(); other.set_cookie('member_session',first_cookie)
        self.assertEqual(other.get('/api/member/session').status_code,401)
        other=self.app.test_client()
        self.assertEqual(self.post('/login',{'email':'member@example.com','password':'new secure password'},client=other).status_code,200)
        MemberLoginLink.update(requested_at=utcnow()-timedelta(minutes=2)).execute()
        self.assertEqual(self.post('/login-link',{'email':'member@example.com'}).status_code,200)
        pending=self.token()
        second=self.post('/password',{'password':'another secure password'},changed.get_json()['csrf_token'])
        self.assertEqual(second.status_code,200)
        self.assertEqual(other.get('/api/member/session').status_code,401)
        self.assertEqual(self.post('/login-link/consume',{'token':pending}).status_code,401)
        self.assertEqual(self.client.get('/api/member/session').status_code,200)
    def test_origin_expiration_disabled_account_and_delivery_failure(self):
        self.assertEqual(self.post('/register',{'email':'member@example.com'},origin='https://evil.example').status_code,403)
        self.assertEqual(MemberUser.select().count(),0)
        self.post('/register',{'email':'member@example.com'}); token=self.token()
        MemberLoginLink.update(expires_at=utcnow()-timedelta(seconds=1)).execute()
        self.assertEqual(self.post('/login-link/consume',{'token':token}).status_code,401)
        MemberLoginLink.update(expires_at=utcnow()+timedelta(minutes=1)).execute()
        MemberUser.update(is_active=False).execute()
        self.assertEqual(self.post('/login-link/consume',{'token':token}).status_code,401)
        self.assertEqual(self.post('/login-link',{'email':'member@example.com'}).status_code,403)
        import requests
        self.sender.side_effect=requests.RequestException('failure')
        self.assertEqual(self.post('/register',{'email':'second@example.com'}).status_code,503)
        self.assertIsNone(MemberLoginLink.get(MemberLoginLink.member==MemberUser.get(MemberUser.email=='second@example.com')).token_hash)
        self.assertEqual(MemberSession.select().count(),0)
    def test_newsletter_requires_verified_owner_and_explicit_consent(self):
        subscriber=NewsletterSubscriber.create(email='member@example.com',status='unsubscribed',language='fr',source='footer',consent_at=utcnow(),consent_text_version='v1',consent_source='footer',preferences_token=secrets.token_urlsafe(48))
        csrf=self.signup()
        access=self.client.get('/api/member/newsletter/access').get_json()
        self.assertEqual(access['status'],'unsubscribed'); self.assertNotIn('token',access)
        payload={'email':'member@example.com','language':'fr','source':'member-account','consentTextVersion':'v1','consent':False}
        self.assertEqual(self.post('/newsletter/subscribe',payload,csrf).status_code,400)
        self.assertEqual(NewsletterSubscriber.get().status,'unsubscribed')
        payload['consent']=True; payload['email']='someone@example.com'
        self.assertEqual(self.post('/newsletter/subscribe',payload,csrf).status_code,403)
        payload['email']='member@example.com'
        with patch('app.routes.newsletter.send_welcome_email'):
            self.assertEqual(self.post('/newsletter/subscribe',payload,csrf).status_code,201)
        access=self.client.get('/api/member/newsletter/access')
        self.assertNotIn('token',access.get_json())
        self.assertEqual(access.get_json()['status'],'active')
        self.assertEqual(self.client.get('/api/member/newsletter/preferences').status_code,200)
        self.assertEqual(self.post('/newsletter/preferences',{'weather':False,'newsletter_frequency':'monthly'},csrf).status_code,200)
        self.assertFalse(SnowNewsletterPreference.get().weather)
        self.assertEqual(SnowNewsletterPreference.get().newsletter_frequency,'monthly')
        self.assertEqual(self.post('/newsletter/preferences',{'email':'someone@example.com'},csrf).status_code,400)
        self.assertEqual(access.headers['Cache-Control'],'no-store')
        self.assertEqual(self.post('/newsletter/unsubscribe',csrf=csrf).status_code,200)
        self.assertEqual(NewsletterSubscriber.get().status,'unsubscribed')
        self.assertEqual(self.client.get('/api/member/newsletter/preferences').status_code,409)
        with patch('app.routes.newsletter.send_welcome_email'):
            self.assertEqual(self.post('/newsletter/subscribe',payload,csrf).status_code,201)
        self.assertEqual(SnowNewsletterPreference.get().newsletter_frequency,'monthly')
        NewsletterSubscriber.update(status='complained').execute()
        self.assertEqual(self.post('/newsletter/subscribe',payload,csrf).status_code,409)
    def test_stale_password_check_cannot_create_a_session_after_password_change(self):
        csrf = self.signup()
        self.assertEqual(self.post('/password', {'password': 'original secure password'}, csrf).status_code, 200)
        before = MemberSession.select().count()
        replacement = hash_password('replacement secure password')
        def changed_during_verification(*args):
            MemberUser.update(password_hash=replacement, password_changed_at=utcnow()).execute()
            return True
        with patch('app.routes.member.verify_password', side_effect=changed_during_verification):
            response = self.post('/login', {'email': 'member@example.com', 'password': 'original secure password'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(MemberSession.select().count(), before)
        self.assertEqual(MemberUser.get().password_hash, replacement)

    def test_rate_limit_and_credentialed_cors(self):
        for _ in range(10): self.assertEqual(self.post('/login-link',{'email':'missing@example.com'}).status_code,404)
        self.assertEqual(self.post('/login-link',{'email':'missing@example.com'}).status_code,429)
        app=create_app({**self.app.config,'SKIP_DATABASE_INIT':True,'PUBLIC_CACHE_ENABLED':False})
        with patch('app.db'):
            response=app.test_client().options('/api/member/login',headers={'Origin':ORIGIN,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'Content-Type,X-CSRF-Token'})
        self.assertEqual(response.headers.get('Access-Control-Allow-Credentials'),'true')
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'),ORIGIN)

if __name__=='__main__': unittest.main()
