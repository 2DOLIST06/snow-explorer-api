"""Pure, objective checks. Optional omissions are information, not errors."""
import math


def filled(value):
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def valid_coordinates(station):
    lat, lon = station.get("latitude"), station.get("longitude")
    return (type(lat) in (int, float) and type(lon) in (int, float)
            and math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180)


def finding(code, severity, field):
    return {"code": code, "severity": severity, "field": field}


def _range_checks(data, pairs):
    result = []
    for low_field, high_field in pairs:
        low, high = data.get(low_field), data.get(high_field)
        if low is not None and high is not None and low > high:
            result.append(finding("altitude_inconsistent", "error", f"{low_field},{high_field}"))
    return result


def validate_ski_area(area):
    result = _range_checks(area, [("altitude_min_m", "altitude_max_m")])
    colours = [area.get(f"{colour}_pistes_count") for colour in ("green", "blue", "red", "black")]
    total = area.get("pistes_count")
    if total is not None and all(type(v) is int and v >= 0 for v in colours) and sum(colours) != total:
        result.append(finding("piste_total_inconsistent", "warning", "pistes_count"))
    for field in ("ski_area_km", "pistes_count", "lifts_count", "snowparks_count",
                  "green_pistes_count", "blue_pistes_count", "red_pistes_count", "black_pistes_count"):
        if area.get(field) is not None and area[field] < 0:
            result.append(finding("negative_count", "error", field))
    if (area.get("forecast_open_date") and area.get("forecast_close_date")
            and area["forecast_open_date"] > area["forecast_close_date"]):
        result.append(finding("season_dates_inconsistent", "error", "forecast_open_date,forecast_close_date"))
    return result


def validate_station(station):
    result = []
    for field in ("name", "slug"):
        if not filled(station.get(field)):
            result.append(finding(f"missing_{field}", "error", field))
    if station.get("latitude") is None or station.get("longitude") is None:
        result.append(finding("missing_coordinates", "warning", "coordinates"))
    elif not valid_coordinates(station):
        result.append(finding("coordinates_invalid", "error", "coordinates"))
    if not filled(station.get("country_code")):
        result.append(finding("missing_country", "warning", "country_code"))
    low = station.get("altitude_min_m")
    high = station.get("altitude_max_m")
    # Same fallback as the public DTO, only for validation; raw values are retained.
    low = station.get("altitude_base_m") if low is None else low
    high = station.get("altitude_top_m") if high is None else high
    if low is None or high is None:
        result.append(finding("missing_altitude", "warning", "altitude"))
    result.extend(_range_checks(station, [("altitude_min_m", "altitude_max_m"),
                                          ("altitude_base_m", "altitude_top_m")]))
    if low is not None and high is not None and low > high and not any(
            item["code"] == "altitude_inconsistent" for item in result):
        result.append(finding("altitude_inconsistent", "error", "altitude"))
    for field in ("pistes_count", "lifts_count", "ski_area_km"):
        if station.get(field) is not None and station[field] < 0:
            result.append(finding("negative_count", "error", field))
    for field, code in (("cover_image_url", "missing_cover_image"), ("logo_url", "missing_logo"),
                        ("website_url", "missing_official_website")):
        if not filled(station.get(field)):
            result.append(finding(code, "info", field))
    if not station.get("ski_areas"):
        result.append(finding("missing_ski_area", "info", "ski_areas"))
    widgets = station.get("widgets", {})
    pistes_widget = widgets.get("pistes", {})
    pistes_widget = pistes_widget if isinstance(pistes_widget, dict) else {}
    colours = [pistes_widget.get(colour) for colour in ("green", "blue", "red", "black")]
    if (station.get("pistes_count") is None and not station.get("pistes", {}).get("row_count")
            and not any(v is not None for v in colours)):
        result.append(finding("missing_piste_data", "info", "pistes"))
    if (station.get("pistes_count") is not None
            and all(type(v) is int and v >= 0 for v in colours)
            and sum(colours) != station["pistes_count"]):
        result.append(finding("piste_total_inconsistent", "warning", "widgets.pistes"))
    if not any(filled(value) for value in (station.get("pistes_small_map_url"),
                                           station.get("pistes_large_map_url"),
                                           pistes_widget.get("officialMapUrl"),
                                           pistes_widget.get("smallMapUrl"),
                                           pistes_widget.get("largeMapUrl"))) and not station.get("maps"):
        result.append(finding("missing_trail_map", "info", "piste_map"))
    if (station.get("season_open_date") and station.get("season_close_date")
            and station["season_open_date"] > station["season_close_date"]):
        result.append(finding("season_dates_inconsistent", "error", "season_open_date,season_close_date"))
    if station.get("widgets_state") == "invalid_json":
        result.append(finding("widgets_invalid_json", "warning", "widgets"))
    for field in ("snowparks", "snowpark"):
        block = widgets.get(field)
        if isinstance(block, dict) and "count" in block and block["count"] is not None:
            if type(block["count"]) is not int or block["count"] < 0:
                result.append(finding("invalid_count", "warning", f"widgets.{field}.count"))
    return result
