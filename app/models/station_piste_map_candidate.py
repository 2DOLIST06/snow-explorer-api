import json

from peewee import BigAutoField, CharField, ForeignKeyField, IntegerField, TextField

from app.datetime_utils import UTCDateTimeField, utcnow
from .admin_user import AdminUser
from .base import BaseModel
from .resort import Resort


class StationPisteMapCandidate(BaseModel):
    id = BigAutoField()
    station = ForeignKeyField(Resort, column_name="station_id", backref="piste_map_candidates", on_delete="CASCADE")
    external_station_id = CharField(max_length=255)
    anmsm_media_id = CharField(max_length=255)
    anmsm_title = TextField(null=True); anmsm_credit = TextField(null=True)
    plan_type = CharField(max_length=255, null=True); source_url = TextField()
    source_checksum = CharField(max_length=64); source_format = CharField(max_length=16)
    source_width = IntegerField(null=True); source_height = IntegerField(null=True)
    source_size_bytes = IntegerField(); original_s3_key = TextField()
    display_s3_key = TextField(null=True); display_width = IntegerField(null=True)
    display_height = IntegerField(null=True); display_size_bytes = IntegerField(null=True)
    status = CharField(max_length=16, default="pending"); warnings = TextField(default="[]")
    previous_plan_url = TextField(null=True); previous_plan_s3_key = TextField(null=True)
    detected_at = UTCDateTimeField(default=utcnow); approved_at = UTCDateTimeField(null=True)
    approved_by = ForeignKeyField(AdminUser, null=True, column_name="approved_by", on_delete="SET NULL")
    ignored_at = UTCDateTimeField(null=True)
    ignored_by = ForeignKeyField(AdminUser, null=True, column_name="ignored_by", on_delete="SET NULL")
    error_code = CharField(max_length=64, null=True); error_message = TextField(null=True)
    created_at = UTCDateTimeField(default=utcnow); updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "station_piste_map_candidates"
        indexes = ((('station', 'anmsm_media_id', 'source_checksum'), True), (('status',), False))

    def warning_codes(self):
        try: return self.warnings if isinstance(self.warnings, list) else json.loads(self.warnings or "[]")
        except (TypeError, ValueError): return []
