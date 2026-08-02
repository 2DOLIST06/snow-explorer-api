from datetime import datetime, timezone

from peewee import BooleanField, CharField, DateTimeField, TextField

from .base import BaseModel


def utcnow():
    return datetime.now(timezone.utc)


class AdminUser(BaseModel):
    email = CharField(max_length=320, unique=True, index=True)
    password_hash = TextField()
    role = CharField(max_length=50, default="admin")
    is_active = BooleanField(default=True, index=True)
    created_at = DateTimeField(default=utcnow)
    updated_at = DateTimeField(default=utcnow)
    last_login_at = DateTimeField(null=True)
    password_changed_at = DateTimeField(default=utcnow)

    class Meta:
        table_name = "admin_users"

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        self.updated_at = utcnow()
        return super().save(*args, **kwargs)
