"""Unverified requests are drafts only; only a member session can apply them."""
import json
import secrets
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask import current_app
from app.datetime_utils import utcnow
from app.models.member import MemberLoginLink
from app.models.newsletter import NewsletterSubscriber, NewsletterSubscriberStation, SnowNewsletterPreference, NewsletterStationPreference
from app.models.resort import Resort

TOPICS = {name: name + '_enabled' for name in (
    'snowfall', 'snow_conditions', 'weather', 'resort_updates', 'articles',
    'opening_closing', 'lift_updates', 'ski_pass_updates',
)}
FREQUENCIES = {'immediate', 'weekly', 'monthly'}
WEATHER_FREQUENCIES = {'daily', 'friday', 'weekly', 'disabled'}


def normalize_intent(value):
    if not isinstance(value, dict) or set(value) - {'stations', 'articles', 'consent', 'language', 'source'}:
        raise ValueError('invalid_newsletter_intent')
    if value.get('consent') is not True:
        raise ValueError('explicit_consent_required')
    language = value.get('language', 'fr')
    source = value.get('source', 'member-account')
    if language not in ('fr', 'en') or not isinstance(source, str) or not 1 <= len(source.strip()) <= 100:
        raise ValueError('invalid_newsletter_intent')
    result = {'stations': [], 'consent': True, 'language': language, 'source': source.strip()}
    if 'articles' in value:
        if type(value['articles']) is not bool:
            raise ValueError('invalid_newsletter_intent')
        result['articles'] = value['articles']
    rows = value.get('stations', [])
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError('invalid_newsletter_intent')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'station_id', 'station_name', 'topics', 'newsletter_frequency', 'weather_frequency'}:
            raise ValueError('invalid_newsletter_intent')
        station_id = row.get('station_id')
        topics = row.get('topics')
        frequency = row.get('newsletter_frequency', 'weekly')
        weather = row.get('weather_frequency', 'friday')
        if (not isinstance(station_id, (str, int)) or isinstance(station_id, bool)
                or not isinstance(topics, list) or not topics or len(topics) > len(TOPICS)
                or any(not isinstance(topic, str) or topic not in TOPICS for topic in topics)
                or not isinstance(frequency, str) or frequency not in FREQUENCIES
                or not isinstance(weather, str) or weather not in WEATHER_FREQUENCIES):
            raise ValueError('invalid_newsletter_intent')
        station_id = str(station_id)
        if len(station_id) > 255 or station_id in seen:
            raise ValueError('invalid_newsletter_intent')
        seen.add(station_id)
        station = Resort.get_or_none((Resort.id == station_id) & (Resort.is_active == True))
        if not station:
            raise ValueError('station_not_found')
        result['stations'].append({'station_id': station.id, 'station_name': station.name,
                                   'topics': sorted(set(topics)), 'newsletter_frequency': frequency,
                                   'weather_frequency': weather})
    return result


def _serializer():
    return URLSafeTimedSerializer(current_app.config['ADMIN_SESSION_SECRET'], salt='newsletter-draft-v1')


def draft_token(member):
    link = MemberLoginLink.get(MemberLoginLink.member == member)
    return _serializer().dumps({'member': member.id, 'hash': link.token_hash})


def update_draft(token, intent):
    if not isinstance(token, str) or len(token) > 2048:
        return False
    try:
        claim = _serializer().loads(token, max_age=900)
    except (BadSignature, SignatureExpired):
        return False
    # Capability is deliberately write-only and applies only to an unconfirmed
    # request. It cannot read or change an existing subscriber's preferences.
    return MemberLoginLink.update(newsletter_intent=json.dumps(intent)).where(
        (MemberLoginLink.member == claim['member']) & (MemberLoginLink.token_hash == claim['hash'])
        & (MemberLoginLink.expires_at > utcnow()) & MemberLoginLink.newsletter_intent.is_null(False)
    ).execute() == 1


def capture_pending(email, intent):
    now = utcnow()
    subscriber, created = NewsletterSubscriber.get_or_create(email=email, defaults={
        'status': 'pending', 'language': intent['language'], 'source': intent['source'],
        'consent_at': now, 'consent_text_version': 'v1', 'consent_source': intent['source'],
        'preferences_token': secrets.token_urlsafe(48),
    })
    if created:
        SnowNewsletterPreference.create(subscriber=subscriber, **{
            topic: False for topic in TOPICS
        })
    return subscriber


def apply_intent(email, intent):
    """Caller must own a verified member session; add choices without resetting others."""
    subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == email)
    if subscriber and subscriber.status in {'bounced', 'complained'}:
        raise ValueError('newsletter_unavailable')
    with NewsletterSubscriber._meta.database.atomic():
        subscriber = capture_pending(email, intent)
        updated = NewsletterSubscriber.update(
            status='active', confirmed_at=subscriber.confirmed_at or utcnow(),
            unsubscribed_at=None, consent_at=utcnow(), consent_text_version='v1',
            consent_source=intent['source'], updated_at=utcnow(),
        ).where((NewsletterSubscriber.id == subscriber.id) & NewsletterSubscriber.status.not_in(['bounced', 'complained'])).execute()
        if updated != 1:
            raise ValueError('newsletter_unavailable')
        general, _ = SnowNewsletterPreference.get_or_create(subscriber=subscriber)
        if 'articles' in intent:
            general.articles = intent['articles']
            general.updated_at = utcnow()
            general.save()
        for row in intent['stations']:
            station_id = row['station_id']
            NewsletterSubscriberStation.get_or_create(subscriber=subscriber, station=station_id)
            preference, created = NewsletterStationPreference.get_or_create(subscriber=subscriber, station=station_id, defaults={
                **{field: False for field in TOPICS.values()}, 'weather_frequency': 'disabled',
            })
            # Only change the requested topics. Adding a station's weather must
            # never unsubscribe another station or reset unrelated settings.
            for topic in row['topics']:
                setattr(preference, TOPICS[topic], True)
            if created or 'weather' in row['topics']:
                preference.weather_frequency = row['weather_frequency']
            if created or any(topic != 'weather' for topic in row['topics']):
                preference.newsletter_frequency = row['newsletter_frequency']
            preference.updated_at = utcnow()
            preference.save()
    return subscriber
