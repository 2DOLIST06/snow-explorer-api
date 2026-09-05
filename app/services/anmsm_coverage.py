import unicodedata

from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.anmsm_station_snapshot import AnmsmStationSnapshot
from app.models.resort import Resort
from app.models.station_logo_candidate import StationLogoCandidate
from app.models.station_piste_map_candidate import StationPisteMapCandidate
from app.services.anmsm_station_mappings import suggestions

RESOURCE_TYPES = ("logo", "piste_map")


def _norm(value):
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()


def _iso(value):
    return value.isoformat() if value else None


def _candidates_by_station(model):
    result = {}
    for candidate in model.select().order_by(model.updated_at.desc(), model.id.desc()):
        result.setdefault(str(candidate.station_id), []).append(candidate)
    return result


def _published_by_anmsm(current_url, candidate, key_field):
    if not current_url or not candidate or candidate.status != "approved" or not candidate.approved_at:
        return False
    key = getattr(candidate, key_field, None)
    return bool(key and current_url.split("?", 1)[0].rstrip("/").endswith("/" + key.lstrip("/")))


def _resource(kind, resort, snapshot, candidates):
    is_logo = kind == "logo"
    candidate = candidates[0] if candidates else None
    current = resort.logo_url if is_logo else resort.pistes_large_map_url
    if not snapshot:
        available = None
    elif is_logo:
        available = snapshot.logo_available
    else:
        available = (snapshot.piste_map_available
                     if snapshot.piste_map_observation_complete or snapshot.piste_map_available is True
                     else None)
    original = (snapshot.logo_url if is_logo else snapshot.piste_map_url) if snapshot else None
    if candidate:
        original = candidate.source_url or original
    preview = None
    preparation_required = False
    if candidate:
        preview = candidate.optimized_url if is_logo else (
            candidate.source_url if candidate.display_s3_key else None)
        preparation_required = bool(
            candidate.status == "pending" and
            (not candidate.optimized_s3_key if is_logo else not candidate.display_s3_key))
    published_candidate = next((item for item in candidates if _published_by_anmsm(
        current, item, "optimized_s3_key" if is_logo else "display_s3_key")), None)
    published = published_candidate is not None
    error = None
    if candidate and (candidate.status == "error" or candidate.error_code or candidate.error_message):
        error = {"code": candidate.error_code, "message": candidate.error_message}
    exploitable_candidate = bool(candidate and candidate.status in {"pending", "approved"} and preview)
    if error:
        workflow = "error"
    elif published:
        workflow = "published"
    elif candidate and candidate.status == "pending" and preparation_required:
        workflow = "to_prepare"
    elif candidate and candidate.status == "pending" and preview:
        workflow = "ready_to_review"
    elif available is True and not candidate:
        workflow = "available_not_imported"
    elif available is False and not current and not exploitable_candidate:
        workflow = "missing_from_anmsm"
    else:
        workflow = "unknown"
    needs_contact = bool(available is False and not current and not exploitable_candidate)
    return {
        "supported": True,
        "available_from_anmsm": available,
        "availability_status": "available" if available is True else "unavailable" if available is False else "unknown",
        "workflow_status": workflow,
        "candidate_id": candidate.id if candidate else None,
        "candidate_status": candidate.status if candidate else None,
        "current_published_url": current,
        "candidate_original_url": original,
        "candidate_preview_url": preview,
        "preparation_required": preparation_required,
        "error": error,
        "published_source": "anmsm" if published else "unknown" if current else None,
        "needs_station_contact": needs_contact,
        "contact_reason": "confirmed_missing_from_anmsm_and_no_exploitable_resource" if needs_contact else None,
    }


def build_coverage():
    resorts = list(Resort.select())
    snapshots = {row.external_station_id.casefold(): row for row in AnmsmStationSnapshot.select()}
    mappings_by_station = {}
    mapped_external_ids = set()
    mapping_errors = set()
    for mapping in AnmsmStationMapping.select().where(AnmsmStationMapping.source == "anmsm"):
        if mapping.station_id and mapping.verified:
            key = str(mapping.station_id)
            if key in mappings_by_station:
                mapping_errors.add(key)
            else:
                mappings_by_station[key] = mapping
                mapped_external_ids.add(mapping.external_station_id.casefold())
    logos = _candidates_by_station(StationLogoCandidate)
    maps = _candidates_by_station(StationPisteMapCandidate)
    station_rows = []
    for resort in resorts:
        mapping = mappings_by_station.get(str(resort.id))
        snapshot = snapshots.get(mapping.external_station_id.casefold()) if mapping else None
        resources = {
            "logo": _resource("logo", resort, snapshot, logos.get(str(resort.id), [])),
            "piste_map": _resource("piste_map", resort, snapshot, maps.get(str(resort.id), [])),
        }
        missing = [kind for kind in RESOURCE_TYPES if resources[kind]["needs_station_contact"]]
        unknown = any(resource["availability_status"] == "unknown" and not resource["current_published_url"]
                      and not resource["candidate_preview_url"] for resource in resources.values())
        workflows = {resource["workflow_status"] for resource in resources.values()}
        if missing: coverage = "needs_station_contact"
        elif unknown: coverage = "needs_control"
        elif "available_not_imported" in workflows: coverage = "available_not_imported"
        elif "to_prepare" in workflows: coverage = "to_prepare"
        elif "ready_to_review" in workflows: coverage = "ready_to_review"
        elif "error" in workflows: coverage = "error"
        elif all(resource["current_published_url"] or resource["candidate_preview_url"] for resource in resources.values()): coverage = "covered"
        else: coverage = "partial"
        station_rows.append({
            "station_id": resort.id, "station_name": resort.name, "station_slug": resort.slug,
            "station_is_active": bool(resort.is_active),
            "anmsm_external_station_id": mapping.external_station_id if mapping else None,
            "anmsm_station_name": snapshot.station_name if snapshot else None,
            "mapping_status": "mapping_error" if str(resort.id) in mapping_errors else "matched" if mapping else "unmatched",
            "mapping_validated": bool(mapping and mapping.verified),
            "last_anmsm_sync_at": _iso(snapshot.last_seen_at) if snapshot else None,
            "coverage_status": coverage, "needs_station_contact": bool(missing),
            "needs_availability_control": unknown, "missing_resource_types": missing,
            "resources": resources,
        })
    only = []
    latest_catalog_at = max((row.station_catalog_seen_at for row in snapshots.values()
                             if row.station_catalog_seen_at), default=None)
    for key, snapshot in snapshots.items():
        if latest_catalog_at and snapshot.station_catalog_seen_at != latest_catalog_at: continue
        if key in mapped_external_ids: continue
        suggestion = next(iter(suggestions(snapshot.station_name, resorts)), None)
        only.append({
            "anmsm_external_station_id": snapshot.external_station_id,
            "anmsm_station_name": snapshot.station_name, "anmsm_station_slug": snapshot.station_slug,
            "last_seen_at": _iso(snapshot.last_seen_at), "logo_available": snapshot.logo_available,
            "piste_map_available": snapshot.piste_map_available,
            "suggested_snow_explorer_station": suggestion, "status": "anmsm_only",
        })
    return station_rows, only


def stats(rows, only):
    def count_resource(kind, predicate):
        return sum(predicate(row["resources"][kind]) for row in rows)
    return {
        "snow_explorer_stations_total": len(rows),
        "snow_explorer_stations_active": sum(row["station_is_active"] for row in rows),
        "snow_explorer_stations_matched": sum(row["mapping_validated"] for row in rows),
        "snow_explorer_stations_unmatched": sum(row["mapping_status"] == "unmatched" for row in rows),
        "anmsm_only_stations_total": len(only),
        "stations_needing_contact": sum(row["needs_station_contact"] for row in rows),
        "stations_needing_availability_control": sum(row["needs_availability_control"] for row in rows),
        "stations_without_exploitable_logo": count_resource("logo", lambda r: not r["current_published_url"] and not r["candidate_preview_url"]),
        "stations_without_exploitable_piste_map": count_resource("piste_map", lambda r: not r["current_published_url"] and not r["candidate_preview_url"]),
        "logos_available_not_imported": count_resource("logo", lambda r: r["workflow_status"] == "available_not_imported"),
        "piste_maps_available_not_imported": count_resource("piste_map", lambda r: r["workflow_status"] == "available_not_imported"),
        "logos_to_prepare": count_resource("logo", lambda r: r["workflow_status"] == "to_prepare"),
        "piste_maps_to_prepare": count_resource("piste_map", lambda r: r["workflow_status"] == "to_prepare"),
        "logos_ready_to_review": count_resource("logo", lambda r: r["workflow_status"] == "ready_to_review"),
        "piste_maps_ready_to_review": count_resource("piste_map", lambda r: r["workflow_status"] == "ready_to_review"),
        "logos_published_by_anmsm": count_resource("logo", lambda r: r["workflow_status"] == "published"),
        "piste_maps_published_by_anmsm": count_resource("piste_map", lambda r: r["workflow_status"] == "published"),
        "errors": sum(resource["workflow_status"] == "error" for row in rows for resource in row["resources"].values()) + sum(row["mapping_status"] == "mapping_error" for row in rows),
    }
