from peewee import BooleanField, CharField, FloatField, ForeignKeyField, IntegerField

from app.datetime_utils import UTCDateTimeField, utcnow
from .base import BaseModel
from .resort import Resort


class MeteoFranceStationMapping(BaseModel):
    resort = ForeignKeyField(Resort, column_name="resort_id", unique=True,
                             backref="meteo_france_mappings", on_delete="CASCADE")
    meteo_france_station_id = CharField(max_length=8)
    station_name = CharField(max_length=255)
    latitude = FloatField()
    longitude = FloatField()
    altitude_m = IntegerField()
    distance_km = FloatField(null=True)
    is_manual_override = BooleanField(default=False)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "meteo_france_station_mappings"
