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

@bp_widgets.get("/<string:slug>/widgets")
def get_widgets(slug: str):
    try:
        row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
        if not row:
            cfg = dict(DEFAULT_CFG)
            cfg["stationSlug"] = slug
            return jsonify(cfg)
        data = StationWidgets.from_json(row.config)
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
    merged["stationSlug"] = slug

    if not row:
        row = StationWidgets.create(station_slug=slug, config=StationWidgets.to_json(merged))
    else:
        row.config = StationWidgets.to_json(merged)
        row.save()

    return jsonify({"ok": True, "stationSlug": slug, "merged": True})

