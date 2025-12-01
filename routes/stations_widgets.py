from flask import Blueprint, request, jsonify, abort
from app import db
from models.station_widgets import StationWidgets

bp = Blueprint("stations_widgets", __name__, url_prefix="/api/stations")

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
    return jsonify(row.config)

@bp.post("/<string:slug>/widgets")
def upsert_widgets(slug):
    payload = request.get_json(force=True)
    if not isinstance(payload, dict):
        abort(400, "Invalid payload")

    row = StationWidgets.query.filter_by(station_slug=slug).first()
    if not row:
        row = StationWidgets(station_slug=slug, config=payload)
        db.session.add(row)
    else:
        row.config = payload

    db.session.commit()
    return jsonify({"ok": True, "stationSlug": slug})
