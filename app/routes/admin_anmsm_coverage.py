import csv
import io
import math

from flask import Blueprint, Response, jsonify, request

from app.services.anmsm_coverage import RESOURCE_TYPES, build_coverage, stats

bp_admin_anmsm_coverage = Blueprint(
    "admin_anmsm_coverage", __name__, url_prefix="/api/admin/anmsm/coverage")


def _boolean(name):
    value = request.args.get(name)
    if value is None: return None
    if value.lower() in {"true", "1"}: return True
    if value.lower() in {"false", "0"}: return False
    raise ValueError(name)


def _filtered(rows):
    search = request.args.get("search", "").strip().casefold()
    mapping = request.args.get("mapping_status")
    missing = request.args.get("missing_resource")
    availability = request.args.get("availability_status")
    workflow = request.args.get("workflow_status")
    resource = request.args.get("resource")
    if mapping and mapping not in {"matched", "unmatched", "mapping_error"}: raise ValueError("mapping_status")
    if missing and missing not in RESOURCE_TYPES: raise ValueError("missing_resource")
    if resource and resource not in RESOURCE_TYPES: raise ValueError("resource")
    if availability and availability not in {"available", "unavailable", "unknown"}: raise ValueError("availability_status")
    allowed_workflows = {"published", "ready_to_review", "to_prepare", "available_not_imported", "missing_from_anmsm", "error", "unknown"}
    if workflow and workflow not in allowed_workflows: raise ValueError("workflow_status")
    active, contact, control = _boolean("active"), _boolean("needs_station_contact"), _boolean("needs_availability_control")
    result = []
    for row in rows:
        selected = [row["resources"][resource]] if resource else row["resources"].values()
        if search and search not in row["station_name"].casefold() and search not in row["station_slug"].casefold(): continue
        if mapping and row["mapping_status"] != mapping: continue
        if active is not None and row["station_is_active"] != active: continue
        if contact is not None and row["needs_station_contact"] != contact: continue
        if control is not None and row["needs_availability_control"] != control: continue
        if missing and missing not in row["missing_resource_types"]: continue
        if availability and not any(item["availability_status"] == availability for item in selected): continue
        if workflow and not any(item["workflow_status"] == workflow for item in selected): continue
        result.append(row)
    return result


def _sort(rows):
    sort = request.args.get("sort", "coverage")
    direction = request.args.get("direction", "asc")
    if sort not in {"name", "coverage", "missing_resources"} or direction not in {"asc", "desc"}:
        raise ValueError("sort")
    priority = {"needs_station_contact": 0, "needs_control": 1, "available_not_imported": 2,
                "to_prepare": 3, "ready_to_review": 4, "error": 5, "partial": 6, "covered": 7}
    if sort == "name": key = lambda row: row["station_name"].casefold()
    elif sort == "missing_resources": key = lambda row: (len(row["missing_resource_types"]), row["station_name"].casefold())
    else: key = lambda row: (priority.get(row["coverage_status"], 99), row["station_name"].casefold())
    return sorted(rows, key=key, reverse=direction == "desc")


def _csv(rows):
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["station_name", "station_slug", "anmsm_mapping", "logo_requested",
                     "piste_map_requested", "reason", "last_anmsm_sync_at"])
    for row in rows:
        if not row["needs_station_contact"]: continue
        writer.writerow([row["station_name"], row["station_slug"], row["anmsm_external_station_id"] or "",
                         "logo" in row["missing_resource_types"], "piste_map" in row["missing_resource_types"],
                         "+".join(row["missing_resource_types"]), row["last_anmsm_sync_at"] or ""])
    return Response(output.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=anmsm-stations-to-contact.csv"})


@bp_admin_anmsm_coverage.get("")
def coverage():
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 25))))
        rows, only = build_coverage()
        global_stats = stats(rows, only)
        rows = _sort(_filtered(rows))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_query"}), 400
    if request.args.get("format") == "csv": return _csv(rows)
    if request.args.get("format") not in {None, "json"}: return jsonify({"ok": False, "error": "invalid_format"}), 400
    scope = request.args.get("scope", "all")
    if scope not in {"all", "snow_explorer", "anmsm_only"}: return jsonify({"ok": False, "error": "invalid_scope"}), 400
    start = (page - 1) * per_page; total = len(rows)
    only_search = request.args.get("search", "").strip().casefold()
    if only_search:
        only = [row for row in only if only_search in row["anmsm_station_name"].casefold()]
    only.sort(key=lambda row: row["anmsm_station_name"].casefold())
    return jsonify({
        "ok": True,
        "snow_explorer_stations": rows[start:start + per_page] if scope != "anmsm_only" else [],
        "anmsm_only_stations": only[start:start + per_page] if scope != "snow_explorer" else [],
        "pagination": {"page": page, "per_page": per_page, "total": total,
                       "pages": math.ceil(total / per_page)},
        "anmsm_only_pagination": {"page": page, "per_page": per_page, "total": len(only),
                                  "pages": math.ceil(len(only) / per_page)},
        "stats": global_stats,
    })
