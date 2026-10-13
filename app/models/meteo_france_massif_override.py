"""Explicit manual mapping only; public GET requests never write mappings."""
from peewee import Check, ForeignKeyField, IntegerField

from app.datetime_utils import UTCDateTimeField, utcnow
from .base import BaseModel
from .resort import Resort


class MeteoFranceMassifOverride(BaseModel):
    resort = ForeignKeyField(Resort, column_name="resort_id", unique=True,
                             backref="bra_massif_overrides", on_delete="CASCADE")
    massif_id = IntegerField(constraints=[Check("massif_id > 0")])
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "meteo_france_massif_overrides"
