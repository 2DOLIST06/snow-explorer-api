"""OAuth-only persistence. Created by an explicit migration, never app startup."""
from peewee import CharField, ForeignKeyField, IntegerField, TextField

from app.datetime_utils import UTCDateTimeField, utcnow
from .base import BaseModel
from .admin_user import AdminUser


class OAuthFlow(BaseModel):
    handle_hash = CharField(max_length=64, unique=True)
    browser_hash = CharField(max_length=64)
    client_id = TextField()
    redirect_uri = TextField()
    resource = TextField()
    scopes = TextField()
    state = TextField(null=True)
    code_challenge = CharField(max_length=43)
    created_at = UTCDateTimeField(default=utcnow)
    expires_at = UTCDateTimeField(index=True)
    consumed_at = UTCDateTimeField(null=True)

    class Meta:
        table_name = 'station_ops_oauth_flows'


class OAuthCode(BaseModel):
    code_hash = CharField(max_length=64, unique=True)
    admin_user = ForeignKeyField(AdminUser, field=AdminUser.id, on_delete='CASCADE')
    client_id = TextField()
    redirect_uri = TextField()
    resource = TextField()
    scopes = TextField()
    code_challenge = CharField(max_length=43)
    code_challenge_method = CharField(max_length=4, default='S256')
    created_at = UTCDateTimeField(default=utcnow)
    expires_at = UTCDateTimeField(index=True)
    consumed_at = UTCDateTimeField(null=True)

    class Meta:
        table_name = 'station_ops_oauth_codes'


class OAuthGrant(BaseModel):
    admin_user = ForeignKeyField(AdminUser, field=AdminUser.id, on_delete='CASCADE')
    client_id = TextField()
    resource = TextField()
    scopes = TextField()
    created_at = UTCDateTimeField(default=utcnow)
    expires_at = UTCDateTimeField(index=True)
    revoked_at = UTCDateTimeField(null=True, index=True)

    class Meta:
        table_name = 'station_ops_oauth_grants'


class OAuthToken(BaseModel):
    token_hash = CharField(max_length=64, unique=True)
    grant = ForeignKeyField(OAuthGrant, on_delete='CASCADE')
    kind = CharField(max_length=7)
    created_at = UTCDateTimeField(default=utcnow)
    expires_at = UTCDateTimeField(index=True)
    consumed_at = UTCDateTimeField(null=True)
    revoked_at = UTCDateTimeField(null=True, index=True)

    class Meta:
        table_name = 'station_ops_oauth_tokens'


class OAuthRateBucket(BaseModel):
    key_hash = CharField(max_length=64, unique=True)
    count = IntegerField(default=0)
    expires_at = UTCDateTimeField(index=True)

    class Meta:
        table_name = 'station_ops_oauth_rate_buckets'


OAUTH_MODELS = [OAuthFlow, OAuthCode, OAuthGrant, OAuthToken, OAuthRateBucket]
