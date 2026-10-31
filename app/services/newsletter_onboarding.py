"""Immediate newsletter signup; account access remains verified separately."""
import json
import secrets
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask import current_app
from app.datetime_utils import utcnow
from app.models.member import MemberLoginLink
from app.models.newsletter import NewsletterSubscriber, NewsletterSubscriberStation, SnowNewsletterPreference, NewsletterStationPreference, SnowAlert
from app.models.resort import Resort
from app.services.admin_auth import _digest

TOPICS = {name: name + '_enabled' for name in (
    'snowfall', 'snow_conditions', 'weather', 'resort_updates', 'articles',
    'opening_closing', 'lift_updates', 'ski_pass_updates',
)}
FREQUENCIES = {'immediate', 'weekly', 'monthly'}
WEATHER_FREQUENCIES = {'daily', 'friday', 'weekly', 'disabled'}


def normalize_intent(value, retained_station_ids=()):
    if not isinstance(value, dict) or set(value) - {'stations', 'articles', 'consent', 'language', 'source', 'newsletter_frequency'}:
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
    if 'newsletter_frequency' in value:
        frequency = value['newsletter_frequency']
        if not isinstance(frequency, str) or frequency not in FREQUENCIES:
            raise ValueError('invalid_newsletter_intent')
        result['newsletter_frequency'] = frequency
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
                or not isinstance(topics, list) or len(topics) > len(TOPICS)
                or any(not isinstance(topic, str) or topic not in TOPICS for topic in topics)
                or not isinstance(frequency, str) or frequency not in FREQUENCIES
                or not isinstance(weather, str) or weather not in WEATHER_FREQUENCIES):
            raise ValueError('invalid_newsletter_intent')
        station_id = str(station_id)
        if len(station_id) > 255 or station_id in seen:
            raise ValueError('invalid_newsletter_intent')
        seen.add(station_id)
        station = Resort.get_or_none((Resort.id == station_id) & (Resort.is_active == True))
        if not station and station_id in retained_station_ids:
            station = Resort.get_or_none(Resort.id == station_id)
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
    if not isinstance(claim, dict) or set(claim) != {'member', 'hash'}:
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
    subscriber._newsletter_created = created
    return subscriber


def apply_intent(email, intent, consent_version="v1"):
    """Apply an explicit signup or an authenticated member choice, preserving others."""
    subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == email)
    if subscriber and subscriber.status in {'bounced', 'complained'}:
        raise ValueError('newsletter_unavailable')
    with NewsletterSubscriber._meta.database.atomic():
        subscriber = capture_pending(email, intent)
        updated = NewsletterSubscriber.update(
            status='active', confirmed_at=subscriber.confirmed_at or utcnow(),
            unsubscribed_at=None, consent_at=utcnow(), consent_text_version=consent_version,
            consent_source=intent['source'], updated_at=utcnow(),
        ).where((NewsletterSubscriber.id == subscriber.id) & NewsletterSubscriber.status.not_in(['bounced', 'complained'])).execute()
        if updated != 1:
            raise ValueError('newsletter_unavailable')
        general, _ = SnowNewsletterPreference.get_or_create(subscriber=subscriber)
        if 'articles' in intent or 'newsletter_frequency' in intent:
            if 'articles' in intent:
                general.articles = intent['articles']
            if 'newsletter_frequency' in intent:
                general.newsletter_frequency = intent['newsletter_frequency']
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


def signup_token(subscriber):
    # Issued only on first signup; it can edit that signup but cannot log in or
    # retrieve any saved preference. Duplicate email submissions never get it.
    return _serializer().dumps({'scope': 'signup', 'subscriber': subscriber.id,
                               'hash': _digest(subscriber.preferences_token, b'newsletter-signup:')})


def update_signup(token, intent):
    if not isinstance(token, str) or len(token) > 2048:
        return False
    try:
        claim = _serializer().loads(token, max_age=900)
    except (BadSignature, SignatureExpired):
        return False
    if claim.get('scope') != 'signup':
        return False
    subscriber = NewsletterSubscriber.get_or_none((NewsletterSubscriber.id == claim.get('subscriber')) & (NewsletterSubscriber.status == 'active'))
    if not subscriber or claim.get('hash') != _digest(subscriber.preferences_token, b'newsletter-signup:'):
        return False
    replace_preferences(subscriber.email, intent, consent_version='v2-newsletter-button')
    return True


def replace_preferences(email, intent, consent_version='v1'):
    """Save the same complete editor snapshot, atomically, for every entry point."""
    with NewsletterSubscriber._meta.database.atomic():
        subscriber = capture_pending(email, intent)
        if subscriber.status in {'bounced', 'complained'}:
            raise ValueError('newsletter_unavailable')
        keep = [row['station_id'] for row in intent['stations']]
        for model in (SnowAlert, NewsletterStationPreference, NewsletterSubscriberStation):
            query = model.delete().where(model.subscriber == subscriber)
            if keep:
                query = query.where(model.station.not_in(keep))
            query.execute()
        for row in intent['stations']:
            NewsletterStationPreference.update(**{field: False for field in TOPICS.values()}).where(
                (NewsletterStationPreference.subscriber == subscriber) & (NewsletterStationPreference.station == row['station_id'])
            ).execute()
        apply_intent(email, intent, consent_version=consent_version)
        # A full editor save also persists frequencies when every topic is off.
        for row in intent['stations']:
            NewsletterStationPreference.update(newsletter_frequency=row['newsletter_frequency'], weather_frequency=row['weather_frequency']).where(
                (NewsletterStationPreference.subscriber == subscriber) & (NewsletterStationPreference.station == row['station_id'])
            ).execute()


def current_preferences(email):
    subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == email)
    general = SnowNewsletterPreference.get_or_none(SnowNewsletterPreference.subscriber == subscriber) if subscriber else None
    intent = {'stations': [], 'articles': bool(general.articles) if general else True,
              'newsletter_frequency': general.newsletter_frequency if general else 'weekly',
              'consent': True, 'language': subscriber.language if subscriber else 'fr', 'source': 'member-account'}
    if subscriber:
        followed = NewsletterSubscriberStation.select(NewsletterSubscriberStation, Resort).join(Resort).where(NewsletterSubscriberStation.subscriber == subscriber).order_by(Resort.name, Resort.id)
        preferences = {row.station_id: row for row in NewsletterStationPreference.select().where(NewsletterStationPreference.subscriber == subscriber)}
        for relation in followed:
            preference = preferences.get(relation.station_id)
            intent['stations'].append({'station_id': relation.station_id, 'station_name': relation.station.name,
                                      'topics': [key for key, field in TOPICS.items() if preference and getattr(preference, field)],
                                      'newsletter_frequency': preference.newsletter_frequency if preference else 'weekly',
                                      'weather_frequency': preference.weather_frequency if preference else 'disabled'})
    return {'status': subscriber.status if subscriber else 'not_subscribed', 'intent': intent}


def follow_station(email, station_id, topic, language='fr'):
    """Add one explicit station CTA choice without resetting saved preferences."""
    intent = normalize_intent({'stations': [{'station_id': station_id, 'topics': ['weather', 'snowfall', 'snow_conditions'] if topic == 'weather' else [topic]}],
                               'consent': True, 'language': language, 'source': 'station_page'})
    row = intent['stations'][0]
    with NewsletterSubscriber._meta.database.atomic():
        subscriber = NewsletterSubscriber.get_or_none(NewsletterSubscriber.email == email)
        if subscriber and subscriber.status in {'bounced', 'complained'}:
            raise ValueError('newsletter_unavailable')
        preference = NewsletterStationPreference.get_or_none(
            (NewsletterStationPreference.subscriber == subscriber) & (NewsletterStationPreference.station == row['station_id'])) if subscriber else None
        already_followed = bool(subscriber and subscriber.status == 'active' and preference and all(getattr(preference, TOPICS[key]) for key in row['topics'])
                                and (topic != 'weather' or preference.weather_frequency != 'disabled'))
        if preference:
            row['newsletter_frequency'] = preference.newsletter_frequency
            row['weather_frequency'] = preference.weather_frequency
            if topic == 'weather' and row['weather_frequency'] == 'disabled':
                row['weather_frequency'] = 'friday'
        if not subscriber or not NewsletterSubscriberStation.select().where(
                (NewsletterSubscriberStation.subscriber == subscriber) & (NewsletterSubscriberStation.station == row['station_id'])).exists():
            count = NewsletterSubscriberStation.select().where(NewsletterSubscriberStation.subscriber == subscriber).count() if subscriber else 0
            if count >= 20:
                raise ValueError('station_limit_reached')
        apply_intent(email, intent)
    return {'station': row, 'already_followed': already_followed}
