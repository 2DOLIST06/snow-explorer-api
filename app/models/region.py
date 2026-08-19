from peewee import CharField, TextField
from .base import BaseModel
from app.services.region_ids import canonical_region_id
from app.datetime_utils import UTCDateTimeField, utcnow

class Region(BaseModel):
    id = CharField(primary_key=True)     # ex: "auvergne-rhone-alpes"
    name = CharField()
    country_code = CharField(max_length=2, default="FR")
    description_html = TextField(null=True)
    meta_title = TextField(null=True)
    meta_description = TextField(null=True)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "regions"

    def to_dict(self):
        public_id = canonical_region_id(self.id)
        return {
            "id": public_id,
            "slug": public_id,
            "name": self.name,
            "country_code": self.country_code,
            "description_html": self.description_html,
            "meta_title": self.meta_title,
            "meta_description": self.meta_description,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
