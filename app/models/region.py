from peewee import CharField
from .base import BaseModel

class Region(BaseModel):
    id = CharField(primary_key=True)     # ex: "auvergne-rhone-alpes"
    name = CharField()
    country_code = CharField(max_length=2, default="FR")


