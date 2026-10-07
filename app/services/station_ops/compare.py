"""Batch comparison only. No persistence, research, or APPLY operations."""
from collections import defaultdict
import hashlib

from peewee import fn

from app.datetime_utils import utcnow
from app.models.region import Region
from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from .candidates import COMPARE_VERSION, COLLECTION_FIELDS, SCALAR_FIELDS, parse_batch
from .matching import IDENTITY_FIELDS, MatchingIndex, station_reference
from .duplicates import normalized_name
from .normalization import json_normalized, normalize, number
from .scan import CONTENT_FIELDS, SCAN_MODELS, _json_value, read_only_scan
from .schema import PhysicalSchema

STATUSES = ("new", "unchanged", "changes_detected", "review_required", "invalid")


def _summary(results):
    return {"total_candidates": len(results),
            **{status: sum(row["status"] == status for row in results) for status in STATUSES}}


def compare_candidates(payload):
    prepared = parse_batch(payload)
    database = Resort._meta.database
    if any(item["valid"] for item in prepared):
        with read_only_scan(database):
            schema_findings, catalog_findings = _compare(prepared, database)
    else:
        # No business reads needed for a wholly unusable batch.
        schema_findings, catalog_findings = [], []
    results = [item["result"] for item in prepared]
    return {"schema_version": "1.0", "compare_version": COMPARE_VERSION,
            "generated_at": utcnow().isoformat(), "summary": _summary(results),
            "results": results, "schema_findings": schema_findings, "catalog_findings": catalog_findings}


def _select(schema, model, names):
    """Explicit physical projection, with no hidden SELECT of all model fields."""
    fields = []
    for name in sorted(names):
        if not schema.has(model, name):
            continue
        field = model._meta.fields[name]
        if name in CONTENT_FIELDS and model is Resort:
            fields.extend((fn.LENGTH(field).coerce(False).alias(name + "__length"),
                           fn.MD5(field).alias(name + "__md5")))
        else:
            fields.append(field.coerce(False) if (model, name) in schema.raw_numeric_fields else field)
    return model.select(*fields).dicts()


def _compare(prepared, database):
    schema = PhysicalSchema(database, SCAN_MODELS)
    schema.require(Resort, "id")
    index_rows = list(_select(schema, Resort, IDENTITY_FIELDS).order_by(Resort.id))
    index = MatchingIndex(index_rows)
    if schema.has(Region, "id"):
        catalog_findings = [] if Region.select(Region.id).exists() else [
            {"code": "region_catalog_empty", "severity": "info", "table": "regions"}]
    else:
        catalog_findings = [{"code": "region_catalog_unavailable", "severity": "info", "table": "regions"}]
    details_needed, matched_ids = set(), set()
    valid = [item for item in prepared if item["valid"]]
    for item in valid:
        result = item["result"]
        decision = index.match(item["normalized"])
        item["decision"] = decision
        result["match_reasons"] = decision["reasons"]
        result["review_items"].extend(decision["review_items"])
        matched = decision["station"]
        if matched:
            result["matched_station"] = station_reference(matched)
            if decision["distance_m"] is not None:
                result["match_distance_m"] = round(decision["distance_m"], 2)
            matched_ids.add(matched["id"])
            details_needed.update(set(item["normalized"]) | set(item["clear_fields"]))
        else:
            missing_identity = [field for field in IDENTITY_FIELDS
                                if field in item["normalized"] and not schema.has(Resort, field)]
            if missing_identity:
                result["review_items"].append({"code": "matching_columns_unavailable", "fields": missing_identity})
    details_needed -= set(IDENTITY_FIELDS)
    details = {row["id"]: dict(row) for row in index_rows if row["id"] in matched_ids}
    if matched_ids and any(schema.has(Resort, field) for field in details_needed):
        for row in _select(schema, Resort, details_needed | {"id"}).where(Resort.id.in_(sorted(matched_ids))):
            details[row["id"]].update(row)
    needs_areas = any(item["data"].get("ski_areas") is not None for item in valid)
    areas, links = {}, defaultdict(set)
    areas_available = (schema.has(SkiArea, "id") and
                       all(schema.has(SkiAreaResort, field) for field in ("resort", "ski_area")))
    if needs_areas and areas_available:
        areas = {row["id"]: row for row in _select(schema, SkiArea, ("id", "slug", "name")).order_by(SkiArea.id)}
        area_station_ids = sorted({item["decision"]["station"]["id"] for item in valid
                                   if item["data"].get("ski_areas") is not None and item["decision"]["station"]})
        if area_station_ids:
            for link in _select(schema, SkiAreaResort, ("resort", "ski_area")).where(SkiAreaResort.resort.in_(area_station_ids)):
                links[link["resort"]].add(link["ski_area"])
    area_indexes = {"slug": defaultdict(list), "name": defaultdict(list)}
    for area in areas.values():
        if area.get("slug"):
            area_indexes["slug"][area["slug"].strip()].append(area["id"])
        if normalized_name(area.get("name")):
            area_indexes["name"][normalized_name(area["name"])].append(area["id"])
    for item in valid:
        result, decision = item["result"], item["decision"]
        matched = decision["station"]
        identity_review = bool(decision["review_items"]) or any(
            review["code"] == "matching_columns_unavailable" for review in result["review_items"])
        for field in sorted(COLLECTION_FIELDS & set(item["data"])):
            if item["data"][field] is not None:
                result["review_items"].append({"code": "collection_comparison_not_supported", "field": field})
        if not identity_review:
            existing = details[matched["id"]] if matched else {}
            for field in sorted(set(item["normalized"]) | set(item["clear_fields"])):
                if not schema.has(Resort, field):
                    result["review_items"].append({"code": "field_unavailable_in_database", "field": field})
                    continue
                _diff_field(item, field, existing)
        if item["data"].get("ski_areas") is not None:
            if not areas_available:
                result["review_items"].append({"code": "ski_area_catalog_unavailable", "field": "ski_areas"})
            else:
                resolved, reviews = _resolve_areas(item["data"]["ski_areas"], areas, area_indexes)
                result["review_items"].extend(reviews)
                if not reviews and not identity_review:
                    current = links[matched["id"]] if matched else set()
                    if any(identity not in areas for identity in current):
                        result["review_items"].append({"code": "existing_ski_area_relation_unresolved", "field": "ski_areas"})
                    elif current != resolved:
                        result["changes"].append({"field": "ski_areas", "existing": sorted(current), "candidate": sorted(resolved),
                                                  "normalized_existing": sorted(current), "normalized_candidate": sorted(resolved),
                                                  "change": "modified" if matched else "added",
                                                  "relations": {"added": sorted(resolved - current), "removed": sorted(current - resolved),
                                                                "unchanged": sorted(current & resolved)}})
        if result["review_items"]:
            result["status"] = "review_required"
        elif matched is None:
            result["status"] = "new"
        else:
            result["status"] = "changes_detected" if result["changes"] else "unchanged"
    return schema.findings, catalog_findings


def _content(value):
    return None if value is None else {"length": len(value), "md5": hashlib.md5(value.encode("utf-8")).hexdigest()}


def _diff_field(item, field, existing):
    result = item["result"]
    clear = field in item["clear_fields"]
    received = None if clear else item["data"][field]
    if field in CONTENT_FIELDS:
        old = ({"length": existing[field + "__length"], "md5": existing[field + "__md5"]}
               if existing.get(field + "__length") is not None else None)
        candidate = _content(received)
        normalized_old, normalized_new = old, candidate
    else:
        old, candidate = existing.get(field), received
        try:
            # Empty nullable legacy values are absent information, not URL/date errors.
            normalized_old = None if old is None or (old == "" and field in {"website_url", "cover_image_url", "logo_url",
                "pistes_small_map_url", "pistes_large_map_url", "snowpark_map_url", "season_open_date", "season_close_date"}) else normalize(field, old)
        except (ValueError, TypeError, UnicodeError, OverflowError):
            result["review_items"].append({"code": "existing_value_not_comparable", "field": field, "existing": _json_value(old)})
            return
        normalized_new = None if clear else item["normalized"][field]
    if normalized_old == normalized_new:
        return
    result["changes"].append({"field": field, "existing": _json_value(old), "candidate": _json_value(candidate),
                              "normalized_existing": _json_value(json_normalized(normalized_old)),
                              "normalized_candidate": _json_value(json_normalized(normalized_new)),
                              "change": "cleared" if clear else ("added" if normalized_old is None else "modified")})


def _resolve_areas(references, areas, indexes):
    resolved, reviews = set(), []
    for position, reference in enumerate(references):
        identity = int(number(reference["id"])) if "id" in reference else None
        slug_ids = indexes["slug"].get(reference.get("slug", "").strip(), [])
        if identity in areas and (not slug_ids or slug_ids == [identity]):
            # A conflicting slug that resolves nowhere still needs human review.
            if reference.get("slug") and areas[identity].get("slug") != reference["slug"].strip():
                options = [identity]
            else:
                resolved.add(identity)
                continue
        elif identity is None and len(slug_ids) == 1:
            resolved.add(slug_ids[0])
            continue
        else:
            options = sorted(set(slug_ids + ([identity] if identity in areas else []) +
                                 indexes["name"].get(normalized_name(reference.get("name")), [])))
        reviews.append({"code": "ski_area_reference_unresolved", "field": f"ski_areas.{position}",
                        "reference": reference, "candidates": [station_reference(areas[key]) for key in options]})
    return resolved, reviews
