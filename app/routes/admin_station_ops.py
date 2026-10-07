"""Admin-only SCAN API; authentication is enforced by the application hook."""
from flask import Blueprint, current_app, jsonify, request

from app.services.station_ops.scan import scan_stations

bp_admin_station_ops = Blueprint("admin_station_ops", __name__, url_prefix="/api/admin/station-ops")


@bp_admin_station_ops.get("/snapshot")
def snapshot():
    try:
        result = scan_stations(request.args)
    except ValueError as exc:
        return jsonify({"error": "invalid_filters", "message": str(exc)}), 400
    except Exception:
        current_app.logger.exception("Unable to scan Station Ops snapshot")
        return jsonify({"error": "station_ops_scan_failed"}), 500
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response
