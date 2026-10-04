"""Publication et état de préparation des contenus de fiche station V2."""

from app.models.ski_pass import SkiPassPrice, SkiPassProduct, SkiPassSeason


SECTION_CONTENT_FIELDS = {
    "overview": "v2_overview_html",
    "weather_snow": "v2_weather_snow_html",
    "ski_pass": "v2_ski_pass_html",
    "piste_map": "v2_piste_map_html",
    "webcams": "v2_webcam_html",
}


def _filled(value):
    return isinstance(value, str) and bool(value.strip())


def _enabled_url(block):
    return (
        isinstance(block, dict)
        and block.get("enabled") is True
        and _filled(block.get("iframeUrl"))
    )


def _has_webcam(config):
    block = config.get("webcams") if isinstance(config, dict) else None
    if not isinstance(block, dict):
        return None
    if block.get("enabled") is not True:
        return False
    return any(
        isinstance(item, dict) and _filled(item.get("url"))
        for item in block.get("items", [])
    )


def _has_ski_pass(resort):
    """An active grid is usable only when at least one actual price exists."""
    return (
        SkiPassPrice.select(SkiPassPrice.id)
        .join(SkiPassProduct)
        .join(SkiPassSeason)
        .where(
            (SkiPassSeason.resort == resort.id)
            & (SkiPassSeason.is_active == True)
        )
        .exists()
    )


def _business_data(resort, config):
    config = config if isinstance(config, dict) else {}
    pistes = config.get("pistes")
    official_map = pistes.get("officialMapUrl") if isinstance(pistes, dict) else None
    weather_supported = isinstance(config.get("meteo"), dict) or isinstance(
        config.get("snow"), dict
    )
    return {
        "overview": True,
        "weather_snow": (
            _enabled_url(config.get("meteo")) or _enabled_url(config.get("snow"))
            if weather_supported else None
        ),
        "ski_pass": _has_ski_pass(resort),
        "piste_map": any(_filled(value) for value in (
            resort.pistes_small_map_url,
            resort.pistes_large_map_url,
            official_map,
        )),
        "webcams": _has_webcam(config),
    }


def station_v2_state(resort, config):
    """Return admin readiness plus public availability for every V2 section."""
    data = _business_data(resort, config)
    is_v2 = resort.page_layout_version == "v2"
    readiness = {}
    available = {}
    for section, field in SECTION_CONTENT_FIELDS.items():
        has_content = _filled(getattr(resort, field, None))
        has_data = data[section]
        if not has_content:
            status = "content_missing"
        elif has_data is None:
            status = "not_available"
        elif not has_data:
            status = "business_data_missing"
        else:
            status = "ready"
        readiness[section] = {
            "status": status,
            "has_content": has_content,
            "has_business_data": has_data,
        }
        available[section] = bool(is_v2 and status == "ready")
    return {"readiness": readiness, "available": available}


def v2_public_fields(resort, config):
    state = station_v2_state(resort, config)
    return {
        "page_layout_version": resort.page_layout_version,
        "v2_content": {
            section: getattr(resort, field, None)
            for section, field in SECTION_CONTENT_FIELDS.items()
        },
        "v2_sections": state["available"],
    }
