from flask import Blueprint, request, jsonify, abort
from app import db
from models.station_widgets import StationWidgets

bp = Blueprint("stations_widgets", __name__, url_prefix="/api/stations")

def _normalize_forfait_item(item, idx):
    itm = item if isinstance(item, dict) else {}
    columns = itm.get("columns") if isinstance(itm.get("columns"), list) else []
    if columns:
        norm_cols = []
        for i, col in enumerate(columns, start=1):
            c = col if isinstance(col, dict) else {}
            norm_cols.append({
                "id": str(c.get("id") or f"c-{i}"),
                "label": "" if c.get("label") is None else str(c.get("label")),
                "value": "" if c.get("value") is None else str(c.get("value")),
            })
        out = dict(itm)
        out["id"] = str(itm.get("id") or f"f-{idx}")
        out["columns"] = norm_cols
        return out

    legacy_columns = []
    if itm.get("title") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-1", "label": "title", "value": str(itm.get("title"))})
    if itm.get("price") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-2", "label": "price", "value": str(itm.get("price"))})
    if itm.get("url") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-3", "label": "url", "value": str(itm.get("url"))})
    out = dict(itm)
    out["id"] = str(itm.get("id") or f"f-{idx}")
    out["columns"] = legacy_columns
    return out


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

@bp.get("/<string:slug>/widgets")
def get_widgets(slug):
    row = StationWidgets.query.filter_by(station_slug=slug).first()
    if not row:
        return jsonify({
            "stationSlug": slug,
            "pistes": {"enabled": False},
            "meteo": {"enabled": False},
            "description": {"enabled": False},
            "forfaits": {"enabled": False, "items": []},
            "webcams": {"enabled": False, "items": []},
            "snow": {"enabled": False}
        })
    return jsonify(_normalize_widgets_config(row.config))

@bp.post("/<string:slug>/widgets")
def upsert_widgets(slug):
    payload = request.get_json(force=True)
    if not isinstance(payload, dict):
        abort(400, "Invalid payload")
    payload = _normalize_widgets_config(payload)

    row = StationWidgets.query.filter_by(station_slug=slug).first()
    if not row:
        row = StationWidgets(station_slug=slug, config=payload)
        db.session.add(row)
    else:
        row.config = payload

    db.session.commit()
    return jsonify({"ok": True, "stationSlug": slug})
