"""Admin-only read-only SCAN/COMPARE/REVIEW; uses the application auth hook."""
import json
import math

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from app.services.station_ops.candidates import ComparePayloadError, MAX_BODY_BYTES
from app.services.station_ops.apply import apply_candidates
from app.services.station_ops.apply_contract import ApplyError
from app.services.station_ops.compare import compare_candidates
from app.services.station_ops.research import validate_research
from app.services.station_ops.review import ReviewDecisionError, review_candidates
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


def _compare_payload(operation="COMPARE"):
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        raise ComparePayloadError(f"{operation} body exceeds 16 MiB", 413)
    try:
        body = request.stream.read(MAX_BODY_BYTES + 1)
    except RequestEntityTooLarge as exc:
        raise ComparePayloadError(f"{operation} body exceeds the request limit", 413) from exc
    except BadRequest as exc:
        raise ComparePayloadError("Malformed or truncated request body") from exc
    if len(body) > MAX_BODY_BYTES:
        raise ComparePayloadError(f"{operation} body exceeds 16 MiB", 413)
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


@bp_admin_station_ops.post("/review")
def review():
    if not request.is_json:
        return jsonify({"error": "json_content_type_required"}), 415
    try:
        result = review_candidates(_compare_payload("REVIEW"))
    except ComparePayloadError as exc:
        return jsonify({"error": "invalid_review_payload", "message": str(exc)}), exc.status
    except ReviewDecisionError as exc:
        return jsonify({"error": "invalid_review_decisions", "issues": exc.issues}), 409
    except SchemaCompatibilityError as exc:
        return jsonify({"error": "station_ops_schema_incompatible", "message": str(exc),
                        "schema_findings": exc.findings}), 503
    except Exception:
        current_app.logger.exception("Unable to review Station Ops candidates")
        return jsonify({"error": "station_ops_review_failed"}), 500
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp_admin_station_ops.post("/apply")
def apply():
    # Deliberately absent from the read-only auth/session exemptions.
    if not request.is_json:
        return jsonify({"error": "json_content_type_required"}), 415
    try:
        result = apply_candidates(_compare_payload("APPLY"), actor_id=g.admin_user.id)
    except ApplyError as exc:
        result = {"error": exc.code, "message": str(exc), "execution_id": exc.execution_id, **exc.details}
        if exc.code == "station_ops_apply_commit_disabled":
            result.pop("execution_id", None)
        response = jsonify(result)
        response.status_code = exc.status
    except ComparePayloadError as exc:
        response = jsonify({"error": "invalid_apply_payload", "message": str(exc)})
        response.status_code = exc.status
    except SchemaCompatibilityError as exc:
        response = jsonify({"error": "station_ops_schema_incompatible", "message": str(exc),
                            "schema_findings": exc.findings, "execution_id": getattr(exc, "execution_id", None)})
        response.status_code = 503
    except Exception as exc:
        # SQL exception strings/tracebacks can include editorial values. Keep
        # logs diagnostic without dumping SQL parameters or client content.
        current_app.logger.error("Station Ops APPLY failed type=%s execution_id=%s", type(exc).__name__,
                                 getattr(exc, "execution_id", None))
        response = jsonify({"error": "station_ops_apply_failed", "execution_id": getattr(exc, "execution_id", None)})
        response.status_code = 500
    else:
        response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp_admin_station_ops.post("/research/validate")
def research_validate():
    """Pure contract validation; admin authentication is the existing hook."""
    if not request.is_json:
        response = jsonify({"error": "json_content_type_required"})
        response.status_code = 415
    else:
        try:
            result = validate_research(_compare_payload("RESEARCH"))
            response = jsonify(result)
            response.status_code = 200 if result["valid"] else 400
        except ComparePayloadError as exc:
            response = jsonify({"error": "invalid_research_payload", "message": str(exc)})
            response.status_code = exc.status
    response.headers["Cache-Control"] = "no-store"
    return response
