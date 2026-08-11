import re
import unicodedata

from peewee import CharField, DateTimeField, TextField

from app.datetime_utils import utcnow
from .base import BaseModel


def slugify_region(value: str) -> str:
    """Return the canonical, accent-free slug shared by every region endpoint."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


class Region(BaseModel):
    id = CharField(primary_key=True)
    name = CharField(null=False)
    slug = CharField(null=False, unique=True, index=True)
    country_code = CharField(max_length=2, default="FR")
    seo_text = TextField(null=True)
    meta_title = CharField(max_length=70, null=True)
    meta_description = CharField(max_length=170, null=True)
    created_at = DateTimeField(default=utcnow)
    updated_at = DateTimeField(default=utcnow)

    class Meta:
        table_name = "regions"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify_region(self.name)
        self.country_code = (self.country_code or "FR").upper()
        return super().save(*args, **kwargs)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "country_code": self.country_code,
            "seo_text": self.seo_text,
            "meta_title": self.meta_title,
            "meta_description": self.meta_description,
        }
