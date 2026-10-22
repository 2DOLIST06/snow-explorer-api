from peewee import CharField, ForeignKeyField
from app.datetime_utils import UTCDateTimeField
from .base import BaseModel
from .admin_user import AdminUser


class AdminLoginLink(BaseModel):
    admin_user = ForeignKeyField(AdminUser, unique=True, on_delete="CASCADE")
    token_hash = CharField(max_length=64, null=True, unique=True)
    requested_at = UTCDateTimeField(null=True)
    expires_at = UTCDateTimeField(null=True)

    class Meta:
        table_name = "admin_login_links"
