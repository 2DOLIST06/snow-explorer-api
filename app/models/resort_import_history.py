import uuid

from peewee import CharField, DateTimeField, IntegerField, TextField

from app.models.base import BaseModel


class ResortImportHistory(BaseModel):
    id = CharField(primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = DateTimeField(constraints=[], null=False)
    user_id = CharField(null=True)
    file_name = TextField(null=True)
    schema_version = CharField(max_length=16)
    import_type = CharField(max_length=16)
    status = CharField(max_length=16)
    target_station_id = CharField(null=True)
    stations_total = IntegerField(default=0)
    stations_updated = IntegerField(default=0)
    stations_created = IntegerField(default=0)
    stations_ignored = IntegerField(default=0)
    stations_failed = IntegerField(default=0)
    changes_summary = TextField(default="[]")
    errors_summary = TextField(default="[]")
    checksum = CharField(max_length=64)

    class Meta:
        table_name = "resort_import_history"
