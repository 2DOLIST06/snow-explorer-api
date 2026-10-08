"""Bounded, parameterized writes and bulk post-write verification."""
from collections import defaultdict
import uuid

from peewee import PostgresqlDatabase

from app.datetime_utils import utcnow
from app.models.resort import Resort
from app.models.ski_area import SkiAreaResort
from .apply_contract import ApplyError
from .apply_validation import load_stations, relation_state
from .compare import _content
from .normalization import json_normalized, normalize
from .operations import canonical_json
from .scan import CONTENT_FIELDS


def quoted_table(model):
    parts = [model._meta.schema, model._meta.table_name] if model._meta.schema else [model._meta.table_name]
    return '.'.join('"' + part.replace('"', '""') + '"' for part in parts)


def _column(model, field):
    return '"' + model._meta.fields[field].column_name.replace('"', '""') + '"'


def _chunks(rows, size):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def insert_rows(database, model, rows):
    """Raw driver binding avoids IntegerField truncating legacy REAL values.

    All field names originate in internal whitelists/model metadata. SQL values
    remain parameters; no defaults from absent model columns are injected.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(sorted(row))].append(row)
    marker = '%s' if isinstance(database, PostgresqlDatabase) else '?'
    for fields, group in sorted(groups.items()):
        for batch in _chunks(group, max(1, 900 // len(fields))):
            sql = ('INSERT INTO ' + quoted_table(model) + ' (' + ','.join(_column(model, f) for f in fields) +
                   ') VALUES ' + ','.join('(' + ','.join([marker] * len(fields)) + ')' for _ in batch))
            database.execute_sql(sql, [row[field] for row in batch for field in fields])


def update_stations(database, changes, now):
    marker = '%s' if isinstance(database, PostgresqlDatabase) else '?'
    groups = defaultdict(list)
    for identity, values in sorted(changes.items()):
        groups[tuple(sorted(values))].append((identity, values))
    for fields, rows in sorted(groups.items()):
        # Per field: station ID and value; plus IDs in WHERE and timestamp.
        size = max(1, 899 // (2 * len(fields) + 1))
        for batch in _chunks(rows, size):
            assignments, params = [], []
            for field in fields:
                assignments.append(_column(Resort, field) + ' = CASE "id" ' +
                                   ' '.join('WHEN ' + marker + ' THEN ' + marker for _ in batch) +
                                   ' ELSE ' + _column(Resort, field) + ' END')
                params.extend(value for identity, values in batch for value in (identity, values[field]))
            assignments.append('"updated_at" = ' + marker)
            params.append(now)
            params.extend(identity for identity, _ in batch)
            sql = ('UPDATE ' + quoted_table(Resort) + ' SET ' + ','.join(assignments) +
                   ' WHERE "id" IN (' + ','.join([marker] * len(batch)) + ')')
            if database.execute_sql(sql, params).rowcount != len(batch):
                raise ApplyError('stale_precondition', 'Station update affected an unexpected number of rows', 409)


def execute_plan(database, validated):
    now, targets, new_rows, changes = utcnow(), {}, [], defaultdict(dict)
    add, remove = set(), set()
    for op in validated['ordered']:
        identity, kind = op['operation_id'], op['operation']
        if kind == 'create_station':
            values = dict(validated['values'][identity])
            values.setdefault('id', str(uuid.uuid4()))
            values['updated_at'] = now
            new_rows.append(values)
            targets[op['client_ref']] = values['id']
        elif kind in {'set', 'replace', 'clear'}:
            changes[op['target_id']].update(validated['values'][identity])
        else:
            station_id = targets[op['client_ref']] if op.get('depends_on') else op['target_id']
            (add if kind == 'add_ski_area_relation' else remove).add((station_id, op['related_id']))
            # A real change to station membership also updates its editorial time.
            changes[station_id]
    insert_rows(database, Resort, new_rows)
    # New rows already have this timestamp; avoid redundant updates for links.
    new_ids = {row['id'] for row in new_rows}
    update_stations(database, {key: value for key, value in changes.items() if key not in new_ids}, now)
    marker = '%s' if isinstance(database, PostgresqlDatabase) else '?'
    for batch in _chunks(sorted(remove), 400):
        sql = ('DELETE FROM ' + quoted_table(SkiAreaResort) + ' WHERE ' +
               ' OR '.join('("resort_id"=' + marker + ' AND "ski_area_id"=' + marker + ')' for _ in batch))
        if database.execute_sql(sql, [value for pair in batch for value in pair]).rowcount != len(batch):
            raise ApplyError('stale_precondition', 'Relation removal affected unexpected rows', 409)
    links = [{'resort': station, 'ski_area': area,
              **({'created_at': now} if validated['schema'].has(SkiAreaResort, 'created_at') else {})}
             for station, area in sorted(add)]
    insert_rows(database, SkiAreaResort, links)
    return {'targets': targets, 'new_rows': new_rows, 'changes': dict(changes), 'add': add, 'remove': remove, 'timestamp': now}


def verify_written(validated, execution):
    """Relire tout l'état affecté avant COMMIT, jamais un SELECT par opération."""
    fields = set().union(*(row.keys() for row in execution['new_rows']),
                         *(row.keys() for row in execution['changes'].values())) - {'updated_at'}
    ids = set(execution['changes']) | {row['id'] for row in execution['new_rows']}
    rows = load_stations(validated['schema'], ids, fields | {'updated_at'})
    expected = {row['id']: dict(row) for row in execution['new_rows']}
    for identity, changes in execution['changes'].items():
        expected.setdefault(identity, {}).update(changes)
        expected[identity]['updated_at'] = execution['timestamp']
    for identity, values in expected.items():
        actual = rows.get(identity)
        if actual is None:
            raise ApplyError('post_write_verification_failed', 'Written station cannot be read back', 409)
        for field, value in values.items():
            if field in CONTENT_FIELDS:
                stored = ({'length': actual[field + '__length'], 'md5': actual[field + '__md5']}
                          if actual.get(field + '__length') is not None else None)
                equal = stored == _content(value)
            elif field == 'updated_at':
                equal = actual[field] == value
            elif field in {'name', 'id', 'slug'}:
                equal = actual[field] == value
            else:
                equal = canonical_json(json_normalized(normalize(field, actual[field]))) == canonical_json(json_normalized(normalize(field, value)))
            if not equal:
                raise ApplyError('post_write_verification_failed', 'Stored value differs from the approved value', 409, field=field)
    if execution['add'] or execution['remove']:
        links = relation_state(ids)
        if not execution['add'] <= links or execution['remove'] & links:
            raise ApplyError('post_write_verification_failed', 'Stored relation state differs from the approved plan', 409)
    return sorted({row['slug'] for row in rows.values()})
