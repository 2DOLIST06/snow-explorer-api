"""Bulk target resolution and precondition/constraint checks before any write."""
from collections import defaultdict

from peewee import PostgresqlDatabase

from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from .apply_contract import (ALLOWED_OPERATIONS, CLEAR_WRITE_FIELDS, CREATE_WRITE_FIELDS,
                             SCALAR_WRITE_FIELDS, ApplyError, execution_order, storage_value)
from .compare import _select
from .creation import creation_constraints
from .matching import IDENTITY_FIELDS, MatchingIndex
from .operations import canonical_json, fingerprint
from .scan import _json_value


def load_stations(schema, ids, fields):
    fields = sorted(set(fields) | {"id", "slug"})
    query = _select(schema, Resort, fields)
    if ids is not None:
        query = query.where(Resort.id.in_(sorted(ids)))
    return {row["id"]: row for row in query.dicts()}


def relation_state(station_ids):
    if not station_ids:
        return set()
    return {(row["resort"], row["ski_area"]) for row in
            SkiAreaResort.select(SkiAreaResort.resort, SkiAreaResort.ski_area)
            .where(SkiAreaResort.resort.in_(sorted(station_ids))).dicts()}


def validate_plan(operations, prepared, database, context):
    ordered = execution_order(operations)
    if not operations:
        return {"ordered": [], "values": {}, "stations": {}, "relations": set(), "schema": None}
    schema = context["schema"]
    schema.require(Resort, "id", "slug", "updated_at")
    constraints = context.get("creation_constraints") or creation_constraints(database)
    if constraints["unknown_checks"]:
        raise ApplyError("constraint_requires_review", "Physical CHECK constraints are not supported by APPLY V1")
    timestamp_type = constraints["columns"]["updated_at"]["type"].lower().split("(", 1)[0].strip()
    if timestamp_type not in {"timestamptz", "timestamp", "timestamp with time zone", "timestamp without time zone", "datetime"}:
        raise ApplyError("physical_type_incompatible", "updated_at requires a compatible physical timestamp")
    originals = {item["result"]["client_ref"]: item for item in prepared}
    values, fields, station_ids, relation_ids = {}, set(), set(), set()
    creations = {op["operation_id"]: op for op in operations if op["operation"] == "create_station"}
    for op in ordered:
        kind, field = op["operation"], op["field"]
        if kind not in ALLOWED_OPERATIONS or op["decision"] != "approved" or op["target_type"] != "station":
            raise ApplyError("unsupported_operation", "Only approved Station Ops V1 operations can be executed")
        if kind in {"clear", "remove_ski_area_relation"} and not op["sensitive"]:
            raise ApplyError("sensitive_approval_required", "Destructive operation must be explicitly sensitive")
        norm = originals[op["client_ref"]]["normalized"]
        if kind in {"set", "replace", "clear"}:
            if field not in SCALAR_WRITE_FIELDS:
                raise ApplyError("field_not_writable", "Field is outside the explicit write whitelist", field=field)
            if kind == "clear" and (field not in CLEAR_WRITE_FIELDS or
                                    field not in originals[op["client_ref"]]["clear_fields"]):
                raise ApplyError("clear_not_allowed", "clear_fields must explicitly authorize this nullable field", field=field)
            proposed = None if kind == "clear" else norm[field]
            if field == "name" and proposed is not None:
                proposed = originals[op["client_ref"]]["data"][field].strip()
            values[op["operation_id"]] = {field: _stored(field, proposed, constraints, database)}
            fields.add(field)
            station_ids.add(op["target_id"])
        elif kind == "create_station":
            if set(op["candidate"]) - CREATE_WRITE_FIELDS:
                raise ApplyError("field_not_writable", "Creation includes a field outside the whitelist")
            if fingerprint(constraints) != op["preconditions"]["creation_constraints_fingerprint"]:
                raise ApplyError("stale_precondition", "Physical creation constraints changed; repeat REVIEW", 409)
            value = {field: _stored(field, value, constraints, database) for field, value in op["candidate"].items()}
            for field, policy in op.get("creation_policy", {}).items():
                if policy["policy"] == "existing_admin_create_default":
                    value[field] = _stored(field, policy["value"], constraints, database)
                elif (field, policy["policy"]) not in {("id", "server_uuid_at_apply"), ("updated_at", "server_utcnow_at_apply")}:
                    raise ApplyError("unsupported_creation_policy", "Unsupported creation policy")
                elif field == "id":
                    _stored("id", "00000000-0000-4000-8000-000000000000", constraints, database)
            values[op["operation_id"]] = value
        else:
            schema.require(SkiArea, "id")
            schema.require(SkiAreaResort, "resort", "ski_area")
            relation_ids.add(op["related_id"])
            if op.get("depends_on"):
                if len(op["depends_on"]) != 1 or op["depends_on"][0] not in creations:
                    raise ApplyError("invalid_dependencies", "Relation needs an approved creation", 409)
            else:
                station_ids.add(op["target_id"])
    if None in station_ids:
        raise ApplyError("target_missing", "Operation requires a real station ID", 409)
    stations = load_stations(schema, station_ids, fields) if station_ids else {}
    if set(stations) != station_ids:
        raise ApplyError("stale_precondition", "A target station no longer exists", 409)
    links = relation_state(station_ids) if relation_ids else set()
    if relation_ids:
        actual_areas = {row["id"] for row in SkiArea.select(SkiArea.id).where(SkiArea.id.in_(sorted(relation_ids))).dicts()}
        if actual_areas != relation_ids:
            raise ApplyError("stale_precondition", "A target ski area no longer exists", 409)
        if any(op["operation"] == "add_ski_area_relation" for op in ordered):
            relation_constraints = creation_constraints(database, SkiAreaResort)
            known = {"id", "resort_id", "ski_area_id", "created_at"}
            missing = [name for name, column in relation_constraints["columns"].items()
                       if column["required"] and column["default"] is None and name not in known]
            if missing or relation_constraints["unknown_checks"]:
                raise ApplyError("relation_constraint_requires_review", "Unsupported physical membership constraints")
    for op in ordered:
        kind, pre = op["operation"], op["preconditions"]
        if kind in {"set", "replace", "clear"}:
            stored = stations[op["target_id"]]
            field = op["field"]
            if pre["comparison"] == "length_md5":
                actual = ({"length": stored[field + "__length"], "md5": stored[field + "__md5"]}
                          if stored.get(field + "__length") is not None else None)
            else:
                actual = _json_value(stored.get(field))
            if canonical_json(actual) != canonical_json(pre["expected_existing"]):
                raise ApplyError("stale_precondition", "The stored value changed; repeat REVIEW", 409,
                                 operation_id=op["operation_id"])
        elif kind.endswith("ski_area_relation"):
            exists = False if op.get("depends_on") else (op["target_id"], op["related_id"]) in links
            if exists != pre["expected_relation_exists"]:
                raise ApplyError("stale_precondition", "The relation changed; repeat REVIEW", 409,
                                 operation_id=op["operation_id"])
    _validate_effect_conflicts(ordered)
    if creations:
        # Re-read after locks and check against the entire final identity state,
        # including scalar geographic/name edits and other approved creations.
        identities = load_stations(schema, None, IDENTITY_FIELDS)
        for op in ordered:
            if op["operation"] in {"set", "replace", "clear"} and op["field"] in IDENTITY_FIELDS:
                identities[op["target_id"]].update(values[op["operation_id"]])
        existing_index = MatchingIndex(identities.values())
        proposed_rows = []
        for op in ordered:
            if op["operation"] != "create_station":
                continue
            proposed = values[op["operation_id"]]
            normalized = originals[op["client_ref"]]["normalized"]
            matches = [existing_index.match(normalized), MatchingIndex(proposed_rows).match(normalized)]
            if any(match["station"] or match["review_items"] for match in matches):
                raise ApplyError("station_already_exists", "Creation now matches an existing or proposed station", 409)
            identity = proposed.get("id") or "pending:" + op["operation_id"]
            if identity in identities:
                raise ApplyError("station_already_exists", "Creation ID already exists", 409)
            identities[identity] = {**proposed, "id": identity}
            proposed_rows.append(identities[identity])
    return {"ordered": ordered, "values": values, "stations": stations, "relations": links, "schema": schema}


def _stored(field, value, constraints, database):
    column = constraints["columns"].get(field)
    if column is None:
        raise ApplyError("field_unavailable", "Physical column is unavailable", field=field)
    if field == "page_layout_version" and value not in {"legacy", "v2"}:
        raise ApplyError("invalid_layout", "Unsupported layout")
    return storage_value(field, value, {**column, "postgresql": isinstance(database, PostgresqlDatabase)})


def _validate_effect_conflicts(ordered):
    effects = defaultdict(set)
    for op in ordered:
        if op["operation"].endswith("ski_area_relation"):
            target = (("new", op["target_client_ref"]) if op.get("target_client_ref") else ("existing", op["target_id"]))
            effects[(target, op["related_id"])].add(op["operation"])
    if any(len(kinds) > 1 for kinds in effects.values()):
        raise ApplyError("conflicting_operations", "Contradictory relation actions", 409)
