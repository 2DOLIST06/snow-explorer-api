from peewee import BooleanField, CharField, ForeignKeyField
from app.datetime_utils import UTCDateTimeField, utcnow
from .base import BaseModel
from .resort import Resort

class AnmsmStationMapping(BaseModel):
    station = ForeignKeyField(Resort, column_name="station_id", backref="anmsm_mappings", null=True, on_delete="CASCADE")
    source = CharField(max_length=32, default="anmsm")
    external_station_id = CharField(max_length=255)
    verified = BooleanField(default=False)
    created_at = UTCDateTimeField(default=utcnow); updated_at = UTCDateTimeField(default=utcnow)
    class Meta:
        table_name = "station_external_mappings"
        indexes = ((('source', 'external_station_id'), True),)
