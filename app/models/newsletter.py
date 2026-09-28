from peewee import (
    AutoField, BooleanField, CharField, Check, ForeignKeyField, IntegerField,
)

from app.datetime_utils import UTCDateTimeField, utcnow
from app.models.base import BaseModel
from app.models.resort import Resort


class NewsletterSubscriber(BaseModel):
    id = AutoField()
    email = CharField(max_length=320, unique=True)
    status = CharField(max_length=16, default="pending", index=True)
    language = CharField(max_length=2, index=True)
    source = CharField(max_length=100)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)
    confirmed_at = UTCDateTimeField(null=True)
    unsubscribed_at = UTCDateTimeField(null=True)
    consent_at = UTCDateTimeField()
    consent_text_version = CharField(max_length=50)
    consent_source = CharField(max_length=100)
    confirmation_token = CharField(max_length=128, unique=True, null=True)
    preferences_token = CharField(max_length=128, unique=True)

    class Meta:
        table_name = "newsletter_subscribers"
        constraints = [
            Check("status IN ('pending', 'active', 'unsubscribed', 'bounced', 'complained')"),
            Check("language IN ('fr', 'en')"),
            Check("email = lower(email)"),
        ]


class SnowNewsletterPreference(BaseModel):
    id = AutoField()
    subscriber = ForeignKeyField(
        NewsletterSubscriber, backref="general_preferences", unique=True,
        on_delete="CASCADE",
    )
    snow_conditions = BooleanField(default=True)
    snowfall = BooleanField(default=True)
    weather = BooleanField(default=True)
    resort_updates = BooleanField(default=True)
    opening_closing = BooleanField(default=True)
    lift_updates = BooleanField(default=True)
    ski_pass_updates = BooleanField(default=True)
    articles = BooleanField(default=True)
    newsletter_frequency = CharField(max_length=16, default="weekly", index=True)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "snow_newsletter_preferences"
        constraints = [Check("newsletter_frequency IN ('immediate', 'weekly', 'monthly')")]


class NewsletterSubscriberStation(BaseModel):
    id = AutoField()
    subscriber = ForeignKeyField(NewsletterSubscriber, backref="followed_stations", on_delete="CASCADE")
    station = ForeignKeyField(Resort, backref="newsletter_followers", on_delete="CASCADE")
    created_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "newsletter_subscriber_stations"
        indexes = ((('subscriber', 'station'), True),)


class NewsletterStationPreference(BaseModel):
    id = AutoField()
    subscriber = ForeignKeyField(NewsletterSubscriber, backref="station_preferences", on_delete="CASCADE")
    station = ForeignKeyField(Resort, backref="newsletter_preferences", on_delete="CASCADE")
    weather_enabled = BooleanField(default=True)
    snow_conditions_enabled = BooleanField(default=True)
    resort_updates_enabled = BooleanField(default=True)
    weather_frequency = CharField(max_length=16, default="weekly", index=True)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "newsletter_station_preferences"
        indexes = ((('subscriber', 'station'), True),)
        constraints = [Check("weather_frequency IN ('daily', 'friday', 'weekly', 'disabled')")]


class SnowAlert(BaseModel):
    id = AutoField()
    subscriber = ForeignKeyField(NewsletterSubscriber, backref="snow_alerts", on_delete="CASCADE")
    station = ForeignKeyField(Resort, backref="snow_alerts", on_delete="CASCADE")
    alert_type = CharField(max_length=16, default="snowfall", index=True)
    threshold_cm = IntegerField()
    forecast_period_hours = IntegerField()
    is_active = BooleanField(default=True, index=True)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "snow_alerts"
        constraints = [
            Check("alert_type = 'snowfall'"),
            Check("threshold_cm BETWEEN 1 AND 500"),
            Check("forecast_period_hours IN (24, 48, 72)"),
        ]
