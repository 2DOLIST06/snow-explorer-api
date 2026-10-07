"""Read-only, bulk snapshot of stored data, separate from public DTO fallbacks."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math

from peewee import PostgresqlDatabase, SqliteDatabase

from app.datetime_utils import utcnow
from app.models.lift import Lift
from app.models.piste import Piste
from app.models.region import Region
from app.models.resort import Resort
from app.models.resort_map import ResortMap
from app.models.ski_area import SkiArea, SkiAreaResort
from app.models.ski_pass import SkiPassSeason, SkiPassPeriod, SkiPassProduct, SkiPassPrice
from app.models.station_widgets import StationWidgets
from app.services.public_resort import PUBLIC_CFG_KEYS, _unwrap_widgets
from .duplicates import potential_duplicates
from .validation import finding, validate_ski_area, validate_station
from .schema import PhysicalSchema

SCHEMA_VERSION = "1.0"
FILTER_FIELDS = ("id", "slug", "country_code", "region_id", "department", "is_active", "ski_area_id")
CONTENT_FIELDS = ("description_md", "description_html", "v2_overview_html", "v2_weather_snow_html",
                  "v2_ski_pass_html", "v2_piste_map_html", "v2_webcam_html")
SCAN_MODELS = (Resort, Region, SkiArea, SkiAreaResort, StationWidgets, Piste, Lift,
               ResortMap, SkiPassSeason, SkiPassPeriod, SkiPassProduct, SkiPassPrice)


def parse_filters(params=None):
    params = params or {}
    unknown = set(params) - set(FILTER_FIELDS)
    if unknown:
        raise ValueError("Unknown filters: " + ", ".join(sorted(unknown)))
    result = {}
    for key in FILTER_FIELDS:
        if key not in params:
            continue
        if hasattr(params, "getlist") and len(params.getlist(key)) != 1:
            raise ValueError(f"{key} must occur exactly once")
        value = params[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        if key == "is_active":
            if value not in ("true", "false"):
                raise ValueError("is_active must be true or false")
            result[key] = value == "true"
        elif key == "ski_area_id":
            if not value.isascii() or not value.isdecimal() or not 0 < int(value) <= 9223372036854775807:
                raise ValueError("ski_area_id must be a positive bigint")
            result[key] = int(value)
        else:
            result[key] = value
    return result


@contextmanager
def read_only_scan(database):
    """A fresh consistent transaction; never weaken an existing transaction."""
    if database.in_transaction():
        raise RuntimeError("Station Ops requires a fresh read-only transaction")
    if isinstance(database, PostgresqlDatabase):
        with database.atomic(isolation_level="REPEATABLE READ"):
            database.execute_sql("SET TRANSACTION READ ONLY")
            yield
    elif isinstance(database, SqliteDatabase):
        # Existing project tests use isolated SQLite fixtures. Guard even there.
        previous = database.execute_sql("PRAGMA query_only").fetchone()[0]
        database.execute_sql("PRAGMA query_only = ON")
        try:
            with database.atomic():
                yield
        finally:
            database.execute_sql("PRAGMA query_only = " + ("ON" if previous else "OFF"))
    else:
        raise RuntimeError("Unsupported database for a read-only Station Ops scan")


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)  # No loss of tariff precision.
    if isinstance(value, float) and not math.isfinite(value):
        return {"non_finite": str(value)}
    if isinstance(value, str) and value.lstrip().lower().startswith("data:"):
        return {"inline_media": True, "length": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
    return value


def _row_json(row, content_fields=()):
    result = {key: _json_value(value) for key, value in row.items() if "__" not in key}
    if content_fields:
        result["content"] = {
            name: {"present": bool(row[name + "__present"]),
                   "length": row[name + "__length"], "md5": row[name + "__md5"]}
            for name in content_fields if name + "__length" in row
        }
    return result


def _compact_widget(value, key=""):
    if isinstance(value, dict):
        return {k: _compact_widget(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact_widget(v, key) for v in value]
    if isinstance(value, str) and ("html" in key.lower() or value.lstrip().lower().startswith("data:")):
        return {"present": bool(value.strip()), "length": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
    return _json_value(value)


def _widgets(raw):
    if raw is None:
        return {}, "absent", None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        value = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(value, dict):
            raise ValueError("Widgets must be an object")
    except (ValueError, TypeError):
        return {}, "invalid_json", digest
    # Reuse existing wrapper semantics, without manufacturing public defaults.
    clean = _unwrap_widgets(value)
    return {key: _compact_widget(clean[key]) for key in PUBLIC_CFG_KEYS if key in clean}, "valid", digest


def _group(query, key):
    result = defaultdict(list)
    for row in query:
        result[row[key]].append(_row_json(row))
    return result


def scan_stations(params=None):
    filters = parse_filters(params)
    database = Resort._meta.database
    with read_only_scan(database):
        return _scan(filters)


def _scan(filters):
    schema = PhysicalSchema(Resort._meta.database, SCAN_MODELS)
    # Identity/link columns cannot be substituted without changing semantics.
    # Fail with structured diagnostics before any data SELECT if these are absent.
    required = {
        Resort: ("id", "slug", "region_id", "is_active"), Region: ("id",),
        SkiArea: ("id",), SkiAreaResort: ("resort", "ski_area"),
        StationWidgets: ("station_slug", "config"), Piste: ("id", "resort"),
        Lift: ("id", "resort"), ResortMap: ("id", "resort"),
        SkiPassSeason: ("id", "resort"), SkiPassPeriod: ("id", "season"),
        SkiPassProduct: ("id", "season"), SkiPassPrice: ("id", "product"),
    }
    for model, fields in required.items():
        schema.require(model, *fields)
    base = Resort.select()
    for key, value in filters.items():
        if key == "ski_area_id":
            base = base.where(Resort.id.in_(SkiAreaResort.select(SkiAreaResort.resort)
                                          .where(SkiAreaResort.ski_area == value)))
        else:
            schema.require(Resort, key)
            base = base.where(getattr(Resort, key) == value)
    ids = base.select(Resort.id)
    slugs = base.select(Resort.slug)
    stations = [_row_json(row, CONTENT_FIELDS) for row in
                schema.projection(Resort, CONTENT_FIELDS).where(Resort.id.in_(ids)).order_by(Resort.id)]
    regions = [_row_json(row, ("description_html", "seo_text")) for row in
               schema.projection(Region, ("description_html",), extra_fields=("slug", "created_at"),
                                 extra_content_fields=("seo_text",)).where(
                   Region.id.in_(base.select(Resort.region_id))).order_by(Region.id)]
    region_by_id = {row["id"]: row for row in regions}
    links = _group(schema.projection(SkiAreaResort).where(SkiAreaResort.resort.in_(ids))
                   .order_by(SkiAreaResort.ski_area, SkiAreaResort.resort).dicts(), "resort")
    areas = [_row_json(row, ("description",)) for row in
             schema.projection(SkiArea, ("description",)).where(SkiArea.id.in_(
                 SkiAreaResort.select(SkiAreaResort.ski_area).where(SkiAreaResort.resort.in_(ids))))
             .order_by(SkiArea.id)]
    area_by_id = {row["id"]: row for row in areas}
    for area in areas:
        area["findings"] = validate_ski_area(area)
    widgets = {row["station_slug"]: row["config"] for row in StationWidgets.select(
        StationWidgets.station_slug, StationWidgets.config).where(StationWidgets.station_slug.in_(slugs)).dicts()}
    pistes = _group(schema.projection(Piste).where(Piste.resort.in_(ids)).order_by(Piste.id), "resort")
    lifts = _group(schema.projection(Lift).where(Lift.resort.in_(ids)).order_by(Lift.id), "resort")
    maps = _group(schema.projection(ResortMap).where(ResortMap.resort.in_(ids)).order_by(ResortMap.id), "resort")
    seasons_query = schema.projection(SkiPassSeason).where(SkiPassSeason.resort.in_(ids))
    season_ids = seasons_query.select(SkiPassSeason.id)
    seasons = _group(seasons_query.order_by(SkiPassSeason.id).dicts(), "resort")
    periods = _group(schema.projection(SkiPassPeriod).where(SkiPassPeriod.season.in_(season_ids))
                     .order_by(SkiPassPeriod.id).dicts(), "season")
    products_query = schema.projection(SkiPassProduct).where(SkiPassProduct.season.in_(season_ids))
    products = _group(products_query.order_by(SkiPassProduct.id).dicts(), "season")
    prices = _group(schema.projection(SkiPassPrice).where(SkiPassPrice.product.in_(products_query.select(SkiPassProduct.id)))
                    .order_by(SkiPassPrice.id).dicts(), "product")
    for station in stations:
        station_id = station["id"]
        station["region"] = region_by_id.get(station["region_id"])
        station["ski_area_links"] = links[station_id]
        station["ski_areas"] = [{key: area_by_id[link["ski_area"]][key] for key in ("id", "name", "slug", "status")
                                if key in area_by_id[link["ski_area"]]}
                                for link in links[station_id] if link["ski_area"] in area_by_id]
        station["widgets"], station["widgets_state"], station["widgets_sha256"] = _widgets(widgets.get(station["slug"]))
        station["pistes"] = {"items": pistes[station_id], "row_count": len(pistes[station_id]),
                              "counts_by_difficulty": dict(sorted(_counts(pistes[station_id], "difficulty").items()))}
        station["lifts"] = {"items": lifts[station_id], "row_count": len(lifts[station_id]),
                             "counts_by_type": dict(sorted(_counts(lifts[station_id], "type").items()))}
        station["maps"] = maps[station_id]
        station["ski_pass_seasons"] = seasons[station_id]
        for season in station["ski_pass_seasons"]:
            season["periods"] = periods[season["id"]]
            season["products"] = products[season["id"]]
            for product in season["products"]:
                product["prices"] = prices[product["id"]]
        station["has_ski_pass_data"] = any(product["prices"] for season in station["ski_pass_seasons"]
                                            for product in season["products"])
        forfaits = station["widgets"].get("forfaits")
        station["has_legacy_forfait_items"] = bool(forfaits.get("items")) if isinstance(forfaits, dict) else False
        station["has_forfait_data"] = station["has_ski_pass_data"] or station["has_legacy_forfait_items"]
        station["findings"] = validate_station(station)
        if station["region_id"] and station["region"] is None:
            station["findings"].append(finding("region_not_found", "warning", "region_id"))
        for area in station["ski_areas"]:
            station["findings"].extend({**item, "field": f"ski_areas.{area['id']}.{item['field']}"}
                                       for item in area_by_id[area["id"]]["findings"])
    duplicates = potential_duplicates(stations)
    summary = {"total_stations": len(stations),
               "active_stations": sum(row["is_active"] is True for row in stations),
               "inactive_stations": sum(row["is_active"] is False for row in stations),
               "stations_with_errors": sum(any(f["severity"] == "error" for f in row["findings"]) for row in stations),
               "stations_with_warnings": sum(any(f["severity"] == "warning" for f in row["findings"]) for row in stations),
               "potential_duplicates": len(duplicates)}
    return {"schema_version": SCHEMA_VERSION, "generated_at": utcnow().isoformat(),
            "scope": {"filters": filters, "summary": "filtered_stations", "duplicates": "within_filtered_stations"},
            "summary": summary, "stations": stations, "ski_areas": areas,
            "potential_duplicates": duplicates, "schema_findings": schema.findings}


def _counts(rows, key):
    counts = defaultdict(int)
    for row in rows:
        if key in row:
            counts[row[key]] += 1
    return counts
