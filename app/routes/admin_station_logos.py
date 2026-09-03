"""Bounded, resumable administration workflow for ANMSM station logos."""
from urllib.parse import unquote, urlparse

from flask import Blueprint, abort, current_app, g, jsonify, request

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.resort import Resort
from app.models.station_logo_candidate import StationLogoCandidate
from app.services import s3
from app.services.anmsm_station_mappings import suggestions
from app.services.public_cache import invalidate_station

bp_admin_station_logos = Blueprint(
    "admin_station_logos", __name__, url_prefix="/api/admin/anmsm/logos"
)


def _preview(candidate):
    if not candidate or not candidate.optimized_s3_key:
        return None
    return s3.preview_url(candidate.optimized_s3_key)


def _candidate_json(candidate):
    preview = _preview(candidate)
    return {"id": candidate.id, "candidate_id": candidate.id,
        "station_id": candidate.station_id, "station_name": candidate.station.name,
        "external_station_id": candidate.external_station_id,
        "current_logo_url": candidate.station.logo_url,
        "previous_logo_url": candidate.previous_logo_url,
        "anmsm_media_id": candidate.anmsm_media_id, "anmsm_title": candidate.anmsm_title,
        "anmsm_credit": candidate.anmsm_credit, "source_url": candidate.source_url,
        "source_checksum": candidate.source_checksum, "source_format": candidate.source_format,
        "source_width": candidate.source_width, "source_height": candidate.source_height,
        "source_size_bytes": candidate.source_size_bytes,
        "optimized_s3_key": candidate.optimized_s3_key,
        # optimized_url remains for old clients, but is generated afresh for a
        # private bucket and is never read from a browser request.
        "optimized_url": preview, "candidate_preview_url": preview,
        "optimized_width": candidate.optimized_width,
        "optimized_height": candidate.optimized_height,
        "optimized_size_bytes": candidate.optimized_size_bytes,
        "content_width": candidate.content_width, "content_height": candidate.content_height,
        "aspect_ratio": candidate.aspect_ratio,
        "visual_occupancy_width": candidate.visual_occupancy_width,
        "visual_occupancy_height": candidate.visual_occupancy_height,
        "warnings": candidate.warning_codes(), "status": candidate.status,
        "detected_at": candidate.detected_at.isoformat() if candidate.detected_at else None,
        "checked_at": candidate.checked_at.isoformat() if candidate.checked_at else None,
        "error_code": candidate.error_code, "error_message": candidate.error_message}


def _workspace_data(stations):
    """Assemble the workspace from the feed and all three persisted datasets.

    The feed is deliberately not the left-hand side of a database join: a
    temporarily missing ANMSM record must not hide a mapping or candidate.
    """
    def join_id(value):
        return str(value or "").strip().casefold()

    resorts = list(Resort.select().order_by(Resort.name))
    resorts_by_id = {join_id(row.id): row for row in resorts}
    mappings = {}
    for mapping in AnmsmStationMapping.select().where(AnmsmStationMapping.source == "anmsm"):
        mappings[join_id(mapping.external_station_id)] = mapping
    candidates = {}
    for candidate in StationLogoCandidate.select().order_by(StationLogoCandidate.updated_at.desc()):
        candidates.setdefault(join_id(candidate.external_station_id), []).append(candidate)

    sources = {}
    for source in stations:
        sources[join_id(source["external_station_id"])] = source

    # Preserve feed order, then append persisted records absent from the feed.
    keys = list(sources)
    keys.extend(key for key in mappings if key not in sources)
    keys.extend(key for key in candidates if key not in sources and key not in mappings)
    rows = []
    for key in keys:
        source = sources.get(key)
        mapping = mappings.get(key)
        station = resorts_by_id.get(join_id(mapping.station_id)) if mapping and mapping.station_id else None
        matched = bool(mapping and mapping.verified and station)
        external_id = (source["external_station_id"] if source else
                       mapping.external_station_id if mapping else
                       candidates[key][0].external_station_id)
        logo = source["logo"] if source else {}
        name = source["external_name"] if source else ""
        matched_candidates = candidates.get(key) or [None]
        for candidate in matched_candidates:
            candidate_data = _candidate_json(candidate) if candidate else None
            row = {
                "external_station_id": external_id,
                "anmsm_station_name": name,
                "anmsm_media_id": logo.get("media_id"), "anmsm_title": logo.get("title"),
                "anmsm_credit": logo.get("credit"), "source_url": logo.get("url"),
                "source_has_logo": bool(logo.get("url")),
                "mapping_status": "matched" if matched else "unmatched",
                "mapping_method": "existing" if matched else None,
                "station_id": station.id if matched else None,
                "station_name": station.name if matched else None,
                "current_logo_url": station.logo_url if matched else None,
                "suggestion": (None if matched or not source else
                               next(iter(suggestions(name, resorts)), None)),
                "candidate_id": candidate.id if candidate else None,
                "candidate_status": candidate.status if candidate else None,
                "candidate_preview_url": (candidate_data["candidate_preview_url"]
                                          if candidate else None),
                "candidate_size_bytes": candidate.optimized_size_bytes if candidate else None,
                "candidate_width": candidate.optimized_width if candidate else None,
                "candidate_height": candidate.optimized_height if candidate else None,
                "warnings": candidate.warning_codes() if candidate else [],
                "preparation_required": bool(matched and logo.get("url") and
                                             (not candidate or not candidate.optimized_s3_key)),
                "preparation_error": (candidate.error_message
                                      if candidate and candidate.status == "error" else None),
            }
            rows.append(row)
    statuses = [row["candidate_status"] for row in rows]
    stats = {"stations_received": len(rows),
        "stations_matched": sum(row["mapping_status"] == "matched" for row in rows),
        "stations_unmatched": sum(row["mapping_status"] == "unmatched" for row in rows),
        "logos_available": sum(row["source_has_logo"] for row in rows),
        "logos_without_source": sum(not row["source_has_logo"] for row in rows),
        "candidates_pending": statuses.count("pending"),
        "candidates_approved": statuses.count("approved"),
        "candidates_in_error": statuses.count("error"),
        "candidates_to_prepare": sum(row["preparation_required"] for row in rows)}
    serialized_candidate_ids = {row["candidate_id"] for row in rows
                                if row["candidate_id"] is not None}
    database_candidate_ids = {candidate.id for values in candidates.values() for candidate in values}
    if serialized_candidate_ids != database_candidate_ids:
        raise RuntimeError("ANMSM workspace omitted persisted logo candidates")
    if (sum(row["candidate_status"] == "pending" for row in rows) !=
            stats["candidates_pending"] or
            sum(row["mapping_status"] == "matched" for row in rows) !=
            stats["stations_matched"]):
        raise RuntimeError("ANMSM workspace statistics are inconsistent with rows")
    return {"ok": True, "rows": rows, "stats": stats}


@bp_admin_station_logos.get("/workspace")
def workspace():
    from app.services.anmsm_logos import LogoImportError, fetch_stations
    try:
        return jsonify(_workspace_data(fetch_stations()))
    except LogoImportError as exc:
        return jsonify({"ok": False, "error": exc.code, "message": str(exc)}), 502


@bp_admin_station_logos.get("")
def candidates():
    """Deprecated listing retained for the old screen."""
    query = StationLogoCandidate.select(StationLogoCandidate, Resort).join(Resort)
    status = request.args.get("status")
    if status:
        if status not in {"pending", "approved", "ignored", "updated", "error"}:
            abort(400, "statut invalide")
        query = query.where(StationLogoCandidate.status == status)
    response = jsonify({"ok": True, "deprecated": True,
        "items": [_candidate_json(item) for item in query.order_by(StationLogoCandidate.detected_at.desc())]})
    response.headers["Deprecation"] = "true"
    return response


@bp_admin_station_logos.get("/selection")
def selection():
    """Deprecated alias: it performs metadata work only."""
    response = workspace()
    if not isinstance(response, tuple):
        response.headers["Deprecation"] = "true"
    return response


@bp_admin_station_logos.post("/prepare")
def prepare_one():
    payload = request.get_json(silent=True)
    external_id = payload.get("external_station_id") if isinstance(payload, dict) else None
    if not isinstance(external_id, str) or not external_id.strip():
        return jsonify({"ok": False, "error": "invalid_external_station_id"}), 400
    from app.services.anmsm_logos import LogoImportError, prepare
    try:
        candidate, unchanged = prepare(external_id.strip())
        return jsonify({"ok": True, "unchanged": unchanged,
                        "candidate": _candidate_json(candidate)})
    except LogoImportError as exc:
        status = 422 if exc.code in {"station_not_mapped", "source_logo_missing",
                                     "unknown_external_station"} else 502
        return jsonify({"ok": False, "error": exc.code, "message": str(exc),
                        "external_station_id": external_id.strip()}), status
    except Exception:
        current_app.logger.exception("ANMSM single-logo preparation failed")
        return jsonify({"ok": False, "error": "preparation_failed",
                        "external_station_id": external_id.strip()}), 502


@bp_admin_station_logos.post("/sync")
def sync_anmsm_logos():
    """Deprecated bounded endpoint: still processes no more than one station."""
    payload = request.get_json(silent=True) or {}
    if "external_station_id" in payload:
        return prepare_one()
    cursor, batch_size = payload.get("cursor"), payload.get("batch_size", 1)
    if (cursor is not None and (not isinstance(cursor, str) or not cursor.strip())) or batch_size != 1:
        return jsonify({"ok": False, "error": "invalid_bounded_sync_request"}), 400
    try:
        from app.services.anmsm_logos import sync
        response = jsonify({"ok": True, "deprecated": True,
                            **sync(cursor=cursor, batch_size=1)})
        response.headers["Deprecation"] = "true"
        return response
    except Exception:
        current_app.logger.exception("ANMSM logo synchronization failed")
        return jsonify({"ok": False, "error": "anmsm_logo_sync_failed"}), 502


def _previous_key(url):
    base = s3.setting("AWS_S3_PUBLIC_URL").rstrip("/")
    if not base and s3.bucket() and s3.setting("AWS_REGION"):
        base = f"https://{s3.bucket()}.s3.{s3.setting('AWS_REGION')}.amazonaws.com"
    return unquote(urlparse(url).path.lstrip("/")) if url and base and url.startswith(base + "/") else None


def _approve_one(candidate_id):
    resort = None
    try:
        preliminary = StationLogoCandidate.get_or_none(StationLogoCandidate.id == candidate_id)
        if not preliminary:
            return {"candidate_id": candidate_id, "ok": False, "error": "candidate_not_found"}
        if preliminary.status == "approved":
            return {"candidate_id": preliminary.id, "ok": True, "status": "approved",
                    "station_id": preliminary.station_id,
                    "published_logo_url": preliminary.station.logo_url, "unchanged": True}
        checked_key = preliminary.optimized_s3_key
        if (not preliminary.station_id or not checked_key or
                not s3.validate_webp(checked_key)):
            return {"candidate_id": preliminary.id, "ok": False,
                    "error": "invalid_candidate_object"}
        database = StationLogoCandidate._meta.database
        with database.atomic():
            query = StationLogoCandidate.select().where(StationLogoCandidate.id == candidate_id)
            if database.__class__.__name__ != "SqliteDatabase":
                query = query.for_update()
            candidate = query.first()
            if not candidate:
                return {"candidate_id": candidate_id, "ok": False, "error": "candidate_not_found"}
            if candidate.status == "approved":
                return {"candidate_id": candidate.id, "ok": True, "status": "approved",
                        "station_id": candidate.station_id,
                        "published_logo_url": candidate.station.logo_url, "unchanged": True}
            if not candidate.station_id or candidate.optimized_s3_key != checked_key:
                return {"candidate_id": candidate.id, "ok": False, "error": "invalid_candidate_object"}
            resort_query = Resort.select().where(Resort.id == candidate.station_id)
            if database.__class__.__name__ != "SqliteDatabase":
                resort_query = resort_query.for_update()
            resort = resort_query.first()
            if not resort:
                return {"candidate_id": candidate.id, "ok": False, "error": "station_not_found"}
            # Only the first approval stores history. An idempotent retry exits above.
            candidate.previous_logo_url = resort.logo_url
            candidate.previous_logo_s3_key = _previous_key(resort.logo_url)
            published = s3.public_url(candidate.optimized_s3_key)
            resort.logo_url = published; resort.updated_at = utcnow(); resort.save()
            now = utcnow(); candidate.status = "approved"; candidate.approved_at = now
            candidate.checked_at = now; candidate.updated_at = now
            candidate.approved_by = getattr(g, "admin_user", None); candidate.save()
        invalidate_station(resort.slug)
        return {"candidate_id": candidate.id, "ok": True, "status": "approved",
                "station_id": resort.id, "published_logo_url": resort.logo_url}
    except Exception as exc:
        current_app.logger.exception("ANMSM candidate approval failed id=%s", candidate_id)
        return {"candidate_id": candidate_id, "ok": False,
                "error": "approval_failed", "message": str(exc)[:500]}


@bp_admin_station_logos.post("/bulk-approve")
def bulk_approve():
    payload = request.get_json(silent=True)
    candidate_ids = payload.get("candidate_ids") if isinstance(payload, dict) else None
    if (not isinstance(candidate_ids, list) or not candidate_ids or
            any(isinstance(value, bool) or not isinstance(value, int) for value in candidate_ids)):
        return jsonify({"ok": False, "error": "invalid_candidate_ids"}), 400
    # De-duplicate while retaining response order; each call has its own transaction.
    results = [_approve_one(value) for value in dict.fromkeys(candidate_ids)]
    approved = sum(result["ok"] for result in results)
    return jsonify({"ok": True, "approved_count": approved,
                    "failed_count": len(results) - approved, "results": results})


@bp_admin_station_logos.post("/<int:candidate_id>/approve")
def approve(candidate_id):
    """Deprecated single-candidate compatibility wrapper."""
    result = _approve_one(candidate_id)
    return jsonify({"ok": result["ok"], "deprecated": True, "result": result}), (200 if result["ok"] else 409)


@bp_admin_station_logos.post("/<int:candidate_id>/ignore")
def ignore(candidate_id):
    candidate = StationLogoCandidate.get_or_none(StationLogoCandidate.id == candidate_id)
    if not candidate: abort(404, "candidat introuvable")
    if candidate.status not in {"pending", "updated"}: abort(409, "ce candidat a déjà été traité")
    candidate.status = "ignored"; candidate.ignored_at = utcnow(); candidate.checked_at = candidate.ignored_at
    candidate.ignored_by = g.admin_user; candidate.updated_at = utcnow(); candidate.save()
    return jsonify({"ok": True, "candidate": _candidate_json(candidate)})
