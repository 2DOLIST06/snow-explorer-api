from peewee import CharField, IntegerField, ForeignKeyField
from .base import BaseModel
from .resort import Resort

class Lift(BaseModel):
    id = CharField(primary_key=True)
    resort = ForeignKeyField(Resort, backref="lifts", on_delete="CASCADE")
    name = CharField()
    type = CharField()  # gondola|chair|drag|tram|cog
    capacity_per_hour = IntegerField(null=True)

