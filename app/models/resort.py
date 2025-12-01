from peewee import (
    Model, CharField, TextField, IntegerField, FloatField, BooleanField, DateField
)
from app.models.base import db
from datetime import date
import unicodedata, re

def _slugify(name: str) -> str:
    if not name: return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii","ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+","-", n.lower()).strip("-")

def _as_int(v):
    try:
        if v is None: return None
        return int(round(float(v)))
    except Exception: return None

def _as_float(v):
    try:
        if v is None: return None
        return float(v)
    except Exception: return None

def _as_str(v):
    try:
        if v is None: return None
        s = str(v).strip()
        return s if s else None
    except Exception: return None

def _fmt_date(d):
    if not d: return None
    if isinstance(d, date): return d.isoformat()
    return _as_str(d)

class Resort(Model):
    # Identité
    id   = CharField(primary_key=True)
    name = TextField(null=False)
    slug = CharField(null=False, unique=True)

    # Activation
    is_active = BooleanField(null=True, default=True)

    # Localisation
    region_id = CharField(null=True)
    region_name = CharField(null=True)
    country_code = CharField(null=True)
    department = CharField(null=True)

    # Géo
    latitude = FloatField(null=True)
    longitude = FloatField(null=True)

    # Altitudes
    altitude_base_m = IntegerField(null=True)
    altitude_top_m  = IntegerField(null=True)
    altitude_min_m  = IntegerField(null=True)
    altitude_max_m  = IntegerField(null=True)

    # Domaine skiable
    lifts_count  = IntegerField(null=True)
    pistes_count = IntegerField(null=True)
    ski_area_km  = IntegerField(null=True)

    # Contenu / médias
    website_url     = TextField(null=True)
    cover_image_url = TextField(null=True)
    logo_url        = TextField(null=True)
    amenities       = TextField(null=True)
    description_md  = TextField(null=True)      # markdown (héritage)
    description_html = TextField(null=True)     # nouvelle source HTML
    meta_title       = TextField(null=True)
    meta_description = TextField(null=True)

    # Plan des pistes
    pistes_small_map_url = TextField(null=True)
    pistes_large_map_url = TextField(null=True)
    pistes_caption       = TextField(null=True)

    # Snowpark
    snowpark_map_url = TextField(null=True)
    snowpark_caption = TextField(null=True)

    # Saison
    season_open_date  = DateField(null=True)
    season_close_date = DateField(null=True)

    class Meta:
        database = db
        table_name = "resort"

    def to_dict(self):
        alt_min = self.altitude_min_m
        alt_max = self.altitude_max_m
        alt_base = self.altitude_base_m
        alt_top  = self.altitude_top_m
        altitude_min_m = _as_int(alt_min if alt_min is not None else alt_base)
        altitude_max_m = _as_int(alt_max if alt_max is not None else alt_top)

        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug or _slugify(self.name or ""),

            "region": {
                "id": _as_str(self.region_id),
                "name": _as_str(self.region_name),
                "country_code": _as_str(self.country_code),
            },
            "region_id": _as_str(self.region_id),
            "department": _as_str(self.department),

            "altitude_base_m": _as_int(self.altitude_base_m),
            "altitude_top_m": _as_int(self.altitude_top_m),
            "altitude_min_m": altitude_min_m,
            "altitude_max_m": altitude_max_m,

            "lifts_count": _as_int(self.lifts_count),
            "pistes_count": _as_int(self.pistes_count),
            "ski_area_km": _as_int(self.ski_area_km),

            "latitude": _as_float(self.latitude),
            "longitude": _as_float(self.longitude),

            "website_url": _as_str(self.website_url),
            "cover_image_url": _as_str(self.cover_image_url),
            "logo_url": _as_str(self.logo_url),
            "amenities": _as_str(self.amenities),

            "description_md": _as_str(self.description_md),
            "description_html": _as_str(self.description_html),
            "meta_title": _as_str(self.meta_title),
            "meta_description": _as_str(self.meta_description),

            "pistes_small_map_url": _as_str(self.pistes_small_map_url),
            "pistes_large_map_url": _as_str(self.pistes_large_map_url),
            "pistes_caption": _as_str(self.pistes_caption),

            "snowpark_map_url": _as_str(self.snowpark_map_url),
            "snowpark_caption": _as_str(self.snowpark_caption),

            "season_open_date": _fmt_date(self.season_open_date),
            "season_close_date": _fmt_date(self.season_close_date),

            "is_active": bool(self.is_active) if self.is_active is not None else True,
        }

    def __str__(self) -> str:
        return f"<Resort {self.id} {self.name}>"

