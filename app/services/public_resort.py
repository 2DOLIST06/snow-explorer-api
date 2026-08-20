"""Construction du DTO public d'une station (sans données d'administration)."""

from peewee import prefetch

from app.models.lift import Lift
from app.models.piste import Piste
from app.models.region import Region
from app.models.resort import Resort
from app.models.ski_pass import (
    SkiPassPeriod,
    SkiPassPrice,
    SkiPassProduct,
    SkiPassSeason,
)
from app.models.station_widgets import StationWidgets
from app.services.ski_passes import serialize_season


PUBLIC_CFG_KEYS = (
    "pistes", "meteo", "description", "forfaits", "webcams", "snow",
    "snowpark", "remontees", "snowparks",
)


def _non_empty(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _date(value):
    return value.isoformat() if hasattr(value, "isoformat") else _non_empty(value)


def _valid_count(stored, model, resort_id):
    """Stored non-negative value wins; otherwise use the public child rows."""
    if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 0:
        return stored
    # Piste/Lift currently have no publication flag: every row is public.
    return model.select().where(model.resort == resort_id).count()


def _unwrap_widgets(value):
    """Remove legacy response wrappers which caused widgets.widgets chains."""
    current = value if isinstance(value, dict) else {}
    merged = {}
    seen = set()
    while isinstance(current, dict) and id(current) not in seen:
        seen.add(id(current))
        for key, item in current.items():
            if key not in ("widgets", "cfg"):
                merged[key] = item
        nested = current.get("cfg")
        if not isinstance(nested, dict):
            nested = current.get("widgets")
        if not isinstance(nested, dict):
            break
        current = nested
    return merged


def public_cfg(raw):
    clean = _unwrap_widgets(raw)
    # Explicit allow-list: old wrappers and administration-only keys cannot leak.
    return {key: clean.get(key, {}) if isinstance(clean.get(key), dict) else {}
            for key in PUBLIC_CFG_KEYS}


def _active_ski_pass(resort):
    """Load the latest active normalized season and all its rows in bulk."""
    seasons = (
        SkiPassSeason.select(SkiPassSeason, Resort)
        .join(Resort)
        .where(
            (SkiPassSeason.resort == resort.id)
            & (SkiPassSeason.is_active == True)
        )
        .order_by(SkiPassSeason.season.desc(), SkiPassSeason.id.desc())
        .limit(1)
    )
    rows = prefetch(
        seasons,
        SkiPassPeriod.select(),
        SkiPassProduct.select(),
        SkiPassPrice.select(),
    )
    if not rows:
        return None

    # Keep the established public ``id`` values while also exposing the
    # explicit external identifiers expected by station pages.  The shared
    # serializer remains untouched because it is also used by admin/import
    # endpoints.
    data = serialize_season(rows[0])
    for period in data["periods"]:
        period["external_id"] = period["id"]
    for product in data["passes"]:
        product["external_id"] = product["id"]
    return data


def _snowparks_count(raw_cfg):
    snowparks = raw_cfg.get("snowparks") if isinstance(raw_cfg, dict) else None
    count = snowparks.get("count") if isinstance(snowparks, dict) else None
    return (
        count
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
        else None
    )


def get_public_resort(slug):
    resort = Resort.get_or_none((Resort.slug == slug) & (Resort.is_active == True))
    if resort is None:
        return None

    region = None
    if resort.region_id:
        region = Region.get_or_none(Region.id == resort.region_id)
    region_name = region.name if region is not None else resort.region_name

    widget_row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    raw_cfg = StationWidgets.from_json(widget_row.config) if widget_row else {}

    return {
        "id": str(resort.id),
        "name": resort.name,
        "slug": resort.slug,
        "is_active": True,
        "cover_image_url": _non_empty(resort.cover_image_url),
        "logo_url": _non_empty(resort.logo_url),
        "description_md": resort.description_md,
        "description_html": resort.description_html,
        "meta_title": resort.meta_title,
        "meta_description": resort.meta_description,
        "department": _non_empty(resort.department),
        "region": {
            "id": _non_empty(resort.region_id),
            "name": _non_empty(region_name),
        },
        "region_id": _non_empty(resort.region_id),
        "latitude": resort.latitude,
        "longitude": resort.longitude,
        "altitude_base_m": resort.altitude_base_m,
        "altitude_top_m": resort.altitude_top_m,
        "altitude_min_m": resort.altitude_min_m if resort.altitude_min_m is not None else resort.altitude_base_m,
        "altitude_max_m": resort.altitude_max_m if resort.altitude_max_m is not None else resort.altitude_top_m,
        "ski_area_km": resort.ski_area_km,
        "pistes_count": _valid_count(resort.pistes_count, Piste, resort.id),
        "lifts_count": _valid_count(resort.lifts_count, Lift, resort.id),
        "snowparks_count": _snowparks_count(raw_cfg),
        "season_open_date": _date(resort.season_open_date),
        "season_close_date": _date(resort.season_close_date),
        "website_url": _non_empty(resort.website_url),
        "pistes_small_map_url": _non_empty(resort.pistes_small_map_url),
        "pistes_large_map_url": _non_empty(resort.pistes_large_map_url),
        "snowpark_map_url": _non_empty(resort.snowpark_map_url),
        "updated_at": _date(resort.updated_at),
        "cfg": public_cfg(raw_cfg),
        "ski_pass": _active_ski_pass(resort),
    }
