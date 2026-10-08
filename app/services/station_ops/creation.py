"""Read physical creation constraints and describe existing application defaults."""
import json
import re

from peewee import PostgresqlDatabase

from app.models.resort import Resort
from .candidates import COMPARE_VERSION
from .matching import IDENTITY_FIELDS
from .normalization import json_normalized
from .operations import base_operation, fingerprint


def _known_layout_check(check):
    text = re.sub(r'::(?:text|character varying(?:\(\d+\))?)(?:\[\])?', '', check.lower())
    text = re.sub(r'[\s"()]', '', text)
    return text in {"checkpage_layout_versionin'legacy','v2'",
                    "checkpage_layout_version=anyarray['legacy','v2']"}


def creation_constraints(database, model=Resort):
    """One PG metadata SELECT, or one SQLite PRAGMA + one schema SELECT per batch."""
    table, schema = model._meta.table_name, model._meta.schema
    if isinstance(database, PostgresqlDatabase):
        parts = [schema, table] if schema else [table]
        relation = '.'.join('"' + part.replace('"', '""') + '"' for part in parts)
        sql = (
            "SELECT a.attname, a.attnotnull, pg_catalog.pg_get_expr(d.adbin, d.adrelid), "
            "pg_catalog.format_type(a.atttypid, a.atttypmod), "
            "(SELECT COALESCE(json_agg(pg_catalog.pg_get_constraintdef(c.oid)), '[]'::json) "
            "FROM pg_catalog.pg_constraint c WHERE c.conrelid=a.attrelid AND c.contype='c') "
            "FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d "
            "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
            "WHERE a.attrelid=pg_catalog.to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped "
            "ORDER BY a.attnum"
        )
        rows = database.execute_sql(sql, (relation,)).fetchall()
        checks = rows[0][4] if rows else []
        if isinstance(checks, str):
            checks = json.loads(checks)
        columns = {name: {"required": required, "default": default, "type": data_type}
                   for name, required, default, data_type, _ in rows}
    else:
        columns = {column.name: {"required": not column.null or column.primary_key,
                                "default": column.default, "type": column.data_type}
                   for column in database.get_columns(table, schema)}
        sql = database.execute_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        definition = sql[0] if sql else ''
        without_known = re.sub(r"CHECK\s*\(\s*\"?page_layout_version\"?\s+IN\s*\(\s*'legacy'\s*,\s*'v2'\s*\)\s*\)",
                              '', definition, flags=re.I)
        checks = ['unrecognized_sqlite_check'] if re.search(r'\bCHECK\s*\(', without_known, re.I) else []
    return {"columns": columns, "unknown_checks": [check for check in checks if not _known_layout_check(check)]}


def create_operation(result, prepared, constraints):
    norm, columns = prepared['normalized'], constraints['columns']
    missing = [field for field in ('name', 'slug') if not norm.get(field)]
    reviews = [{"code": "create_required_fields_missing", "fields": missing}] if missing else []
    defaults = {
        "id": {"policy": "server_uuid_at_apply"},
        "is_active": {"policy": "existing_admin_create_default", "value": True},
        "page_layout_version": {"policy": "existing_admin_create_default", "value": "legacy"},
        "updated_at": {"policy": "server_utcnow_at_apply"},
    }
    required_missing = [name for name, column in columns.items()
                        if column['required'] and (column['default'] is None or re.fullmatch(
                            r'null(?:::[a-z_0-9.\[\],]+)?', re.sub(r'[\s()]', '', str(column['default']).lower())))
                        and name not in norm and name not in defaults]
    if required_missing:
        reviews.append({"code": "create_required_columns_missing", "fields": sorted(required_missing)})
    if constraints['unknown_checks']:
        reviews.append({"code": "create_constraint_requires_review", "constraints": constraints['unknown_checks']})
    for field, value in norm.items():
        column = columns.get(field)
        if column is None:
            reviews.append({"code": "create_column_unavailable", "field": field})
            continue
        bound = re.search(r'(?:character varying|varchar|character|char)\s*\((\d+)\)', column['type'], re.I)
        bound_value = int(bound.group(1)) if bound else None
        if bound_value and isinstance(value, str) and len(value) > bound_value:
            reviews.append({"code": "create_field_exceeds_length", "field": field, "max_length": bound_value})
    if reviews:
        return None, reviews
    candidate = json_normalized(dict(norm))
    # Preserve the intended spelling when proposing a new record, unlike name
    # normalization used only for matching an existing row.
    candidate['name'] = prepared['data']['name'].strip()
    policy = {field: default for field, default in defaults.items() if field not in norm}
    sources_by_field = {field: rows for field, rows in sorted(result['field_sources'].items()) if field in candidate}
    sources = [source for rows in sources_by_field.values() for source in rows]
    operation = base_operation(result['client_ref'], norm.get('id'), 'create_station', None, None,
        candidate, None, candidate, sources,
        {"station_must_not_exist": True, "id_must_be_absent": norm.get('id'), "slug_must_be_absent": norm['slug'],
         "no_matching_station": True, "match_input": json_normalized({field: norm[field] for field in IDENTITY_FIELDS if field in norm}),
         "compare_version": COMPARE_VERSION, "revalidate_physical_constraints": True,
         "creation_constraints_fingerprint": fingerprint(constraints),
         "required_columns": sorted(name for name, column in columns.items() if column['required'])},
        creation_policy=policy, field_sources=sources_by_field, sensitive=False,
        target_client_ref=result['client_ref'])
    return operation, []
