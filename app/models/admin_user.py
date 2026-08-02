from peewee import BooleanField, CharField, TextField

from app.datetime_utils import UTCDateTimeField, utcnow
from .base import BaseModel


class AdminUser(BaseModel):
    email = CharField(max_length=320, unique=True, index=True)
    password_hash = TextField()
    role = CharField(max_length=50, default="admin")
    is_active = BooleanField(default=True, index=True)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)
    last_login_at = UTCDateTimeField(null=True)
    password_changed_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "admin_users"

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        self.updated_at = utcnow()
        return super().save(*args, **kwargs)
