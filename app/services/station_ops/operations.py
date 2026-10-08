"""Deterministic operation/plan representations; no database access."""
from copy import deepcopy
import hashlib
import json

from .scan import CONTENT_FIELDS
from .normalization import number

REVIEW_VERSION = "1.0"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def identify(operation):
    identity = {"review_version": REVIEW_VERSION,
                **{key: operation.get(key) for key in (
                    "client_ref", "target_type", "target_id", "target_client_ref", "operation", "field",
                    "related_id", "normalized_candidate", "preconditions", "creation_policy", "depends_on")}}
    operation["operation_id"] = fingerprint(identity)
    return operation


def base_operation(client_ref, target_id, operation, field, existing, candidate,
                   normalized_existing, normalized_candidate, sources, preconditions, **extras):
    return identify({"client_ref": client_ref, "target_type": "station", "target_id": target_id,
                     "target_client_ref": client_ref if target_id is None else None,
                     "operation": operation, "field": field, "existing": deepcopy(existing),
                     "candidate": deepcopy(candidate), "normalized_existing": deepcopy(normalized_existing),
                     "normalized_candidate": deepcopy(normalized_candidate), "decision": "pending",
                     "requires_explicit_approval": True, "sensitive": operation in {"clear", "remove_ski_area_relation"},
                     "sources": deepcopy(sources), "source_count": len(sources),
                     "preconditions": deepcopy(preconditions), **extras})


def scalar_operation(result, change, original):
    field = change["field"]
    old = change["existing"]
    kind = "clear" if change["change"] == "cleared" else ("set" if change["change"] == "added" else "replace")
    comparison = "length_md5" if field in CONTENT_FIELDS else (
        "length_sha256" if isinstance(old, dict) and old.get("inline_media") else "serialized_stored_value")
    extras = {}
    if kind != "clear" and (field in CONTENT_FIELDS or
                            isinstance(change["candidate"], dict) and change["candidate"].get("inline_media")):
        extras["candidate_input"] = original["data"][field]
    return base_operation(result["client_ref"], result["matched_station"]["id"], kind, field,
                          old, change["candidate"], change["normalized_existing"], change["normalized_candidate"],
                          result["field_sources"].get(field, []),
                          {"station_id": result["matched_station"]["id"], "station_exists": True,
                           "field": field, "comparison": comparison, "expected_existing": old}, **extras)


def relation_operations(result, change, original, areas, create_id=None):
    operations = []
    new_station = result["matched_station"] is None
    station_id = result["matched_station"]["id"] if not new_station else original["normalized"].get("id")
    for direction, kind in (("added", "add_ski_area_relation"), ("removed", "remove_ski_area_relation")):
        for area_id in change["relations"][direction]:
            sources = list(result["field_sources"].get("ski_areas", []))
            if direction == "added":
                for position, reference in enumerate(original["data"].get("ski_areas", [])):
                    # References were already validated/resolved by COMPARE.
                    same = (('id' in reference and int(number(reference['id'])) == area_id) or
                            bool(reference.get('slug')) and reference['slug'].strip() == areas[area_id].get('slug'))
                    if same:
                        prefix = f"ski_areas.{position}"
                        sources.extend(source for key, rows in sorted(result["field_sources"].items())
                                       if key == prefix or key.startswith(prefix + ".") for source in rows)
            operations.append(base_operation(result["client_ref"], station_id, kind, "ski_areas",
                direction == "removed", direction == "added", direction == "removed", direction == "added", sources,
                {"station_id": station_id, "station_exists": not new_station,
                 "station_client_ref": result["client_ref"] if new_station else None,
                 "ski_area_id": area_id, "ski_area_exists": True, "expected_relation_exists": direction == "removed"},
                related_id=area_id, depends_on=[create_id] if create_id else [],
                target_client_ref=result["client_ref"] if new_station else None))
    return operations


def apply_plan(results):
    approved = sorted((deepcopy(op) for row in results if row["status"] not in {"blocked", "invalid"}
                       for op in row["operations"] if op["decision"] == "approved"), key=lambda op: op["operation_id"])
    return {"operations": approved, "plan_fingerprint": fingerprint({"review_version": REVIEW_VERSION, "operations": approved})}
