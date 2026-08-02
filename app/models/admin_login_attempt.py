from peewee import CharField, DateTimeField

from .admin_user import utcnow
from .base import BaseModel


class AdminLoginAttempt(BaseModel):
    ip_address = CharField(max_length=64, index=True)
    email = CharField(max_length=320, index=True)
    attempted_at = DateTimeField(default=utcnow, index=True)

    class Meta:
        table_name = "admin_login_attempts"
        indexes = ((('ip_address', 'email', 'attempted_at'), False),)
