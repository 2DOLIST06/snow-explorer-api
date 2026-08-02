from peewee import CharField, DateTimeField, ForeignKeyField, TextField

from .admin_user import AdminUser, utcnow
from .base import BaseModel


class AdminSession(BaseModel):
    admin_user = ForeignKeyField(AdminUser, backref="sessions", on_delete="CASCADE", index=True)
    token_hash = CharField(max_length=64, unique=True, index=True)
    csrf_token_hash = CharField(max_length=64)
    created_at = DateTimeField(default=utcnow)
    expires_at = DateTimeField(index=True)
    last_seen_at = DateTimeField(default=utcnow)
    revoked_at = DateTimeField(null=True, index=True)
    ip_address = CharField(max_length=64, null=True)
    user_agent = TextField(null=True)

    class Meta:
        table_name = "admin_sessions"
