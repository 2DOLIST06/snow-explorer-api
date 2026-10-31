from peewee import BooleanField, CharField, ForeignKeyField, TextField
from app.datetime_utils import UTCDateTimeField, utcnow
from .base import BaseModel


class MemberUser(BaseModel):
    email = CharField(max_length=320, unique=True)
    password_hash = TextField(null=True)
    is_active = BooleanField(default=True)
    verified_at = UTCDateTimeField(null=True)
    password_changed_at = UTCDateTimeField(null=True)
    created_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "member_users"


class MemberSession(BaseModel):
    member = ForeignKeyField(MemberUser, on_delete="CASCADE")
    token_hash = CharField(max_length=64, unique=True)
    created_at = UTCDateTimeField(default=utcnow)
    expires_at = UTCDateTimeField(index=True)
    revoked_at = UTCDateTimeField(null=True)

    class Meta:
        table_name = "member_sessions"


class MemberLoginLink(BaseModel):
    member = ForeignKeyField(MemberUser, unique=True, on_delete="CASCADE")
    token_hash = CharField(max_length=64, unique=True, null=True)
    requested_at = UTCDateTimeField(null=True)
    expires_at = UTCDateTimeField(null=True)
    newsletter_intent = TextField(null=True)

    class Meta:
        table_name = "member_login_links"


class MemberLoginAttempt(BaseModel):
    key_hash = CharField(max_length=64, index=True)
    ip_address = CharField(max_length=64, index=True)
    attempted_at = UTCDateTimeField(default=utcnow, index=True)

    class Meta:
        table_name = "member_login_attempts"
