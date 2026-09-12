import json

from flask import Blueprint, jsonify, request
from peewee import fn

from app.models.resort import Resort
from app.models.ski_area import (SkiAreaCatalogImport,
                                 SkiAreaExpectedMembership,
                                 SkiAreaExpectedStation)
from app.services.ski_area_catalog import (CatalogError, apply_catalog,
                                            candidate_rows, preview_catalog,
                                            resolve_identity)

bp_admin_ski_area_catalog = Blueprint(
    "admin_ski_area_catalog", __name__, url_prefix="/api/admin/ski-area-catalog"
)


@bp_admin_ski_area_catalog.after_request
def disable_catalog_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _error(code, message, status=400, details=None):
    body = {"error": code, "message": message}
    if details is not None: body["details"] = details
    return jsonify(body), status


def _page():
    try: page, per_page = int(request.args.get("page", 1)), int(request.args.get("per_page", 25))
    except ValueError: raise CatalogError("invalid_pagination", "page and per_page must be integers")
    if page < 1 or not 1 <= per_page <= 100: raise CatalogError("invalid_pagination", "page >= 1 and per_page between 1 and 100 required")
    return page, per_page


def _membership_json(row):
    area = row.catalog_area
    return {"id": row.id, "catalog_key": area.catalog_key, "ski_area_id": area.ski_area_id,
            "area_name": area.name, "area_kind": area.area_kind, "notes": area.notes,
            "sources": json.loads(area.sources_json or "[]"), "evidence_status": row.evidence_status,
            "relation_kind": row.relation_kind, "state": row.state,
            "decision_origin": row.decision_origin, "decision_note": row.decision_note}


def _expected_json(row, memberships=True):
    body = {"id": row.id, "catalog_id": row.catalog_id, "station_ref": row.station_ref,
            "name": row.name, "country_code": row.country_code, "department": row.department,
            "aliases": json.loads(row.aliases_json or "[]"), "origin_resolution": row.origin_resolution,
            "covered_by_resort_ids": json.loads(row.covered_by_json or "[]"),
            "resort_id": str(row.resort_id) if row.resort_id else None,
            "resolution_state": row.resolution_state, "resolution_note": row.resolution_note}
    if memberships: body["expected_memberships"] = [_membership_json(link) for link in row.expected_memberships]
    return body


@bp_admin_ski_area_catalog.post("/preview")
def preview():
    try: return jsonify({"preview": preview_catalog()})
    except CatalogError as exc: return _error(exc.code, str(exc), details=exc.details)


@bp_admin_ski_area_catalog.post("/imports")
def apply():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or set(payload) - {"expected_sha256"}:
        return _error("validation_error", "Only expected_sha256 is accepted")
    try: run, result = apply_catalog(payload.get("expected_sha256"))
    except CatalogError as exc: return _error(exc.code, str(exc), 409 if exc.code == "catalog_changed" else 400, exc.details)
    return jsonify({"import": {"id": run.id, "catalog_id": run.catalog_id, "batch_id": run.batch_id,
                    "status": run.status, "sha256": run.file_sha256, "applied_at": run.applied_at.isoformat(),
                    "result": result}}), 201


@bp_admin_ski_area_catalog.get("/imports/<run_id>")
def import_detail(run_id):
    run = SkiAreaCatalogImport.get_or_none(SkiAreaCatalogImport.id == run_id)
    if not run: return _error("import_not_found", "Import not found", 404)
    return jsonify({"import": {"id": run.id, "catalog_id": run.catalog_id, "batch_id": run.batch_id,
        "schema_version": run.schema_version, "sha256": run.file_sha256, "status": run.status,
        "created_at": run.created_at.isoformat(), "applied_at": run.applied_at.isoformat() if run.applied_at else None,
        "preview": json.loads(run.preview_json), "result": json.loads(run.result_json) if run.result_json else None}})


@bp_admin_ski_area_catalog.get("/expectations")
def expectations():
    try: page, per_page = _page()
    except CatalogError as exc: return _error(exc.code, str(exc))
    query = SkiAreaExpectedStation.select()
    state, search = request.args.get("state"), (request.args.get("q") or "").strip()
    if state: query = query.where(SkiAreaExpectedStation.resolution_state == state)
    if search: query = query.where((SkiAreaExpectedStation.name ** f"%{search}%") | (SkiAreaExpectedStation.station_ref ** f"%{search}%"))
    total = query.count(); rows = query.order_by(SkiAreaExpectedStation.name, SkiAreaExpectedStation.id).paginate(page, per_page)
    return jsonify({"items": [_expected_json(row) for row in rows],
                    "pagination": {"page": page, "per_page": per_page, "total": total, "pages": (total + per_page - 1)//per_page}})


@bp_admin_ski_area_catalog.get("/expectations/<int:expected_id>")
def expectation_detail(expected_id):
    row = SkiAreaExpectedStation.get_or_none(SkiAreaExpectedStation.id == expected_id)
    return jsonify({"expectation": _expected_json(row)}) if row else _error("expectation_not_found", "Expectation not found", 404)


@bp_admin_ski_area_catalog.post("/expectations/<int:expected_id>/decision")
def decide(expected_id):
    row = SkiAreaExpectedStation.get_or_none(SkiAreaExpectedStation.id == expected_id)
    if not row: return _error("expectation_not_found", "Expectation not found", 404)
    payload = request.get_json(silent=True) or {}; decision = payload.get("decision")
    if decision not in ("confirm", "ignore"): return _error("validation_error", "decision must be confirm or ignore")
    resort = None
    if decision == "confirm":
        resort_id = payload.get("resort_id")
        resort = Resort.get_or_none(Resort.id == resort_id)
        if not resort: return _error("station_not_found", "Station not found", 404)
    linked = resolve_identity(row, resort, ignored=decision == "ignore", note=payload.get("note"))
    return jsonify({"expectation": _expected_json(row), "memberships_linked": linked})


@bp_admin_ski_area_catalog.get("/stations/<station_id>/candidates")
def candidates(station_id):
    station = Resort.get_or_none(Resort.id == station_id)
    if not station: return _error("station_not_found", "Station not found", 404)
    items = [{"expectation": _expected_json(row), "confidence": confidence, "signals": signals,
              "automatic_link": False} for row, confidence, signals in candidate_rows(station)]
    return jsonify({"station_id": str(station.id), "items": items})


@bp_admin_ski_area_catalog.post("/reconcile")
def reconcile():
    # Deliberately proposal-only: normalized names never establish identity.
    stations, proposals = list(Resort.select()), []
    for station in stations:
        for row, confidence, signals in candidate_rows(station):
            proposals.append({"station_id": str(station.id), "expected_station_id": row.id,
                              "confidence": confidence, "signals": signals})
    return jsonify({"scanned_stations": len(stations), "proposals": proposals,
                    "linked_automatically": 0})
