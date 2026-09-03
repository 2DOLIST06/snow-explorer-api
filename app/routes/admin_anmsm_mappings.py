import math

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.resort import Resort
from app.services.anmsm_logos import fetch_stations
from app.services.anmsm_station_mappings import (
    confirm_mappings, mapping_json, normalize_name, resort_json, suggestions,
)

bp_admin_anmsm_mappings = Blueprint("admin_anmsm_mappings", __name__, url_prefix="/api/admin/anmsm")


@bp_admin_anmsm_mappings.errorhandler(HTTPException)
def json_http_error(error):
    return jsonify({"ok": False, "error": error.name.lower().replace(" ", "_")}), error.code


@bp_admin_anmsm_mappings.errorhandler(Exception)
def json_internal_error(error):
    current_app.logger.exception("ANMSM administration request failed", exc_info=error)
    return jsonify({"ok": False, "error": "internal_error"}), 500


def _feed_or_error():
    try:
        return fetch_stations(), None
    except Exception:
        current_app.logger.exception("ANMSM station feed retrieval failed")
        return None, (jsonify({"ok": False, "error": "anmsm_feed_unavailable"}), 502)


@bp_admin_anmsm_mappings.get("/station-mappings")
def station_mappings():
    status = request.args.get("status", "all")
    if status not in {"all", "matched", "unmatched"}:
        return jsonify({"ok": False, "error": "invalid_status"}), 400
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 25))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid_pagination"}), 400
    stations, error = _feed_or_error()
    if error: return error
    resorts = list(Resort.select())
    mappings = {row.external_station_id: row for row in AnmsmStationMapping.select().where(
        AnmsmStationMapping.source == "anmsm")}
    items = []
    for station in stations:
        mapping = mappings.get(station["external_station_id"])
        matched = bool(mapping and mapping.station_id and mapping.verified)
        if status != "all" and matched != (status == "matched"): continue
        search = normalize_name(request.args.get("search", ""))
        if search and search not in normalize_name(station["external_name"]): continue
        public_station = {**station, "logo": {key: station["logo"].get(key)
                          for key in ("url", "title", "credit")}}
        items.append({**public_station, "mapping": mapping_json(mapping),
                      "suggestions": suggestions(station["external_name"], resorts)})
    sort = request.args.get("sort", "name")
    if sort not in {"name", "-name", "external_station_id", "-external_station_id"}:
        return jsonify({"ok": False, "error": "invalid_sort"}), 400
    field = "external_name" if "name" in sort else "external_station_id"
    items.sort(key=lambda item: normalize_name(item[field]), reverse=sort.startswith("-"))
    total = len(items); start = (page - 1) * per_page
    matched_count = sum(bool(mappings.get(s["external_station_id"]) and
        mappings[s["external_station_id"]].station_id and mappings[s["external_station_id"]].verified) for s in stations)
    return jsonify({"ok": True, "items": items[start:start + per_page],
        "pagination": {"page": page, "per_page": per_page, "total": total,
                       "pages": math.ceil(total / per_page)},
        "stats": {"received": len(stations), "matched": matched_count,
                  "unmatched": len(stations) - matched_count,
                  "without_logo": sum(not s["logo"]["url"] for s in stations)}})


@bp_admin_anmsm_mappings.get("/resorts/search")
def search_resorts():
    query = normalize_name(request.args.get("q", ""))
    mapped = {row.station_id: row.external_station_id for row in AnmsmStationMapping.select().where(
        (AnmsmStationMapping.source == "anmsm") & AnmsmStationMapping.station.is_null(False))}
    items = []
    for resort in Resort.select().order_by(Resort.name):
        if query and query not in normalize_name(resort.name) and query not in normalize_name(resort.slug): continue
        item = resort_json(resort)
        if resort.id in mapped: item["mapped_external_station_id"] = mapped[resort.id]
        items.append(item)
        if len(items) == 25: break
    return jsonify({"ok": True, "items": items})


@bp_admin_anmsm_mappings.post("/station-mappings/confirm")
def confirm():
    payload = request.get_json(silent=True)
    mappings = payload.get("mappings") if isinstance(payload, dict) else None
    invalid_indexes = []
    if isinstance(mappings, list):
        invalid_indexes = [
            index for index, mapping in enumerate(mappings)
            if not isinstance(mapping, dict)
            or not isinstance(mapping.get("external_station_id"), str)
            or not mapping["external_station_id"].strip()
            or not isinstance(mapping.get("station_id"), str)
            or not mapping["station_id"].strip()
        ]
    if not isinstance(mappings, list) or not mappings or invalid_indexes:
        return jsonify({
            "ok": False,
            "error": "invalid_mapping_payload",
            "message": "Chaque correspondance doit contenir external_station_id et station_id.",
            "invalid_indexes": invalid_indexes,
        }), 400
    stations, error = _feed_or_error()
    if error: return error
    results = confirm_mappings(mappings, {s["external_station_id"] for s in stations})
    # Return the same complete rows consumed by the frontend so it never has
    # to reconstruct a station identifier from a display label.
    from app.routes.admin_station_logos import _workspace_data
    rows = {row["external_station_id"]: row for row in _workspace_data(stations)["rows"]}
    for result in results:
        if result["ok"]:
            result["row"] = rows.get(result["external_station_id"])
    return jsonify({"ok": all(result["ok"] for result in results), "results": results}), 200


@bp_admin_anmsm_mappings.delete("/station-mappings/<path:external_station_id>")
def delete_mapping(external_station_id):
    deleted = (AnmsmStationMapping.delete().where(
        (AnmsmStationMapping.source == "anmsm") &
        (AnmsmStationMapping.external_station_id == external_station_id)).execute())
    if not deleted: return jsonify({"ok": False, "error": "mapping_not_found"}), 404
    return jsonify({"ok": True})
