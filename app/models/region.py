from peewee import CharField, TextField
from .base import BaseModel

class Region(BaseModel):
    id = CharField(primary_key=True)     # ex: "auvergne-rhone-alpes"
    name = CharField()
    country_code = CharField(max_length=2, default="FR")
    description_html = TextField(null=True)
    meta_title = TextField(null=True)
    meta_description = TextField(null=True)

    class Meta:
        table_name = "regions"

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.id,
            "name": self.name,
            "country_code": self.country_code,
            "description_html": self.description_html,
            "meta_title": self.meta_title,
            "meta_description": self.meta_description,
        }

