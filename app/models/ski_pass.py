from peewee import (
    AutoField, BooleanField, CharField, Check, DateField, DecimalField, ForeignKeyField,
    IntegerField, TextField,
)

from app.datetime_utils import UTCDateTimeField, utcnow
from app.models.base import BaseModel
from app.models.resort import Resort


class SkiPassSeason(BaseModel):
    id = AutoField()
    resort = ForeignKeyField(Resort, backref="ski_pass_seasons", on_delete="CASCADE")
    season = CharField()
    currency = CharField(max_length=3, default="EUR")
    source_url = TextField(null=True)
    # Visibility is deliberately independent from the existence of the grid.
    is_active = BooleanField(default=False)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "ski_pass_seasons"
        indexes = ((('resort', 'season'), True),)


class SkiPassPeriod(BaseModel):
    id = AutoField()
    season = ForeignKeyField(SkiPassSeason, backref="periods", on_delete="CASCADE")
    external_id = CharField()
    name = CharField()
    start_date = DateField()
    end_date = DateField()
    sort_order = IntegerField(default=0)

    class Meta:
        table_name = "ski_pass_periods"
        indexes = ((('season', 'external_id'), True),)
        constraints = [Check('start_date <= end_date')]


class SkiPassProduct(BaseModel):
    id = AutoField()
    season = ForeignKeyField(SkiPassSeason, backref="products", on_delete="CASCADE")
    external_id = CharField()
    name = CharField()
    duration_days = IntegerField(null=True)
    duration_label = CharField()
    sort_order = IntegerField(default=0)

    class Meta:
        table_name = "ski_pass_products"
        indexes = ((('season', 'external_id'), True),)
        constraints = [Check('duration_days IS NULL OR duration_days > 0')]


class SkiPassPrice(BaseModel):
    id = AutoField()
    product = ForeignKeyField(SkiPassProduct, backref="prices", on_delete="CASCADE")
    period = ForeignKeyField(SkiPassPeriod, backref="prices", on_delete="CASCADE")
    category = CharField()
    category_label = CharField()
    price_type = CharField(max_length=7)
    price = DecimalField(max_digits=12, decimal_places=2, null=True, auto_round=True)
    price_min = DecimalField(max_digits=12, decimal_places=2, null=True, auto_round=True)
    price_max = DecimalField(max_digits=12, decimal_places=2, null=True, auto_round=True)
    dynamic_label = TextField(null=True)
    note = TextField(null=True)
    sort_order = IntegerField(default=0)

    class Meta:
        table_name = "ski_pass_prices"
        indexes = ((('product', 'period', 'category'), True),)
        constraints = [Check("(price_type = 'fixed' AND price IS NOT NULL AND price_min IS NULL AND price_max IS NULL) OR (price_type = 'dynamic' AND price IS NULL AND price_min IS NOT NULL AND price_max IS NOT NULL AND price_min <= price_max)")]
