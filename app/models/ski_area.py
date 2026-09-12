from peewee import (BigAutoField, CharField, Check, DateField, ForeignKeyField,
                    IntegerField, Model, TextField)

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


class SkiAreaCatalogImport(Model):
    id = CharField(primary_key=True, max_length=36)
    catalog_id = CharField(max_length=255)
    batch_id = CharField(max_length=255)
    schema_version = CharField(max_length=255)
    file_sha256 = CharField(max_length=64)
    status = CharField(max_length=16)
    preview_json = TextField()
    result_json = TextField(null=True)
    created_at = UTCDateTimeField(default=utcnow)
    applied_at = UTCDateTimeField(null=True)

    class Meta:
        database = db
        table_name = "ski_area_catalog_imports"


class SkiAreaCatalogArea(Model):
    catalog_id = CharField(max_length=255)
    catalog_key = CharField(max_length=255)
    ski_area = ForeignKeyField(SkiArea, null=True, backref="catalog_entries", on_delete="SET NULL")
    name = TextField()
    proposed_slug = CharField(max_length=255)
    area_kind = CharField(null=True, max_length=64)
    notes = TextField(null=True)
    sources_json = TextField(default="[]")
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        database = db
        table_name = "ski_area_catalog_areas"
        indexes = ((('catalog_id', 'catalog_key'), True),)


class SkiAreaExpectedStation(Model):
    catalog_id = CharField(max_length=255)
    station_ref = CharField(max_length=255)
    name = TextField()
    country_code = CharField(null=True, max_length=8)
    department = CharField(null=True, max_length=255)
    aliases_json = TextField(default="[]")
    origin_resolution = CharField(max_length=32)
    covered_by_json = TextField(default="[]")
    resort = ForeignKeyField(Resort, null=True, backref="catalog_identities", on_delete="SET NULL")
    resolution_state = CharField(default="pending", max_length=32)
    resolution_note = TextField(null=True)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        database = db
        table_name = "ski_area_expected_stations"
        indexes = ((('catalog_id', 'station_ref'), True),)


class SkiAreaExpectedMembership(Model):
    catalog_area = ForeignKeyField(SkiAreaCatalogArea, backref="expected_memberships", on_delete="CASCADE")
    expected_station = ForeignKeyField(SkiAreaExpectedStation, backref="expected_memberships", on_delete="CASCADE")
    evidence_status = CharField(max_length=32)
    relation_kind = CharField(max_length=64)
    source_json = TextField(default="{}")
    state = CharField(default="pending", max_length=24)
    decision_origin = CharField(default="catalog", max_length=24)
    decision_note = TextField(null=True)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        database = db
        table_name = "ski_area_expected_memberships"
        indexes = ((('catalog_area', 'expected_station'), True),)


class SkiAreaCatalogNotice(Model):
    catalog_id = CharField(max_length=255)
    notice_key = CharField(max_length=64)
    kind = CharField(max_length=32)
    payload_json = TextField()
    created_at = UTCDateTimeField(default=utcnow)

    class Meta:
        database = db
        table_name = "ski_area_catalog_notices"
        indexes = ((('catalog_id', 'notice_key'), True),)
