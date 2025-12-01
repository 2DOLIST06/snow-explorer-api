from peewee import CharField, ForeignKeyField
from .base import BaseModel
from .resort import Resort

class ResortMap(BaseModel):
    id = CharField(primary_key=True)
    resort = ForeignKeyField(Resort, backref="maps", on_delete="CASCADE")
    image_url = CharField()
    season = CharField(null=True)  # "2025-2026"
    note = CharField(null=True)

