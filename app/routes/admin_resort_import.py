import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request
from peewee import prefetch

from app.models.base import db
from app.models.lift import Lift
from app.models.piste import Piste
from app.models.resort import Resort
from app.models.resort_import_history import ResortImportHistory
from app.models.station_widgets import StationWidgets
from app.services.admin_auth import admin_required
from app.services.resort_json import (SCHEMA_VERSION, ValidationProblem, apply_record,
    checksum, differences, export_document, parse_upload, preview_token,
    serialize_station, validate_document, verify_token)

bp_resort_json = Blueprint("admin_resort_json", __name__, url_prefix="/api/admin/resorts")


def _find(identifier):
    return Resort.get_or_none((Resort.id == identifier) | (Resort.slug == identifier))


def _json_file(data, filename):
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    return Response(body, content_type="application/json; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _error(exc):
    if isinstance(exc, OverflowError): return jsonify({"error": "file_too_large"}), 413
    if isinstance(exc, ValueError): return jsonify({"error": "invalid_json"}), 400
    return jsonify({"valid": False, "errors": exc.errors}), 422


@bp_resort_json.get("/export")
def export_all():
    query = Resort.select().order_by(Resort.slug.asc())
    active = request.args.get("active")
    if active in {"true", "false"}: query = query.where(Resort.is_active == (active == "true"))
    resorts = list(prefetch(query, Piste, Lift))
    widgets = {w.station_slug: StationWidgets.from_json(w.config) for w in StationWidgets.select().where(StationWidgets.station_slug.in_([r.slug for r in resorts]))} if resorts else {}
    stations = [serialize_station(r, widgets=widgets.get(r.slug, {}), pistes=list(r.pistes), lifts=list(r.lifts)) for r in resorts]
    data = {"schema_version": SCHEMA_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(), "stations": stations}
    return _json_file(data, f"snow-explorer-stations-{datetime.now(timezone.utc).date().isoformat()}.json")


@bp_resort_json.get("/template")
@bp_resort_json.get("/import/template")
@bp_resort_json.get("/import-template")
def template():
    station = {key: None for key in Resort._meta.fields if key in {"id", "slug", "name", "is_active", "department", "region_id", "region_name", "country_code", "website_url", "cover_image_url", "logo_url", "amenities", "description_md", "description_html", "meta_title", "meta_description", "altitude_min_m", "altitude_max_m", "altitude_base_m", "altitude_top_m", "ski_area_km", "pistes_count", "lifts_count", "season_open_date", "season_close_date", "latitude", "longitude"}}
    station.update({"id": "example-id", "slug": "example-slug", "name": "Example station", "is_active": False})
    return _json_file({"schema_version": SCHEMA_VERSION, "exported_at": None, "station": station}, "snow-explorer-station-import-template.json")


@bp_resort_json.get("/history")
@bp_resort_json.get("/imports/history")
@bp_resort_json.get("/import-history")
def histories():
    return jsonify({"items": [_history_dict(h) for h in ResortImportHistory.select().order_by(ResortImportHistory.created_at.desc()).limit(100)]})


@bp_resort_json.get("/history/<history_id>")
@bp_resort_json.get("/imports/history/<history_id>")
@bp_resort_json.get("/import-history/<history_id>")
def history(history_id):
    row = ResortImportHistory.get_or_none(ResortImportHistory.id == history_id)
    return (jsonify(_history_dict(row)), 200) if row else (jsonify({"error": "not_found"}), 404)


@bp_resort_json.get("/<identifier>/export")
@admin_required
def export_one(identifier):
    resort = _find(identifier)
    if not resort: return jsonify({"error": "not_found"}), 404
    return _json_file(export_document(resort, widgets=_widgets(resort.slug)), f"snow-explorer-station-{resort.slug}.json")


@bp_resort_json.post("/<identifier>/import/preview")
@admin_required
def preview_one(identifier):
    resort = _find(identifier)
    if not resort: return jsonify({"error": "not_found"}), 404
    try:
        document, filename = parse_upload(request); record = validate_document(document)[0]
    except (OverflowError, ValueError, ValidationProblem) as exc: return _error(exc)
    identity = record["station"]
    if identity.get("id") != str(resort.id) and identity.get("slug") != resort.slug:
        return jsonify({"valid": False, "errors": [{"path": "station", "message": "target id or slug does not match"}]}), 422
    if identity.get("id") and identity.get("slug") and identity["id"] != str(resort.id) and identity["slug"] == resort.slug:
        return jsonify({"valid": False, "errors": [{"path": "station", "message": "id/slug conflict"}]}), 422
    changes, unchanged = differences(resort, record); options = {"type": "single", "target": str(resort.id)}
    return jsonify({"valid": True, "schema_version": SCHEMA_VERSION, "target": {"id": str(resort.id), "slug": resort.slug, "name": resort.name}, "changes": changes, "unchanged_fields": unchanged, "warnings": [], "errors": [], "checksum": checksum(document), "preview_token": preview_token(document, options)})


@bp_resort_json.post("/<identifier>/import/confirm")
@admin_required
def confirm_one(identifier):
    resort = _find(identifier)
    if not resort: return jsonify({"error": "not_found"}), 404
    token = request.form.get("preview_token") or request.headers.get("X-Preview-Token")
    try: document, filename = parse_upload(request); record = validate_document(document)[0]
    except (OverflowError, ValueError, ValidationProblem) as exc: return _error(exc)
    options = {"type": "single", "target": str(resort.id)}
    if not verify_token(document, options, token): return jsonify({"error": "invalid_preview_token"}), 409
    changes, _ = differences(resort, record)
    try:
        with db.atomic():
            updated, relations = apply_record(resort, record)
            hist = _history(filename, document, "single", "success", resort.id, 1, bool(changes), 0, not bool(changes), 0, changes, [])
    except Exception as exc:
        return jsonify({"error": "import_failed", "details": str(exc)}), 422
    return jsonify({"success": True, "station": {"id": str(resort.id), "slug": resort.slug, "name": resort.name}, "updated_fields": updated, "created_relations": [], "updated_relations": relations, "deleted_relations": [], "warnings": [], "history_id": hist.id})


def _bulk_options():
    def flag(name, default):
        value = request.form.get(name, request.args.get(name))
        return default if value is None else str(value).lower() == "true"
    return {"type": "bulk", "create_missing": flag("create_missing", False), "all_or_nothing": flag("all_or_nothing", True)}


@bp_resort_json.post("/import/preview")
@admin_required
def preview_bulk():
    options = _bulk_options()
    try: document, _ = parse_upload(request); records = validate_document(document, bulk=True)
    except (OverflowError, ValueError, ValidationProblem) as exc: return _error(exc)
    result, counts, errors = _classify(records, options["create_missing"])
    summary = {"total": len(records), **counts, "errors": len(errors)}
    return jsonify({"valid": not errors, "summary": summary, "stations": result, "warnings": [], "errors": errors, "checksum": checksum(document), "preview_token": preview_token(document, options)})


@bp_resort_json.post("/import/confirm")
@admin_required
def confirm_bulk():
    options = _bulk_options(); token = request.form.get("preview_token") or request.headers.get("X-Preview-Token")
    try: document, filename = parse_upload(request); records = validate_document(document, bulk=True)
    except (OverflowError, ValueError, ValidationProblem) as exc: return _error(exc)
    if not verify_token(document, options, token): return jsonify({"error": "invalid_preview_token"}), 409
    classified, _, errors = _classify(records, options["create_missing"])
    if errors and options["all_or_nothing"]: return jsonify({"error": "strict_import_has_errors", "errors": errors}), 422
    updated = created = ignored = failed = 0; all_changes = []
    try:
        with db.atomic():
            for record, item in zip(records, classified):
                if item["status"] in {"missing", "conflict", "invalid"}: ignored += 1; continue
                resort = _resolve(record)
                if not resort:
                    station = record["station"]
                    resort = Resort.create(**{k: v for k, v in station.items() if k in Resort._meta.fields}); created += 1
                changes, _ = differences(resort, record); apply_record(resort, record)
                if changes: updated += 1; all_changes.extend(changes)
                else: ignored += 1
            status = "partial" if errors else "success"
            hist = _history(filename, document, "bulk", status, None, len(records), updated, created, ignored, failed, all_changes, errors)
    except Exception as exc: return jsonify({"error": "import_failed", "details": str(exc)}), 422
    return jsonify({"success": True, "stations_updated": updated, "stations_created": created, "stations_ignored": ignored, "stations_failed": failed, "history_id": hist.id})


def _resolve(record):
    identity = record["station"]; by_id = Resort.get_or_none(Resort.id == identity.get("id")) if identity.get("id") else None
    by_slug = Resort.get_or_none(Resort.slug == identity.get("slug")) if identity.get("slug") else None
    if by_id and by_slug and by_id.id != by_slug.id: return "conflict"
    return by_id or by_slug


def _classify(records, create):
    result = []; counts = {"existing": 0, "missing": 0, "unchanged": 0}; errors = []
    for i, record in enumerate(records):
        found = _resolve(record); identity = record["station"]
        if found == "conflict": status = "conflict"; errors.append({"path": f"stations.{i}.station", "message": "id/slug conflict"})
        elif not found: status = "create" if create else "missing"; counts["missing"] += 1
        else:
            changes, _ = differences(found, record); status = "update" if changes else "unchanged"; counts["existing"] += 1; counts["unchanged"] += not bool(changes)
        result.append({"id": identity.get("id"), "slug": identity.get("slug"), "status": status, "changes": [] if not found or found == "conflict" else differences(found, record)[0]})
    return result, counts, errors


def _widgets(slug):
    row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    return StationWidgets.from_json(row.config) if row else {}


def _history(filename, document, kind, status, target, total, updated, created, ignored, failed, changes, errors):
    return ResortImportHistory.create(created_at=datetime.now(timezone.utc), user_id=request.headers.get("X-Admin-User"), file_name=filename, schema_version=SCHEMA_VERSION, import_type=kind, status=status, target_station_id=str(target) if target else None, stations_total=total, stations_updated=int(updated), stations_created=created, stations_ignored=int(ignored), stations_failed=failed, changes_summary=json.dumps(changes, ensure_ascii=False), errors_summary=json.dumps(errors, ensure_ascii=False), checksum=checksum(document))


def _history_dict(row):
    return {"id": row.id, "created_at": row.created_at.isoformat(), "user_id": row.user_id, "file_name": row.file_name, "schema_version": row.schema_version, "import_type": row.import_type, "status": row.status, "target_station_id": row.target_station_id, "stations_total": row.stations_total, "stations_updated": row.stations_updated, "stations_created": row.stations_created, "stations_ignored": row.stations_ignored, "stations_failed": row.stations_failed, "changes_summary": json.loads(row.changes_summary), "errors_summary": json.loads(row.errors_summary), "checksum": row.checksum}
