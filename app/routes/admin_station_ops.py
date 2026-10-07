"""Admin-only read-only SCAN/COMPARE; authentication uses the application hook."""
import json
import math

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from app.services.station_ops.candidates import ComparePayloadError, MAX_BODY_BYTES
from app.services.station_ops.compare import compare_candidates
from app.services.station_ops.scan import scan_stations
from app.services.station_ops.schema import SchemaCompatibilityError

bp_admin_station_ops = Blueprint("admin_station_ops", __name__, url_prefix="/api/admin/station-ops")


@bp_admin_station_ops.get("/snapshot")
def snapshot():
    try:
        result = scan_stations(request.args)
    except ValueError as exc:
        return jsonify({"error": "invalid_filters", "message": str(exc)}), 400
    except SchemaCompatibilityError as exc:
        return jsonify({"error": "station_ops_schema_incompatible", "message": str(exc),
                        "schema_findings": exc.findings}), 503
    except Exception:
        current_app.logger.exception("Unable to scan Station Ops snapshot")
        return jsonify({"error": "station_ops_scan_failed"}), 500
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _finite_json_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def _compare_payload():
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        raise ComparePayloadError("COMPARE body exceeds 16 MiB", 413)
    try:
        body = request.stream.read(MAX_BODY_BYTES + 1)
    except RequestEntityTooLarge as exc:
        raise ComparePayloadError("COMPARE body exceeds the request limit", 413) from exc
    except BadRequest as exc:
        raise ComparePayloadError("Malformed or truncated request body") from exc
    if len(body) > MAX_BODY_BYTES:
        raise ComparePayloadError("COMPARE body exceeds 16 MiB", 413)
    try:
        return json.loads(body, object_pairs_hook=_unique_json_object, parse_float=_finite_json_float,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ComparePayloadError("Malformed or invalid JSON") from exc


@bp_admin_station_ops.post("/compare")
def compare():
    if not request.is_json:
        return jsonify({"error": "json_content_type_required"}), 415
    try:
        result = compare_candidates(_compare_payload())
    except ComparePayloadError as exc:
        return jsonify({"error": "invalid_compare_payload", "message": str(exc)}), exc.status
    except SchemaCompatibilityError as exc:
        return jsonify({"error": "station_ops_schema_incompatible", "message": str(exc),
                        "schema_findings": exc.findings}), 503
    except Exception:
        current_app.logger.exception("Unable to compare Station Ops candidates")
        return jsonify({"error": "station_ops_compare_failed"}), 500
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response
