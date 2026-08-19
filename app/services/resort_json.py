"""Versioned, allow-listed station import/export domain service."""
import hashlib
import hmac
import json
import os
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

from flask import current_app

from app.models.lift import Lift
from app.models.piste import Piste
from app.models.resort import Resort
from app.models.station_widgets import StationWidgets
from app.datetime_utils import utcnow

SCHEMA_VERSION = "1.0"
MAX_FILE_SIZE = 1024 * 1024
MAX_STATIONS = 500
MAX_JSON_DEPTH = 20
MAX_ARRAY_ITEMS = 1000

STATION_FIELDS = (
    "id", "slug", "name", "is_active", "department", "region_id", "region_name",
    "country_code", "website_url", "cover_image_url", "logo_url", "amenities",
    "description_md", "description_html", "meta_title", "meta_description",
    "altitude_min_m", "altitude_max_m", "altitude_base_m", "altitude_top_m",
    "ski_area_km", "pistes_count", "lifts_count", "season_open_date",
    "season_close_date", "latitude", "longitude",
)
STATION_MUTABLE_FIELDS = tuple(field for field in STATION_FIELDS if field != "id")
# Resort uses an application-provided CharField primary key, so imports may set
# an id while creating a station. This allow-list is deliberately separate from
# the update allow-list: a primary key is never mutable after creation.
STATION_CREATE_FIELDS = STATION_FIELDS
# Identity fields may be omitted in partial imports.  When present, the slug and
# name cannot be cleared; the id is explicitly allowed to be null so an export
# can be used to create or match a station by slug.
REQUIRED = {"slug", "name"}
BOOL_FIELDS = {"is_active"}
INT_FIELDS = {"altitude_min_m", "altitude_max_m", "altitude_base_m", "altitude_top_m", "ski_area_km", "pistes_count", "lifts_count"}
FLOAT_FIELDS = {"latitude", "longitude"}
DATE_FIELDS = {"season_open_date", "season_close_date"}
URL_FIELDS = {"website_url", "cover_image_url", "logo_url"}
HTML_FIELDS = {"description_html"}
OPTIONAL_TEXT = set(STATION_FIELDS) - REQUIRED - BOOL_FIELDS - INT_FIELDS - FLOAT_FIELDS - DATE_FIELDS
BLOCKS = {"pistes", "remontees", "snowpark", "webcams", "meteo", "snow", "forfaits"}
BLOCK_FIELDS = {
    "pistes": {"enabled", "green", "blue", "red", "black", "small_map_url", "large_map_url", "caption", "items"},
    "remontees": {"tire_fesses", "telesieges", "telepheriques", "items"},
    "snowpark": {"enabled", "count", "map_url", "image_url", "logo_url", "caption", "description_html"},
    "webcams": {"enabled", "items"}, "meteo": {"enabled", "iframe_url"},
    "snow": {"enabled", "iframe_url", "opening_date", "closing_date"},
    "forfaits": {"enabled", "columns", "items"},
}


class ValidationProblem(Exception):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [{"path": "$", "message": str(errors)}]


class _Cleaner(HTMLParser):
    allowed = {"p", "br", "strong", "em", "ul", "ol", "li", "a", "h2", "h3", "blockquote"}
    blocked = {"script", "style", "iframe", "object", "embed"}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in self.blocked: self.skip += 1; return
        if self.skip or tag not in self.allowed: return
        safe = []
        if tag == "a":
            for key, value in attrs:
                if key == "href" and valid_url(value): safe.append((key, value))
        rendered = "".join(f' {k}="{html_escape(v)}"' for k, v in safe)
        self.parts.append(f"<{tag}{rendered}>")
    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.skip = max(0, self.skip - 1); return
        if not self.skip and tag in self.allowed and tag != "br": self.parts.append(f"</{tag}>")
    def handle_data(self, data):
        if not self.skip: self.parts.append(html_escape(data))


def html_escape(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def sanitize_html(value):
    parser = _Cleaner(); parser.feed(value); parser.close(); return "".join(parser.parts)


def valid_url(value):
    if not isinstance(value, str): return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not any(c.isspace() for c in value)


def iso(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _widget(cfg, block, key, default=None):
    data = cfg.get(block, {}) if isinstance(cfg.get(block), dict) else {}
    return data.get(key, default)


def serialize_station(resort, widgets=None, pistes=None, lifts=None):
    cfg = widgets if isinstance(widgets, dict) else {}
    pistes = list(pistes if pistes is not None else Piste.select().where(Piste.resort == resort.id))
    lifts = list(lifts if lifts is not None else Lift.select().where(Lift.resort == resort.id))
    station = {field: iso(getattr(resort, field, None)) for field in STATION_FIELDS}
    difficulties = {colour: sum(p.difficulty == colour for p in pistes) for colour in ("green", "blue", "red", "black")}
    lift_types = {"tire_fesses": "drag", "telesieges": "chair", "telepheriques": "gondola"}
    out = {"station": station}
    out["pistes"] = {
        "enabled": bool(_widget(cfg, "pistes", "enabled", False)), **difficulties,
        "small_map_url": resort.pistes_small_map_url, "large_map_url": resort.pistes_large_map_url,
        "caption": resort.pistes_caption,
        "items": [{"id": p.id, "name": p.name, "difficulty": p.difficulty, "length_m": p.length_m, "elevation_diff_m": p.elevation_diff_m} for p in pistes],
    }
    out["remontees"] = {name: sum(l.type == typ for l in lifts) for name, typ in lift_types.items()}
    out["remontees"]["items"] = [{"id": l.id, "name": l.name, "type": l.type, "capacity_per_hour": l.capacity_per_hour} for l in lifts]
    out["snowpark"] = {"enabled": bool(_widget(cfg, "snowpark", "enabled", False)), "count": _widget(cfg, "snowpark", "count"), "map_url": resort.snowpark_map_url, "image_url": _widget(cfg, "snowpark", "imageUrl"), "logo_url": _widget(cfg, "snowpark", "logoUrl"), "caption": resort.snowpark_caption, "description_html": _widget(cfg, "snowpark", "descriptionHtml")}
    for block in ("webcams", "forfaits"):
        out[block] = {"enabled": bool(_widget(cfg, block, "enabled", False)), "items": _widget(cfg, block, "items", [])}
    out["forfaits"]["columns"] = _widget(cfg, "forfaits", "columns", [])
    out["meteo"] = {"enabled": bool(_widget(cfg, "meteo", "enabled", False)), "iframe_url": _widget(cfg, "meteo", "iframeUrl")}
    out["snow"] = {"enabled": bool(_widget(cfg, "snow", "enabled", False)), "iframe_url": _widget(cfg, "snow", "iframeUrl"), "opening_date": _widget(cfg, "snow", "openingDate"), "closing_date": _widget(cfg, "snow", "closingDate")}
    return out


def export_document(resort, **related):
    return {"schema_version": SCHEMA_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(), **serialize_station(resort, **related)}


def canonical_bytes(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def checksum(document): return hashlib.sha256(canonical_bytes(document)).hexdigest()


def preview_token(document, options):
    # The application uses ADMIN_SESSION_SECRET for browser authentication and
    # does not otherwise need Flask's SECRET_KEY.  Requiring only SECRET_KEY here
    # made every valid preview fail with an unhandled 500 in that deployment.
    # Prefer a purpose-specific secret, while retaining both historical and
    # application-secret fallbacks for existing installations.
    secret = (
        current_app.config.get("RESORT_IMPORT_SECRET")
        or current_app.config.get("SECRET_KEY")
        or current_app.config.get("ADMIN_SESSION_SECRET")
        or os.getenv("RESORT_IMPORT_SECRET")
        or os.getenv("SECRET_KEY")
        or os.getenv("ADMIN_SESSION_SECRET")
    )
    if not secret:
        raise RuntimeError(
            "RESORT_IMPORT_SECRET, SECRET_KEY, or ADMIN_SESSION_SECRET must be configured"
        )
    payload = checksum(document) + ":" + json.dumps(options, sort_keys=True, separators=(",", ":"))
    return hmac.new(str(secret).encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_token(document, options, token):
    return bool(token) and hmac.compare_digest(preview_token(document, options), token)


def check_structure(value, depth=0):
    if depth > int(current_app.config.get("RESORT_IMPORT_MAX_DEPTH", MAX_JSON_DEPTH)): raise ValidationProblem("JSON nesting is too deep")
    if isinstance(value, list):
        if len(value) > int(current_app.config.get("RESORT_IMPORT_MAX_ARRAY_ITEMS", MAX_ARRAY_ITEMS)): raise ValidationProblem("array contains too many items")
        for item in value: check_structure(item, depth + 1)
    elif isinstance(value, dict):
        for item in value.values(): check_structure(item, depth + 1)


def validate_document(document, bulk=False):
    errors = []
    if not isinstance(document, dict): raise ValidationProblem("root must be an object")
    check_structure(document)
    allowed_root = {"schema_version", "exported_at", "station", *BLOCKS}
    if bulk:
        allowed_root.add("stations")
    for key in set(document) - allowed_root: errors.append({"path": key, "message": "unknown field"})
    if document.get("schema_version") != SCHEMA_VERSION: errors.append({"path": "schema_version", "message": "missing or unsupported schema version"})
    if bulk and "stations" in document:
        records = document.get("stations")
        if not isinstance(records, list):
            errors.append({"path": "stations", "message": "must be an array"})
            records = []
        if "station" in document or BLOCKS & set(document):
            errors.append({"path": "$", "message": "use either station or stations, not both"})
    else:
        # The bulk routes also accept the canonical single-station export.  This
        # lets clients use one endpoint for files containing one or many items.
        records = [{k: v for k, v in document.items() if k not in {"schema_version", "exported_at"}}]
    if len(records) > int(current_app.config.get("RESORT_IMPORT_MAX_STATIONS", MAX_STATIONS)): errors.append({"path": "stations", "message": "too many stations"})
    normalized = []
    for index, record in enumerate(records):
        prefix = f"stations.{index}." if bulk else ""
        if not isinstance(record, dict): errors.append({"path": prefix.rstrip("."), "message": "must be an object"}); continue
        unknown_blocks = set(record) - ({"station"} | BLOCKS)
        for key in unknown_blocks: errors.append({"path": prefix + key, "message": "unknown field"})
        station = record.get("station")
        if not isinstance(station, dict): errors.append({"path": prefix + "station", "message": "station object is required"}); continue
        clean = dict(record); clean["station"] = dict(station)
        if not station.get("id") and not station.get("slug"):
            errors.append({"path": prefix + "station", "message": "id or slug is required"})
        for field in set(station) - set(STATION_FIELDS): errors.append({"path": prefix + "station." + field, "message": "unknown field"})
        for field, value in station.items():
            path = prefix + "station." + field
            if field in REQUIRED and (value is None or value == ""): errors.append({"path": path, "message": "required value cannot be empty"}); continue
            if value is None: continue
            if field in (OPTIONAL_TEXT | REQUIRED) and not isinstance(value, str): errors.append({"path": path, "message": "must be a string"}); continue
            if field in OPTIONAL_TEXT and value == "": clean["station"][field] = None; continue
            if field in BOOL_FIELDS and type(value) is not bool: errors.append({"path": path, "message": "must be a boolean"})
            elif field in INT_FIELDS and (type(value) is not int or value < 0): errors.append({"path": path, "message": "must be a non-negative integer"})
            elif field in FLOAT_FIELDS and type(value) not in (int, float): errors.append({"path": path, "message": "must be a number"})
            elif field == "latitude" and not -90 <= value <= 90: errors.append({"path": path, "message": "must be between -90 and 90"})
            elif field == "longitude" and not -180 <= value <= 180: errors.append({"path": path, "message": "must be between -180 and 180"})
            elif field in DATE_FIELDS:
                try: date.fromisoformat(value)
                except (TypeError, ValueError): errors.append({"path": path, "message": "must use YYYY-MM-DD"})
            elif field in URL_FIELDS and not valid_url(value): errors.append({"path": path, "message": "must be an http(s) URL"})
            elif field == "slug" and (not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) or len(value) > 120): errors.append({"path": path, "message": "invalid slug"})
            elif field in HTML_FIELDS and isinstance(value, str): clean["station"][field] = sanitize_html(value)
        for block in BLOCKS & set(record):
            data = record[block]
            if not isinstance(data, dict): errors.append({"path": prefix + block, "message": "must be an object"}); continue
            for field in set(data) - BLOCK_FIELDS[block]: errors.append({"path": prefix + block + "." + field, "message": "unknown field"})
            for field in {"items", "columns"} & set(data):
                if not isinstance(data[field], list): errors.append({"path": prefix + block + "." + field, "message": "must be an array"})
            if "enabled" in data and type(data["enabled"]) is not bool: errors.append({"path": prefix + block + ".enabled", "message": "must be a boolean"})
            for field, value in data.items():
                path = prefix + block + "." + field
                if field.endswith("_url") and value is not None and not valid_url(value): errors.append({"path": path, "message": "must be an http(s) URL"})
                if field in {"opening_date", "closing_date"} and value is not None:
                    try: date.fromisoformat(value)
                    except (TypeError, ValueError): errors.append({"path": path, "message": "must use YYYY-MM-DD"})
                if field in {"green", "blue", "red", "black", "tire_fesses", "telesieges", "telepheriques", "count"} and value is not None and (type(value) is not int or value < 0): errors.append({"path": path, "message": "must be a non-negative integer"})
            if block == "snowpark" and isinstance(data.get("description_html"), str): clean[block] = dict(data); clean[block]["description_html"] = sanitize_html(data["description_html"])
        normalized.append(clean)
    if errors: raise ValidationProblem(errors)
    return normalized


def differences(resort, record):
    changes, unchanged = [], []
    current = serialize_station(resort, widgets=_load_widgets(resort.slug))
    for block, values in record.items():
        if block not in current or not isinstance(values, dict): continue
        for field, new in values.items():
            # Identity metadata must never become a previewed change.
            if block == "station" and field == "id": continue
            # Aggregate counters are informative; item arrays are authoritative relations.
            if block in {"pistes", "remontees"} and field not in {"enabled", "small_map_url", "large_map_url", "caption", "items"}: continue
            old = current[block].get(field)
            path = f"{block}.{field}"
            (unchanged if old == new else changes).append(path if old == new else {"path": path, "old_value": old, "new_value": new, "action": "clear" if new is None else "update"})
    return changes, unchanged


def _load_widgets(slug):
    row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    return StationWidgets.from_json(row.config) if row else {}


def apply_record(resort, record):
    updated, relation_updates = [], []
    old_slug = resort.slug
    cfg = _load_widgets(old_slug)
    station = record.get("station", {})
    for field, value in station.items():
        if field not in STATION_MUTABLE_FIELDS: continue
        if field in DATE_FIELDS and value is not None: value = date.fromisoformat(value)
        if getattr(resort, field) != value:
            setattr(resort, field, value); updated.append("station." + field)
    if updated:
        resort.updated_at = utcnow()
        resort.save()
    if resort.slug != old_slug:
        widget_row = StationWidgets.get_or_none(StationWidgets.station_slug == old_slug)
        if widget_row:
            widget_row.station_slug = resort.slug
            widget_row.save()
    mappings = {"small_map_url": "smallMapUrl", "large_map_url": "largeMapUrl", "iframe_url": "iframeUrl", "map_url": "mapUrl", "image_url": "imageUrl", "logo_url": "logoUrl", "description_html": "descriptionHtml", "opening_date": "openingDate", "closing_date": "closingDate"}
    for block in BLOCKS & set(record):
        data = record[block]; widget = dict(cfg.get(block, {})) if isinstance(cfg.get(block), dict) else {}
        for key, value in data.items():
            if block == "pistes" and key in {"small_map_url", "large_map_url", "caption"}:
                attr = "pistes_" + key; setattr(resort, attr, value); resort.updated_at = utcnow(); resort.save(); relation_updates.append(f"pistes.{key}"); continue
            if block == "snowpark" and key in {"map_url", "caption"}:
                attr = "snowpark_" + key; setattr(resort, attr, value); resort.updated_at = utcnow(); resort.save(); relation_updates.append(f"snowpark.{key}"); continue
            if block == "pistes" and key == "items": _replace_pistes(resort, value); relation_updates.append("pistes.items"); continue
            if block == "remontees" and key == "items": _replace_lifts(resort, value); relation_updates.append("remontees.items"); continue
            if block in {"pistes", "remontees"}: continue
            widget[mappings.get(key, key)] = value; relation_updates.append(f"{block}.{key}")
        if block not in {"remontees"}: cfg[block] = widget
    row = StationWidgets.get_or_none(StationWidgets.station_slug == resort.slug)
    if row: row.config = StationWidgets.to_json(cfg); row.save()
    elif cfg: StationWidgets.create(station_slug=resort.slug, config=StationWidgets.to_json(cfg))
    elif relation_updates:
        Resort.update(updated_at=utcnow()).where(Resort.id == resort.id).execute()
    return updated, relation_updates


def _replace_pistes(resort, items):
    Piste.delete().where(Piste.resort == resort.id).execute()
    for item in items: Piste.create(resort=resort.id, **item)


def _replace_lifts(resort, items):
    Lift.delete().where(Lift.resort == resort.id).execute()
    for item in items: Lift.create(resort=resort.id, **item)


def parse_upload(req):
    limit = int(current_app.config.get("RESORT_IMPORT_MAX_FILE_SIZE", MAX_FILE_SIZE))
    uploaded = req.files.get("file")
    raw = uploaded.read(limit + 1) if uploaded else req.get_data(cache=True)
    if len(raw) > limit: raise OverflowError
    try: payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError): raise ValueError("invalid_json")

    # Besides a raw export, accept the JSON envelope used by admin clients that
    # parse the selected file in the browser before posting it.  A JavaScript
    # File serialized directly becomes ``{}``; reject that explicitly because
    # the server cannot recover bytes that were never transmitted.
    document = payload
    if not uploaded and isinstance(payload, dict):
        candidate = payload.get("document", payload.get("file"))
        if candidate is not None:
            if not isinstance(candidate, dict) or not candidate:
                raise ValueError("file_content_missing")
            document = candidate

    return document, (uploaded.filename if uploaded else req.headers.get("X-File-Name", "import.json"))
