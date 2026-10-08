"""Explicit APPLY V1 write boundary and physical-type validation."""
from decimal import Decimal, InvalidOperation
import re
import struct

from .candidates import CLEARABLE_FIELDS
from .normalization import DATE_FIELDS, NUMERIC_FIELDS, number

APPLY_VERSION = "1.0"
# Bound transaction duration and locks: at most 1000 field/relation actions,
# even when 1000 candidates each propose many fields. Reads remain batched.
MAX_APPLY_OPERATIONS = 1000
SCALAR_WRITE_FIELDS = frozenset({
    "name", "is_active", "region_id", "region_name", "country_code", "department",
    "latitude", "longitude", "altitude_base_m", "altitude_top_m", "altitude_min_m", "altitude_max_m",
    "lifts_count", "pistes_count", "ski_area_km", "website_url", "cover_image_url", "logo_url",
    "amenities", "description_md", "description_html", "meta_title", "meta_description",
    "page_layout_version", "v2_overview_html", "v2_weather_snow_html", "v2_ski_pass_html",
    "v2_piste_map_html", "v2_webcam_html", "pistes_small_map_url", "pistes_large_map_url",
    "pistes_caption", "snowpark_map_url", "snowpark_caption", "season_open_date", "season_close_date",
})
CLEAR_WRITE_FIELDS = SCALAR_WRITE_FIELDS & CLEARABLE_FIELDS
CREATE_WRITE_FIELDS = SCALAR_WRITE_FIELDS | {"id", "slug"}
ALLOWED_OPERATIONS = frozenset({"set", "replace", "clear", "create_station",
                                "add_ski_area_relation", "remove_ski_area_relation"})


class ApplyError(ValueError):
    def __init__(self, code, message, status=400, **details):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details
        self.execution_id = None


def storage_value(field, normalized, column):
    """Already normalized pipeline value -> driver value, without model coercion.

    The physical column wins over Peewee (notably legacy REAL ski_area_km).
    Reject loss of precision, unknown types, overflow, NUL and truncation.
    """
    if normalized is None:
        if column["required"]:
            raise ApplyError("field_not_nullable", "The physical field is not nullable", field=field)
        return None
    physical = column["type"].lower().strip()
    base = physical.split("(", 1)[0].strip()
    if field in NUMERIC_FIELDS:
        value = number(normalized)
        if base in {"integer", "int", "smallint", "bigint"}:
            bits = {"smallint": 16, "bigint": 64}.get(base, 32)
            if value != value.to_integral_value() or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                raise ApplyError("physical_type_incompatible", "Integer would overflow or lose precision", field=field)
            return int(value)
        if base in {"real", "float", "double precision", "numeric", "decimal"}:
            if base in {"numeric", "decimal"}:
                bound = re.search(r"\((\d+)\s*,\s*(\d+)\)", physical)
                if bound:
                    precision, scale = map(int, bound.groups())
                    try:
                        invalid = value != value.quantize(Decimal(1).scaleb(-scale)) or abs(value) >= Decimal(10) ** (precision - scale)
                    except InvalidOperation:
                        invalid = True
                    if invalid:
                        raise ApplyError("physical_type_incompatible", "Decimal would round or overflow", field=field)
                return value
            try:
                floating = float(value)
                # SQLite REAL is a double. PostgreSQL real is IEEE float32.
                if base == "real" and column.get("postgresql"):
                    floating = struct.unpack("!f", struct.pack("!f", floating))[0]
                if Decimal(str(floating)) != value:
                    raise ValueError("Loss of precision")
                return floating
            except (ValueError, OverflowError):
                raise ApplyError("physical_type_incompatible", "Float would lose reviewed precision", field=field)
        raise ApplyError("physical_type_incompatible", "Unsupported physical numeric type", field=field)
    if field == "is_active":
        if base not in {"boolean", "bool", "integer", "int"} or type(normalized) is not bool:
            raise ApplyError("physical_type_incompatible", "Unsupported physical boolean type", field=field)
        return normalized
    if field in DATE_FIELDS:
        if base != "date":
            raise ApplyError("physical_type_incompatible", "Unsupported physical date type", field=field)
        return normalized  # ISO date already validated by COMPARE.
    if base not in {"text", "varchar", "character varying", "char", "character"} or not isinstance(normalized, str):
        raise ApplyError("physical_type_incompatible", "Unsupported physical text type", field=field)
    bound = re.search(r"\((\d+)\)", physical)
    if "\x00" in normalized or bound and len(normalized) > int(bound.group(1)):
        raise ApplyError("physical_type_incompatible", "Text contains NUL or exceeds its physical length", field=field)
    return normalized


def execution_order(operations):
    """Stable topological order; dependencies must be approved creations."""
    by_id = {op["operation_id"]: op for op in operations}
    if len(by_id) != len(operations):
        raise ApplyError("invalid_dependencies", "Duplicate operation IDs", 409)
    remaining, ordered = dict(by_id), []
    while remaining:
        ready = []
        for identity, op in remaining.items():
            deps = op.get("depends_on", [])
            if any(dep not in by_id for dep in deps):
                raise ApplyError("invalid_dependencies", "Unknown or unapproved dependency", 409)
            if all(dep not in remaining for dep in deps):
                ready.append(identity)
        if not ready:
            raise ApplyError("invalid_dependencies", "Dependency cycle", 409)
        for identity in sorted(ready):
            ordered.append(remaining.pop(identity))
    for op in ordered:
        if any(by_id[dep]["operation"] != "create_station" or
               by_id[dep]["client_ref"] != op["client_ref"] for dep in op.get("depends_on", [])):
            raise ApplyError("invalid_dependencies", "Dependency targets a different station", 409)
    return ordered
