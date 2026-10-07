"""Versioned candidate contract and pure validation; no database access."""
from datetime import datetime
import re

from .duplicates import normalized_name
from .normalization import NUMERIC_FIELDS, normalize, number, normalized_url
from .validation import finding, validate_station

COMPARE_VERSION = "1.0"
MAX_CANDIDATES = 1000
MAX_BODY_BYTES = 16 * 1024 * 1024
SCALAR_FIELDS = frozenset({
    "id", "name", "slug", "is_active", "region_id", "region_name", "country_code", "department",
    "latitude", "longitude", "altitude_base_m", "altitude_top_m", "altitude_min_m", "altitude_max_m",
    "lifts_count", "pistes_count", "ski_area_km", "website_url", "cover_image_url", "logo_url",
    "amenities", "description_md", "description_html", "meta_title", "meta_description",
    "page_layout_version", "v2_overview_html", "v2_weather_snow_html", "v2_ski_pass_html",
    "v2_piste_map_html", "v2_webcam_html", "pistes_small_map_url", "pistes_large_map_url",
    "pistes_caption", "snowpark_map_url", "snowpark_caption", "season_open_date", "season_close_date",
})
COLLECTION_FIELDS = frozenset({"pistes", "lifts", "maps", "webcams", "widgets", "ski_pass_seasons",
                              "ski_pass_periods", "ski_pass_products", "ski_pass_prices"})
CANDIDATE_FIELDS = SCALAR_FIELDS | COLLECTION_FIELDS | {"ski_areas"}
CLEARABLE_FIELDS = SCALAR_FIELDS - {"id", "name", "slug", "is_active", "page_layout_version"}


class ComparePayloadError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def parse_batch(payload):
    if not isinstance(payload, dict) or set(payload) != {"candidates"} or not isinstance(payload["candidates"], list):
        raise ComparePayloadError("Expected an object containing only a candidates array")
    if not payload["candidates"]:
        raise ComparePayloadError("candidates must contain at least one candidate")
    if len(payload["candidates"]) > MAX_CANDIDATES:
        raise ComparePayloadError(f"Batch exceeds {MAX_CANDIDATES} candidates", 413)
    return [prepare_candidate(item) for item in payload["candidates"]]


def _sources_valid(sources):
    if not isinstance(sources, dict):
        return False
    for field, rows in sources.items():
        if (not isinstance(field, str) or len(field) > 256 or
                not re.fullmatch(r"[a-z_][a-z_0-9]*(?:\.[a-z_0-9]+)*", field) or
                field.split(".", 1)[0] not in CANDIDATE_FIELDS or not isinstance(rows, list)):
            return False
        if "." in field and field.split(".", 1)[0] not in COLLECTION_FIELDS | {"ski_areas"}:
            return False
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"url", "source_type", "observed_at"} or "url" not in row:
                return False
            try:
                normalized_url(row["url"])
                if "source_type" in row and (not isinstance(row["source_type"], str) or
                                              not row["source_type"].strip() or len(row["source_type"]) > 64):
                    return False
                if "observed_at" in row:
                    stamp = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        return False
            except (ValueError, TypeError, AttributeError, UnicodeError):
                return False
    return True


def prepare_candidate(item):
    result = {"client_ref": item.get("client_ref") if isinstance(item, dict) else None,
              "status": "invalid", "matched_station": None, "match_reasons": [], "changes": [],
              "validation": {"errors": [], "warnings": [], "info": []}, "review_items": [],
              "field_sources": {}}
    prepared = {"result": result, "data": {}, "normalized": {}, "clear_fields": [], "valid": False}
    errors = result["validation"]["errors"]
    def error(code, field):
        errors.append(finding(code, "error", field))
    if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
        error("candidate_payload_invalid", "data")
        return prepared
    if set(item) - {"client_ref", "data", "clear_fields", "field_sources"}:
        error("unknown_candidate_keys", "candidate")
    if not isinstance(item.get("client_ref"), str) or not item["client_ref"].strip() or len(item["client_ref"]) > 256:
        error("client_ref_invalid", "client_ref")
    data = item["data"]
    prepared["data"] = data
    for field in sorted(set(data) - CANDIDATE_FIELDS):
        error("unsupported_candidate_field", field)
    clear = item.get("clear_fields", [])
    if not isinstance(clear, list) or any(not isinstance(field, str) or field not in CLEARABLE_FIELDS for field in clear):
        error("clear_fields_invalid", "clear_fields")
    else:
        prepared["clear_fields"] = sorted(set(clear))
        for field in prepared["clear_fields"]:
            if data.get(field) is not None:
                error("clear_field_conflicts_with_value", field)
    sources = item.get("field_sources", {})
    if not _sources_valid(sources):
        error("field_sources_invalid", "field_sources")
    else:
        result["field_sources"] = sources
    for field, value in data.items():
        if field not in CANDIDATE_FIELDS:
            continue
        if value is None:
            if field not in prepared["clear_fields"]:
                result["validation"]["info"].append(finding("null_ignored", "info", field))
            continue
        if field in SCALAR_FIELDS:
            if field in CLEARABLE_FIELDS and isinstance(value, str) and not value.strip():
                result["validation"]["info"].append(finding("blank_ignored", "info", field))
                continue
            try:
                normalized = normalize(field, value)
                if field in {"id", "slug", "name"} and not normalized.strip():
                    raise ValueError("Identity cannot be blank")
                if field == "page_layout_version" and normalized not in {"legacy", "v2"}:
                    raise ValueError("Unknown layout version")
                prepared["normalized"][field] = normalized
            except (ValueError, TypeError, UnicodeError, OverflowError):
                error("field_value_invalid", field)
        elif field == "widgets":
            if not isinstance(value, dict):
                error("collection_type_invalid", field)
        elif not isinstance(value, list):
            error("collection_type_invalid", field)
        elif field in COLLECTION_FIELDS and any(not isinstance(row, dict) for row in value):
            error("collection_item_type_invalid", field)
        elif field == "ski_areas":
            for area in value:
                if not isinstance(area, dict) or not area or set(area) - {"id", "slug", "name"}:
                    error("ski_area_reference_invalid", field)
                    continue
                try:
                    if "id" in area:
                        identity = number(area["id"])
                        if identity <= 0 or identity != identity.to_integral_value() or identity > 9223372036854775807:
                            raise ValueError("Expected positive bigint")
                    for key in ("slug", "name"):
                        if key in area and (not isinstance(area[key], str) or not area[key].strip()):
                            raise ValueError("Expected nonempty text")
                except (ValueError, TypeError):
                    error("ski_area_reference_invalid", field)
    norm = prepared["normalized"]
    if not any(norm.get(field) for field in ("id", "slug")) and not normalized_name(norm.get("name")):
        error("identity_missing", "data")
    # Reuse objective checks only for supplied values, never validate an invented
    # complete station or complain about absent optional fields.
    numeric = {field: float(value) for field, value in norm.items() if field in {"latitude", "longitude"}}
    numeric.update({field: value for field, value in norm.items() if field in NUMERIC_FIELDS - {"latitude", "longitude"}})
    numeric.update({field: norm[field] for field in ("season_open_date", "season_close_date") if field in norm})
    relevant = {"coordinates_invalid", "altitude_inconsistent", "negative_count", "season_dates_inconsistent"}
    for check in validate_station(numeric):
        if check["code"] in relevant:
            result["validation"]["warnings"].append({**check, "severity": "warning"})
    if ("latitude" in norm) != ("longitude" in norm):
        result["validation"]["warnings"].append(finding("partial_coordinates", "warning", "coordinates"))
    prepared["valid"] = not errors
    return prepared
