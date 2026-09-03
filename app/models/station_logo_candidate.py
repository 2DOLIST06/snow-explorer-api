import json
from peewee import BigAutoField, CharField, FloatField, ForeignKeyField, IntegerField, TextField
from app.datetime_utils import UTCDateTimeField, utcnow
from .admin_user import AdminUser
from .base import BaseModel
from .resort import Resort

class StationLogoCandidate(BaseModel):
    id = BigAutoField(); station = ForeignKeyField(Resort, column_name="station_id", backref="logo_candidates", on_delete="CASCADE")
    external_station_id = CharField(max_length=255); anmsm_media_id = CharField(max_length=255, null=True)
    anmsm_title = TextField(null=True); anmsm_credit = TextField(null=True); source_url = TextField()
    source_checksum = CharField(max_length=64); source_format = CharField(max_length=16)
    source_width = IntegerField(); source_height = IntegerField(); source_size_bytes = IntegerField()
    optimized_s3_key = TextField(null=True); optimized_url = TextField(null=True)
    optimized_width = IntegerField(null=True); optimized_height = IntegerField(null=True); optimized_size_bytes = IntegerField(null=True)
    content_width = IntegerField(null=True); content_height = IntegerField(null=True); aspect_ratio = FloatField(null=True)
    visual_occupancy_width = FloatField(null=True); visual_occupancy_height = FloatField(null=True)
    warnings = TextField(default="[]"); status = CharField(max_length=16, default="pending")
    detected_at = UTCDateTimeField(default=utcnow); checked_at = UTCDateTimeField(null=True)
    approved_at = UTCDateTimeField(null=True); approved_by = ForeignKeyField(AdminUser, null=True, column_name="approved_by", on_delete="SET NULL")
    ignored_at = UTCDateTimeField(null=True); ignored_by = ForeignKeyField(AdminUser, null=True, column_name="ignored_by", on_delete="SET NULL")
    previous_logo_url = TextField(null=True); previous_logo_s3_key = TextField(null=True)
    error_code = CharField(max_length=64, null=True); error_message = TextField(null=True)
    created_at = UTCDateTimeField(default=utcnow); updated_at = UTCDateTimeField(default=utcnow)
    class Meta:
        table_name = "station_logo_candidates"
        indexes = ((('station', 'source_checksum'), True), (('status',), False))
    def warning_codes(self):
        if isinstance(self.warnings, list):
            return self.warnings
        try: return json.loads(self.warnings or "[]")
        except (TypeError, ValueError): return []
