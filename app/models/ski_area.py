from peewee import (BigAutoField, BooleanField, CharField, Check, DateField,
                    ForeignKeyField, IntegerField, Model, TextField)

from app.datetime_utils import UTCDateTimeField, utcnow
from app.models.base import db
from app.models.resort import Resort


class SkiArea(Model):
    id = BigAutoField()
    name = TextField()
    slug = CharField(unique=True, max_length=255)
    status = CharField(default="draft", constraints=[Check("status IN ('draft', 'published')")])
    description = TextField(null=True)
    cover_image_url = TextField(null=True)
    piste_map_url = TextField(null=True)
    altitude_min_m = IntegerField(null=True)
    altitude_max_m = IntegerField(null=True)
    ski_area_km = IntegerField(null=True)
    pistes_count = IntegerField(null=True)
    green_pistes_count = IntegerField(null=True)
    blue_pistes_count = IntegerField(null=True)
    red_pistes_count = IntegerField(null=True)
    black_pistes_count = IntegerField(null=True)
    lifts_count = IntegerField(null=True)
    forecast_open_date = DateField(null=True)
    forecast_close_date = DateField(null=True)
    season = CharField(null=True, max_length=32)
    source = TextField(null=True)
    verified_at = UTCDateTimeField(null=True)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        database = db
        table_name = "ski_areas"


class SkiAreaResort(Model):
    ski_area = ForeignKeyField(SkiArea, backref="station_links", on_delete="CASCADE")
    resort = ForeignKeyField(Resort, backref="ski_area_links", on_delete="CASCADE")
    created_at = UTCDateTimeField(default=utcnow)

    class Meta:
        database = db
        table_name = "ski_area_resorts"
        indexes = ((('ski_area', 'resort'), True),)
