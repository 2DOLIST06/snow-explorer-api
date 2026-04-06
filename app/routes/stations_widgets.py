from flask import Blueprint, request, jsonify, abort
from app.models.station_widgets import StationWidgets

bp_widgets = Blueprint("stations_widgets", __name__, url_prefix="/api/stations")

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
    "pistes": {"enabled": False, "smallMapUrl": None, "largeMapUrl": None, "caption": None},
    "meteo": {"enabled": False, "iframeUrl": None},
    "description": {"enabled": False, "html": None, "metaTitle": None, "metaDescription": None},
    "forfaits": {"enabled": False, "items": []},
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
    return out

@bp_widgets.get("/<string:slug>/widgets")
def get_widgets(slug: str):
    try:
        row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
        if not row:
            cfg = dict(DEFAULT_CFG)
            cfg["stationSlug"] = slug
            return jsonify(cfg)
        data = StationWidgets.from_json(row.config)
        data = _normalize_widgets_config(data)
        if "stationSlug" not in data:
            data["stationSlug"] = slug
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_widgets.post("/<string:slug>/widgets")
def upsert_widgets(slug: str):
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
