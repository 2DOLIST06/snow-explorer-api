"""Import and reconcile the bundled, versioned ski-area catalogue."""
import hashlib
import json
import re
import unicodedata
import uuid
from pathlib import Path

from peewee import IntegrityError

from app.datetime_utils import utcnow
from app.models.resort import Resort
from app.models.ski_area import (SkiArea, SkiAreaCatalogArea,
                                 SkiAreaCatalogImport, SkiAreaCatalogNotice,
                                 SkiAreaExpectedMembership,
                                 SkiAreaExpectedStation, SkiAreaResort)

SCHEMA_VERSION = "snow-explorer-area-catalog/1"
MAX_BYTES = 2 * 1024 * 1024
CATALOG_PATH = Path(__file__).resolve().parents[2] / "data/imports/snow_explorer_domaines_import.json"


class CatalogError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details


def _normalized(value):
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _load():
    size = CATALOG_PATH.stat().st_size
    if size > MAX_BYTES:
        raise CatalogError("catalog_too_large", f"Catalogue exceeds {MAX_BYTES} bytes")
    raw = CATALOG_PATH.read_bytes()
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError("invalid_catalog_json", str(exc)) from exc
    return data, hashlib.sha256(raw).hexdigest(), size


def _validate(data):
    required = {"schema_version", "catalog_id", "batch_id", "areas", "station_catalog",
                "existing_resorts_inventory", "review_proposals", "alerts"}
    missing = sorted(required - set(data)) if isinstance(data, dict) else sorted(required)
    if missing:
        raise CatalogError("invalid_catalog", "Required catalogue keys are missing", missing)
    if data["schema_version"] != SCHEMA_VERSION:
        raise CatalogError("unsupported_catalog_version", "Unsupported schema_version")
    station_refs = [s.get("station_ref") for s in data["station_catalog"]]
    area_keys = [a.get("catalog_key") for a in data["areas"]]
    errors = []
    if None in station_refs or len(station_refs) != len(set(station_refs)): errors.append("duplicate_or_empty_station_ref")
    if None in area_keys or len(area_keys) != len(set(area_keys)): errors.append("duplicate_or_empty_catalog_key")
    known = set(station_refs)
    pairs = set()
    for area in data["areas"]:
        if not area.get("name") or not area.get("slug"): errors.append(f"invalid_area:{area.get('catalog_key')}")
        for member in area.get("members", []):
            pair = (area.get("catalog_key"), member.get("station_ref"))
            if member.get("station_ref") not in known: errors.append(f"unknown_station_ref:{member.get('station_ref')}")
            if pair in pairs: errors.append(f"duplicate_membership:{pair[0]}:{pair[1]}")
            pairs.add(pair)
    if errors:
        raise CatalogError("invalid_catalog", "Catalogue consistency checks failed", errors)


def _identity_assessment(entry):
    """Only the stable exported id + coherent slug authorizes automatic identity."""
    rid = entry.get("existing_resort_id")
    if rid:
        resort = Resort.get_or_none(Resort.id == rid)
        if not resort: return "conflict", None, "exported_id_not_found"
        if resort.slug != entry.get("existing_slug"): return "conflict", resort, "exported_slug_mismatch"
        return "matched", resort, "stable_id_and_slug"
    if entry.get("resolution") == "optional_detail":
        return "optional_detail", None, "covered_by_is_not_identity"
    # Geography narrows candidates but never turns a name match into an automatic link.
    normalized_names = {_normalized(entry.get("name")), *(_normalized(v) for v in entry.get("aliases", []))}
    candidates = []
    for resort in Resort.select():
        if _normalized(resort.name) not in normalized_names: continue
        if entry.get("country_code") and resort.country_code and resort.country_code != entry["country_code"]: continue
        if entry.get("department") and resort.department and resort.department != entry["department"]: continue
        candidates.append(resort)
    return ("candidate" if candidates else "missing"), None, [str(r.id) for r in candidates]


def preview_catalog():
    data, digest, size = _load(); _validate(data)
    area_rows, station_rows = [], []
    counts = {k: 0 for k in ("areas_new", "areas_mapped", "area_collisions", "stations_matched",
                              "stations_inactive", "stations_missing", "stations_optional_detail",
                              "stations_candidates", "conflicts", "memberships_linkable", "memberships_pending")}
    for area in data["areas"]:
        mapped = SkiAreaCatalogArea.get_or_none((SkiAreaCatalogArea.catalog_id == data["catalog_id"]) &
                                                (SkiAreaCatalogArea.catalog_key == area["catalog_key"]))
        collision = SkiArea.get_or_none(SkiArea.slug == area["slug"]) if not mapped else None
        state = "mapped" if mapped and mapped.ski_area_id else ("collision" if collision else "new")
        counts[{"mapped": "areas_mapped", "collision": "area_collisions", "new": "areas_new"}[state]] += 1
        area_rows.append({"catalog_key": area["catalog_key"], "name": area["name"], "proposed_slug": area["slug"],
                          "state": state, "ski_area_id": mapped.ski_area_id if mapped else None,
                          "collision_ski_area_id": collision.id if collision else None})
    assessments = {}
    for station in data["station_catalog"]:
        state, resort, reason = _identity_assessment(station); assessments[station["station_ref"]] = (state, resort)
        key = {"matched": "stations_matched", "missing": "stations_missing", "candidate": "stations_candidates",
               "optional_detail": "stations_optional_detail", "conflict": "conflicts"}[state]
        counts[key] += 1
        if state == "matched" and not resort.is_active: counts["stations_inactive"] += 1
        station_rows.append({"station_ref": station["station_ref"], "name": station["name"], "state": state,
                             "resort_id": str(resort.id) if resort else None, "is_active": bool(resort.is_active) if resort else None,
                             "reason_or_candidates": reason})
    for area in data["areas"]:
        for member in area["members"]:
            state, _ = assessments[member["station_ref"]]
            counts["memberships_linkable" if state == "matched" else "memberships_pending"] += 1
    return {"schema_version": data["schema_version"], "catalog_id": data["catalog_id"],
            "batch_id": data["batch_id"], "sha256": digest, "size_bytes": size, "counts": counts,
            "areas": area_rows, "stations": station_rows, "review_proposals": data["review_proposals"],
            "alerts": data["alerts"], "non_exhaustive": True}


def _upsert(model, where, create, update):
    row = model.get_or_none(where)
    if row is None: return model.create(**create), True
    for key, value in update.items(): setattr(row, key, value)
    row.save(); return row, False


def apply_catalog(expected_sha256=None):
    data, digest, _ = _load(); _validate(data)
    if expected_sha256 and expected_sha256 != digest:
        raise CatalogError("catalog_changed", "Catalogue changed after preview")
    preview = preview_catalog()
    result = {"areas_created": 0, "areas_reused": 0, "area_collisions": 0,
              "stations_linked": 0, "stations_pending": 0, "memberships_created": 0,
              "memberships_linked": 0, "memberships_unchanged": 0, "conflicts": []}
    database = SkiArea._meta.database
    with database.atomic():
        run = SkiAreaCatalogImport.create(id=str(uuid.uuid4()), catalog_id=data["catalog_id"], batch_id=data["batch_id"],
            schema_version=data["schema_version"], file_sha256=digest, status="applying",
            preview_json=json.dumps(preview, ensure_ascii=False))
        area_map = {}
        for value in data["areas"]:
            existing = SkiAreaCatalogArea.get_or_none((SkiAreaCatalogArea.catalog_id == data["catalog_id"]) &
                                                       (SkiAreaCatalogArea.catalog_key == value["catalog_key"]))
            area = existing.ski_area if existing and existing.ski_area_id else None
            if area: result["areas_reused"] += 1
            elif SkiArea.get_or_none(SkiArea.slug == value["slug"]):
                result["area_collisions"] += 1
                result["conflicts"].append({"catalog_key": value["catalog_key"], "reason": "area_slug_collision"})
            else:
                area = SkiArea.create(name=value["name"], slug=value["slug"], status="draft")
                result["areas_created"] += 1
            row, _ = _upsert(SkiAreaCatalogArea,
                (SkiAreaCatalogArea.catalog_id == data["catalog_id"]) & (SkiAreaCatalogArea.catalog_key == value["catalog_key"]),
                dict(catalog_id=data["catalog_id"], catalog_key=value["catalog_key"], ski_area=area, name=value["name"],
                     proposed_slug=value["slug"], area_kind=value.get("area_kind"), notes=value.get("notes") or None,
                     sources_json=json.dumps(value.get("sources", []), ensure_ascii=False)),
                dict(ski_area=area, name=value["name"], proposed_slug=value["slug"], area_kind=value.get("area_kind"),
                     notes=value.get("notes") or None, sources_json=json.dumps(value.get("sources", []), ensure_ascii=False), updated_at=utcnow()))
            area_map[value["catalog_key"]] = row
        station_map = {}
        for value in data["station_catalog"]:
            state, resort, reason = _identity_assessment(value)
            resolution_state = "linked" if state == "matched" else ("needs_review" if state in ("candidate", "conflict") else state)
            row, _ = _upsert(SkiAreaExpectedStation,
                (SkiAreaExpectedStation.catalog_id == data["catalog_id"]) & (SkiAreaExpectedStation.station_ref == value["station_ref"]),
                dict(catalog_id=data["catalog_id"], station_ref=value["station_ref"], name=value["name"],
                     country_code=value.get("country_code"), department=value.get("department"), aliases_json=json.dumps(value.get("aliases", []), ensure_ascii=False),
                     origin_resolution=value["resolution"], covered_by_json=json.dumps(value.get("covered_by_resort_ids", [])), resort=resort,
                     resolution_state=resolution_state, resolution_note=json.dumps(reason) if reason else None),
                # A prior manual identity is durable; catalogue observations cannot replace it.
                dict(name=value["name"], country_code=value.get("country_code"), department=value.get("department"),
                     aliases_json=json.dumps(value.get("aliases", []), ensure_ascii=False), origin_resolution=value["resolution"],
                     covered_by_json=json.dumps(value.get("covered_by_resort_ids", [])), updated_at=utcnow()))
            if not row.resort_id and resort:
                row.resort, row.resolution_state, row.resolution_note = resort, "linked", "stable_id_and_slug"; row.save()
            station_map[value["station_ref"]] = row
            result["stations_linked" if row.resort_id else "stations_pending"] += 1
            if state == "conflict": result["conflicts"].append({"station_ref": value["station_ref"], "reason": reason})
        for area_value in data["areas"]:
            area_row = area_map[area_value["catalog_key"]]
            for member in area_value["members"]:
                station_row = station_map[member["station_ref"]]
                membership, created = _upsert(SkiAreaExpectedMembership,
                    (SkiAreaExpectedMembership.catalog_area == area_row) & (SkiAreaExpectedMembership.expected_station == station_row),
                    dict(catalog_area=area_row, expected_station=station_row, evidence_status=member["evidence_status"],
                         relation_kind=member["relation_kind"], source_json=json.dumps(area_value.get("sources", []), ensure_ascii=False)),
                    dict(evidence_status=member["evidence_status"], relation_kind=member["relation_kind"],
                         source_json=json.dumps(area_value.get("sources", []), ensure_ascii=False), updated_at=utcnow()))
                result["memberships_created" if created else "memberships_unchanged"] += 1
                if membership.state == "ignored": continue
                if area_row.ski_area_id and station_row.resort_id and member["evidence_status"] == "confirmed":
                    SkiAreaResort.get_or_create(ski_area=area_row.ski_area_id, resort=station_row.resort_id)
                    membership.state = "linked"; membership.save(); result["memberships_linked"] += 1
                elif membership.state != "needs_review":
                    membership.state = "needs_review" if station_row.resolution_state == "needs_review" else "pending"; membership.save()
        for kind in ("review_proposals", "alerts"):
            for payload in data[kind]:
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                key = hashlib.sha256((kind + encoded).encode()).hexdigest()
                SkiAreaCatalogNotice.get_or_create(catalog_id=data["catalog_id"], notice_key=key,
                                                    defaults={"kind": kind, "payload_json": encoded})
        run.status, run.result_json, run.applied_at = "applied", json.dumps(result, ensure_ascii=False), utcnow(); run.save()
    return run, result


def candidate_rows(station):
    output = []
    for expected in SkiAreaExpectedStation.select().where(SkiAreaExpectedStation.resort.is_null()):
        names = [expected.name] + json.loads(expected.aliases_json or "[]")
        name_match = _normalized(station.name) in {_normalized(v) for v in names}
        geography = bool(expected.country_code and expected.department and
                         station.country_code == expected.country_code and station.department == expected.department)
        if name_match:
            output.append((expected, "strong" if geography else "ambiguous", ["normalized_name"] + (["country", "department"] if geography else [])))
    return output


def resolve_identity(expected, resort, ignored=False, note=None):
    with SkiArea._meta.database.atomic():
        expected.resort = None if ignored else resort
        expected.resolution_state = "ignored" if ignored else "linked"
        expected.resolution_note = note
        expected.updated_at = utcnow(); expected.save()
        linked = 0
        for membership in expected.expected_memberships:
            if ignored:
                membership.state, membership.decision_origin, membership.decision_note = "ignored", "manual", note
            elif membership.state != "ignored" and membership.catalog_area.ski_area_id and membership.evidence_status == "confirmed":
                SkiAreaResort.get_or_create(ski_area=membership.catalog_area.ski_area_id, resort=resort)
                membership.state, membership.decision_origin = "linked", "manual"; linked += 1
            membership.updated_at = utcnow(); membership.save()
    return linked


def mark_membership_ignored(area_id, resort_id, note="relation removed manually"):
    query = (SkiAreaExpectedMembership.select().join(SkiAreaCatalogArea)
             .switch(SkiAreaExpectedMembership).join(SkiAreaExpectedStation)
             .where((SkiAreaCatalogArea.ski_area == area_id) & (SkiAreaExpectedStation.resort == resort_id)))
    for membership in query:
        membership.state, membership.decision_origin, membership.decision_note = "ignored", "manual", note
        membership.updated_at = utcnow(); membership.save()
