from peewee import BooleanField, CharField, TextField

from app.datetime_utils import UTCDateTimeField
from .base import BaseModel


class AnmsmStationSnapshot(BaseModel):
    """Latest successfully persisted ANMSM observations (not coverage state)."""

    external_station_id = CharField(max_length=255, primary_key=True)
    station_name = TextField()
    station_slug = TextField(null=True)
    logo_available = BooleanField(null=True)
    logo_url = TextField(null=True)
    logo_seen_at = UTCDateTimeField(null=True)
    piste_map_available = BooleanField(null=True)
    piste_map_url = TextField(null=True)
    piste_map_seen_at = UTCDateTimeField(null=True)
    piste_map_observation_complete = BooleanField(default=False)
    station_catalog_seen_at = UTCDateTimeField(null=True)
    last_seen_at = UTCDateTimeField()

    class Meta:
        table_name = "anmsm_station_snapshots"
