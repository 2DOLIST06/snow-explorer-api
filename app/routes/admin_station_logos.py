from urllib.parse import unquote, urlparse

from flask import Blueprint, abort, current_app, g, jsonify, request

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.base import db
from app.models.resort import Resort
from app.models.station_logo_candidate import StationLogoCandidate
from app.services import s3
from app.services.public_cache import invalidate_station

bp_admin_station_logos = Blueprint(
    "admin_station_logos", __name__, url_prefix="/api/admin/anmsm/logos"
)

def _candidate_json(candidate):
    return {"id": candidate.id, "station_id": candidate.station_id,
        "station_name": candidate.station.name, "external_station_id": candidate.external_station_id,
        "anmsm_media_id": candidate.anmsm_media_id, "anmsm_title": candidate.anmsm_title,
        "anmsm_credit": candidate.anmsm_credit, "source_url": candidate.source_url,
        "source_checksum": candidate.source_checksum, "source_format": candidate.source_format,
        "source_width": candidate.source_width, "source_height": candidate.source_height,
        "source_size_bytes": candidate.source_size_bytes, "optimized_s3_key": candidate.optimized_s3_key,
        "optimized_url": candidate.optimized_url, "optimized_width": candidate.optimized_width,
        "optimized_height": candidate.optimized_height, "optimized_size_bytes": candidate.optimized_size_bytes,
        "content_width": candidate.content_width, "content_height": candidate.content_height,
        "aspect_ratio": candidate.aspect_ratio, "visual_occupancy_width": candidate.visual_occupancy_width,
        "visual_occupancy_height": candidate.visual_occupancy_height, "warnings": candidate.warning_codes(),
        "status": candidate.status, "detected_at": candidate.detected_at.isoformat() if candidate.detected_at else None,
        "checked_at": candidate.checked_at.isoformat() if candidate.checked_at else None,
        "error_code": candidate.error_code, "error_message": candidate.error_message}

@bp_admin_station_logos.get("")
def candidates():
    query = StationLogoCandidate.select(StationLogoCandidate, Resort).join(Resort)
    status = request.args.get("status")
    if status:
        if status not in {"pending", "approved", "ignored", "updated", "error"}: abort(400, "statut invalide")
        query = query.where(StationLogoCandidate.status == status)
    return jsonify({"items": [_candidate_json(item) for item in query.order_by(StationLogoCandidate.detected_at.desc())]})


@bp_admin_station_logos.post("/sync")
def sync_anmsm_logos():
    """Fetch ANMSM logos and create candidates for administrator review."""
    payload = request.get_json(silent=True) or {}
    cursor = payload.get("cursor")
    batch_size = payload.get("batch_size", 2)
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        return jsonify({"ok": False, "error": "invalid_cursor",
                        "message": "cursor doit être null ou une chaîne non vide."}), 400
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 3:
        return jsonify({"ok": False, "error": "invalid_batch_size",
                        "message": "batch_size doit être un entier compris entre 1 et 3."}), 400
    try:
        # Keep the import local so importing the routes never starts or
        # configures the external integration.
        from app.services.anmsm_logos import sync

        return jsonify({"ok": True, **sync(cursor=cursor, batch_size=batch_size)})
    except Exception:
        # Upstream, image-processing and object-storage failures must retain
        # the API's JSON contract instead of returning Flask's HTML 500 page.
        current_app.logger.exception("ANMSM logo synchronization failed")
        return jsonify({"error": "anmsm_logo_sync_failed"}), 502

@bp_admin_station_logos.get("/mappings")
def mappings():
    rows = AnmsmStationMapping.select().order_by(AnmsmStationMapping.created_at.desc())
    return jsonify({"items": [{"id": row.id, "station_id": row.station_id,
        "external_station_id": row.external_station_id, "source": row.source, "verified": row.verified} for row in rows]})

@bp_admin_station_logos.put("/mappings/<int:mapping_id>")
def verify_mapping(mapping_id):
    payload = request.get_json(silent=True) or {}; station = Resort.get_or_none(Resort.id == payload.get("station_id"))
    mapping = AnmsmStationMapping.get_or_none(AnmsmStationMapping.id == mapping_id)
    if not mapping or not station: abort(404, "association ou station introuvable")
    mapping.station = station; mapping.verified = True; mapping.updated_at = utcnow(); mapping.save()
    return jsonify({"ok": True})

def _previous_key(url):
    base = s3.setting("AWS_S3_PUBLIC_URL").rstrip("/")
    if not base and s3.bucket() and s3.setting("AWS_REGION"):
        base = f"https://{s3.bucket()}.s3.{s3.setting('AWS_REGION')}.amazonaws.com"
    return unquote(urlparse(url).path.lstrip("/")) if url and base and url.startswith(base + "/") else None

@bp_admin_station_logos.post("/<int:candidate_id>/approve")
def approve(candidate_id):
    with db.atomic():
        candidate = StationLogoCandidate.get_or_none(StationLogoCandidate.id == candidate_id)
        if not candidate: abort(404, "candidat introuvable")
        if candidate.status not in {"pending", "updated"}: abort(409, "ce candidat a déjà été traité")
        if not candidate.optimized_url or candidate.optimized_size_bytes > 50 * 1024: abort(409, "fichier optimisé invalide")
        resort = candidate.station
        candidate.previous_logo_url = resort.logo_url; candidate.previous_logo_s3_key = _previous_key(resort.logo_url)
        resort.logo_url = candidate.optimized_url; resort.updated_at = utcnow(); resort.save()
        candidate.status = "approved"; candidate.approved_at = utcnow(); candidate.checked_at = candidate.approved_at
        candidate.approved_by = g.admin_user; candidate.updated_at = utcnow(); candidate.save()
    invalidate_station(resort.slug)
    return jsonify({"ok": True, "candidate": _candidate_json(candidate)})

@bp_admin_station_logos.post("/<int:candidate_id>/ignore")
def ignore(candidate_id):
    candidate = StationLogoCandidate.get_or_none(StationLogoCandidate.id == candidate_id)
    if not candidate: abort(404, "candidat introuvable")
    if candidate.status not in {"pending", "updated"}: abort(409, "ce candidat a déjà été traité")
    candidate.status = "ignored"; candidate.ignored_at = utcnow(); candidate.checked_at = candidate.ignored_at
    candidate.ignored_by = g.admin_user; candidate.updated_at = utcnow(); candidate.save()
    return jsonify({"ok": True, "candidate": _candidate_json(candidate)})
