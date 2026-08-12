from flask import Blueprint, request, jsonify, abort
from werkzeug.exceptions import NotFound
from app.models.station_widgets import StationWidgets
from app.models.resort import Resort
from app.services.resort_access import get_public_active_resort_or_404
from app.services.public_cache import get_public_resorts_version

bp_widgets = Blueprint("stations_widgets", __name__, url_prefix="/api/stations")
bp_forfaits = Blueprint("public_forfaits", __name__, url_prefix="/api/forfaits")

def _deep_merge(dst: dict, src: dict) -> dict:
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return src
    out = dict(dst)
    for k, v in src.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

DEFAULT_CFG = {
    "stationSlug": "",
    "pistes": {"enabled": False, "smallMapUrl": None, "largeMapUrl": None, "officialMapUrl": None, "caption": None},
    "meteo": {"enabled": False, "iframeUrl": None},
    "description": {"enabled": False, "html": None, "metaTitle": None, "metaDescription": None},
    "forfaits": {"enabled": False, "columns": [], "items": []},
    "webcams": {"enabled": False, "items": []},
    "snow": {"enabled": False, "iframeUrl": None},
}


def _normalize_forfait_columns(columns):
    if not isinstance(columns, list):
        return []
    out = []
    for i, col in enumerate(columns, start=1):
        c = col if isinstance(col, dict) else {}
        out.append({
            "id": str(c.get("id") or f"c-{i}"),
            "label": "" if c.get("label") is None else str(c.get("label")),
            "value": "" if c.get("value") is None else str(c.get("value")),
        })
    return out


def _normalize_forfait_item(item, idx):
    itm = item if isinstance(item, dict) else {}
    columns = _normalize_forfait_columns(itm.get("columns"))
    if columns:
        merged = dict(itm)
        merged["id"] = str(itm.get("id") or f"f-{idx}")
        merged["columns"] = columns
        return merged

    legacy_columns = []
    if itm.get("title") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-1", "label": "title", "value": str(itm.get("title"))})
    if itm.get("price") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-2", "label": "price", "value": str(itm.get("price"))})
    if itm.get("url") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-3", "label": "url", "value": str(itm.get("url"))})
    merged = dict(itm)
    merged["id"] = str(itm.get("id") or f"f-{idx}")
    merged["columns"] = legacy_columns
    return merged


def _normalize_widgets_config(cfg):
    if not isinstance(cfg, dict):
        return {}
    out = dict(cfg)
    forfaits = out.get("forfaits")
    if not isinstance(forfaits, dict):
        forfaits = {}
    items = forfaits.get("items")
    if not isinstance(items, list):
        items = []
    forfaits["items"] = [_normalize_forfait_item(item, i) for i, item in enumerate(items, start=1)]
    out["forfaits"] = forfaits
    pistes = out.get("pistes")
    pistes = dict(pistes) if isinstance(pistes, dict) else {"enabled": False}
    pistes.setdefault("officialMapUrl", None)
    out["pistes"] = pistes
    return out


def _text(value):
    """Serialize a present tariff value without inventing a zero."""
    return "" if value is None else str(value)


def _canonical_forfaits(config):
    """Return the public tariff schema, including compatibility with legacy rows."""
    raw = config.get("forfaits") if isinstance(config, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []

    columns = []
    column_ids = set()
    for index, value in enumerate(raw.get("columns") if isinstance(raw.get("columns"), list) else [], 1):
        column = value if isinstance(value, dict) else {}
        column_id = str(column.get("id") or f"column-{index}")
        label = _text(column.get("label")).strip() or column_id
        if column_id not in column_ids:
            columns.append({"id": column_id, "label": label})
            column_ids.add(column_id)

    # Historical rows stored the column definitions and values on every item.
    for value in raw_items:
        item = value if isinstance(value, dict) else {}
        legacy_columns = item.get("columns") if isinstance(item.get("columns"), list) else []
        for index, value_column in enumerate(legacy_columns, 1):
            column = value_column if isinstance(value_column, dict) else {}
            column_id = str(column.get("id") or f"column-{index}")
            label = _text(column.get("label")).strip() or column_id
            if column_id not in column_ids:
                columns.append({"id": column_id, "label": label})
                column_ids.add(column_id)

    # The oldest title/price shape has one implicit price column.
    if any(isinstance(item, dict) and "price" in item for item in raw_items) and not columns:
        columns = [{"id": "price", "label": "Prix"}]
        column_ids = {"price"}

    items = []
    for index, value in enumerate(raw_items, 1):
        item = value if isinstance(value, dict) else {}
        prices = {}
        raw_prices = item.get("prices") if isinstance(item.get("prices"), dict) else {}
        for column in columns:
            column_id = column["id"]
            if column_id in raw_prices and raw_prices[column_id] is not None:
                prices[column_id] = _text(raw_prices[column_id])

        legacy_columns = item.get("columns") if isinstance(item.get("columns"), list) else []
        for column in legacy_columns:
            if not isinstance(column, dict):
                continue
            column_id = str(column.get("id") or "")
            if column_id in column_ids and column.get("value") is not None:
                prices[column_id] = _text(column.get("value"))
        if "price" in column_ids and "price" in item and item.get("price") is not None:
            prices["price"] = _text(item.get("price"))

        item_id = str(item.get("id") or f"item-{index}")
        title = _text(item.get("title")).strip() or item_id
        items.append({"id": item_id, "title": title, "prices": prices})

    return {"enabled": bool(raw.get("enabled", False)), "columns": columns, "items": items}


def _has_price(forfaits):
    return any(
        isinstance(value, str) and bool(value.strip())
        for item in forfaits["items"]
        for value in item["prices"].values()
    )

@bp_widgets.get("/<string:slug>/widgets")
def get_widgets(slug: str):
    try:
        get_public_active_resort_or_404(slug)
    except NotFound:
        return jsonify({"error": "station_not_found", "message": "Station not found"}), 404
    try:
        row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
        if not row:
            cfg = {**DEFAULT_CFG, "forfaits": dict(DEFAULT_CFG["forfaits"])}
            cfg["stationSlug"] = slug
            response = jsonify(cfg)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Public-Resorts-Version"] = str(get_public_resorts_version())
            return response
        data = StationWidgets.from_json(row.config)
        data = _normalize_widgets_config(data)
        data["forfaits"] = _canonical_forfaits(data)
        # Only documented public widgets leave this endpoint. In particular,
        # arbitrary administration-only top-level values are never reflected.
        data = {key: data[key] for key in DEFAULT_CFG if key in data}
        data["stationSlug"] = slug
        response = jsonify(data)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Public-Resorts-Version"] = str(get_public_resorts_version())
        return response
    except Exception:
        return jsonify({"error": "internal_server_error", "message": "Unable to load station widgets"}), 500


@bp_forfaits.get("/stations")
def list_forfait_stations():
    """Return active stations with usable tariffs in one joined database query."""
    try:
        query = (
            Resort.select(Resort, StationWidgets)
            .join(StationWidgets, on=(Resort.slug == StationWidgets.station_slug))
            .where(Resort.is_active == True)
            .order_by(Resort.name.asc(), Resort.id.asc())
        )
        stations = []
        for resort in query:
            widgets = StationWidgets.from_json(resort.stationwidgets.config)
            forfaits = _canonical_forfaits(widgets)
            if not forfaits["enabled"] or not _has_price(forfaits):
                continue
            stations.append({
                "id": resort.id,
                "name": resort.name,
                "slug": resort.slug,
                "region": {"name": resort.region_name},
                "department": {"name": resort.department},
                "forfaits": forfaits,
            })
    except Exception:
        return jsonify({"error": "internal_server_error", "message": "Unable to load tariffs"}), 500
    response = jsonify(stations)
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    response.headers["X-Public-Resorts-Version"] = str(get_public_resorts_version())
    return response

@bp_widgets.post("/<string:slug>/widgets")
def upsert_widgets(slug: str):
    get_public_active_resort_or_404(slug)
    if not request.is_json:
        abort(400, "Expected JSON")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, "Invalid payload")
    payload = _normalize_widgets_config(payload)

    # Deep-merge: DEFAULT_CFG  <- current (if any) <- payload
    current_cfg: dict = {}
    row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    if row:
        try:
            current_cfg = StationWidgets.from_json(row.config)
            if not isinstance(current_cfg, dict):
                current_cfg = {}
        except Exception:
            current_cfg = {}

    merged = _deep_merge(DEFAULT_CFG, current_cfg)
    merged = _deep_merge(merged, payload)
    merged = _normalize_widgets_config(merged)
    merged["stationSlug"] = slug

    if not row:
        row = StationWidgets.create(station_slug=slug, config=StationWidgets.to_json(merged))
    else:
        row.config = StationWidgets.to_json(merged)
        row.save()

    return jsonify({"ok": True, "stationSlug": slug, "merged": True})
