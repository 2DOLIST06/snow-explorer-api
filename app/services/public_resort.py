"""Construction du DTO public d'une station (sans données d'administration)."""

from app.models.region import Region
from app.models.resort import Resort
from app.models.station_widgets import StationWidgets


PUBLIC_CFG_KEYS = (
    "pistes", "meteo", "description", "forfaits", "webcams", "snow",
    "snowpark", "remontees", "snowparks",
)
REMOVED_COUNT_KEYS = {
    "pistes": {"green", "blue", "red", "black"},
    "remontees": {"tire_fesses", "telesieges", "telepheriques"},
}


def _non_empty(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _date(value):
    return value.isoformat() if hasattr(value, "isoformat") else _non_empty(value)


def _season_label(open_date, close_date):
    """Return the season years advertised by the resort, when both are known."""
    if not open_date or not close_date:
        return None
    try:
        return f"{open_date.year}-{close_date.year}"
    except AttributeError:
        try:
            return f"{str(open_date)[:4]}-{str(close_date)[:4]}"
        except (TypeError, ValueError):
            return None


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
    public = {key: dict(clean.get(key, {})) if isinstance(clean.get(key), dict) else {}
              for key in PUBLIC_CFG_KEYS}
    for block, removed in REMOVED_COUNT_KEYS.items():
        public[block] = {
            key: value for key, value in public[block].items() if key not in removed
        }
    return public


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
        "altitude_min_m": resort.altitude_min_m if resort.altitude_min_m is not None else resort.altitude_base_m,
        "altitude_max_m": resort.altitude_max_m if resort.altitude_max_m is not None else resort.altitude_top_m,
        "ski_area_km": resort.ski_area_km,
        # The database column names remain unchanged for compatibility. Public
        # names now describe the values shown on the station page.
        "snowparks_count": resort.pistes_count,
        "family_parks_count": resort.lifts_count,
        "season_open_date": _date(resort.season_open_date),
        "season_close_date": _date(resort.season_close_date),
        "season_label": _season_label(resort.season_open_date, resort.season_close_date),
        "website_url": _non_empty(resort.website_url),
        "pistes_small_map_url": _non_empty(resort.pistes_small_map_url),
        "pistes_large_map_url": _non_empty(resort.pistes_large_map_url),
        "snowpark_map_url": _non_empty(resort.snowpark_map_url),
        "cfg": public_cfg(raw_cfg),
    }
