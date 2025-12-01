from peewee import CharField, IntegerField, ForeignKeyField
from .base import BaseModel
from .resort import Resort

class Piste(BaseModel):
    id = CharField(primary_key=True)
    resort = ForeignKeyField(Resort, backref="pistes", on_delete="CASCADE")
    name = CharField()
    difficulty = CharField()  # green|blue|red|black
    length_m = IntegerField(null=True)
    elevation_diff_m = IntegerField(null=True)

