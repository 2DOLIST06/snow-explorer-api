"""Dynamic stored catalogue inventory. No public defaults or business writes."""
from collections import defaultdict
import json
from peewee import Column, ForeignKeyField, PostgresqlDatabase
from app.models.resort import Resort
from app.models.region import Region
from app.models.ski_area import SkiArea, SkiAreaResort
from app.models.station_widgets import StationWidgets
from app.services.public_resort import _unwrap_widgets
from .scan import SCAN_MODELS, read_only_scan
from .schema import PhysicalSchema
from .apply_contract import SCALAR_WRITE_FIELDS


def filled(value):
    """Recursive business presence; zero and false are valid scalar values."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(filled(v) for v in value.values())
    if isinstance(value, list):
        return any(filled(v) for v in value)
    return True


def value_type(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'number'
    return 'string'


def sql_type(raw):
    raw = raw.lower()
    if '[]' in raw:
        return 'array'
    if 'bool' in raw:
        return 'boolean'
    if 'timestamp' in raw or 'datetime' in raw:
        return 'datetime'
    if raw == 'date':
        return 'date'
    if 'int' in raw:
        return 'integer'
    if any(t in raw for t in ('numeric', 'decimal', 'real', 'double', 'float')):
        return 'number'
    if 'json' in raw:
        return 'object'
    return 'string'


def catalog_schema(params=None):
    params = dict(params or {})
    filters = {'entity': 'station', 'min_fill_rate': 0, 'max_fill_rate': 1,
               'include_nested': True, **params}
    if filters['min_fill_rate'] > filters['max_fill_rate']:
        raise ValueError('min_fill_rate must be <= max_fill_rate')
    database = Resort._meta.database
    with read_only_scan(database):
        return _catalog(database, filters)


def _catalog(database, filters):
    schema = PhysicalSchema(database, SCAN_MODELS)
    nullable = {}
    if isinstance(database, PostgresqlDatabase):
        # Same search_path resolution as PhysicalSchema, one metadata query.
        relations = []
        for model in SCAN_MODELS:
            parts = [model._meta.schema, model._meta.table_name] if model._meta.schema else [model._meta.table_name]
            relations.append('.'.join('"' + p.replace('"', '""') + '"' for p in parts))
        sql = ('SELECT c.relname, a.attname, NOT a.attnotnull FROM pg_catalog.pg_attribute a '
               'JOIN pg_catalog.pg_class c ON c.oid=a.attrelid '
               'WHERE a.attrelid IN (' + ','.join('pg_catalog.to_regclass(%s)' for _ in relations) + ') '
               'AND a.attnum>0 AND NOT a.attisdropped')
        nullable = {(t, c): n for t, c, n in database.execute_sql(sql, relations).fetchall()}
    else:
        for model in SCAN_MODELS:
            nullable.update({(model._meta.table_name, c.name): c.null
                             for c in database.get_columns(model._meta.table_name)})
    rows, metadata = {}, {}
    for model in SCAN_MODELS:
        table = model._meta.table_name
        metadata[model] = {}
        columns = []
        for column in sorted(schema.columns[table]):
            field = model._meta.columns.get(column)
            name = field.name if field else column
            columns.append(Column(model._meta.table, column).alias(name))
            internal = bool(field and (field.primary_key or isinstance(field, ForeignKeyField))) or name.startswith('_') or name.endswith('_at')
            metadata[model][name] = dict(type=sql_type(schema.types[table][column]),
                nullable=nullable.get((table, column), True), collection=False,
                internal=internal, readable=True,
                writable=model is Resort and name in SCALAR_WRITE_FIELDS)
        rows[model] = list(model.select(*columns).dicts()) if columns else []
        for row in rows[model]:
            for name, value in list(row.items()):
                declared_json = name == 'config' or name.endswith('_json') or metadata[model][name]['type'] == 'object'
                container_text = isinstance(value, str) and value.lstrip().startswith(('{', '['))
                if isinstance(value, str) and (declared_json or container_text):
                    try:
                        row[name] = json.loads(value)
                    except (ValueError, TypeError):
                        pass  # Invalid JSON still counts as stored text, never manufacture data.

    # Reverse foreign-key edges are discovered from supported model metadata.
    edges = defaultdict(list)
    indexes = {}
    for child in SCAN_MODELS:
        for field in child._meta.fields.values():
            if isinstance(field, ForeignKeyField) and field.rel_model in rows:
                edges[field.rel_model].append((field.backref, child, field.name))
                index = defaultdict(list)
                for row in rows[child]:
                    index[row.get(field.name)].append(row)
                indexes[child, field.name] = index
    identities = {model: {row.get(model._meta.primary_key.name): row for row in items}
                  for model, items in rows.items()}
    widget_index = {row['station_slug']: row for row in rows[StationWidgets]}
    specs = {}

    def register(path, meta):
        specs.setdefault(path, dict(meta))

    def expand(model, row, prefix='', visited=()):
        result = dict(row)
        for name, meta in metadata[model].items():
            register(prefix + name, meta)
        visited = (*visited, model)
        for name, child, key in sorted(edges[model], key=lambda e: e[0]):
            if child in visited:
                continue
            path = prefix + name
            register(path, dict(type='array', nullable=False, collection=True,
                                internal=False, readable=True, writable=False))
            children = indexes[child, key].get(row.get(model._meta.primary_key.name), [])
            # Discover even entirely empty collections from their model schema.
            expand(child, {}, path + '.', visited)
            result[name] = [expand(child, item, path + '.', visited) for item in children]
        for field in model._meta.fields.values():
            if not isinstance(field, ForeignKeyField) or field.rel_model not in rows or field.rel_model in visited:
                continue
            target = field.rel_model
            name = field.name + '_record'
            path = prefix + name
            register(path, dict(type='object', nullable=True, collection=False,
                                internal=False, readable=True, writable=False))
            expand(target, {}, path + '.', visited)
            linked = identities[target].get(row.get(field.name))
            result[name] = expand(target, linked, path + '.', visited) if linked else None
        if model is Resort:
            register(prefix + 'widgets', dict(type='object', nullable=True, collection=False,
                                             internal=False, readable=True, writable=False))
            stored_widget = widget_index.get(row.get('slug'))
            for name, target, linked in (('station_widgets', StationWidgets, stored_widget),
                                         ('region', Region, identities[Region].get(row.get('region_id')))):
                register(prefix + name, dict(type='object', nullable=True, collection=False,
                                             internal=False, readable=True, writable=False))
                expand(target, {}, prefix + name + '.', visited)
                result[name] = expand(target, linked, prefix + name + '.', visited) if linked else None
            config = (stored_widget or {}).get('config')
            result['widgets'] = _unwrap_widgets(config) if isinstance(config, dict) else config
        return result

    root = Resort if filters['entity'] == 'station' else SkiArea
    documents = [expand(root, row) for row in rows[root]]
    expand(root, {})

    observed_types = defaultdict(set)
    inferred_paths = set()

    def flatten(value, path='', output=None):
        output = output if output is not None else {}
        if path:
            output.setdefault(path, []).append(value)
            kind = value_type(value)
            if kind:
                observed_types[path].add(kind)
            if path not in specs:
                inferred_paths.add(path)
                register(path, dict(type=kind or 'unknown', nullable=True,
                    collection=isinstance(value, list), internal=any(p.startswith('_') for p in path.split('.')),
                    readable=True, writable=False))
            if path in inferred_paths or kind in ('object', 'array'):
                inferred_paths.add(path)
                specs[path]['type'] = '|'.join(sorted(observed_types[path])) or 'unknown'
            if isinstance(value, list):
                specs[path]['collection'] = True
        if isinstance(value, dict):
            for key, item in value.items():
                flatten(item, path + '.' + key if path else key, output)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for key, nested in item.items():
                        flatten(nested, path + '.' + key, output)
        return output

    flattened = [flatten(doc) for doc in documents]
    # Field discovery uses the entire catalogue; rates use only the filtered population.
    station_ids = {row['id'] for row in rows[Resort]
                   if ('country_code' not in filters or row.get('country_code') == filters['country_code'])
                   and ('is_active' not in filters or bool(row.get('is_active')) == filters['is_active'])}
    area_ids = {row['ski_area'] for row in rows[SkiAreaResort] if row['resort'] in station_ids}
    selected = []
    for doc, flat in zip(documents, flattened):
        match = doc['id'] in station_ids if root is Resort else (
            doc['id'] in area_ids if 'country_code' in filters or 'is_active' in filters else True)
        if match:
            selected.append(flat)
    total = len(selected)
    fields = []
    for path, meta in sorted(specs.items()):
        if not filters['include_nested'] and '.' in path:
            continue
        count = sum(any(filled(v) for v in row.get(path, [])) for row in selected)
        rate = count / total if total else 0
        if filters['min_fill_rate'] <= rate <= filters['max_fill_rate']:
            fields.append(dict(path=path, **meta, filled_count=count, total_count=total, fill_rate=round(rate, 4)))
    return dict(schema_version='1.0', filters=filters, population={'total_records': total},
                entities={filters['entity']: {'fields': fields}})
