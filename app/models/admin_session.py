from peewee import CharField, ForeignKeyField, TextField

from app.datetime_utils import UTCDateTimeField, utcnow
from .admin_user import AdminUser
from .base import BaseModel


class AdminSession(BaseModel):
    admin_user = ForeignKeyField(AdminUser, backref="sessions", on_delete="CASCADE", index=True)
    token_hash = CharField(max_length=64, unique=True, index=True)
    csrf_token_hash = CharField(max_length=64)
    created_at = UTCDateTimeField(default=utcnow)
    expires_at = UTCDateTimeField(index=True)
    last_seen_at = UTCDateTimeField(default=utcnow)
    revoked_at = UTCDateTimeField(null=True, index=True)
    ip_address = CharField(max_length=64, null=True)
    user_agent = TextField(null=True)

    class Meta:
        table_name = "admin_sessions"
